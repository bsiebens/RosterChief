"""API authentication.

Login, logout, password reset, MFA and passkeys are handled by allauth's headless API under ``/_allauth/``. Once a
user has a session, these authenticators accept it:

* ``X-Session-Token`` header, for app (mobile / non-browser) clients: ``/_allauth/app/v1/...``
* the session cookie, for browser clients: ``/_allauth/browser/v1/...`` (unsafe methods also need ``X-CSRFToken``)

Both set ``request.user`` (token clients are anonymous to Django's own middleware) and enforce the same MFA rule
for admins as ``RequireMFAMiddleware``, which cannot see token-authenticated users.
"""

from allauth.headless.contrib.ninja.security import XSessionTokenAuth
from ninja.security import SessionAuth

from authorization.middleware import mfa_required


class MFARequired(Exception):
    pass


class NoManagementAccess(Exception):
    """Signed in, but neither a club admin, owner nor team staff: the management app is not for them."""


def _enforce_mfa(request, user, *, door=True):
    if mfa_required(request, user):
        raise MFARequired
    if door:
        _enforce_management_access(request, user)
    return user


def _enforce_management_access(request, user):
    """Inside a club only admins, owners and team staff use this API. The platform host (club picker) is exempt."""
    from django_tenants.utils import get_public_schema_name

    from members.services.access import can_access_management

    tenant = getattr(request, "tenant", None)
    if tenant is None or tenant.schema_name == get_public_schema_name():
        return
    if not can_access_management(user):
        raise NoManagementAccess


class TokenAuth(XSessionTokenAuth):
    """``door=False`` is for endpoints that have their own access rule and also serve people who are not admins or
    staff (the private pictures): the MFA rule still applies, the management-app door does not."""

    def __init__(self, *, door: bool = True):
        super().__init__()
        self.door = door

    def __call__(self, request):
        user = super().__call__(request)
        if user is None:
            return None
        request.user = user
        return _enforce_mfa(request, user, door=self.door)


class CookieAuth(SessionAuth):
    def __init__(self, *, door=True):
        super().__init__()
        self.door = door

    def authenticate(self, request, key):
        user = super().authenticate(request, key)
        return _enforce_mfa(request, user, door=self.door) if user else user


AUTHENTICATORS = [TokenAuth(), CookieAuth()]
