"""Member-mode screens (M1-M7) plus the PWA plumbing (manifest, service worker,
icon, push subscribe) they all sit on top of. Coach mode (C1-C6) is a later
phase -- see design_handoff_rosterchief_platform/README.md -- and has no
routes here yet.
"""

import datetime
import itertools
import json

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import Http404, HttpResponse, HttpResponseBadRequest, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import TemplateView

from club.models import ClubMembership
from club.services.access import current_season, has_management_access, teams_managed_by
from club.services.fees import open_dues_rows
from club.services.onboarding import checklist_for
from club.services.sponsors import active_sponsors
from controlpanel.messages import notify
from events.models import Attendance, Event
from events.services.calendar import week_bounds
from members.models import FamilyMembership, Member
from members.views import ClubScopedPublicMixin
from news.models import News
from notifications.models import Notification
from teams.models import TeamMembership

from .forms import MemberProfileForm
from .mixins import PersonScopeMixin
from .models import CalendarFeedToken, PushSubscription
from .services.calendar_feed import build_feed
from .services.icons import render_fallback_icon


class ManifestView(ClubScopedPublicMixin, View):
    """Per-club web-app manifest -- the club's own logo (or its server-rendered
    initials, see services/icons.py) becomes the home-screen icon, never a
    generic RosterChief mark."""

    def get(self, request):
        club = request.club
        manifest = {
            "name": club.name,
            "short_name": club.name[:24],
            "start_url": "/app/",
            "scope": "/app/",
            "display": "standalone",
            "background_color": "#101e36",
            "theme_color": club.primary_color or "#101e36",
            "icons": [
                {"src": reverse("mobile:icon", kwargs={"size": 192}), "sizes": "192x192", "purpose": "any"},
                {"src": reverse("mobile:icon", kwargs={"size": 512}), "sizes": "512x512", "purpose": "any"},
            ],
        }
        return JsonResponse(manifest, content_type="application/manifest+json")


class AppIconView(ClubScopedPublicMixin, View):
    def get(self, request, size):
        club = request.club
        if club.logo:
            return HttpResponseRedirect(club.logo.url)
        return HttpResponse(render_fallback_icon(club, size=size), content_type="image/png")


class ServiceWorkerView(View):
    """Served literally at /app/sw.js (mobile/urls.py), not under /static/ --
    a service worker's default scope is everything below the path it's served
    from, so this is what makes the worker's scope /app/ without an explicit
    Service-Worker-Allowed header."""

    def get(self, request):
        return HttpResponse(render_to_string("mobile/sw.js", {}), content_type="application/javascript")


class PushSubscribeView(LoginRequiredMixin, ClubScopedPublicMixin, View):
    """Called by mobile/static/mobile/app.js once the browser hands back a
    PushSubscription. Keyed on endpoint (globally unique per browser
    registration, see PushSubscription's own docstring): re-subscribing the
    same browser updates its row instead of creating a duplicate."""

    def post(self, request):
        member = Member.objects.filter(user=request.user).first()
        if member is None:
            return HttpResponseBadRequest("No member record for this account.")

        try:
            payload = json.loads(request.body)
            endpoint = payload["endpoint"]
            p256dh = payload["keys"]["p256dh"]
            auth = payload["keys"]["auth"]
        except (KeyError, TypeError, ValueError):
            return HttpResponseBadRequest("Malformed subscription payload.")

        PushSubscription.objects.update_or_create(
            endpoint=endpoint,
            defaults={
                "club": request.club,
                "member": member,
                "p256dh": p256dh,
                "auth": auth,
                "user_agent": request.META.get("HTTP_USER_AGENT", "")[:255],
            },
        )
        return JsonResponse({"status": "ok"})

    def delete(self, request):
        try:
            endpoint = json.loads(request.body)["endpoint"]
        except (KeyError, TypeError, ValueError):
            return HttpResponseBadRequest("Malformed unsubscribe payload.")
        PushSubscription.objects.filter(endpoint=endpoint).delete()
        return JsonResponse({"status": "ok"})


