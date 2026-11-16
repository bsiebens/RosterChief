from allauth.mfa.utils import is_mfa_enabled
from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse

from .permissions import is_club_admin

# Auth endpoints must stay reachable so an admin can enrol an MFA method.
EXEMPT_PREFIXES = ("/accounts/", "/_allauth/", "/static/")


def is_local_development(request) -> bool:
    """MFA enrolment is not enforced on localhost (or *.localhost) while DEBUG is on."""
    host = request.get_host().split(":")[0]
    return settings.DEBUG and (host == "localhost" or host.endswith(".localhost"))


MFA_REQUIRED_MESSAGE = "Multi-factor authentication is required for your role."


def mfa_required(request, user) -> bool:
    """True if ``user`` must enrol an MFA method before using ``request.path``."""
    return bool(
        user.is_authenticated
        and not request.path.startswith(EXEMPT_PREFIXES)
        and not is_local_development(request)
        and is_club_admin(user)
        and not is_mfa_enabled(user)
    )


class RequireMFAMiddleware:
    """Platform owners and club admins must enrol an MFA method before using the app (except local development)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if mfa_required(request, request.user):
            if request.path.startswith("/api/"):
                return JsonResponse({"detail": MFA_REQUIRED_MESSAGE, "code": "mfa_required"}, status=403)
            return redirect(reverse("mfa_index"))
        return self.get_response(request)
