from django.http import HttpResponse, JsonResponse
from django_tenants.utils import get_public_schema_name

from .models import Maintenance

# On the base domain these stay open during maintenance, so staff can switch it off and everyone can still sign in.
OPEN_PREFIXES = ("/api/platform/", "/_allauth/", "/accounts/", "/admin/", "/static/", "/media/")
DEFAULT_MESSAGE = "RosterChief is down for maintenance. It will be back shortly."


class MaintenanceMiddleware:
    """503 for everything while maintenance is on: club domains entirely, the base domain except OPEN_PREFIXES."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        maintenance = Maintenance.current()
        if not maintenance.is_active:
            return self.get_response(request)
        club = getattr(request, "tenant", None)
        on_base_domain = club is None or club.schema_name == get_public_schema_name()
        if on_base_domain and request.path.startswith(OPEN_PREFIXES):
            return self.get_response(request)
        message = maintenance.message or DEFAULT_MESSAGE
        if request.path.startswith("/api/") or "application/json" in request.headers.get("Accept", ""):
            response = JsonResponse({"error": {"code": "maintenance", "message": message}}, status=503)
        else:
            response = HttpResponse(message, status=503, content_type="text/plain; charset=utf-8")
        response["Retry-After"] = "3600"
        return response