class CalendarFeedView(ClubScopedPublicMixin, View):
    """The .ics subscription feed a calendar app polls -- URL-token
    authenticated, not LoginRequiredMixin (a calendar app can't do
    interactive login). See mobile.services.calendar_feed.build_feed for the
    feed's own shape/scope, and CalendarFeedToken's own docstring for why one
    token covers every club the account belongs to."""

    def get(self, request, token):
        feed_token = get_object_or_404(CalendarFeedToken.objects.select_related("user"), token=token)
        me = Member.objects.filter(user=feed_token.user).first()
        people = []
        if me is not None:
            children = Member.objects.filter(
                family_memberships__role=FamilyMembership.FamilyRole.CHILD,
                family_memberships__family__memberships__member=me,
                family_memberships__family__memberships__role__in=[FamilyMembership.FamilyRole.PARENT, FamilyMembership.FamilyRole.GUARDIAN],
                member_of__club=request.club,
            ).distinct()
            people = [me, *children]

        feed = build_feed(request.club, people)
        return HttpResponse(feed, content_type="text/calendar; charset=utf-8")


class CalendarFeedSettingsView(PersonScopeMixin, LoginRequiredMixin, TemplateView):
    """M5's "Calendar sync" row -- the webcal://.../https:// subscription
    links for this account's combined feed, plus a reset action that
    immediately invalidates the old URL (e.g. a link shared/leaked by
    accident)."""

    template_name = "mobile/calendar_feed_settings.html"
    screen_title = _("Calendar sync")
    active_tab = "me"

    def get_context_data(self, **kwargs):
        feed_token, _created = CalendarFeedToken.objects.get_or_create(user=self.request.user)
        feed_url = self.request.build_absolute_uri(reverse("mobile:calendar_feed", kwargs={"token": feed_token.token}))
        return super().get_context_data(feed_url=feed_url, webcal_url=feed_url.replace("https://", "webcal://", 1).replace("http://", "webcal://", 1), **kwargs)

    def post(self, request, *args, **kwargs):
        feed_token, _created = CalendarFeedToken.objects.get_or_create(user=request.user)
        feed_token.regenerate()
        title = _("Calendar link reset")
        body = _("Your old calendar link no longer works. Re-subscribe using the new one below.")
        notify(request, f"s|{title}|{body}")
        return HttpResponseRedirect(reverse("mobile:calendar_feed_settings"))


class HomeView(PersonScopeMixin, LoginRequiredMixin, TemplateView):
    """M1 -- design_handoff_rosterchief_platform/README.md's M1 section: a
    hero card for the soonest upcoming event across everyone currently in
    scope (with a quick In/Out RSVP -- see EventDetailView.post below), a
    "needs your answer" list of upcoming events still NO_RESPONSE/MAYBE, a
    season-dues card per person who owes money, and a news teaser. Every
    card is independently optional -- an empty-state screen is just the
    four `if`s below all being falsy.

    Scoped to ``self.people_in_scope`` (mobile/mixins.py), not just
    ``scope_person`` -- with the header's "All" chip now the default the
    moment there's more than one managed person, this screen aggregates
    across everyone in that case rather than showing only one person's
    cards.
    """

    template_name = "mobile/home.html"
    screen_title = _("Home")
    active_tab = "home"

    #: Keeps the card from crowding the dues/news cards below it off the first
    #: screenful -- Calendar is the place to see everything still awaiting a reply.
    NEEDS_ANSWER_LIMIT = 5
    #: Same reasoning -- mobile:news_list is the place to see everything.
    NEWS_LIMIT = 3

    def get_context_data(self, **kwargs):
        people = self.people_in_scope
        now = timezone.now()

        hero_attendance = None
        rsvp_closed = False
        needs_answer = []
        needs_answer_total = 0
        dues_rows = []
        news_items = []

        if people:
            upcoming = Attendance.objects.filter(
                member__in=people,
                event__club=self.request.club,
                event__cancelled=False,
                event__start__gte=now,
            ).select_related("event", "event__location", "member")

            hero_attendance = upcoming.order_by("event__start").first()
            if hero_attendance is not None:
                deadline = hero_attendance.event.deadline
                rsvp_closed = deadline is not None and deadline < now

            needs_answer_qs = upcoming.filter(status__in=[Attendance.AttendanceStatus.NO_RESPONSE, Attendance.AttendanceStatus.MAYBE]).order_by("event__start")
            if hero_attendance is not None:
                needs_answer_qs = needs_answer_qs.exclude(pk=hero_attendance.pk)
            needs_answer_total = needs_answer_qs.count()
            needs_answer = list(needs_answer_qs[: self.NEEDS_ANSWER_LIMIT])

            season = current_season(self.request.club)
            if season is not None:
                dues_rows = open_dues_rows(self.request.club, people, season)
                team_ids = list(TeamMembership.objects.filter(member__in=people, season=season).values_list("team_id", flat=True))
            else:
                team_ids = []

            news_items = list(
                News.objects.filter(
                    club=self.request.club,
                    status=News.Status.PUBLISHED,
                    published_at__lte=now,
                    visibility__in=[News.Visibility.INTERNAL, News.Visibility.BOTH],
                )
                .filter(Q(teams__isnull=True) | Q(teams__id__in=team_ids))
                .prefetch_related("teams")
                .order_by("-published_at")
                .distinct()[: self.NEWS_LIMIT]
            )

        return super().get_context_data(
            hero_attendance=hero_attendance,
            rsvp_closed=rsvp_closed,
            needs_answer=needs_answer,
            needs_answer_remaining=max(needs_answer_total - len(needs_answer), 0),
            dues_rows=dues_rows,
            news_items=news_items,
            # Club-wide, not person-specific -- shown regardless of managed_people,
            # unlike every other card on this screen. Reshuffled on every request
            # (see club.services.sponsors.active_sponsors) rather than once per
            # session, same as the public-website sponsor strip it shares logic with.
            sponsors=active_sponsors(self.request.club, randomize=True),
            **kwargs,
        )


