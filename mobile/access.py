"""Who may use a club's member app (/app/) at all.

A session is valid on every club subdomain, so being signed in says nothing
about whether the account belongs to *this* club. ``has_club_tie`` is the one
rule every member-app screen is gated on (see mobile.mixins.MemberAppMixin);
without a tie the whole app 404s, exactly as if the page didn't exist.
"""

from django.db.models import Q

from club.models import Club, ClubMembership, ClubRole, EvaluationManager, ShopManager
from club.services.access import is_platform_superuser
from members.models import FamilyMembership, Member
from teams.models import StaffAssignment

#: A cancelled membership is the one status that ends the tie: PENDING must
#: count (a family that has just registered follows its onboarding status in
#: the app), and ACTIVE/LAPSED members keep access to their history, dues and
#: the re-registration screen.
_TIE_EXCLUDED_STATUSES = (ClubMembership.StatusChoices.CANCELLED,)


def has_club_tie(user, club: Club) -> bool:
    """True when ``user`` has a real relationship with ``club``:

    * a non-cancelled ClubMembership (any season, any kind) of their own Member;
    * a parent/guardian FamilyMembership in one of this club's families whose
      child holds a non-cancelled ClubMembership here (any season);
    * any ClubRole, ShopManager or EvaluationManager grant at this club;
    * a StaffAssignment on one of this club's teams (any season);
    * or a platform superuser (the same override club.services.access uses).
    """
    if user is None or not user.is_authenticated or club is None:
        return False
    if is_platform_superuser(user):
        return True

    member = Member.objects.filter(user=user).first()
    if member is None:
        return False

    live_memberships = ClubMembership.objects.filter(club=club).exclude(status__in=_TIE_EXCLUDED_STATUSES)
    if live_memberships.filter(member=member).exists():
        return True

    parent_family_ids = FamilyMembership.objects.filter(
        member=member,
        family__club=club,
        role__in=[FamilyMembership.FamilyRole.PARENT, FamilyMembership.FamilyRole.GUARDIAN],
    ).values("family_id")
    if live_memberships.filter(
        Q(member__family_memberships__family_id__in=parent_family_ids) & Q(member__family_memberships__role=FamilyMembership.FamilyRole.CHILD),
    ).exists():
        return True

    if ClubRole.objects.filter(club=club, member=member).exists():
        return True
    if ShopManager.objects.filter(club=club, member=member).exists() or EvaluationManager.objects.filter(club=club, member=member).exists():
        return True
    return StaffAssignment.objects.filter(member=member, team__club=club).exists()
