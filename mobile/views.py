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
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseRedirect, JsonResponse
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import TemplateView

from members.models import Member
from members.views import ClubScopedPublicMixin

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


class HomeView(_PlaceholderScreen):
    screen_title = _("Home")
    active_tab = "home"


class CalendarView(_PlaceholderScreen):
    screen_title = _("Calendar")
    active_tab = "calendar"


class EventDetailView(_PlaceholderScreen):
    screen_title = _("Event")
    active_tab = "calendar"


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