class CalendarView(PersonScopeMixin, LoginRequiredMixin, TemplateView):
    """M3 -- README's M3 section: a chronological agenda list (not the desktop
    week/month grid events.services.calendar was built for) grouped under
    "This week"/"Next week", then everything further out grouped by calendar
    month (a "later_months" list of {month_start, rows}, each its own sticky
    header) -- an unbounded agenda rather than a fixed lookahead window, since
    a hard cutoff just hid events a member would reasonably expect to still
    find here. No ?month= paging beyond that grouping -- there's no season
    browser here, just "everything upcoming, readably grouped".

    Always scoped to every one of ``self.managed_people`` -- unlike Home,
    this screen has no person switcher and no "every club event" toggle: it's
    just "what is my family invited to", full stop. A ``?kind=`` filter (All/
    Games/Practices, the two dominant event kinds) narrows that down -- a
    real, working version of the design mock's own "Games only" pill, not
    reproducing its List/Month toggle (that needs a whole second view mode,
    not a filter) or its per-person "All members" (removed on purpose, see
    above).
    """

    template_name = "mobile/calendar.html"
    screen_title = _("Calendar")
    active_tab = "calendar"

    #: ?kind= values this screen actually understands, mapped to the Event.EventKind
    #: they filter on -- anything else (including no param at all) means "All".
    KIND_FILTERS = {"game": Event.EventKind.GAME, "training": Event.EventKind.TRAINING}

    #: Pill styling for a per-person RSVP status (assets/mobile.css's .pill-*).
    STATUS_PILL_CLASSES = {
        Attendance.AttendanceStatus.PRESENT: "pill-ok",
        Attendance.AttendanceStatus.SELECTED: "pill-ok",
        Attendance.AttendanceStatus.ABSENT: "pill-danger",
        Attendance.AttendanceStatus.NOT_SELECTED: "pill-neutral",
        Attendance.AttendanceStatus.EXCUSED: "pill-neutral",
        Attendance.AttendanceStatus.MAYBE: "pill-warn",
        Attendance.AttendanceStatus.NO_RESPONSE: "pill-warn",
    }

    def get_context_data(self, **kwargs):
        now = timezone.now()
        today = timezone.localdate()
        _this_week_start, this_week_end = week_bounds(today)
        next_week_end = this_week_end + datetime.timedelta(days=7)
        kind_filter = self.request.GET.get("kind")

        rows = []
        if self.managed_people:
            attendances = (
                Attendance.objects.filter(
                    member__in=self.managed_people,
                    event__club=self.request.club,
                    event__cancelled=False,
                    event__start__gte=now,
                )
                .select_related("event", "event__location", "event__opponent", "member")
                .prefetch_related("event__teams")
                .order_by("event__start")
            )
            if kind_filter in self.KIND_FILTERS:
                attendances = attendances.filter(event__kind=self.KIND_FILTERS[kind_filter])

            # Only worth naming whose row it is once there's more than one managed
            # person to tell apart -- a lone member's own agenda doesn't need it.
            show_member = len(self.managed_people) > 1
            rows = [
                {"event": attendance.event, "pill_class": self.STATUS_PILL_CLASSES.get(attendance.status, "pill-neutral"), "pill_label": attendance.get_status_display(), "member": attendance.member if show_member else None}
                for attendance in attendances
            ]

        this_week, next_week, later_rows = [], [], []
        for row in rows:
            event_date = timezone.localtime(row["event"].start).date()
            if event_date <= this_week_end:
                this_week.append(row)
            elif event_date <= next_week_end:
                next_week.append(row)
            else:
                later_rows.append(row)

        # ``rows`` is already start-ordered, so a plain groupby (no sorting) is
        # enough to split later_rows into one chronological run per month.
        later_months = [
            {"month_start": month_start, "rows": list(month_rows)}
            for month_start, month_rows in itertools.groupby(later_rows, key=lambda row: timezone.localtime(row["event"].start).date().replace(day=1))
        ]

        return super().get_context_data(this_week=this_week, next_week=next_week, later_months=later_months, kind_filter=kind_filter if kind_filter in self.KIND_FILTERS else "", **kwargs)


