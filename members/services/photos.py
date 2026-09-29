"""Who may see a member's photo (Member.photo, stored privately).

* anyone, once the member gave ``photo_public_consent`` (the public roster);
* the member themselves, and their parents/guardians -- a PARENT/GUARDIAN in
  any family where the member is a CHILD -- on any host;
* on a club's host, staff whose authority over that member comes from that
  club: whoever may manage members (for anyone attached to the club), and
  everyone else by the same ``members_visible_to`` rule the member detail page
  uses. Teammates are deliberately not included.
"""

from django.db.models import Q

from members.models import FamilyMembership, Member


def is_guardian_of(user, member: Member) -> bool:
    return Member.objects.filter(
        user=user,
        family_memberships__role__in=[FamilyMembership.FamilyRole.PARENT, FamilyMembership.FamilyRole.GUARDIAN],
        family_memberships__family__memberships__member=member,
        family_memberships__family__memberships__role=FamilyMembership.FamilyRole.CHILD,
    ).exists()


def _attached_to_club(member: Member, club) -> bool:
    return Member.objects.filter(pk=member.pk).filter(Q(member_of__club=club) | Q(team_memberships__team__club=club) | Q(staff_assignments__team__club=club) | Q(roles__club=club)).exists()


def can_view_member_photo(user, member: Member, club=None) -> bool:
    if member.photo_public_consent:
        return True
    if user is None or not user.is_authenticated:
        return False
    if member.user_id is not None and member.user_id == user.pk:
        return True
    if is_guardian_of(user, member):
        return True
    if club is None:
        return False

    from club.services.access import can_manage_members, members_visible_to

    if can_manage_members(user, club):
        return _attached_to_club(member, club)
    return members_visible_to(user, club, include_guardians=True).filter(pk=member.pk).exists()
