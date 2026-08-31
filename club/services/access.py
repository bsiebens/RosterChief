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
from club.models import Club, ClubMembership, ClubRole, EvaluationManager, Season, ShopManager
from events.models import Event
from members.models import FamilyMembership, Group, Member
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


def is_platform_superuser(user: User) -> bool:
    """A Django superuser sees and manages every club as if they held ADMIN there,
    with no ClubRole row needed -- the platform-operator override. Already forced
    through MFA regardless (authentication.middleware.mfa_required_for checks
    is_superuser directly), so this bypass never skips that."""
    return bool(user and user.is_authenticated and user.is_superuser)


def is_club_admin(user: User, club: Club) -> bool:
    return is_platform_superuser(user) or has_club_role(user, club, ClubRole.Roles.ADMIN)


def is_member_admin(user: User, club: Club) -> bool:
    """MEMBER_ADMIN: full read/write on people (members, families, groups, parent
    claims, teams, referee setup, onboarding requirements) without Finance/Shop,
    Club identity, Sponsors, or the ability to grant/revoke ClubRole itself --
    see can_manage_members for the actual gate, this is just the role check."""
    return has_club_role(user, club, ClubRole.Roles.MEMBER_ADMIN)


def can_manage_members(user: User, club: Club) -> bool:
    """The gate for club.mixins.MemberAdminRequiredMixin -- real ADMIN (which already
    includes the superuser bypass), or MEMBER_ADMIN specifically."""
    return is_club_admin(user, club) or is_member_admin(user, club)


def has_management_access(user: User, club: Club) -> bool:
    """Anyone with real authority in the club: ADMIN/EDITOR/MEMBER_ADMIN, a platform
    superuser, or *any* current-season staff assignment (coach, team manager,
    physio, ...).

    Deliberately excludes the plain MEMBER role -- every signed-up player (or club
    member generally) holds that automatically the moment their ClubMembership goes
    active (club/signals.py), so it says nothing about whether someone is staff.
    """
    if is_platform_superuser(user):
        return True
    elevated = ClubRole.objects.filter(member__user=user, club=club, role__in=(ClubRole.Roles.ADMIN, ClubRole.Roles.EDITOR, ClubRole.Roles.MEMBER_ADMIN)).exists()
    return elevated or teams_staffed_by(user, club).exists()


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


def groups_manageable_by(user: User, club: Club) -> QuerySet[Group]:
    """Groups the user may schedule an event for: all for an ADMIN, else only
    the ones they're themselves a member of -- unlike Team, Group has no
    manager/owner concept, so membership is the only claim there is to check."""
    if is_club_admin(user, club):
        return Group.objects.filter(club=club)
    return Group.objects.filter(club=club, memberships__member__user=user).distinct()


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


def _guardians_only(club: Club) -> QuerySet[Member]:
    """People whose *only* tie to ``club`` is being a parent of a member.

    Subtracted rather than filtered out at the source, because a bare MEMBER
    ClubRole with no ClubMembership is a real state -- someone the club knows
    but hasn't signed up for a season yet -- and narrowing the role branch to
    weed guardians out would take those people with it. Anyone who also holds a
    real membership, plays, is on a team's staff or runs the club is a member
    who happens to be a parent, and stays visible.
    """
    return Member.objects.filter(member_of__club=club, member_of__kind=ClubMembership.Kind.GUARDIAN).exclude(
        Q(member_of__club=club, member_of__kind=ClubMembership.Kind.MEMBER)
        | Q(team_memberships__team__club=club)
        | Q(staff_assignments__team__club=club)
        | Q(roles__club=club, roles__role__in=[ClubRole.Roles.ADMIN, ClubRole.Roles.EDITOR])
    )


def members_visible_to(user: User, club: Club, *, include_guardians: bool = False) -> QuerySet[Member]:
    """Members the user may see.

    ADMIN: everyone linked to the club (membership, roster, staff or role).
    Otherwise: themselves, their children, and the current-season players *and*
    staff of every team they're staffed on.

    Guardians -- parents attached to the club only through a child, see
    ``ClubMembership.Kind`` -- are **excluded by default**: they aren't members,
    so they don't belong in a member list or any member count. Pass
    ``include_guardians=True`` where the page is about a *person* rather than
    about members: opening a guardian's own detail page, editing them, putting
    them in a group, or showing a family (whose parents are the whole point).
    """
    if is_club_admin(user, club):
        attached = Member.objects.filter(Q(member_of__club=club) | Q(team_memberships__team__club=club) | Q(staff_assignments__team__club=club) | Q(roles__club=club)).distinct()
        return attached if include_guardians else attached.exclude(pk__in=_guardians_only(club))

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
    roster = Member.objects.filter(Q(team_memberships__team__in=teams, team_memberships__season=season) | Q(staff_assignments__team__in=teams, staff_assignments__season=season))

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


def is_shop_admin(user: User, club: Club) -> bool:
    """An additive grant (club.models.ShopManager), not a ClubRole value -- see
    that model's own docstring for why. Layered on top of whatever ClubRole a
    member already holds, the same way is_coach_manager layers on top via
    StaffAssignment instead of competing for the single ClubRole slot."""
    return ShopManager.objects.filter(member__user=user, club=club).exists()


def can_manage_shop(user: User, club: Club) -> bool:
    return is_club_admin(user, club) or is_shop_admin(user, club)


def is_evaluation_manager(user: User, club: Club) -> bool:
    """An additive grant (club.models.EvaluationManager), not a ClubRole value --
    same shape as is_shop_admin, for the same reason: see that model's own
    docstring."""
    return EvaluationManager.objects.filter(member__user=user, club=club).exists()


def can_manage_evaluations(user: User, club: Club) -> bool:
    return is_club_admin(user, club) or is_evaluation_manager(user, club)


def can_add_news(user: User, club: Club) -> bool:
    """ADMIN, EDITOR, or a current-season coach_manager -- who's trusted to
    author club content, not just anyone on staff (a physio shouldn't post news)."""
    return is_club_admin(user, club) or has_club_role(user, club, ClubRole.Roles.EDITOR) or is_coach_manager(user, club)


def can_publish_news(user: User, club: Club) -> bool:
    """Only ADMIN/EDITOR may push a news item live -- the release-flow gate."""
    return is_club_admin(user, club) or has_club_role(user, club, ClubRole.Roles.EDITOR)


def can_edit_news(user: User, news_item) -> bool:
    """Broad while it's a draft (anyone who could create one); editor/admin-only
    once published -- an editor is accountable for what's actually live."""
    if news_item.status == news_item.Status.PUBLISHED:
        return can_publish_news(user, news_item.club)
    return can_add_news(user, news_item.club)


def can_manage_forms(user: User, club: Club) -> bool:
    """ADMIN, EDITOR, or a current-season coach_manager -- same trusted-content
    role set as can_add_news (ARCHITECTURE.md's RBAC table already assigns
    EDITOR ownership of formbuilder content). One function gates creating a
    form, sending it, and viewing its responses -- unlike News there's no
    draft/review workflow here (creating a FormSend *is* publishing it, see
    FormSend's own docstring), so there's nothing for a separate publish
    check to gate, and no stricter tier for response visibility either."""
    return is_club_admin(user, club) or has_club_role(user, club, ClubRole.Roles.EDITOR) or is_coach_manager(user, club)