class EventDetailView(PersonScopeMixin, LoginRequiredMixin, TemplateView):
    """M2 -- design_handoff_rosterchief_platform/README.md's M2 section:
    "answer for several". A hero header (no event photos in this codebase --
    a solid dark block instead, like M1's hero card), an event-detail card
    (Face-off/Meet/Where -- the design mock's extra "Kit" row and dressing-room
    detail have no backing Event field, so they're simply not shown), a
    per-managed-person RSVP card scoped to ``self.managed_people`` who
    actually have an ``Attendance`` row for this event (i.e. are invited --
    not every managed person necessarily is), and a club/team-visible
    squad-response aggregate (counts only, never who-answered-what) shown
    only when the event has an actual team roster to aggregate.

    POST is still M1's quick In/Out RSVP action (now also accepting "maybe"
    for M2's three-way buttons), reused by any screen that posts the same
    {status, member_id} shape at this URL. An optional ``next=event_detail``
    field redirects back here instead of Home -- M2's own forms send it so a
    parent answering for several people in a row sees each update land
    without bouncing away; M1's/M3's existing forms don't send it, so they
    keep redirecting to Home unchanged.
    """

    template_name = "mobile/event_detail.html"
    screen_title = _("Event")
    active_tab = "calendar"

    def get_context_data(self, **kwargs):
        event = get_object_or_404(Event.objects.select_related("location", "opponent"), pk=self.kwargs["pk"], club=self.request.club)
        season = event.season or current_season(self.request.club)
        rsvp_closed = event.deadline is not None and event.deadline < timezone.now()

        your_answers = []
        if self.managed_people:
            managed_ids = [person.pk for person in self.managed_people]
            attendances_by_member = {attendance.member_id: attendance for attendance in Attendance.objects.filter(event=event, member_id__in=managed_ids).select_related("member")}

            memberships_by_member = {}
            if season is not None:
                memberships_by_member = {
                    membership.member_id: membership
                    for membership in TeamMembership.objects.filter(member_id__in=managed_ids, team__in=event.teams.all(), season=season).select_related("team", "position")
                }

            for person in self.managed_people:
                attendance = attendances_by_member.get(person.pk)
                if attendance is None:
                    continue
                your_answers.append({"member": person, "attendance": attendance, "membership": memberships_by_member.get(person.pk)})

        squad_summary = None
        if event.teams.exists():
            counts = Attendance.objects.filter(event=event).aggregate(
                in_count=Count("id", filter=Q(status__in=[Attendance.AttendanceStatus.PRESENT, Attendance.AttendanceStatus.SELECTED])),
                out_count=Count("id", filter=Q(status__in=[Attendance.AttendanceStatus.ABSENT, Attendance.AttendanceStatus.NOT_SELECTED])),
                no_reply_count=Count("id", filter=Q(status__in=[Attendance.AttendanceStatus.MAYBE, Attendance.AttendanceStatus.NO_RESPONSE])),
            )
            total = counts["in_count"] + counts["out_count"] + counts["no_reply_count"]
            if total:
                squad_summary = {
                    "total": total,
                    "responded": counts["in_count"] + counts["out_count"],
                    "in_count": counts["in_count"],
                    "out_count": counts["out_count"],
                    "no_reply_count": counts["no_reply_count"],
                    "in_pct": round(100 * counts["in_count"] / total),
                    "out_pct": round(100 * counts["out_count"] / total),
                    "no_reply_pct": round(100 * counts["no_reply_count"] / total),
                }

        return super().get_context_data(screen_title=event.title, event=event, rsvp_closed=rsvp_closed, your_answers=your_answers, squad_summary=squad_summary, **kwargs)

    def post(self, request, *args, **kwargs):
        status = request.POST.get("status")
        if status not in (Attendance.AttendanceStatus.PRESENT, Attendance.AttendanceStatus.ABSENT, Attendance.AttendanceStatus.MAYBE):
            return HttpResponseBadRequest(_("Unknown RSVP status."))

        # Every current caller (Home's hero, M2's per-person rows) always sends an
        # explicit member_id -- this is just a defensive fallback for one that doesn't.
        fallback_member = self.scope_person or self.me
        member_id = request.POST.get("member_id") or (str(fallback_member.pk) if fallback_member else None)
        member = next((person for person in self.managed_people if str(person.pk) == member_id), None) if member_id else None
        if member is None:
            return HttpResponseBadRequest(_("You can't RSVP for that person."))

        event = get_object_or_404(Event, pk=kwargs["pk"], club=request.club)
        # Mirrors the read-only treatment Home's hero and this same screen's own
        # "Your answers" card already show once the deadline has passed (see
        # get_context_data's rsvp_closed) -- enforced here too, since a disabled
        # button in the UI is only a hint, not a guarantee against a direct POST.
        if event.deadline is not None and event.deadline < timezone.now():
            return HttpResponseBadRequest(_("Replies are closed for this event."))

        Attendance.objects.update_or_create(event=event, member=member, defaults={"status": status})

        if request.POST.get("next") == "event_detail":
            return HttpResponseRedirect(reverse("mobile:event_detail", kwargs={"pk": event.pk}))
        return HttpResponseRedirect(reverse("mobile:home"))


