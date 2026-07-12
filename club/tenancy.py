from __future__ import annotations

from contextvars import ContextVar, Token
from typing import TYPE_CHECKING

from django.conf import settings

if TYPE_CHECKING:
    from .models import Club

__current_club: ContextVar = ContextVar("current_club", default=None)


def set_current_club(club: Club | None) -> Token:
    """Bind ``club`` to the current context and return a reset token."""
    return __current_club.set(club)


def reset_current_club(token: Token) -> None:
    """Restore the club that was active before ``set_current_club``."""
    __current_club.reset(token)


def get_current_club() -> Club | None:
    return __current_club.get()


def require_current_club() -> Club:
    club = get_current_club()

    if club is None:
        raise RuntimeError("No active club in context.")

    return club


class ClubTenantMiddleware:
    """Resolve the active club from the request's subdomain.

    The club whose ``slug`` matches the left-most host label (below the
    configured base domain) is stored on ``request.club`` and pushed onto the
    ``current_club`` context variable for the duration of the request, so
    service-layer code and managers can read it via ``get_current_club()``.
    Requests that don't map to a club (bare base domain, ``www``, localhost,
    an unknown slug) get ``request.club = None``.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        club = self.get_club(request)
        request.club = club
        token = set_current_club(club)
        try:
            return self.get_response(request)
        finally:
            reset_current_club(token)

    def get_club(self, request) -> Club | None:
        # Imported lazily: club.models imports clubmanager.base, which imports
        # this module, so a top-level import would be circular.
        from .models import Club

        subdomain = self.get_subdomain(request)

        if not subdomain:
            return None

        return Club.objects.filter(slug=subdomain).first()

    @staticmethod
    def get_subdomain(request) -> str | None:
        """Extract the tenant slug from the request host, or ``None``."""
        host = request.get_host().split(":")[0].lower().rstrip(".")

        if not host:
            return None

        base_domain = getattr(settings, "CLUBMANAGER_BASE_DOMAIN", "").lower().strip(".")

        if base_domain:
            # Only hosts under the configured base domain carry a tenant slug.
            if host == base_domain:
                return None
            suffix = f".{base_domain}"
            if not host.endswith(suffix):
                return None
            label = host[: -len(suffix)]
        else:
            # No base domain configured: treat "slug.example.com" style hosts
            # (3+ labels) as tenant-bearing; leave bare/localhost hosts alone.
            labels = host.split(".")
            if len(labels) < 3:
                return None
            label = ".".join(labels[:-2])

        # Use the left-most label only; ignore the marketing/www host.
        subdomain = label.split(".")[0]
        if subdomain in ("", "www"):
            return None

        return subdomain
