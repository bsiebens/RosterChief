"""Shared error helpers for the public API -- see api/urls.py.

Every endpoint is club-scoped via the same subdomain-based tenant resolution
the rest of the platform uses (club.tenancy.ClubTenantMiddleware sets
request.club before any view runs). A request with no club on the host --
the bare base domain, an unknown slug, an archived club -- has nothing to
serve, so it 404s the same way club.mixins.ClubStaffRequiredMixin already
404s the staff-facing app off the base domain.
"""

from ninja.errors import HttpError


def require_club(request):
    if request.club is None:
        raise HttpError(404, "No club found for this host.")
    return request.club
