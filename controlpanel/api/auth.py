"""Platform API authentication: X-Session-Token only, base domain only, platform staff with MFA.

The session cookie is shared across every club subdomain, so cookie auth is deliberately not accepted here.
"""

from allauth.headless.contrib.ninja.security import XSessionTokenAuth
from allauth.mfa.utils import is_mfa_enabled
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django_tenants.utils import get_public_schema_name

from authorization.middleware import is_local_development
from authorization.permissions import is_platform_staff, is_platform_superuser

from .errors import MFAEnrollmentRequired


class PlatformAuth(XSessionTokenAuth):
    def __init__(self, *, superuser: bool = False):
        super().__init__()
        self.superuser = superuser

    def __call__(self, request):
        club = getattr(request, "tenant", None)
        if club is None or club.schema_name != get_public_schema_name():
            raise Http404  # the platform API does not exist on club subdomains
        user = super().__call__(request)
        if user is None or not user.is_active:  # a blocked account is signed out even if a session lingers
            return None
        request.user = user
        if not is_platform_staff(user) or (self.superuser and not is_platform_superuser(user)):
            raise PermissionDenied
        if not is_local_development(request) and not is_mfa_enabled(user):
            raise MFAEnrollmentRequired
        return user


staff_auth = PlatformAuth()
superuser_auth = PlatformAuth(superuser=True)