class NewsListView(PersonScopeMixin, LoginRequiredMixin, TemplateView):
    """"All news" -- what Home's own news card links to once there's more than
    NEWS_LIMIT items to show. Unlike Home's own teaser (team-relevant items
    only), this is the full browsable archive: every published, member-visible
    news item for the club, newest first, regardless of which team it's about.
    Same visibility rule as Home -- internal or both, never external-only
    (that's the public website's own audience, not this app's)."""

    template_name = "mobile/news_list.html"
    screen_title = _("News")
    active_tab = "news"
    PAGE_SIZE = 20

    def get_context_data(self, **kwargs):
        news_items = (
            News.objects.filter(
                club=self.request.club,
                status=News.Status.PUBLISHED,
                published_at__lte=timezone.now(),
                visibility__in=[News.Visibility.INTERNAL, News.Visibility.BOTH],
            )
            .prefetch_related("teams")
            .order_by("-published_at")
        )
        page = Paginator(news_items, self.PAGE_SIZE).get_page(self.request.GET.get("page"))
        return super().get_context_data(page=page, **kwargs)


class NewsDetailView(PersonScopeMixin, LoginRequiredMixin, TemplateView):
    """M4 -- design_handoff_rosterchief_platform/README.md's M4 section: a
    photo-hero permalink for a single published News item. Visibility mirrors
    news.tasks.notify_news_published's own gate -- PUBLISHED *and* actually
    live (published_at in the past) -- so a scheduled-but-not-yet-live item
    404s here exactly like it does everywhere else a member could reach it,
    rather than leaking a preview of it early.

    Language is Django's own active-language detection, not a member-facing
    toggle (unlike management's split-view Dutch/English editing tool) --
    English shows only when the request's active language actually is "en";
    everything else (including no active language at all) shows the native
    (Dutch) text.
    """

    template_name = "mobile/news_detail.html"
    screen_title = _("News")
    active_tab = "news"

    def get_context_data(self, **kwargs):
        news_item = get_object_or_404(
            News.objects.prefetch_related("teams", "photos"),
            club=self.request.club,
            slug=self.kwargs["slug"],
            status=News.Status.PUBLISHED,
            published_at__lte=timezone.now(),
        )

        if get_language() == "en":
            title, body = news_item.effective_title_en, news_item.effective_body_en
        else:
            title, body = news_item.title, news_item.body

        return super().get_context_data(
            screen_title=title,
            news_item=news_item,
            article_title=title,
            article_body=body,
            **kwargs,
        )


