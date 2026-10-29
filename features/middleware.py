"""Platform lock-down.

While maintenance is on, every club subdomain is closed and the base domain keeps only what
is needed to *end* the maintenance: the control panel, the auth screens that get you into it,
the static files those pages need, and the health check.

The exemptions are the whole design. Close /accounts/ as well and you cannot sign in to turn
maintenance off — a lock-down with no key, fixable only from a shell. Close /healthz and the
load balancer concludes the node is dead and stops routing to it, which takes the control
panel down with everything else.
"""

from django.http import JsonResponse
from django.shortcuts import render
from django.utils.translation import gettext_lazy as _

from features.models import Maintenance

#: Reachable on the base domain while the platform is locked down.
OPEN_PREFIXES = (
    "/control/",  # the point of the exercise
    "/accounts/",  # ...which you cannot reach without signing in
    "/admin/",
    "/static/",
    "/__reload__/",  # dev only; absent outside DEBUG
)

#: Reachable on every host, always. The health check must answer or the load balancer will
#: take the node out of rotation and the control panel with it. /media/ has to stay open too:
#: the club maintenance page is rendered through the tenant's own skin specifically so it can
#: show the club's logo, and that logo is itself a /media/ file — closing it outright would
#: serve the maintenance page over the top of its own image.
ALWAYS_OPEN = ("/healthz", "/media/")

RETRY_AFTER_SECONDS = 3600

#: The native app's APIs (api.protected, allauth.headless); see ``render``.
APP_API_PREFIXES = ("/api/v1/", "/api/auth/")


class MaintenanceMiddleware:
    """Runs after ClubTenantMiddleware: whether a request is a club's or the platform's is
    decided by ``request.club``, which the tenant middleware has just resolved."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not self.is_closed(request):
            return self.get_response(request)

        maintenance = Maintenance.current()
        response = self.render(request, maintenance)
        response["Retry-After"] = RETRY_AFTER_SECONDS

        return response

    def is_closed(self, request) -> bool:
        if request.path.startswith(ALWAYS_OPEN):
            return False

        if not Maintenance.is_on():
            return False

        # A club subdomain is closed outright — no login, no shop, nothing (besides the
        # /media/ exemption above, which its own maintenance page needs to render).
        if getattr(request, "club", None) is not None:
            return True

        # The base domain keeps the way back in.
        return not self.is_open_on_base_domain(request.path)

    @staticmethod
    def is_open_on_base_domain(path: str) -> bool:
        # "/control" (no slash) is open too: this runs before CommonMiddleware's APPEND_SLASH
        # redirect could add the slash, so a bare "/control" would otherwise be a 503 -- and
        # that is the URL people type.
        return path.startswith(OPEN_PREFIXES) or path + "/" in OPEN_PREFIXES

    def render(self, request, maintenance):
        message = maintenance.message or _("RosterChief is down for maintenance. It will be back shortly.")

        # The native app's APIs answer in their own error envelope (API.md §6), whatever the
        # Accept header, so the app can show its maintenance screen.
        if request.path.startswith(APP_API_PREFIXES):
            return JsonResponse({"error": {"code": "maintenance", "message": str(message)}}, status=503)

        # An API-ish caller gets JSON rather than a page of HTML it cannot read.
        if request.headers.get("accept", "").startswith("application/json"):
            return JsonResponse({"status": "maintenance", "detail": str(message)}, status=503)

        return render(request, "maintenance.html", {"message": message, "maintenance": maintenance}, status=503)
