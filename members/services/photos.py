"""Who may see a member's picture. Pictures live in private storage (``rosterchief.storage``) and are only ever
streamed by ``members.views.MemberPhotoView`` after this check; there is no public URL for them.

* ``profile_photo``, the self-service picture the apps show: the member themselves, and anyone who is a user of the
  club the request is for. Never public.
* ``player_photo``, the official picture: anyone once ``photo_public_consent`` is set (the public roster); otherwise the
  member themselves, and on a club's own host whoever may manage members (``members.manage_members``), plus the
  platform owner for support.
* no ``field``: the *effective* picture, the profile picture, else the player picture. The player picture used as that
  fallback is shown club-wide like a profile picture (otherwise rosters would show broken images); asked for directly
  it keeps the stricter rules.

Parents and guardians are not modelled in this version; when family links exist they belong next to the "member
themselves" checks below.
"""

from django.core.exceptions import ObjectDoesNotExist

from authorization.permissions import is_platform_owner
from members.models import Member

MANAGE_MEMBERS = "members.manage_members"


def is_member_themselves(user, member: Member) -> bool:
    return member.user_id is not None and member.user_id == user.pk


def is_user_of_club(user, club) -> bool:
    """A signed-in user with a permissions row in ``club`` (checked in the club's schema, which the request has active)."""
    if club is None:
        return False
    try:
        return user.tenant_perms is not None
    except ObjectDoesNotExist:
        return False


def can_manage_members(user, club) -> bool:
    return club is not None and (is_platform_owner(user) or user.has_perm(MANAGE_MEMBERS))


def can_view_member_photo(user, member: Member, club=None, field: str | None = None) -> bool:
    """``field`` is ``"player_photo"``, ``"profile_photo"`` or ``None`` for the effective picture."""
    authenticated = user is not None and user.is_authenticated and user.is_active
    if field is None:
        if member.profile_photo:
            field = "profile_photo"
        elif member.player_photo and can_view_member_photo(user, member, club, field="profile_photo"):
            return True
        else:
            field = "player_photo"
    if field == "profile_photo":
        if not authenticated:
            return False
        return is_member_themselves(user, member) or is_user_of_club(user, club) or can_manage_members(user, club)
    if member.photo_public_consent:
        return True
    if not authenticated:
        return False
    return is_member_themselves(user, member) or can_manage_members(user, club)
