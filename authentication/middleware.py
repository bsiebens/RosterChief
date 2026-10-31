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
from django.core.cache import cache
from django.db.models import Exists, OuterRef
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone

from club.models import ClubRole, EvaluationManager, RefereeManager, ShopManager
from members.models import Member
from teams.models import StaffAssignment

#: Paths a not-yet-enrolled user must still reach (to enrol, or to log out).
#: ``/__reload__/`` is django-browser-reload's event stream, which only exists
#: under DEBUG — without it, live reload dies on the enrolment page itself.
EXEMPT_PREFIXES = ("/accounts/", "/static/", "/media/", "/__reload__/")

#: How long "this user needs no (more) MFA enrolment" is remembered -- see needs_enrolment.
MFA_OK_SECONDS = 60

ELEVATED_ROLES = (ClubRole.Roles.ADMIN, ClubRole.Roles.EDITOR, ClubRole.Roles.MEMBER_ADMIN)


def privileged_members():
    """Members whose own grants make a second factor mandatory: any elevated ClubRole, a
    ShopManager/EvaluationManager/RefereeManager grant, or the derived coach-manager role -- a
    management-position StaffAssignment in the season covering today (the same test as
    club.services.access.is_coach_manager, applied to every club at once, since the
    middleware runs before the tenant is resolved). The one definition behind both the
    gate below and the control panel's "admins without MFA" count."""
    today = timezone.localdate()
    member = OuterRef("pk")
    return Member.objects.filter(
        Exists(ClubRole.objects.filter(member=member, role__in=ELEVATED_ROLES))
        | Exists(ShopManager.objects.filter(member=member))
        | Exists(EvaluationManager.objects.filter(member=member))
        | Exists(RefereeManager.objects.filter(member=member))
        | Exists(StaffAssignment.objects.filter(member=member, position__management_position=True, season__start_date__lte=today, season__end_date__gte=today))
    )


def mfa_required_for(user) -> bool:
    """Privileged users must hold a second factor."""
    if user.is_staff or user.is_superuser:
        return True
    return privileged_members().filter(user=user).exists()


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
        # Only the "fine" answer is remembered, and only briefly: remembering "must enrol"
        # would keep bouncing someone who just enrolled. A newly granted privileged role is
        # therefore gated within MFA_OK_SECONDS rather than instantly.
        key = f"mfa:ok:{user.pk}"
        if cache.get(key):
            return False
        needs = mfa_required_for(user) and not is_mfa_enabled(user)
        if not needs:
            cache.set(key, True, MFA_OK_SECONDS)
        return needs
