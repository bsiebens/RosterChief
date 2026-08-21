"""Member-mode screens (M1-M7) plus the PWA plumbing (manifest, service worker,
icon, push subscribe) they all sit on top of. Coach mode (C1-C6) is a later
phase -- see design_handoff_rosterchief_platform/README.md -- and has no
routes here yet.

The M1-M7 views below are placeholders: each renders a "coming soon" card
inside the real app shell (base.html), at its final URL name, so the shell
(header, role switcher, tab bar, person switcher) can be verified end-to-end
before every screen is built out one at a time.
"""

import datetime
import json

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, Q
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import TemplateView

from club.models import ClubMembership
from club.services.access import current_season
from club.services.fees import remaining_balance
from events.models import Attendance, Event
from events.services.calendar import week_bounds
from members.models import Member
from members.views import ClubScopedPublicMixin
from news.models import News
from notifications.models import Notification
from teams.models import TeamMembership

from .mixins import PersonScopeMixin
from .models import PushSubscription
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


class _PlaceholderScreen(PersonScopeMixin, LoginRequiredMixin, TemplateView):
    """Stand-in for an M-screen not built yet. Each subclass below is replaced
    entirely -- view and template -- when its screen is built; only the URL
    name/path in mobile/urls.py needs to stay put."""

    template_name = "mobile/_placeholder.html"
    screen_title = ""
    active_tab = ""

    def get_context_data(self, **kwargs):
        return super().get_context_data(screen_title=self.screen_title, active_tab=self.active_tab, **kwargs)


class HomeView(PersonScopeMixin, LoginRequiredMixin, TemplateView):
    """M1 -- design_handoff_rosterchief_platform/README.md's M1 section: a
    hero card for scope_person's soonest upcoming event (with a quick In/Out
    RSVP -- see EventDetailView.post below), a "needs your answer" list of
    upcoming events still NO_RESPONSE/MAYBE, a season-dues card when money is
    owed, and a news teaser. Every card is independently optional -- an
    empty-state screen is just the four `if`s below all being falsy.
    """

    template_name = "mobile/home.html"
    screen_title = _("Home")
    active_tab = "home"

    def get_context_data(self, **kwargs):
        scope_person = self.scope_person
        now = timezone.now()

        hero_attendance = None
        rsvp_closed = False
        needs_answer = []
        dues_membership = None
        dues_balance = None
        dues_invoice = None
        news_item = None

        if scope_person is not None:
            upcoming = Attendance.objects.filter(
                member=scope_person,
                event__club=self.request.club,
                event__cancelled=False,
                event__start__gte=now,
            ).select_related("event", "event__location")

            hero_attendance = upcoming.order_by("event__start").first()
            if hero_attendance is not None:
                deadline = hero_attendance.event.deadline
                rsvp_closed = deadline is not None and deadline < now

            needs_answer_qs = upcoming.filter(status__in=[Attendance.AttendanceStatus.NO_RESPONSE, Attendance.AttendanceStatus.MAYBE]).order_by("event__start")
            if hero_attendance is not None:
                needs_answer_qs = needs_answer_qs.exclude(pk=hero_attendance.pk)
            needs_answer = list(needs_answer_qs)

            season = current_season(self.request.club)
            if season is not None:
                membership = (
                    ClubMembership.objects.filter(club=self.request.club, member=scope_person, season=season)
                    .exclude(fee_status=ClubMembership.FeeStatus.WAIVED)
                    .select_related("dues_invoice")
                    .first()
                )
                if membership is not None:
                    balance = remaining_balance(membership)
                    if balance > 0:
                        dues_membership = membership
                        dues_balance = balance
                        dues_invoice = getattr(membership, "dues_invoice", None)

                team_ids = list(TeamMembership.objects.filter(member=scope_person, season=season).values_list("team_id", flat=True))
            else:
                team_ids = []

            news_item = (
                News.objects.filter(club=self.request.club, status=News.Status.PUBLISHED, published_at__lte=now)
                .filter(Q(teams__isnull=True) | Q(teams__id__in=team_ids))
                .order_by("-published_at")
                .distinct()
                .first()
            )

        return super().get_context_data(
            hero_attendance=hero_attendance,
            rsvp_closed=rsvp_closed,
            needs_answer=needs_answer,
            dues_membership=dues_membership,
            dues_balance=dues_balance,
            dues_invoice=dues_invoice,
            news_item=news_item,
            news_team=news_item.teams.first() if news_item is not None else None,
            **kwargs,
        )


