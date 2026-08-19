"""Force MFA enrolment for privileged users.

Anyone who can change other people's data must have a second factor: Django
staff/superusers, and anyone holding an elevated ``ClubRole`` (ADMIN, EDITOR, or
MEMBER_ADMIN) in *any* club. Regular members may enrol, but aren't forced to.

Enrolled users are challenged for their second factor by allauth at login; this
middleware only handles the other half — a privileged user who has never
enrolled is redirected to the MFA setup page until they do.
"""

from allauth.mfa.utils import is_mfa_enabled
from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse

from club.models import ClubRole

#: Paths a not-yet-enrolled user must still reach (to enrol, or to log out).
#: ``/__reload__/`` is django-browser-reload's event stream, which only exists
#: under DEBUG — without it, live reload dies on the enrolment page itself.
EXEMPT_PREFIXES = ("/accounts/", "/static/", "/media/", "/__reload__/")

ELEVATED_ROLES = (ClubRole.Roles.ADMIN, ClubRole.Roles.EDITOR, ClubRole.Roles.MEMBER_ADMIN)


def mfa_required_for(user) -> bool:
    """Privileged users must hold a second factor."""
    if user.is_staff or user.is_superuser:
        return True
    return ClubRole.objects.filter(member__user=user, role__in=ELEVATED_ROLES).exists()


class RequireMFAMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if self.needs_enrolment(request):
            return redirect(reverse(settings.MFA_ENROLMENT_URL_NAME))
        return self.get_response(request)

    def needs_enrolment(self, request) -> bool:
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return False
        if request.path.startswith(EXEMPT_PREFIXES):
            return False
        return mfa_required_for(user) and not is_mfa_enabled(user)
