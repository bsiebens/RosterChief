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


def _enforce_mfa(request, user):
    if mfa_required(request, user):
        raise MFARequired
    return user


class TokenAuth(XSessionTokenAuth):
    def __call__(self, request):
        user = super().__call__(request)
        if user is None:
            return None
        request.user = user
        return _enforce_mfa(request, user)


class CookieAuth(SessionAuth):
    def authenticate(self, request, key):
        user = super().authenticate(request, key)
        return _enforce_mfa(request, user) if user else user


AUTHENTICATORS = [TokenAuth(), CookieAuth()]
