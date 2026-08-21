"""Member-mode screens (M1-M7) plus the PWA plumbing (manifest, service worker,
icon, push subscribe) they all sit on top of. Coach mode (C1-C6) is a later
phase -- see design_handoff_rosterchief_platform/README.md -- and has no
routes here yet.

The M1-M7 views below are placeholders: each renders a "coming soon" card
inside the real app shell (base.html), at its final URL name, so the shell
(header, role switcher, tab bar, person switcher) can be verified end-to-end
before every screen is built out one at a time.
"""

import json

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Q
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import TemplateView

from club.models import ClubMembership
from club.services.access import current_season
from club.services.fees import remaining_balance
from events.models import Attendance, Event
from members.models import Member
from members.views import ClubScopedPublicMixin
from news.models import News
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


class CalendarView(_PlaceholderScreen):
    screen_title = _("Calendar")
    active_tab = "calendar"


class EventDetailView(_PlaceholderScreen):
    """GET is still the M2 placeholder (a later screen owns the full detail
    page); POST is M1's quick In/Out RSVP action, reused by any future screen
    that posts the same {status, member_id} shape at this URL."""

    screen_title = _("Event")
    active_tab = "calendar"

    def post(self, request, *args, **kwargs):
        status = request.POST.get("status")
        if status not in (Attendance.AttendanceStatus.PRESENT, Attendance.AttendanceStatus.ABSENT):
            return HttpResponseBadRequest(_("Unknown RSVP status."))

        member_id = request.POST.get("member_id") or (str(self.scope_person.pk) if self.scope_person else None)
        member = next((person for person in self.managed_people if str(person.pk) == member_id), None) if member_id else None
        if member is None:
            return HttpResponseBadRequest(_("You can't RSVP for that person."))

        event = get_object_or_404(Event, pk=kwargs["pk"], club=request.club)
        Attendance.objects.update_or_create(event=event, member=member, defaults={"status": status})
        return HttpResponseRedirect(reverse("mobile:home"))


class NewsDetailView(_PlaceholderScreen):
    screen_title = _("News")
    active_tab = "news"


class MeView(_PlaceholderScreen):
    screen_title = _("Me")
    active_tab = "me"


class EditProfileView(_PlaceholderScreen):
    screen_title = _("Edit info")
    active_tab = "me"


class NotificationsView(_PlaceholderScreen):
    screen_title = _("Notifications")
    active_tab = "me"