class MeView(PersonScopeMixin, LoginRequiredMixin, TemplateView):
    """M5 -- design_handoff_rosterchief_platform/README.md's M5 section, "Me
    & my people". A header for ``self.me`` (member-since year plus a staff
    label, see below), a "People I manage" card (one row per managed_people,
    each with their current-season team + jersey number when they're on a
    roster), and a settings-ish card linking into M6 (edit_profile) for
    ``self.me`` and into M7 (notifications).

    The mockup's "Household & contacts" row and its "Coach mode" promo card
    have nowhere to lead in this build (no dedicated screen, no Coach mode
    screens at all yet -- see base.html's own comment) and are deliberately
    omitted rather than built as dead or inert links. "Payments & dues" does
    lead somewhere -- PaymentsView below -- with its "N OPEN" pill only
    rendered once there's actually a balance owed.

    There's no license/eligibility field on Member or ClubMembership to power
    the mockup's "licence OK" text, so each managed person's meta line is
    real roster data instead: current-season team + jersey number when
    they're on one, nothing extra otherwise.
    """

    template_name = "mobile/me.html"
    screen_title = _("Me")
    active_tab = "me"

    def get_context_data(self, **kwargs):
        club = self.request.club
        season = current_season(club)

        member_since = None
        team_manager_label = None

        if self.me is not None and season is not None:
            membership = ClubMembership.objects.filter(club=club, member=self.me, season=season).first()
            if membership is not None:
                member_since = membership.created

        # PersonScopeMixin.get_context_data computes this same check for the
        # ``has_staff_access`` context var, but only as a context value, not
        # an attribute on self -- recomputed here since it's needed before
        # that runs.
        if self.me is not None and has_management_access(self.request.user, club):
            # teams_managed_by returns *every* club team for an ADMIN (see its
            # own docstring) -- fine for authority checks, but a wall of team
            # names makes a poor header subtitle, so it only gets spelled out
            # for someone managing a small, specific handful; anyone else
            # (including a full club admin) just reads as "Staff".
            managed_teams = list(teams_managed_by(self.request.user, club))
            if 1 <= len(managed_teams) <= 2:
                team_manager_label = _("Team manager %(teams)s") % {"teams": ", ".join(sorted(team.short_name for team in managed_teams))}
            else:
                team_manager_label = _("Staff")

        people_rows = []
        if self.managed_people:
            memberships_by_member = {}
            if season is not None:
                memberships_by_member = {
                    membership.member_id: membership
                    for membership in TeamMembership.objects.filter(member__in=self.managed_people, season=season).select_related("team")
                }
            people_rows = [{"person": person, "membership": memberships_by_member.get(person.pk)} for person in self.managed_people]

        open_dues_count = len(open_dues_rows(club, self.managed_people, season))

        return super().get_context_data(
            member_since=member_since,
            team_manager_label=team_manager_label,
            people_rows=people_rows,
            open_dues_count=open_dues_count,
            **kwargs,
        )


