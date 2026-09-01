"""Member-mode screens (M1-M7) plus the PWA plumbing (manifest, service worker,
icon, push subscribe) they all sit on top of. Coach mode (C1-C6) is a later
phase -- see design_handoff_rosterchief_platform/README.md -- and has no
routes here yet.
"""

import json
from collections import defaultdict
from decimal import Decimal

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.db.models import Count, F, Q
from django.http import Http404, HttpResponse, HttpResponseBadRequest, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import TemplateView
from waffle import flag_is_active

from club.models import ClubMembership
from club.services.access import current_season, has_management_access, teams_managed_by
from club.services.fees import open_dues_rows
from club.services.onboarding import checklist_for, open_requirements_blocking
from club.services.sponsors import active_sponsors
from controlpanel.messages import notify
from events.models import Attendance, Event, EventTask, Lineup, OfficialSignup, RefereeSignup
from events.services.attendance import blocked_upcoming_events_for_member
from events.services.calendar import agenda_groups, week_bounds
from events.services.lineup import notify_dropout, selected_members_by_position
from events.services.officials import OfficialAssignmentError, accept_official_signup, decline_official_signup
from events.services.referees import RefereeAssignmentError, accept_referee_signup, decline_referee_signup
from events.services.tasks import TaskClaimError, claim_task, unclaim_task
from formbuilder.models import FormSend, Submission
from formbuilder.services import FormSubmissionError, build_form, form_status_rows_for, is_send_open, submit_form
from members.models import FamilyMembership, Member
from members.services.family import claim_label_for
from members.views import ClubScopedPublicMixin
from news.models import News
from news.services import render_body_html
from notifications.models import Notification
from registration.forms import RegistrationEntryFormSet, entries_from_formset
from registration.models import RegistrationBatch
from registration.services import (
    PricingError,
    RegistrationError,
    available_registration_products,
    price_entries,
    priced_rows_with_jersey_fields,
    resolve_chosen_season,
    resolve_registration_season,
    submit_registration,
    team_number_pools,
    variant_registration_kinds,
)
from registration.services.invoicing import RegistrationInvoicePDFError, active_batch_entries, batch_early_payment_offer, batch_invoice_pdf, batch_totals, membership_ids_awaiting_confirmation
from registration.services.notifications import send_registration_confirmation_email
from shop.models import Cart, CartItem, Order, Product, ProductCategory, Voucher
from shop.services.checkout import CheckoutError, find_discount, place_order
from shop.services.invoices import ShopInvoicePDFError, render_invoice_pdf
from shop.services.pricing import cart_totals
from teams.models import StaffAssignment, Team, TeamMembership
from teams.services.numbers import member_current_number