class CalendarView(PersonScopeMixin, LoginRequiredMixin, TemplateView):
    """M3 -- README's M3 section: a chronological agenda list (not the desktop
    week/month grid events.services.calendar was built for) grouped under
    "This week"/"Next week". Browsing-window judgment call: the design doc
    doesn't specify month navigation for the mobile screen, so this only ever
    shows *upcoming* events across the current and next calendar week (no
    "Later"/past bucket, no ?month= paging) -- a simple, bounded agenda rather
    than a full season browser.

    ``?scope=all`` is the design doc's extra "All members" scope on top of
    the normal per-person chip switcher (mobile/mixins.py's scope_person):
    every club event instead of just scope_person's own invites, since
    there's no single person's Attendance row to key off.
    """

    template_name = "mobile/calendar.html"
    screen_title = _("Calendar")
    active_tab = "calendar"

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
        scope_all = self.request.GET.get("scope") == "all"
        now = timezone.now()
        _this_week_start, this_week_end = week_bounds(timezone.localdate())
        next_week_end = this_week_end + datetime.timedelta(days=7)
        window_end = timezone.make_aware(datetime.datetime.combine(next_week_end, datetime.time.max))

        rows = []
        if scope_all:
            events = (
                Event.objects.filter(club=self.request.club, cancelled=False, start__gte=now, start__lte=window_end)
                .select_related("location", "opponent")
                .prefetch_related("teams")
                .order_by("start")
            )
            rows = [{"event": event, "pill_class": "pill-info", "pill_label": event.get_kind_display()} for event in events]
        elif self.scope_person is not None:
            attendances = (
                Attendance.objects.filter(
                    member=self.scope_person,
                    event__club=self.request.club,
                    event__cancelled=False,
                    event__start__gte=now,
                    event__start__lte=window_end,
                )
                .select_related("event", "event__location", "event__opponent")
                .prefetch_related("event__teams")
                .order_by("event__start")
            )
            rows = [{"event": attendance.event, "pill_class": self.STATUS_PILL_CLASSES.get(attendance.status, "pill-neutral"), "pill_label": attendance.get_status_display()} for attendance in attendances]

        this_week, next_week = [], []
        for row in rows:
            bucket = this_week if timezone.localtime(row["event"].start).date() <= this_week_end else next_week
            bucket.append(row)

        return super().get_context_data(scope_all=scope_all, this_week=this_week, next_week=next_week, **kwargs)


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

        return super().get_context_data(screen_title=event.title, event=event, your_answers=your_answers, squad_summary=squad_summary, **kwargs)

    def post(self, request, *args, **kwargs):
        status = request.POST.get("status")
        if status not in (Attendance.AttendanceStatus.PRESENT, Attendance.AttendanceStatus.ABSENT, Attendance.AttendanceStatus.MAYBE):
            return HttpResponseBadRequest(_("Unknown RSVP status."))

        member_id = request.POST.get("member_id") or (str(self.scope_person.pk) if self.scope_person else None)
        member = next((person for person in self.managed_people if str(person.pk) == member_id), None) if member_id else None
        if member is None:
            return HttpResponseBadRequest(_("You can't RSVP for that person."))

        event = get_object_or_404(Event, pk=kwargs["pk"], club=request.club)
        Attendance.objects.update_or_create(event=event, member=member, defaults={"status": status})

        if request.POST.get("next") == "event_detail":
            return HttpResponseRedirect(reverse("mobile:event_detail", kwargs={"pk": event.pk}))
        return HttpResponseRedirect(reverse("mobile:home"))


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


class MeView(_PlaceholderScreen):
    screen_title = _("Me")
    active_tab = "me"


class EditProfileView(_PlaceholderScreen):
    screen_title = _("Edit info")
    active_tab = "me"


class NotificationsView(PersonScopeMixin, LoginRequiredMixin, TemplateView):
    """M7 -- design_handoff_rosterchief_platform/README.md's M7 section
    ("Inbox"). The mockup shows rich per-type cards (RSVP-needed, medical
    form missing, invoice due, line-up published, ...) with inline quick
    actions, but notifications.models.Notification is generic -- title/body/
    created/read_at plus an optional ``source`` -- and the only thing that
    creates member-facing rows today is news.tasks.notify_news_published.
    There's no type/category field to key a richer layout or the mockup's
    "Action"/"Club" filter off, so this is deliberately a flat, generic list:
    day-grouped ("Today"/"Earlier this week"/"Older", echoing Calendar's own
    "This week"/"Next week" bucketing) with an unread treatment and a link to
    the underlying News item when ``source`` happens to resolve to one.

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
                source = notification.source
                news_item = source if isinstance(source, News) else None
                row = {"notification": notification, "news_item": news_item}

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

        if action == "mark_read":
            notification = Notification.objects.filter(pk=request.POST.get("notification_id"), club=request.club, member__in=self.managed_people).first()
            if notification is None:
                return HttpResponseBadRequest(_("You can't mark that notification as read."))
            if notification.read_at is None:
                notification.read_at = timezone.now()
                notification.save(update_fields=["read_at", "modified"])

            # A row whose source resolves to a News item doubles as a link to
            # it (see the class docstring) -- the plain-POST tap that marks
            # it read also lands the member on the article, no separate
            # "next" field needed since the server already has the source.
            if isinstance(notification.source, News):
                return HttpResponseRedirect(reverse("mobile:news_detail", kwargs={"slug": notification.source.slug}))
            return HttpResponseRedirect(reverse("mobile:notifications"))

        return HttpResponseBadRequest(_("Unknown action."))
