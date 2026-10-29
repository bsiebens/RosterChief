"""Who may see a member's player picture (Member.player_photo, stored privately).
The profile picture (Member.profile_photo) is visible to everyone in the club, never public.

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


def _is_in_club(user, club) -> bool:
    from club.services.access import can_manage_members

    if can_manage_members(user, club):
        return True
    own = Member.objects.filter(user=user).first()
    return own is not None and _attached_to_club(own, club)


def can_view_member_photo(user, member: Member, club=None, field: str | None = None) -> bool:
    """``field`` is ``player_photo`` (the official picture: the rules above),
    ``profile_photo`` (the self-service picture used in the apps: anyone in
    the club may see it, never the public) or None for the *effective* picture
    -- the profile picture, else the player picture. The player picture used as
    that fallback is shown club-wide like a profile picture (otherwise rosters
    would show broken images); asked for directly it keeps the stricter rules."""
    if field is None:
        if member.profile_photo:
            field = "profile_photo"
        elif member.player_photo and can_view_member_photo(user, member, club, field="profile_photo"):
            return True
        else:
            field = "player_photo"
    if field == "profile_photo":
        if user is None or not user.is_authenticated:
            return False
        if member.user_id is not None and member.user_id == user.pk:
            return True
        if is_guardian_of(user, member):
            return True
        return club is not None and _attached_to_club(member, club) and _is_in_club(user, club)
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


def set_player_photo(member: Member, upload) -> Member:
    """Replace the official player picture with ``upload``. The bytes must really be an image (Pillow opens
    them, as the management form's ImageField does); anything else raises ``django.core.exceptions.ValidationError``.
    The previous file is removed from storage."""
    from django import forms

    forms.ImageField().clean(upload)
    old = member.player_photo.name if member.player_photo else ""
    member.player_photo = upload
    member.save(update_fields=["player_photo"])
    if old and old != member.player_photo.name:
        member.player_photo.storage.delete(old)
    return member


def clear_player_photo(member: Member) -> Member:
    """Remove the official player picture (and its file). Nothing happens without one."""
    if member.player_photo:
        member.player_photo.delete(save=False)
        member.player_photo = ""
        member.save(update_fields=["player_photo"])
    return member