from .forms import MemberProfileForm, style_dynamic_form
from .mixins import PersonScopeMixin, ShopScopeMixin
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
    "needs your answer" list of upcoming events still NO_RESPONSE/MAYBE, an
    "open tasks" list of unclaimed EventTask slots on those same in-scope
    upcoming events (regardless of RSVP status -- a task is open to anyone,
    not tied to one person's reply), a season-dues card per person who owes
    money, and a news teaser. Every card is independently optional -- an
    empty-state screen is just those `if`s all being falsy.

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
    #: Same reasoning -- an event's own page (linked from each row) is where
    #: every task for it, open or not, actually lives.
    OPEN_TASKS_LIMIT = 5
    #: Same reasoning -- mobile:news_list is the place to see everything.
    NEWS_LIMIT = 3
    #: Same reasoning again -- mobile:me lists every form, not just the pending ones.
    FORMS_LIMIT = 5

    def get_context_data(self, **kwargs):
        people = self.people_in_scope
        now = timezone.now()

        hero_attendance = None
        rsvp_closed = False
        needs_answer = []
        needs_answer_total = 0
        open_tasks = []
        open_tasks_total = 0
        dues_rows = []
        news_items = []
        forms_to_complete = []

        if people:
            upcoming = Attendance.objects.filter(
                member__in=people,
                event__club=self.request.club,
                event__cancelled=False,
                event__start__gte=now,
            ).select_related("event", "event__location", "member")

            # The soonest event nobody's answered yet takes priority over the
            # true chronological next event -- a reply still owed is the more
            # useful thing to surface. Once everything upcoming has an answer,
            # fall back to the true next event so there's still something to
            # show; _hero_rsvp.html colour-codes whichever button matches that
            # already-recorded answer instead of presenting it as unanswered.
            hero_attendance = upcoming.filter(status=Attendance.AttendanceStatus.NO_RESPONSE).order_by("event__start").first()
            if hero_attendance is None:
                hero_attendance = upcoming.order_by("event__start").first()
            if hero_attendance is not None:
                deadline = hero_attendance.event.deadline
                rsvp_closed = deadline is not None and deadline < now

            # Deadline already passed -> replying is no longer possible (see
            # EventDetailView.post's own deadline check), so it doesn't belong
            # in a "still needs a reply" list -- unlike hero_attendance above,
            # which always shows the true next event regardless of RSVP state
            # and falls back to a read-only pill once its own deadline closes.
            needs_answer_qs = (
                upcoming.filter(status__in=[Attendance.AttendanceStatus.NO_RESPONSE, Attendance.AttendanceStatus.MAYBE])
                .filter(Q(event__deadline__isnull=True) | Q(event__deadline__gte=now))
                .order_by("event__start")
            )
            if hero_attendance is not None:
                needs_answer_qs = needs_answer_qs.exclude(pk=hero_attendance.pk)
            needs_answer_total = needs_answer_qs.count()
            needs_answer = list(needs_answer_qs[: self.NEEDS_ANSWER_LIMIT])

            # Its own card, not folded into "Needs your answer" above -- a task
            # ("bring fruit") isn't a reply owed, it's a slot anyone in scope
            # could take, and an event already answered can still have one
            # open. Scoped to the same upcoming/invited events as the rest of
            # this screen (not every upcoming club event), one row per open
            # task rather than per event, so which specific ask is open is
            # visible without a tap through to the event.
            open_tasks_qs = (
                EventTask.objects.filter(event_id__in=upcoming.values_list("event_id", flat=True).distinct())
                .annotate(claim_count=Count("claims"))
                .filter(claim_count__lt=F("needed_quantity"))
                .select_related("event")
                .order_by("event__start")
            )
            open_tasks_total = open_tasks_qs.count()
            open_tasks = list(open_tasks_qs[: self.OPEN_TASKS_LIMIT])
            for task in open_tasks:
                task.open_count = task.needed_quantity - task.claim_count

            season = current_season(self.request.club)
            if season is not None:
                # Holds back a balance nobody's reviewed yet -- same filter
                # PaymentsView applies to the exact same open_dues_rows
                # result, so Home and Payments & dues never disagree (see
                # that function's own docstring).
                awaiting_confirmation = membership_ids_awaiting_confirmation(self.request.club)
                dues_rows = [row for row in open_dues_rows(self.request.club, people, season) if row["membership"].pk not in awaiting_confirmation]

            # Every published item, club-wide or team-tagged -- not narrowed to
            # this account's own teams. News is "things this club wants members
            # to know about", not a per-person feed the way RSVP/dues are.
            news_items = list(
                News.objects.filter(
                    club=self.request.club,
                    status=News.Status.PUBLISHED,
                    published_at__lte=now,
                    visibility__in=[News.Visibility.INTERNAL, News.Visibility.BOTH],
                )
                .prefetch_related("teams", "photos")
                .order_by("-published_at")[: self.NEWS_LIMIT]
            )

            # Above the Club news block, per the design ask -- what's still
            # outstanding, capped the same way as the other "see the full list
            # elsewhere" cards above (mobile:forms_list is that full list
            # here). One row per (send, member) rather than per send -- while
            # on the "All" chip this is what actually shows *whose* form it
            # is (a send addressed to two managed children is two rows, each
            # named), the same shape needs_answer's own per-Attendance rows
            # already use for the exact same reason.
            forms_to_complete = [row for row in form_status_rows_for(people, self.request.club) if row["submitted_at"] is None and is_send_open(row["send"], now)][: self.FORMS_LIMIT]

        return super().get_context_data(
            hero_attendance=hero_attendance,
            rsvp_closed=rsvp_closed,
            needs_answer=needs_answer,
            needs_answer_remaining=max(needs_answer_total - len(needs_answer), 0),
            open_tasks=open_tasks,
            open_tasks_remaining=max(open_tasks_total - len(open_tasks), 0),
            dues_rows=dues_rows,
            news_items=news_items,
            forms_to_complete=forms_to_complete,
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
    month via events.services.calendar.agenda_groups (also behind management.
    views.EventListView's own "List" mode and mobile.coach_views.
    CoachScheduleView, so all three stay behaviourally identical) -- an
    unbounded agenda rather than a fixed lookahead window, since a hard
    cutoff just hid events a member would reasonably expect to still find
    here. No ?month= paging beyond that grouping -- there's no season
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

        # Referee sign-ups are scoped to every managed person, same as the
        # RSVP rows above -- a referee-eligible child is exactly as real as
        # a referee-eligible parent, and a parent signing a kid up to
        # referee is no different from answering an RSVP on their behalf.
        # Merged into the same chronological list (own dict shape) rather
        # than a separate section, so it reads on the actual day it falls
        # on -- distinct styling is what sets it apart (mobile/
        # _calendar_referee_row.html), not a different place on the screen.
        # Declined invites are dropped; accepted ones stay visible as a
        # confirmed commitment.
        if self.managed_people and kind_filter != "training":
            signups = (
                RefereeSignup.objects.filter(
                    member__in=self.managed_people,
                    event__club=self.request.club,
                    event__cancelled=False,
                    event__start__gte=now,
                    status__in=[RefereeSignup.Status.INVITED, RefereeSignup.Status.ACCEPTED],
                )
                .select_related("event", "event__location", "event__opponent", "member")
                .prefetch_related("event__teams")
            )
            rows += [{"event": signup.event, "referee_signup": signup, "referee_member": signup.member if show_member else None} for signup in signups]

        # Official sign-ups -- same reasoning and scope as the referee block
        # above, gated behind the "officials" waffle flag (mirrors
        # EventDetailView's own official_signups, mobile/mixins.py's
        # officials_enabled context var) since the feature isn't on for
        # every club yet.
        if self.managed_people and kind_filter != "training" and flag_is_active(self.request, "officials"):
            official_signups = (
                OfficialSignup.objects.filter(
                    member__in=self.managed_people,
                    event__club=self.request.club,
                    event__cancelled=False,
                    event__start__gte=now,
                    status__in=[OfficialSignup.Status.INVITED, OfficialSignup.Status.ACCEPTED],
                )
                .select_related("event", "event__location", "event__opponent", "member")
                .prefetch_related("event__teams")
            )
            rows += [{"event": signup.event, "official_signup": signup, "official_member": signup.member if show_member else None} for signup in official_signups]

        # A blocked event would otherwise just silently never appear (effective_
        # members() already excludes it from Attendance sync) -- shown here
        # instead, with which onboarding requirement is in the way, rather than
        # a managed person's game quietly vanishing with no explanation at all.
        for person in self.managed_people:
            for event, requirements in blocked_upcoming_events_for_member(person, self.request.club):
                if kind_filter in self.KIND_FILTERS and event.kind != self.KIND_FILTERS[kind_filter]:
                    continue
                rows.append({"event": event, "blocked_requirements": requirements, "blocked_member": person if show_member else None})

        rows.sort(key=lambda row: row["event"].start)

        this_week, next_week, later_months = agenda_groups(rows, start_of=lambda row: row["event"].start, today=today)

        return super().get_context_data(this_week=this_week, next_week=next_week, later_months=later_months, kind_filter=kind_filter if kind_filter in self.KIND_FILTERS else "", **kwargs)


class RefereeSignupRespondView(PersonScopeMixin, LoginRequiredMixin, View):
    """Accept/decline a referee invite, for self.me or any managed person
    (a referee-eligible child is exactly as real as a referee-eligible
    parent) -- from a Calendar row (mobile/_calendar_referee_row.html) or
    the same event's own detail page (event_detail.html's own "Refereeing"
    card, for the same signup). Routes through events.services.referees.
    accept_referee_signup/decline_referee_signup, so capacity is enforced
    in the one place the desktop admin flow already enforces it, and the
    referee-management screen sees the result with no separate sync step.
    Boosted (no explicit hx-boost="false") -- unlike event_detail's own
    RSVP forms, nothing here is Alpine-owned/toggled, so there's no
    htmx/Alpine conflict to dodge."""

    def post(self, request, *args, **kwargs):
        signup = get_object_or_404(RefereeSignup, pk=kwargs["signup_id"], member__in=self.managed_people, event__club=request.club)
        response = request.POST.get("response")

        if response == "accept":
            try:
                accept_referee_signup(signup)
                body = _("%(name)s is confirmed to referee -- see you there.") % {"name": signup.member.get_full_name()}
                notify(request, f"s|{_('Referee sign-up confirmed')}|{body}")
            except RefereeAssignmentError as exc:
                notify(request, f"e|{_('Could not sign up')}|{exc}")
        elif response == "decline":
            decline_referee_signup(signup)
            body = _("%(name)s won't be refereeing this one -- thanks for letting us know.") % {"name": signup.member.get_full_name()}
            notify(request, f"s|{_('Declined')}|{body}")
        else:
            return HttpResponseBadRequest(_("Unknown response."))

        if request.POST.get("next") == "event_detail":
            return HttpResponseRedirect(reverse("mobile:event_detail", kwargs={"pk": signup.event_id}))
        return HttpResponseRedirect(reverse("mobile:calendar"))


class OfficialSignupRespondView(PersonScopeMixin, LoginRequiredMixin, View):
    """The officials counterpart to RefereeSignupRespondView -- same shape,
    same reasoning (see that view's own docstring)."""

    def post(self, request, *args, **kwargs):
        signup = get_object_or_404(OfficialSignup, pk=kwargs["signup_id"], member__in=self.managed_people, event__club=request.club)
        response = request.POST.get("response")

        if response == "accept":
            try:
                accept_official_signup(signup)
                body = _("%(name)s is confirmed as an official -- see you there.") % {"name": signup.member.get_full_name()}
                notify(request, f"s|{_('Official sign-up confirmed')}|{body}")
            except OfficialAssignmentError as exc:
                notify(request, f"e|{_('Could not sign up')}|{exc}")
        elif response == "decline":
            decline_official_signup(signup)
            body = _("%(name)s won't be an official for this one -- thanks for letting us know.") % {"name": signup.member.get_full_name()}
            notify(request, f"s|{_('Declined')}|{body}")
        else:
            return HttpResponseBadRequest(_("Unknown response."))

        if request.POST.get("next") == "event_detail":
            return HttpResponseRedirect(reverse("mobile:event_detail", kwargs={"pk": signup.event_id}))
        return HttpResponseRedirect(reverse("mobile:calendar"))


class EventTaskRespondView(PersonScopeMixin, LoginRequiredMixin, View):
    """Claim/unclaim one slot of an event task (management._event_task_panel.
    html's own read-only counterpart) -- always as self.me, not a managed-
    person picker like RSVP has: a logistics task ("bring fruit") belongs to
    whichever parent/member is handling it, not a specific child on the
    roster. Routes through events.services.tasks.claim_task/unclaim_task, so
    capacity is enforced in the one place the panel's own read-only claim
    list already reflects."""

    def post(self, request, *args, **kwargs):
        if self.me is None:
            return HttpResponseBadRequest(_("No member record for this account."))

        task = get_object_or_404(EventTask, pk=kwargs["task_id"], event__club=request.club)
        response = request.POST.get("response")

        if response == "claim":
            try:
                claim_task(task, self.me)
                notify(request, f"s|{_('Claimed')}|" + _("You're down for “%(title)s”.") % {"title": task.title})
            except TaskClaimError as exc:
                notify(request, f"e|{_('Could not claim')}|{exc}")
        elif response == "unclaim":
            claim = task.claims.filter(member=self.me).first()
            if claim is not None:
                unclaim_task(claim)
                notify(request, f"w|{_('Removed')}|" + _("You're no longer down for “%(title)s”.") % {"title": task.title})
        else:
            return HttpResponseBadRequest(_("Unknown response."))

        if request.POST.get("next") == "event_detail":
            return HttpResponseRedirect(reverse("mobile:event_detail", kwargs={"pk": task.event_id}))
        return HttpResponseRedirect(reverse("mobile:calendar"))


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
        # A published line-up supersedes ordinary RSVP -- the roster's locked
        # in, so "Your answers" below switches to read-only and the line-up
        # itself gets its own card.
        lineup = Lineup.objects.filter(event=event, published_at__isnull=False).first()
        lineup_categories = selected_members_by_position(lineup) if lineup is not None else []

        # Same managed_people scope as the Calendar row this mirrors (mobile/
        # _calendar_referee_row.html) -- a referee-eligible child is exactly
        # as real as a referee-eligible parent. Declined signups are
        # excluded -- nothing left to do. Rare in practice (a game usually
        # has one eligible referee per family, if any), so this is a plain
        # list rather than the "Your answers" card's counts-only aggregate.
        referee_signups = []
        if self.managed_people:
            referee_signups = list(RefereeSignup.objects.filter(event=event, member__in=self.managed_people, status__in=[RefereeSignup.Status.INVITED, RefereeSignup.Status.ACCEPTED]).select_related("member"))

        official_signups = []
        if self.managed_people and flag_is_active(self.request, "officials"):
            official_signups = list(OfficialSignup.objects.filter(event=event, member__in=self.managed_people, status__in=[OfficialSignup.Status.INVITED, OfficialSignup.Status.ACCEPTED]).select_related("member"))

        # Every task on this event, each with its claim labels
        # (members.services.family.claim_label_for -- never the claiming
        # member's own name) and whether self.me has already claimed a slot.
        tasks = list(event.tasks.prefetch_related("claims__member").order_by("created_at"))
        for task in tasks:
            task.claim_labels = [claim_label_for(claim.member) for claim in task.claims.all()]
            task.is_full = len(task.claim_labels) >= task.needed_quantity
            task.claimed_by_me = self.me is not None and any(claim.member_id == self.me.pk for claim in task.claims.all())

        your_answers = []
        blocked_signups = []
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
                    # No Attendance row at all -- either genuinely not invited, or
                    # excluded by an open onboarding requirement (events.services.
                    # attendance.effective_members). The latter still deserves an
                    # explanation here rather than just silently not showing up.
                    if season is not None:
                        requirements = open_requirements_blocking(person, self.request.club, season, event.kind)
                        if requirements:
                            blocked_signups.append({"member": person, "requirements": requirements})
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

        return super().get_context_data(
            screen_title=event.title,
            event=event,
            rsvp_closed=rsvp_closed,
            lineup=lineup,
            lineup_categories=lineup_categories,
            referee_signups=referee_signups,
            official_signups=official_signups,
            tasks=tasks,
            your_answers=your_answers,
            blocked_signups=blocked_signups,
            squad_summary=squad_summary,
            **kwargs,
        )

    def post(self, request, *args, **kwargs):
        status = request.POST.get("status")
        # "dropout" isn't an AttendanceStatus -- it's this same form posting
        # from the "Can't make it after all" button a SELECTED member sees
        # once the line-up's published (event_detail.html). It resolves to
        # ABSENT below, same as an ordinary Out, but skips the closed-deadline
        # guard (the line-up is published well after most deadlines) and
        # additionally pings the event's managers, since by this point only
        # they can still act on it (swap the slot, warn the opponent, ...).
        is_dropout = status == "dropout"
        if not is_dropout and status not in (Attendance.AttendanceStatus.PRESENT, Attendance.AttendanceStatus.ABSENT, Attendance.AttendanceStatus.MAYBE):
            return HttpResponseBadRequest(_("Unknown RSVP status."))

        # Every current caller (Home's hero, M2's per-person rows) always sends an
        # explicit member_id -- this is just a defensive fallback for one that doesn't.
        fallback_member = self.scope_person or self.me
        member_id = request.POST.get("member_id") or (str(fallback_member.pk) if fallback_member else None)
        member = next((person for person in self.managed_people if str(person.pk) == member_id), None) if member_id else None
        if member is None:
            return HttpResponseBadRequest(_("You can't RSVP for that person."))

        event = get_object_or_404(Event, pk=kwargs["pk"], club=request.club)
        existing = Attendance.objects.filter(event=event, member=member).first()

        if is_dropout:
            if existing is None or existing.status != Attendance.AttendanceStatus.SELECTED:
                return HttpResponseBadRequest(_("You're not in the published line-up for this event."))
        # Mirrors the read-only treatment Home's hero and this same screen's own
        # "Your answers" card already show once the deadline has passed (see
        # get_context_data's rsvp_closed) -- enforced here too, since a disabled
        # button in the UI is only a hint, not a guarantee against a direct POST.
        elif event.deadline is not None and event.deadline < timezone.now():
            return HttpResponseBadRequest(_("Replies are closed for this event."))

        # A reason is only ever meaningful attached to Out/Maybe -- clearing it
        # the moment someone flips to In avoids a stale "sick" note hanging
        # around under an answer it no longer explains. Private by
        # construction, not by a visibility flag: nothing renders another
        # member's own note anywhere -- only this member/family's own screens
        # (event_detail's "Your answers") and Coach mode's bench attendance
        # (mobile/templates/mobile/coach/attendance.html) ever read it.
        note = ""
        if is_dropout or status == Attendance.AttendanceStatus.ABSENT:
            note = request.POST.get("note", "").strip()
            # Rejects blank and punctuation-only "answers" (a bare ".", "-",
            # "??") -- mandatory for Out/dropout specifically, unlike Maybe
            # below. Backend-scoped, not the pretty inline-error UX this
            # codebase gives ModelForm submissions elsewhere -- matches this
            # view's own existing style (see "Unknown RSVP status"/"Replies
            # are closed" above, both plain 400s a normal user should never
            # actually see, since the template only ever offers Out through
            # the reason form to begin with).
            if not any(char.isalnum() for char in note):
                return HttpResponseBadRequest(_("Please enter a reason."))
        elif status == Attendance.AttendanceStatus.MAYBE:
            # Optional here -- Maybe doesn't owe anyone an explanation the way
            # a firm no does, but the same field carries it if given one.
            note = request.POST.get("note", "").strip()

        final_status = Attendance.AttendanceStatus.ABSENT if is_dropout else status
        Attendance.objects.update_or_create(event=event, member=member, defaults={"status": final_status, "note": note})
        if is_dropout:
            notify_dropout(event, member, note)

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
            article_body=render_body_html(body),
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
    have nowhere to lead in this build (no dedicated screen) and are
    deliberately omitted rather than built as dead or inert links.
    "Payments & dues" does lead somewhere -- PaymentsView below -- with its
    "N OPEN" pill only rendered once there's actually a balance owed.

    "Teams I coach/manage" is new, beyond the mockup: one row per current-season
    StaffAssignment self.me holds (team + position/role), each linking
    straight into Coach mode for that team (mobile:coach_today?team=<pk>,
    which mobile.coach_mixins.CoachScopeMixin's own ?team= handling already
    resolves and persists). Shown for any staffed team, not just ones
    self.me *manages* -- Coach mode's own screens already render read-only
    for a non-management position, so there's nothing to hide here.

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

        # Same "not reviewed yet" filter PaymentsView/HomeView apply to this
        # same open_dues_rows result -- this badge count must not claim more
        # is owed than the Payments & dues screen it links to actually shows.
        awaiting_confirmation = membership_ids_awaiting_confirmation(club)
        open_dues_count = len([row for row in open_dues_rows(club, self.managed_people, season) if row["membership"].pk not in awaiting_confirmation])

        staff_assignments = []
        if self.me is not None and season is not None:
            staff_assignments = list(StaffAssignment.objects.filter(member=self.me, season=season).select_related("team", "position").order_by("team__name"))

        # Same family scoping as the "Add payment" voucher dropdown
        # (management.forms.AddPaymentForm) -- self.me plus anyone sharing a
        # Family with them (any role, both directions: Member.family_members),
        # not just managed_people's one-directional children. Only ever
        # usable vouchers show at all: expired, deactivated, or fully
        # consumed ones are excluded outright rather than shown with a
        # status pill -- nothing here can actually be spent.
        vouchers = []
        if self.me is not None:
            family_ids = {self.me.pk, *self.me.family_members.values_list("pk", flat=True)}
            vouchers = list(
                Voucher.objects.filter(club=club, issued_to__in=family_ids, is_active=True, expiry_date__gte=timezone.localdate(), consumed_amount__lt=F("amount")).select_related("issued_to").order_by("expiry_date")
            )

        # Just the count for the "Forms" menu row's own pill -- the full,
        # per-person list (formbuilder.services.audience.form_status_rows_for)
        # lives on its own page now, FormsListView below.
        open_forms_count = len([row for row in form_status_rows_for(self.managed_people, club) if row["submitted_at"] is None])

        # Gates the "Register" menu row -- no dead link when the club has no
        # active MEMBERSHIP-type product configured (registration.services.
        # pricing.available_registration_products), same "just absent, not
        # disabled" treatment as every other row here.
        registration_open = available_registration_products(club).exists()

        return super().get_context_data(
            member_since=member_since,
            team_manager_label=team_manager_label,
            people_rows=people_rows,
            open_dues_count=open_dues_count,
            staff_assignments=staff_assignments,
            vouchers=vouchers,
            open_forms_count=open_forms_count,
            registration_open=registration_open,
            **kwargs,
        )


class FormsListView(PersonScopeMixin, LoginRequiredMixin, TemplateView):
    """The "Forms" row off Me -- every form send self.managed_people are or
    were ever addressed to, one row per (send, member), pending or
    completed with its submitted date. Unbounded by is_active/opens_at/
    closes_at unlike Home's own "Forms to complete" card (formbuilder.
    services.audience.is_send_open): this is the full record, not just
    what's currently actionable."""

    template_name = "mobile/forms_list.html"
    screen_title = _("Forms")
    active_tab = "me"

    def get_context_data(self, **kwargs):
        forms_status = form_status_rows_for(self.managed_people, self.request.club)
        return super().get_context_data(forms_status=forms_status, **kwargs)


class FormFillView(PersonScopeMixin, LoginRequiredMixin, TemplateView):
    """Renders and submits one FormSend's dynamic form -- reached from the
    Home "Forms to complete" card, the Me page's Forms section, or a form
    notification's own link (mobile.views._notification_source_link).

    GET always renders (even once closed/already submitted -- the club_wide/
    audience/window checks all live in formbuilder.services.submission.
    submit_form, run for real on POST, not duplicated here as a second,
    read-only copy that could drift from it); a rejected POST re-renders the
    same screen with the service layer's own field-level errors attached,
    the same "translate a service exception into notify() + stay on the
    screen" idiom ShopCheckoutView uses for CheckoutError, except re-render
    instead of redirect so those field errors are actually visible."""

    template_name = "mobile/form_fill.html"
    screen_title = _("Form")

    def get_send(self):
        return get_object_or_404(FormSend.objects.select_related("form").filter(club=self.request.club), pk=self.kwargs["pk"])

    def get(self, request, *args, **kwargs):
        send = self.get_send()
        return self.render_to_response(self.get_context_data(send=send, form=style_dynamic_form(build_form(send.form))))

    def post(self, request, *args, **kwargs):
        send = self.get_send()
        try:
            submit_form(send, self.me, request.POST, files=request.FILES)
        except FormSubmissionError as error:
            # Re-run the same validation Django's own full_clean() already did
            # once inside submit_form, rather than manually replaying
            # error.errors' messages onto a second bound_form via add_error():
            # add_error()'s first call lazily triggers full_clean() as a side
            # effect (it reads self.errors internally), which re-validates
            # every field itself -- so a manually copied "this field is
            # required" landed *on top of* Django's own identical one, twice.
            # Calling is_valid() up front here instead means there's only
            # ever one real validation pass, so nothing to duplicate.
            # error.errors is only non-empty for that field-level case; a
            # window/audience/quota/login rejection has no field to attach
            # to at all, so that's the one case still surfaced by hand.
            bound_form = style_dynamic_form(build_form(send.form, data=request.POST, files=request.FILES))
            bound_form.is_valid()
            if not error.errors:
                bound_form.add_error(None, str(error))
            notify(request, f"e|{_('Could not submit')}|{error}")
            return self.render_to_response(self.get_context_data(send=send, form=bound_form))

        notify(request, f"s|{_('Submitted')}|{_('Thanks -- your response has been recorded.')}")
        return HttpResponseRedirect(reverse("mobile:home"))

    def get_context_data(self, **kwargs):
        return super().get_context_data(screen_title=kwargs["send"].form.title, **kwargs)


class FormResponseView(PersonScopeMixin, LoginRequiredMixin, TemplateView):
    """Read-only "my responses" screen for one already-submitted Submission --
    reached by tapping a "Completed" row on the Forms list (mobile:forms_list).
    Keyed by the submission itself, not the send, so there's no ambiguity
    about which of self.managed_people it belongs to the way a send-keyed
    URL would have. Shows every field on the form (not just Field.is_active
    ones -- a retired field an answer still references stays visible here,
    same "is_active only gates new sends" reasoning as FormFieldUpdateView's
    own docstring), each with the value actually recorded or "--" if it was
    left blank."""

    template_name = "mobile/form_response.html"
    screen_title = _("Your responses")

    def get_submission(self):
        managed_ids = {person.pk for person in self.managed_people}
        submission = get_object_or_404(Submission.objects.select_related("send__form", "member").filter(send__club=self.request.club), pk=self.kwargs["pk"])
        if submission.member_id not in managed_ids:
            raise Http404("Not one of your own submissions.")
        return submission

    def get_context_data(self, **kwargs):
        submission = self.get_submission()
        answers_by_field_id = {answer.field_id: answer.value for answer in submission.answers.all()}
        rows = [{"field": field, "display": _display_answer(answers_by_field_id.get(field.id))} for field in submission.send.form.fields.order_by("order")]
        return super().get_context_data(submission=submission, rows=rows, screen_title=submission.send.form.title, **kwargs)


def _display_answer(value):
    """Render a stored Answer.value for the read-only responses screen --
    None (a skipped optional field) and a CHECKBOX's bool need a word
    rather than Django's own str(), and a MULTICHOICE's list reads better
    comma-joined than as a Python repr."""
    if value is None:
        return None
    if value is True:
        return _("Yes")
    if value is False:
        return _("No")
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value)


class PaymentsView(PersonScopeMixin, LoginRequiredMixin, TemplateView):
    """M5's "Payments & dues" row -- every open season-dues balance across
    ``self.managed_people`` (club.services.fees.open_dues_rows is the shared
    source behind Home's own dues card too). No online payment gateway
    exists yet, so "Pay" is the same non-functional stub as Home's -- this
    screen's job is visibility ("what do I owe, and when"), not collection.

    A membership billed through a *confirmed* registration is grouped with
    every other managed person that same registration covers into one
    combined card (mobile/_dues_registration_receipt.html) -- one balance,
    one "pay early and save" figure, one invoice download, matching the one
    invoice that was actually sent, rather than a separate card per person
    with its own (and, once a manual discount is involved, inaccurate --
    see registration.services.invoicing.batch_early_payment_offer's own
    docstring) early-payment figure. A membership with no registration
    behind it at all (manually created) still gets its own plain card
    (mobile/_dues_receipt.html), unchanged."""

    template_name = "mobile/payments.html"
    screen_title = _("Payments & dues")
    active_tab = "me"

    def get_context_data(self, **kwargs):
        season = current_season(self.request.club)
        # Holds back a balance nobody's reviewed yet -- see
        # registration.services.invoicing.membership_ids_awaiting_confirmation's
        # own docstring. Filtered before the grouping below, so nothing's
        # wasted decorating a row about to be dropped.
        awaiting_confirmation = membership_ids_awaiting_confirmation(self.request.club)
        # include_zero=True: a sibling already settled (or priced at 0/net
        # negative after a credit) still has to show its own line items in a
        # combined registration receipt below -- see open_dues_rows' own
        # docstring on why the usual "still owed" filter would otherwise
        # silently drop them from both the member list and the itemised
        # breakdown. A standalone (non-registration) membership has no group
        # total to reconcile against, so it's filtered back down to
        # balance > 0 below, same as before -- nothing gained by showing an
        # already-settled person their own €0 card on its own.
        dues_rows = [row for row in open_dues_rows(self.request.club, self.managed_people, season, include_zero=True) if row["membership"].pk not in awaiting_confirmation]

        registration_groups = {}
        standalone_rows = []
        for row in dues_rows:
            entries = list(row["membership"].registration_details.select_related("product_variant__product", "requested_team", "batch"))
            row["entries"] = entries
            # Almost always one -- a membership only ever spans more than one
            # registration batch if this person was registered twice in the
            # same season (e.g. a second team added on afterwards), see
            # registration.services.submission's own docstring. Only a
            # *confirmed* batch groups this row -- an unconfirmed one can't
            # exist here at all (already filtered above via
            # membership_ids_awaiting_confirmation).
            batch = next((entry.batch for entry in entries if entry.batch.invoice_sent_at is not None), None)
            if batch is None:
                if row["balance"] > 0:
                    standalone_rows.append(row)
                continue
            group = registration_groups.setdefault(batch.pk, {"batch": batch, "rows": [], "total_balance": Decimal("0")})
            group["rows"].append(row)
            group["total_balance"] += row["balance"]

        display_rows = [{"kind": "standalone", "row": row} for row in standalone_rows]
        for group in registration_groups.values():
            entries = active_batch_entries(group["batch"])
            _subtotal, _discount_amount, total = batch_totals(entries, group["batch"].manual_discount_amount)
            group["early_payment"] = batch_early_payment_offer(group["batch"], entries, total)
            group["member_names"] = ", ".join(row["membership"].member.first_name for row in group["rows"])
            display_rows.append({"kind": "registration", "group": group})

        return super().get_context_data(display_rows=display_rows, payment_instructions=self.request.club.payment_instructions, **kwargs)


class RegistrationInvoicePdfView(PersonScopeMixin, LoginRequiredMixin, View):
    """Payments & dues' own "Download invoice" link -- same PDF as
    registration.views.RegistrationInvoiceView hands the family via the
    public, token-gated status page, and management.views.
    RegistrationInvoicePdfView hands staff; scoped here to
    self.managed_people instead, since a signed-in mobile session already is
    the credential. Mirrors ShopInvoiceView's own try/except pattern. 404s
    until staff has confirmed the invoice (RegistrationBatch.invoice_sent_at)
    -- in practice this is moot for a membership PaymentsView itself would
    show (it's filtered out until then), but a guessed/stale pk shouldn't
    hand out a document that doesn't exist yet either."""

    def get(self, request, *args, **kwargs):
        batch = get_object_or_404(RegistrationBatch.objects.filter(club=request.club, entries__membership__member__in=self.managed_people).distinct(), pk=kwargs["pk"])
        if batch.invoice_sent_at is None:
            raise Http404("This registration's invoice hasn't been confirmed yet.")

        try:
            pdf = batch_invoice_pdf(batch)
        except RegistrationInvoicePDFError as error:
            notify(request, f"e|{_('PDF unavailable')}|{error}")
            return HttpResponseRedirect(reverse("mobile:payments"))

        response = HttpResponse(pdf, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{request.club.slug}-registration-{batch.pk}.pdf"'
        return response


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


class ReRegisterView(PersonScopeMixin, LoginRequiredMixin, TemplateView):
    """Re-register self/managed_people for a new season -- reuses the exact
    same registration.services.submission.submit_registration the public
    registration page (registration.views.RegistrationView) uses, so the
    two can never disagree about what happens next (a new PENDING
    ClubMembership, straight into the existing Sign-up queue, no separate
    review gate of its own).

    One row per managed person by default (existing_member pre-filled, so
    re-registering never creates a duplicate Member), plus one blank row for
    someone not yet known to the club (e.g. a newborn sibling). Each
    person's card also has its own "Add another registration" button
    (mobile/templates/mobile/reregister.html's own extra_body script) that
    clones a second (third, ...) entry row scoped to that same person --
    a second team, or an additional role (player *and* referee), all on
    this one screen rather than a separate entry point. submit_registration
    already handles a member who already has a ClubMembership this season
    by adding to it rather than erroring -- see that function's own
    docstring. render_page groups the formset's forms back by their
    existing_member value (not position) precisely because a person can now
    carry more than one.

    Same two-step "calculate price, then confirm" flow as the public page,
    no client-side pricing logic."""

    template_name = "mobile/reregister.html"
    screen_title = _("Register")
    active_tab = "me"

    def get_season(self, request, data=None):
        source = data if data is not None else request.GET
        return resolve_chosen_season(request.club, source.get("season"))

    def get_initial_entries(self):
        return [
            {"existing_member": person.pk, "first_name": person.first_name, "last_name": person.last_name, "date_of_birth": person.date_of_birth, "is_contact": self.me is not None and person.pk == self.me.pk}
            for person in self.managed_people
        ]

    def get_formset(self, season, data=None):
        # enforce_single_contact=False -- here, is_contact is "Include this
        # person" per managed person (reregister.html), not "this is me, the
        # submitter" the way it is on the public page. Registering yourself
        # and a child in the same batch is the ordinary case, not a conflict.
        kwargs = {
            "club": self.request.club,
            "people": self.managed_people,
            "season": season,
            "team_number_pools": team_number_pools(self.request.club, season),
            "member_current_numbers": self.get_member_current_numbers(season),
            "prefix": "entries",
            "enforce_single_contact": False,
        }
        if data is None:
            kwargs["initial"] = self.get_initial_entries()
        formset = RegistrationEntryFormSet(data, **kwargs)
        for row in formset.forms:
            style_dynamic_form(row)
        return formset

    def get_member_current_numbers(self, season):
        """``{str(member_id): {str(team_id): number}}`` -- every managed
        person's own current number in every pool-scoped team, so
        reregister.html's script can offer "Keep #N" instead of silently
        pre-filling it (the person may pick a different team than the one
        that number came from). Only pool-scoped teams are considered --
        a poolless team has no number step at all, see registration.
        services.pricing.team_number_pools."""
        pools_by_team = {team.pk: team.pool for team in Team.objects.filter(club=self.request.club, pool__isnull=False).select_related("pool")}
        result = {}
        for person in self.managed_people:
            person_numbers = {}
            for team_id, pool in pools_by_team.items():
                current = member_current_number(person, pool, season)
                if current is not None:
                    person_numbers[str(team_id)] = current
            if person_numbers:
                result[str(person.pk)] = person_numbers
        return result

    def render_season_picker(self, available_seasons):
        return self.render_to_response(self.get_context_data(registration_open=bool(available_seasons), needs_season_choice=True, available_seasons=available_seasons))

    def render_page(self, season, entry_formset, priced_entries=None, season_error=None):
        # Grouped by existing_member (not position): a person can now carry
        # more than one row (Add another registration), so the old 1:1
        # zip(self.managed_people, entry_formset.forms) no longer holds. A
        # form with no existing_member at all is the trailing "someone new"
        # row(s) -- normally exactly one (RegistrationEntryFormSet's own
        # extra=1), shown separately from every known person's own card.
        rows_by_member = defaultdict(list)
        extra_rows = []
        for form in entry_formset.forms:
            member_value = form["existing_member"].value()
            (rows_by_member[str(member_value)] if member_value else extra_rows).append(form)
        person_rows = [(person, rows_by_member.get(str(person.pk), [])) for person in self.managed_people]
        return self.render_to_response(
            self.get_context_data(
                # registration_season, not season -- PersonScopeMixin.get_context_data
                # already injects season=current_season(club) itself, a
                # different thing (today's season, not necessarily the one
                # this registration targets) that a same-named kwarg here
                # would collide with.
                entry_formset=entry_formset,
                person_rows=person_rows,
                extra_rows=extra_rows,
                priced_entries=priced_entries,
                # Same amount submit_registration actually charges per entry,
                # summed once -- see registration.views.RegistrationView.
                # render_page's own comment for why min_registrants_discount
                # specifically (not the conditional early-payment one).
                priced_total=sum((row["price"] - row["min_registrants_discount"] for _form, _entry, row in priced_entries), Decimal("0")) if priced_entries else None,
                # See registration.views.RegistrationView.render_page's own
                # comment -- what the total comes to if every entry with an
                # early-payment deadline is paid by its own date.
                priced_early_total=sum((row["price"] - row["min_registrants_discount"] - row["deadline_discount"] for _form, _entry, row in priced_entries), Decimal("0")) if priced_entries else None,
                season_error=season_error,
                registration_open=True,
                registration_season=season,
                variant_registration_kinds=variant_registration_kinds(self.request.club, season),
                team_number_pools=team_number_pools(self.request.club, season),
                member_current_numbers=self.get_member_current_numbers(season),
                # entry_formset.empty_form is a plain @property (BaseFormSet's
                # own, not cached) -- a fresh, unstyled Form instance every
                # access, so #subrow-template (reregister.html) must be built
                # from this one styled instance rather than the template
                # calling .empty_form itself, or style_dynamic_form's work
                # here would just be thrown away.
                subrow_template_form=style_dynamic_form(entry_formset.empty_form),
            )
        )

    def get(self, request, *args, **kwargs):
        season, available_seasons = self.get_season(request)
        if season is None:
            return self.render_season_picker(available_seasons)
        return self.render_page(season, self.get_formset(season))

    def post(self, request, *args, **kwargs):
        season, available_seasons = self.get_season(request, data=request.POST)
        if season is None:
            return self.render_season_picker(available_seasons)

        entry_formset = self.get_formset(season, request.POST)
        if not entry_formset.is_valid():
            return self.render_page(season, entry_formset)

        if request.POST.get("action") == "edit":
            # "Back" from the receipt screen -- same submitted choices
            # (entry_formset is bound to this same POST, so every field
            # redisplays exactly as chosen), just the form again instead of
            # re-pricing straight back into the receipt.
            return self.render_page(season, entry_formset)

        entries = entries_from_formset(entry_formset)
        try:
            resolve_registration_season([entry.product_variant for entry in entries])
        except PricingError as error:
            return self.render_page(season, entry_formset, season_error=str(error))

        priced = price_entries([entry.product_variant for entry in entries])
        priced_rows = priced_rows_with_jersey_fields(entry_formset, entries, priced, team_number_pools(request.club, season), self.get_member_current_numbers(season))

        if request.POST.get("action") != "submit":
            return self.render_page(season, entry_formset, priced_entries=priced_rows)

        contact_member = self.me or (entries[0].existing_member if entries and entries[0].existing_member else None)
        contact = {
            "contact_first_name": contact_member.first_name if contact_member else "",
            "contact_last_name": contact_member.last_name if contact_member else "",
            "contact_email": contact_member.contact_email if contact_member else "",
            "contact_phone": "",
        }

        try:
            batch = submit_registration(request.club, submitted_by_user=request.user, entries=entries, **contact)
        except RegistrationError as error:
            return self.render_page(season, entry_formset, season_error=str(error))

        send_registration_confirmation_email(batch, request=request)
        notify(request, f"s|{_('Registration received')}|{_('Thanks -- the club will review this and be in touch. A confirmation email is on its way with a link to check status.')}")
        return HttpResponseRedirect(reverse("mobile:me"))


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
    if isinstance(source, FormSend):
        return _("Form"), reverse("mobile:form_fill", kwargs={"pk": source.pk})
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


#: Pill styling shared by ShopOrdersView's list rows and ShopOrderDetailView's
#: own header. Payment and the consolidated member-facing status
#: (Order.member_status, which already folds production_status into
#: fulfillment_status -- see its own docstring) are shown as two separate
#: pills, not merged into one: this app is pay-on-pickup, so "Ready for
#: pickup" + "Pending" is a real, common, and informative combination for a
#: member to see at a glance.
ORDER_PAYMENT_STATUS_PILL_CLASSES = {
    Order.PaymentStatus.PENDING: "pill-warn",
    Order.PaymentStatus.PARTIALLY_PAID: "pill-warn",
    Order.PaymentStatus.PAID: "pill-ok",
    Order.PaymentStatus.REFUNDED: "pill-neutral",
}
ORDER_MEMBER_STATUS_PILL_CLASSES = {
    Order.MemberStatus.NOT_READY: "pill-neutral",
    Order.MemberStatus.IN_PRODUCTION: "pill-info",
    Order.MemberStatus.READY_FOR_PICKUP: "pill-info",
    Order.MemberStatus.COMPLETED: "pill-ok",
    Order.MemberStatus.CANCELLED: "pill-neutral",
}


class ShopHomeView(ShopScopeMixin, LoginRequiredMixin, TemplateView):
    """Shop tab landing -- every active+public Product for this club, grid
    style. Only reachable while Club.shop_open is on (ShopScopeMixin 404s
    otherwise); the tab itself is simply absent from the tab bar the rest of
    the time (base.html), matching every other "not applicable to you" case
    in this app."""

    template_name = "mobile/shop_home.html"
    screen_title = _("Shop")
    active_tab = "shop"

    def get_context_data(self, **kwargs):
        # MEMBERSHIP-type products are never orderable through the shop --
        # they're priced through registration/re-registration instead
        # (registration.services.pricing) -- excluded structurally here
        # regardless of is_public, not left to staff remembering to flag
        # each one.
        products = list(Product.objects.filter(club=self.request.club, is_active=True, is_public=True).exclude(product_type=Product.ProductType.MEMBERSHIP).select_related("category"))
        cart = Cart.objects.filter(club=self.request.club, user=self.request.user, status=Cart.CartStatus.OPEN).first()
        cart_item_count = cart.items.count() if cart is not None else 0

        # One products query total (not one per category) -- see ShopHomeView's
        # own template for how "groups" feeds both the top pill row (a same-page
        # anchor jump, not a filter -- the whole grid still scrolls straight
        # through) and each category's own section.
        categories = ProductCategory.objects.filter(club=self.request.club).order_by("name")
        groups = []
        for category in categories:
            items = [product for product in products if product.category_id == category.pk]
            if items:
                groups.append({"category": category, "products": items})
        uncategorized = [product for product in products if product.category_id is None]
        if uncategorized:
            groups.append({"category": None, "products": uncategorized})

        return super().get_context_data(products=products, groups=groups, cart_item_count=cart_item_count, **kwargs)


class ShopProductDetailView(ShopScopeMixin, LoginRequiredMixin, TemplateView):
    """Photo/description/price, a variant picker (when the product has any --
    see ProductVariant's own docstring for why it's one free-text label, not
    separate size/colour axes), a quantity stepper and -- since CartItem.
    beneficiary exists specifically for this -- a chip row to say who it's
    for, mirroring Home's own person-chip pattern. The beneficiary chip row
    only renders once self.managed_people has more than one person: a member
    ordering just for themselves has nothing to disambiguate, so the item's
    beneficiary is simply left unset in that case.

    "Add to cart" get-or-creates the member's open Cart for this club and
    bumps quantity on the (cart, product, variant, beneficiary) row if it
    already exists, rather than erroring on the model's own unique constraint.
    """

    template_name = "mobile/shop_product_detail.html"
    screen_title = _("Shop")
    active_tab = "shop"

    def _product(self):
        return get_object_or_404(Product.objects.exclude(product_type=Product.ProductType.MEMBERSHIP), club=self.request.club, slug=self.kwargs["slug"], is_active=True, is_public=True)

    def get(self, request, *args, **kwargs):
        product = self._product()
        variants = list(product.variants.filter(is_active=True))
        return self.render_to_response(self.get_context_data(product=product, variants=variants))

    def get_context_data(self, **kwargs):
        return super().get_context_data(screen_title=kwargs["product"].name, **kwargs)

    def post(self, request, *args, **kwargs):
        product = self._product()
        if self.me is None:
            return HttpResponseBadRequest(_("No member record for this account."))

        try:
            quantity = max(1, int(request.POST.get("quantity", 1)))
        except ValueError:
            quantity = 1

        variant = None
        variant_id = request.POST.get("variant")
        if variant_id:
            variant = product.variants.filter(pk=variant_id, is_active=True).first()
            if variant is None:
                return HttpResponseBadRequest(_("That option isn't available."))

        beneficiary = None
        beneficiary_id = request.POST.get("beneficiary")
        if beneficiary_id:
            beneficiary = next((person for person in self.managed_people if str(person.pk) == beneficiary_id), None)
            if beneficiary is None:
                return HttpResponseBadRequest(_("You can't order for that person."))

        # Only collected -- and only trusted from the request -- when the
        # product actually opted in; a crafted POST can't sneak personalization
        # onto a product that doesn't offer it.
        personalization_number = ""
        personalization_name = ""
        if product.personalization_enabled:
            personalization_number = request.POST.get("personalization_number", "").strip()[:20]
            personalization_name = request.POST.get("personalization_name", "").strip()[:100]

        unit_price = variant.effective_price if variant is not None else product.price
        cart, _created = Cart.objects.get_or_create(club=request.club, user=request.user, status=Cart.CartStatus.OPEN)
        item, item_created = CartItem.objects.get_or_create(
            cart=cart,
            product=product,
            variant=variant,
            beneficiary=beneficiary,
            personalization_number=personalization_number,
            personalization_name=personalization_name,
            defaults={"quantity": quantity, "unit_price": unit_price},
        )
        if not item_created:
            item.quantity += quantity
            item.save(update_fields=["quantity"])

        title = _("Added to cart")
        body = _("%(product)s added to your cart.") % {"product": product.name}
        notify(request, f"s|{title}|{body}")
        return HttpResponseRedirect(reverse("mobile:shop_cart"))


class ShopCartView(ShopScopeMixin, LoginRequiredMixin, TemplateView):
    """Cart + checkout screen. The discount code is applied via a plain GET
    (``?code=``), recomputed on each render through shop.services.pricing.cart_totals
    -- no htmx/live preview, matching this screen's otherwise-static-form
    shape. "Place order" carries whatever code is currently in the box
    through to ShopCheckoutView as a hidden field; place_order is the only
    place a code is actually redeemed, so it re-validates it itself rather
    than trusting this screen's own preview.
    """

    template_name = "mobile/shop_cart.html"
    screen_title = _("Cart")
    active_tab = "shop"

    def get_context_data(self, **kwargs):
        cart = Cart.objects.filter(club=self.request.club, user=self.request.user, status=Cart.CartStatus.OPEN).first()
        items = list(cart.items.select_related("product", "variant", "beneficiary").order_by("created")) if cart is not None else []
        for item in items:
            item.line_total = item.unit_price * item.quantity

        code = self.request.GET.get("code", "").strip()
        discount = None
        discount_error = None
        if code and cart is not None:
            try:
                discount = find_discount(self.request.club, code)
            except CheckoutError as error:
                discount_error = str(error)

        totals = cart_totals(cart, discount) if cart is not None else {"subtotal": Decimal("0"), "discount_amount": Decimal("0"), "total": Decimal("0")}

        return super().get_context_data(cart=cart, items=items, code=code, discount=discount, discount_error=discount_error, totals=totals, **kwargs)


class ShopCartItemUpdateView(ShopScopeMixin, LoginRequiredMixin, View):
    """+/- and remove on a single cart row. Decrementing below 1 removes the
    item outright -- a row stuck at a quantity nobody asked to keep isn't
    useful, and there's no separate "0 means gone" state to represent."""

    def post(self, request, *args, **kwargs):
        item = get_object_or_404(CartItem, pk=kwargs["item_id"], cart__club=request.club, cart__user=request.user, cart__status=Cart.CartStatus.OPEN)
        action = request.POST.get("action")

        if action == "increment":
            item.quantity += 1
            item.save(update_fields=["quantity"])
        elif action == "decrement":
            if item.quantity > 1:
                item.quantity -= 1
                item.save(update_fields=["quantity"])
            else:
                item.delete()
        elif action == "remove":
            item.delete()
        else:
            return HttpResponseBadRequest(_("Unknown action."))

        return HttpResponseRedirect(reverse("mobile:shop_cart"))


class ShopCheckoutView(ShopScopeMixin, LoginRequiredMixin, View):
    """POST-only target for the cart screen's "Place order" button. Every
    checkout rule (shop open, cart non-empty, a real discount code) lives in
    shop.services.checkout.place_order -- this view's only job is to call it
    and translate CheckoutError into a flashed message instead of a 500."""

    def post(self, request, *args, **kwargs):
        cart = Cart.objects.filter(club=request.club, user=request.user, status=Cart.CartStatus.OPEN).first()
        if cart is None or self.me is None:
            notify(request, f"e|{_('Nothing to order')}|{_('Your cart is empty.')}")
            return HttpResponseRedirect(reverse("mobile:shop_cart"))

        try:
            order = place_order(cart, purchaser=self.me, discount_code=request.POST.get("discount_code", ""))
        except CheckoutError as error:
            notify(request, f"e|{_('Could not place order')}|{error}")
            return HttpResponseRedirect(reverse("mobile:shop_cart"))

        title = _("Order placed")
        body = _("Order %(number)s is in -- pay when you pick it up.") % {"number": order.number}
        notify(request, f"s|{title}|{body}")
        return HttpResponseRedirect(reverse("mobile:shop_order_detail", kwargs={"pk": order.pk}))


class ShopOrdersView(ShopScopeMixin, LoginRequiredMixin, TemplateView):
    """"My orders" -- every past Order across self.managed_people, not just
    self.me. Order.purchaser is always whoever's own login placed it (this
    app gives one Cart per account, never per managed person), so this is the
    same "aggregate across everyone I'm responsible for" scope PaymentsView/
    club.services.fees.open_dues_rows already use, e.g. a parent checking on
    an order a teenager placed under their own login."""

    template_name = "mobile/shop_orders.html"
    screen_title = _("My orders")
    active_tab = "shop"

    def get_context_data(self, **kwargs):
        orders = Order.objects.filter(club=self.request.club, purchaser__in=self.managed_people).select_related("purchaser").order_by("-created") if self.managed_people else Order.objects.none()
        rows = [
            {
                "order": order,
                "payment_pill_class": ORDER_PAYMENT_STATUS_PILL_CLASSES.get(order.payment_status, "pill-neutral"),
                "member_status_pill_class": ORDER_MEMBER_STATUS_PILL_CLASSES.get(order.member_status, "pill-neutral"),
            }
            for order in orders
        ]
        return super().get_context_data(rows=rows, **kwargs)


class ShopOrderDetailView(ShopScopeMixin, LoginRequiredMixin, TemplateView):
    """Line items, status, total and a link to the invoice PDF. Scoped to
    self.managed_people, same reasoning as ShopOrdersView -- an order outside
    that set 404s, same treatment EditProfileView gives an unmanaged member."""

    template_name = "mobile/shop_order_detail.html"
    screen_title = _("Order")
    active_tab = "shop"

    def get_context_data(self, **kwargs):
        order = get_object_or_404(Order.objects.select_related("purchaser"), pk=self.kwargs["pk"], club=self.request.club, purchaser__in=self.managed_people)
        lines = order.order_items.select_related("product", "variant", "beneficiary")
        payment_pill_class = ORDER_PAYMENT_STATUS_PILL_CLASSES.get(order.payment_status, "pill-neutral")
        member_status_pill_class = ORDER_MEMBER_STATUS_PILL_CLASSES.get(order.member_status, "pill-neutral")
        return super().get_context_data(
            screen_title=order.number,
            order=order,
            lines=lines,
            payment_pill_class=payment_pill_class,
            member_status_pill_class=member_status_pill_class,
            **kwargs,
        )


class ShopInvoiceView(ShopScopeMixin, LoginRequiredMixin, View):
    """Rendered on demand via shop.services.invoices.render_invoice_pdf --
    mirrors management.views.DuesInvoicePdfView's own try/except pattern.
    Scoped the same way as ShopOrdersView/ShopOrderDetailView -- never
    another member's invoice, including another managed family's."""

    def get(self, request, *args, **kwargs):
        order = get_object_or_404(Order, pk=kwargs["pk"], club=request.club, purchaser__in=self.managed_people)
        invoice = getattr(order, "invoice", None)
        if invoice is None:
            raise Http404("No invoice for that order.")

        try:
            pdf = render_invoice_pdf(invoice)
        except ShopInvoicePDFError as error:
            notify(request, f"e|{_('PDF unavailable')}|{error}")
            return HttpResponseRedirect(reverse("mobile:shop_order_detail", kwargs={"pk": order.pk}))

        response = HttpResponse(pdf, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{invoice.number}.pdf"'
        return response