class PaymentsView(PersonScopeMixin, LoginRequiredMixin, TemplateView):
    """M5's "Payments & dues" row -- every open season-dues balance across
    ``self.managed_people``, one card per person owed, in the same €-badge /
    amount+due-date / Pay layout as Home's dues card (club.services.fees.
    open_dues_rows is the shared source for both). No online payment gateway
    exists yet, so "Pay" is the same non-functional stub as Home's -- this
    screen's job is visibility ("what do I owe, and when"), not collection.
    """

    template_name = "mobile/payments.html"
    screen_title = _("Payments & dues")
    active_tab = "me"

    def get_context_data(self, **kwargs):
        season = current_season(self.request.club)
        dues_rows = open_dues_rows(self.request.club, self.managed_people, season)
        return super().get_context_data(dues_rows=dues_rows, **kwargs)


class EditProfileView(PersonScopeMixin, LoginRequiredMixin, TemplateView):
    """M6 -- design_handoff_rosterchief_platform/README.md's M6 section,
    "Edit personal info". The design mock also shows a "National register
    no.", an "Address", an "Allergies / notes" field and two "Consent"
    toggles (photos on club channels / share contact with team parents) --
    none of those exist on ``members.models.Member``, so (per this build's
    "no schema changes for a screen-building pass" rule) they're simply not
    part of this screen; MemberProfileForm (mobile/forms.py) only covers the
    fields the model actually has.

    Two more mock rows *do* have real backing data, both rendered read-only
    (never editable here -- family links and staff document review each live
    elsewhere in the platform, not on a member's own edit-info screen):
      - "Contact 1" becomes every one of ``Member.guardians`` (there can be
        more than one, unlike the mock's single row), each with its real
        FamilyMembership role (parent/guardian/other) rather than the mock's
        invented "mother".
      - The "Medical form missing" banner becomes a real, club-defined
        open-requirements list from club.services.onboarding.checklist_for,
        scoped to this person's *current-season* ClubMembership -- shown only
        when at least one active requirement is neither complete nor
        bypassed (an "informational" open requirement with no current-season
        membership at all just means the banner never renders).

    Authorization: the target Member (``member_id`` URL kwarg) must be one of
    ``self.managed_people`` -- anyone else 404s, on both GET and POST. A 404
    (not EventDetailView.post's 400) is the deliberate choice here: that 400
    is for a malformed *value* inside an otherwise-valid POST to a resource
    the requester can already see (the event); this is a different resource
    per person, named directly in the URL, so an unmanaged member should read
    as "no such page" exactly like Event/News already do for another club's
    objects elsewhere in this file, not as a submission-shaped error.
    """

    template_name = "mobile/edit_profile.html"
    screen_title = _("Edit info")
    active_tab = "me"

    def _target_member(self):
        member_id = str(self.kwargs["member_id"])
        member = next((person for person in self.managed_people if str(person.pk) == member_id), None)
        if member is None:
            raise Http404("You can't edit that profile.")
        return member

    def get(self, request, *args, **kwargs):
        member = self._target_member()
        form = MemberProfileForm(instance=member)
        return self.render_to_response(self.get_context_data(member=member, form=form))

    def post(self, request, *args, **kwargs):
        member = self._target_member()
        form = MemberProfileForm(request.POST, instance=member)
        if form.is_valid():
            form.save()
            title = _("Saved")
            body = _("%(name)s's info was updated.") % {"name": member.get_full_name()}
            notify(request, f"s|{title}|{body}")
            return HttpResponseRedirect(reverse("mobile:me"))
        return self.render_to_response(self.get_context_data(member=member, form=form))

    def get_context_data(self, **kwargs):
        member = kwargs["member"]

        guardian_rows = [
            {"member": family_membership.member, "role": family_membership.get_role_display()}
            for family_membership in FamilyMembership.objects.filter(
                role__in=[FamilyMembership.FamilyRole.PARENT, FamilyMembership.FamilyRole.GUARDIAN],
                family__memberships__member=member,
                family__memberships__role=FamilyMembership.FamilyRole.CHILD,
            )
            .select_related("member")
            .distinct()
        ]

        open_requirement_names = []
        season = current_season(self.request.club)
        if season is not None:
            membership = ClubMembership.objects.filter(club=self.request.club, member=member, season=season).first()
            if membership is not None:
                open_requirement_names = [requirement.name for requirement, status in checklist_for(membership) if status is None or not (status.is_complete or status.is_bypassed)]

        return super().get_context_data(
            screen_title=member.get_full_name(),
            guardian_rows=guardian_rows,
            open_requirement_names=open_requirement_names,
            open_requirement_summary=", ".join(open_requirement_names),
            **kwargs,
        )


