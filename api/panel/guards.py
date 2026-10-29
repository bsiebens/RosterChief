"""Shared authorisation for the management routes (api/panel/routers).

Every panel endpoint resolves the club through ``panel_club`` and then, when the resource has a
finer rule than "is staff", checks it with ``require`` against a club.services.access predicate
(``can_manage_members``, ``can_manage_shop``, ``can_add_news`` ...). The decision stays in the
access service; this module only turns a False into the API's 403 envelope.
"""

from collections.abc import Callable

from django.utils.translation import gettext_lazy as _

from api.core.auth import require_club_member
from api.core.errors import ApiError
from club.services.access import has_management_access


def panel_club(request):
    """The club on this host, for a caller with management authority in it (403 ``forbidden`` otherwise)."""
    club = require_club_member(request)
    if not has_management_access(request.user, club):
        raise ApiError(403, "forbidden", _("You don't have access to club management."))
    return club


def require(request, club, predicate: Callable) -> None:
    """403 ``forbidden`` unless ``predicate(request.user, club)`` holds."""
    if not predicate(request.user, club):
        raise ApiError(403, "forbidden", _("You don't have permission to do this."))
