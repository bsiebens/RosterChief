"""Per-club access decisions.

All authorisation goes through this module (ARCHITECTURE §3): stored
``ClubRole`` rows plus object-scoped facts — coach/manager is *derived* from
``StaffAssignment`` (never a ClubRole), and parent/guardian from the family
graph. ADMIN is the club-wide override.

Two axes, deliberately separate:

* **Authority** (``teams_managed_by``, ``can_edit_event``) requires a
  *management* position, and only for the **current season** — a StaffAssignment
  is per-season, so a former coach's authority expires with it. (A ``ClubRole``,
  by contrast, is permanent and survives a lapsed membership.)
* **Visibility** (``teams_staffed_by`` → ``members_visible_to``) covers *any*
  staff position, so support staff (physio, kit manager) can see the roster they
  work with without gaining any authority over it.
"""

from django.db.models import Q, QuerySet
from django.utils import timezone

from authentication.models import User
from club.models import Club, ClubRole, Season
from events.models import Event
from members.models import FamilyMembership, Member
from teams.models import StaffAssignment, Team

#: Derived (never stored) roles.
COACH = "coach"
MANAGER = "manager"
COACH_MANAGER = "coach_manager"


def current_season(club: Club) -> Season | None:
    """The club's season covering today. Staff authority is scoped to it."""
    return Season.covering(club, timezone.localdate())


def event_season(event: Event) -> Season | None:
    """The season an event belongs to (explicit, else derived from its start)."""
    return event.season or Season.covering(event.club, event.start.date())


def has_club_role(user: User, club: Club, role: ClubRole.Roles) -> bool:
    return ClubRole.objects.filter(member__user=user, club=club, role=role).exists()


def is_club_admin(user: User, club: Club) -> bool:
    return has_club_role(user, club, ClubRole.Roles.ADMIN)


def is_coach_manager(user: User, club: Club) -> bool:
    """Derived from a current-season StaffAssignment in a *management* position."""
    return StaffAssignment.objects.filter(
        member__user=user,
        team__club=club,
        position__management_position=True,
        season=current_season(club),
    ).exists()


def roles_in_club(user: User, club: Club) -> set[str]:
    """The user's roles in ``club``, including the derived COACH_MANAGER role."""
    roles = set(ClubRole.objects.filter(member__user=user, club=club).values_list("role", flat=True))
    if is_coach_manager(user, club):
        roles.add(COACH_MANAGER)
    return roles


def teams_managed_by(user: User, club: Club) -> QuerySet[Team]:
    """Teams the user has authority over: all for an ADMIN, else the ones they
    manage *this season* (management position only)."""
    if is_club_admin(user, club):
        return Team.objects.filter(club=club)
    return Team.objects.filter(
        club=club,
        staff_assignments__member__user=user,
        staff_assignments__position__management_position=True,
        staff_assignments__season=current_season(club),
    ).distinct()


def teams_staffed_by(user: User, club: Club) -> QuerySet[Team]:
    """Teams the user is on the staff of this season, management or not.

    Visibility only — being a team's physio grants sight of the roster, never
    authority over it.
    """
    return Team.objects.filter(
        club=club,
        staff_assignments__member__user=user,
        staff_assignments__season=current_season(club),
    ).distinct()


def members_visible_to(user: User, club: Club) -> QuerySet[Member]:
    """Members the user may see.

    ADMIN: everyone linked to the club (membership, roster, staff or role).
    Otherwise: themselves, their children, and the current-season players *and*
    staff of every team they're staffed on.
    """
    if is_club_admin(user, club):
        return Member.objects.filter(
            Q(member_of__club=club) | Q(team_memberships__team__club=club) | Q(staff_assignments__team__club=club) | Q(roles__club=club)
        ).distinct()

    me = Member.objects.filter(user=user).first()
    if me is None:
        return Member.objects.none()

    children = Member.objects.filter(
        family_memberships__role=FamilyMembership.FamilyRole.CHILD,
        family_memberships__family__memberships__member=me,
        family_memberships__family__memberships__role__in=[FamilyMembership.FamilyRole.PARENT, FamilyMembership.FamilyRole.GUARDIAN],
    )

    season = current_season(club)
    teams = teams_staffed_by(user, club)
    roster = Member.objects.filter(
        Q(team_memberships__team__in=teams, team_memberships__season=season) | Q(staff_assignments__team__in=teams, staff_assignments__season=season)
    )

    visible = {me.pk} | set(children.values_list("pk", flat=True)) | set(roster.values_list("pk", flat=True))
    return Member.objects.filter(pk__in=visible)


def can_edit_event(user: User, event: Event) -> bool:
    """ADMIN/EDITOR in the club, the event's owner, or a manager of one of its
    teams *for that event's season*."""
    club = event.club
    if is_club_admin(user, club) or has_club_role(user, club, ClubRole.Roles.EDITOR):
        return True
    if event.created_by_id is not None and event.created_by.user_id == user.pk:
        return True
    return StaffAssignment.objects.filter(
        member__user=user,
        team__in=event.teams.all(),
        position__management_position=True,
        season=event_season(event),
    ).exists()


def can_manage_shop(user: User, club: Club) -> bool:
    return is_club_admin(user, club)
