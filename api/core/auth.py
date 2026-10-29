"""Authentication and request guards for the app API (API.md §3.4, §4, §5).

``AppAuth`` accepts the access token allauth.headless issued (``Authorization: Bearer
<jwt>``) and adds what allauth's own ninja helper doesn't:

* ``request.user`` is set, so club.services.access and friends work unchanged;
* inactive accounts are refused;
* privileged users who haven't enrolled a second factor get ``403
  mfa_enrollment_required`` -- RequireMFAMiddleware never sees API requests (they carry
  no session cookie, so the user is anonymous at middleware time);
* an expired token answers ``token_expired`` rather than ``unauthenticated``, so the
  client knows to refresh.

The token proves *who*, never *which club*: club access is re-derived per request by
``require_club_member``.
"""

import time

import jwt
from allauth.headless.tokens.strategies.jwt.internal import validate_access_token
from allauth.mfa.utils import is_mfa_enabled
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _
from ninja.security import HttpBearer

from api.core.errors import ApiError
from authentication.middleware import mfa_required_for
from mobile.access import has_club_tie
from rosterchief.admin_host import is_tenant_host


def _is_expired(token: str) -> bool:
    """Whether ``token`` is a JWT whose ``exp`` has passed. The signature isn't checked:
    this only picks the error code for a token that was already rejected."""
    try:
        payload = jwt.decode(token, options={"verify_signature": False})
    except jwt.PyJWTError:
        return False
    exp = payload.get("exp")
    return isinstance(exp, int | float) and exp <= time.time()


class AppAuth(HttpBearer):
    def authenticate(self, request, token):
        result = validate_access_token(token)
        if result is None:
            if _is_expired(token):
                raise ApiError(401, "token_expired", _("Your session has expired. Refreshing it will continue."))
            return None
        user, _payload = result
        try:
            active = user.is_active  # evaluates allauth's lazy user
        except get_user_model().DoesNotExist:
            return None
        if not active:
            return None
        request.user = user
        if mfa_required_for(user) and not is_mfa_enabled(user):
            raise ApiError(403, "mfa_enrollment_required", _("Your account needs two-factor authentication. Set it up to continue."))
        return user


app_auth = AppAuth()


def require_base_domain(request) -> None:
    """Account-plane endpoints (``/me``, ``/clubs``, ``/devices``, ``/meta``) answer on
    the base domain only; on a club subdomain they don't exist (API.md §4)."""
    if is_tenant_host(request):
        raise ApiError(404, "not_found")


def require_club_member(request):
    """Resource-plane guard: a club on this host (404 otherwise) that the caller has a
    real tie to (403 ``not_a_member`` otherwise -- a known club, so not a 404)."""
    club = getattr(request, "club", None)
    if club is None:
        raise ApiError(404, "not_found", _("No club found for this host."))
    if not has_club_tie(request.user, club):
        raise ApiError(403, "not_a_member", _("You're not a member of this club."))
    return club
