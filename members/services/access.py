"""Who may see and do what in the management app.

Door: only club admins, owners and team staff get into the management app at all (``can_access_management``);
everyone else gets 403 ``no_access`` from every endpoint. Behind the door there are four tiers:

* **View** (everyone who got in): every member with their details, every team with its roster and staff, the numbers overview
  and the club's referees and officials. Guardian-only and staff-only people (no club membership in any season) are
  left out of the default member listing unless ``include_guardians=True``.
* **Members** (``members.manage_members``, the Owner role or the platform owner): add, edit and delete members and
  everything attached to them (families, groups, memberships, pictures, logins, eligibility).
* **Teams**: editing a team, its roster and its staff needs a *management position* (``Position.management_position``)
  on that very team in the current season. Creating, deleting and reordering teams is club-wide, so it needs the
  settings permission instead.
* **Evaluations** (``members.execute_evaluations``, the Owner role or the platform owner): see and give evaluations.
* **Settings** (``admin.manage_settings``, the Owner role or the platform owner): club details and branding,
  positions, number pools, referee / official levels, creating, deleting and ordering teams.
"""

import datetime

from django.db.models import Exists, OuterRef

from authorization.permissions import is_platform_owner
from members.models import ClubMembership, FamilyMembership, Member

MANAGE_MEMBERS = "members.manage_members"
MANAGE_SETTINGS = "admin.manage_settings"
EXECUTE_EVALUATIONS = "members.execute_evaluations"


def can_manage_members(user) -> bool:
    """Platform owner, or a user holding ``members.manage_members`` in the active club (schema)."""
    if user is None or not user.is_authenticated or not user.is_active:
        return False
    return is_platform_owner(user) or user.has_perm(MANAGE_MEMBERS)


def staffed_team_ids(user, *, on: datetime.date | None = None) -> set:
    """Ids of the teams ``user`` is staffed on (a StaffAssignment) in the season covering ``on`` (default today)."""
    from events.models import Season
    from teams.models import StaffAssignment

    if user is None or not user.is_authenticated:
        return set()
    season = Season.get_current(on)
    if season is None:
        return set()
    return set(StaffAssignment.objects.filter(member__user=user, season=season).values_list("team_id", flat=True))


def members_visible_to(user, *, include_guardians: bool = False, on: datetime.date | None = None):
    """Members ``user`` may see (queryset): everyone, except that guardian-only and staff-only people (no club
    membership in any season) are left out unless ``include_guardians``. Nobody for an anonymous or inactive user."""
    if user is None or not user.is_authenticated or not user.is_active:
        return Member.objects.none()
    qs = Member.objects.all()
    if not include_guardians:
        is_member = ClubMembership.objects.filter(member=OuterRef("pk"))
        only_other_roles = (Member.objects.guardians() | Member.objects.staff()).exclude(Exists(is_member))
        qs = qs.exclude(pk__in=only_other_roles.values("pk"))
    return qs


def can_view_member(user, member: Member, *, on: datetime.date | None = None) -> bool:
    """True if ``member`` is in the set ``members_visible_to`` gives ``user`` (guardian-only people included)."""
    return members_visible_to(user, include_guardians=True, on=on).filter(pk=member.pk).exists()


def can_edit_member(user, member: Member | None = None) -> bool:
    """Only holders of ``members.manage_members`` edit members; everyone else is read-only."""
    return can_manage_members(user)


def has_staff_role(user) -> bool:
    """True if ``user`` is staff on a team in a season that is current or still to come."""
    from django.utils import timezone

    from teams.models import StaffAssignment

    if user is None or not user.is_authenticated:
        return False
    return StaffAssignment.objects.filter(member__user=user, season__end_date__gte=timezone.now().date()).exists()


def can_access_management(user) -> bool:
    """Who may use the management app at all: club admins (any admin-flagged permission), the Owner role, the platform
    owner / staff, and team staff. Everyone else (players, parents, plain members) has no access to it."""
    from authorization.permissions import is_club_admin

    if user is None or not user.is_authenticated or not user.is_active:
        return False
    return is_club_admin(user) or _is_club_owner(user) or has_staff_role(user)


def can_manage_settings(user) -> bool:
    """Platform owner, the club's Owner role, or a user holding ``admin.manage_settings`` in the active club."""
    if user is None or not user.is_authenticated or not user.is_active:
        return False
    return is_platform_owner(user) or user.has_perm(MANAGE_SETTINGS) or _is_club_owner(user)


def can_execute_evaluations(user) -> bool:
    """Platform owner, the club's Owner role, or a user holding ``members.execute_evaluations``: sees and gives evaluations."""
    if user is None or not user.is_authenticated or not user.is_active:
        return False
    return is_platform_owner(user) or user.has_perm(EXECUTE_EVALUATIONS) or _is_club_owner(user)


def _is_club_owner(user) -> bool:
    from django.core.exceptions import ObjectDoesNotExist

    from authorization.catalog import OWNER_SLUG
    from authorization.models import Role

    try:
        groups = user.tenant_perms.groups.all()
    except ObjectDoesNotExist:
        return False
    return Role.objects.filter(slug=OWNER_SLUG, group__in=groups).exists()


def managed_team_ids(user, *, on: datetime.date | None = None) -> set:
    """Ids of the teams ``user`` holds a management position on (head coach, ...) in the season covering ``on``."""
    from events.models import Season
    from teams.models import StaffAssignment

    if user is None or not user.is_authenticated or not user.is_active:
        return set()
    season = Season.get_current(on)
    if season is None:
        return set()
    return set(
        StaffAssignment.objects.filter(member__user=user, season=season, position__management_position=True).values_list("team_id", flat=True)
    )


def can_manage_team(user, team) -> bool:
    """Settings holders manage every team; anyone else needs a management position on this team this season."""
    if can_manage_settings(user):
        return True
    return team is not None and team.pk in managed_team_ids(user)


def can_manage_any_team(user) -> bool:
    return can_manage_settings(user) or bool(managed_team_ids(user))


def require_admin(actor) -> None:
    """Raise ``NotAllowed`` unless ``actor`` may manage members. Every service write starts with this."""
    from .errors import NotAllowed

    if not can_manage_members(actor):
        raise NotAllowed("You are not allowed to manage members.")


def require_settings(actor) -> None:
    """Raise ``NotAllowed`` unless ``actor`` may manage the club's settings."""
    from .errors import NotAllowed

    if not can_manage_settings(actor):
        raise NotAllowed("You are not allowed to manage the club settings.")


def require_team_manager(actor, team) -> None:
    """Raise ``NotAllowed`` unless ``actor`` manages ``team`` (a management position on it, or the settings permission)."""
    from .errors import NotAllowed

    if not can_manage_team(actor, team):
        raise NotAllowed("You are not allowed to manage this team.")