def _notification_source_link(source):
    """(label, url) for a notification's ``source`` when it's something this
    app has a detail page for -- (None, None) otherwise. One place both
    NotificationsView.get_context_data (the row's own label) and .post (the
    tap-to-mark-read redirect target) read from, so a new source type only
    needs adding here."""
    if isinstance(source, News):
        return _("Club news"), reverse("mobile:news_detail", kwargs={"slug": source.slug})
    if isinstance(source, Event):
        return _("Event"), reverse("mobile:event_detail", kwargs={"pk": source.pk})
    return None, None


class NotificationsView(PersonScopeMixin, LoginRequiredMixin, TemplateView):
    """M7 -- design_handoff_rosterchief_platform/README.md's M7 section
    ("Inbox"). The mockup shows rich per-type cards (RSVP-needed, medical
    form missing, invoice due, line-up published, ...) with inline quick
    actions, but notifications.models.Notification is generic -- title/body/
    created/read_at plus an optional ``source`` -- and the only things that
    create member-facing rows today are news.tasks.notify_news_published and
    events.tasks.notify_new_event. There's no type/category field to key a
    richer layout or the mockup's "Action"/"Club" filter off, so this is
    deliberately a flat, generic list: day-grouped ("Today"/"Earlier this
    week"/"Older", echoing Calendar's own "This week"/"Next week" bucketing)
    with an unread treatment and a link to the underlying News/Event when
    ``source`` resolves to one (see _notification_source_link above).

    Scoped to every one of ``self.managed_people`` (not just scope_person) --
    same scope PersonScopeMixin.get_context_data already uses for
    unread_notification_count, since notifications aren't really "per
    switched person" the way RSVPs are.
    """

    template_name = "mobile/notifications.html"
    screen_title = _("Notifications")
    active_tab = "me"

    def get_context_data(self, **kwargs):
        today = []
        earlier_this_week = []
        older = []

        if self.managed_people:
            this_week_start, _this_week_end = week_bounds(timezone.localdate())
            local_today = timezone.localdate()

            notifications = Notification.objects.filter(club=self.request.club, member__in=self.managed_people).select_related("member").order_by("-created")

            for notification in notifications:
                source_label, _url = _notification_source_link(notification.source)
                row = {"notification": notification, "source_label": source_label}

                created_date = timezone.localtime(notification.created).date()
                if created_date == local_today:
                    today.append(row)
                elif created_date >= this_week_start:
                    earlier_this_week.append(row)
                else:
                    older.append(row)

        return super().get_context_data(today=today, earlier_this_week=earlier_this_week, older=older, **kwargs)

    def post(self, request, *args, **kwargs):
        action = request.POST.get("action")

        if action == "mark_all_read":
            Notification.objects.filter(club=request.club, member__in=self.managed_people, read_at__isnull=True).update(read_at=timezone.now())
            return HttpResponseRedirect(reverse("mobile:notifications"))

        if action == "clear_all":
            # A hard delete, not another read-state flip -- "Clear" empties the
            # list itself, same as the clear-all gesture in a phone's own
            # notification centre, rather than just marking everything read.
            Notification.objects.filter(club=request.club, member__in=self.managed_people).delete()
            return HttpResponseRedirect(reverse("mobile:notifications"))

        if action == "mark_read":
            notification = Notification.objects.filter(pk=request.POST.get("notification_id"), club=request.club, member__in=self.managed_people).first()
            if notification is None:
                return HttpResponseBadRequest(_("You can't mark that notification as read."))
            if notification.read_at is None:
                notification.read_at = timezone.now()
                notification.save(update_fields=["read_at", "modified"])

            # A row whose source resolves to something this app has a detail
            # page for doubles as a link to it (see _notification_source_link)
            # -- the plain-POST tap that marks it read also lands the member
            # there, no separate "next" field needed since the server already
            # has the source.
            _label, url = _notification_source_link(notification.source)
            return HttpResponseRedirect(url or reverse("mobile:notifications"))

        return HttpResponseBadRequest(_("Unknown action."))
