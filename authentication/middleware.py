"""Force MFA enrolment for privileged users.

Anyone who can change other people's data must have a second factor: Django
staff/superusers, and anyone holding an elevated ``ClubRole`` (ADMIN, EDITOR, or
MEMBER_ADMIN), a ``ShopManager`` or ``EvaluationManager`` grant, or a
current-season ``StaffAssignment`` in a management position (the derived
coach-manager role, see ``club.services.access.is_coach_manager``) in *any*
club. Regular members may enrol, but aren't forced to.

Enrolled users are challenged for their second factor by allauth at login; this
middleware only handles the other half — a privileged user who has never
enrolled is redirected to the MFA setup page until they do.
"""

from allauth.mfa.utils import is_mfa_enabled
from django.conf import settings
from django.db.models import Exists, OuterRef
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone

from club.models import ClubRole, EvaluationManager, ShopManager
from members.models import Member
from teams.models import StaffAssignment

#: Paths a not-yet-enrolled user must still reach (to enrol, or to log out).
#: ``/__reload__/`` is django-browser-reload's event stream, which only exists
#: under DEBUG — without it, live reload dies on the enrolment page itself.
EXEMPT_PREFIXES = ("/accounts/", "/static/", "/media/", "/__reload__/")

ELEVATED_ROLES = (ClubRole.Roles.ADMIN, ClubRole.Roles.EDITOR, ClubRole.Roles.MEMBER_ADMIN)


def mfa_required_for(user) -> bool:
    """Privileged users must hold a second factor."""
    if user.is_staff or user.is_superuser:
        return True
    # One query over the user's Member record: any elevated
    # ClubRole, a ShopManager/EvaluationManager grant, or the derived
    # coach-manager role -- a management-position StaffAssignment in the season
    # covering today (the same test as club.services.access.is_coach_manager,
    # applied to every club at once, since this middleware runs before the
    # tenant is resolved).
    today = timezone.localdate()
    member = OuterRef("pk")
    return (
        Member.objects.filter(user=user)
        .filter(
            Exists(ClubRole.objects.filter(member=member, role__in=ELEVATED_ROLES))
            | Exists(ShopManager.objects.filter(member=member))
            | Exists(EvaluationManager.objects.filter(member=member))
            | Exists(StaffAssignment.objects.filter(member=member, position__management_position=True, season__start_date__lte=today, season__end_date__gte=today))
        )
        .exists()
    )


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
