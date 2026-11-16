"""Who a FormSend is addressed to.

Same union/subtract shape as ``events.services.attendance``: the rosters of the send's teams (for its season) plus every
member of its groups, or every ACTIVE club member of the season for a club-wide send, plus the invited members, minus the
excluded members (exclusion wins). Everybody in a group counts (``GroupMembership.must_respond`` is an event concept and
is ignored here). A send without a season uses the current season.
"""

from collections import defaultdict

from django.utils import timezone

from events.models import Season
from members.models import ClubMembership, FamilyMembership, GroupMembership, Member
from teams.models import TeamMembership


def resolve_season(send) -> Season | None:
    """The season whose rosters define the send's audience: its own ``season``, else the current one."""
    if send.season_id is not None:
        return send.season
    return Season.get_current()


def audience_ids_by_send(sends) -> dict:
    """``{send.pk: {member ids}}`` for every send, resolved in a fixed number of queries (plus one per send for its M2M
    ids, which are small) -- what lists with an audience count use."""
    sends = list(sends)
    if not sends:
        return {}
    current = None
    season_of = {}
    for send in sends:
        if send.season_id is not None:
            season_of[send.pk] = send.season_id
        else:
            current = current or Season.get_current()
            season_of[send.pk] = current.pk if current else None

    club_wide_seasons = {season_of[s.pk] for s in sends if s.club_wide and season_of[s.pk]}
    active = defaultdict(set)
    if club_wide_seasons:
        for season_id, member_id in ClubMembership.objects.filter(season_id__in=club_wide_seasons, status=ClubMembership.StatusChoices.ACTIVE).values_list("season_id", "member_id"):
            active[season_id].add(member_id)

    scoped = [s for s in sends if not s.club_wide]
    teams = {s.pk: set(s.teams.values_list("pk", flat=True)) for s in scoped}
    groups = {s.pk: set(s.groups.values_list("pk", flat=True)) for s in scoped}
    team_ids = set().union(*teams.values()) if teams else set()
    group_ids = set().union(*groups.values()) if groups else set()
    seasons = {season_of[s.pk] for s in scoped if season_of[s.pk]}

    roster = defaultdict(set)
    if team_ids and seasons:
        for team_id, season_id, member_id in TeamMembership.objects.filter(team_id__in=team_ids, season_id__in=seasons).values_list("team_id", "season_id", "member_id"):
            roster[(team_id, season_id)].add(member_id)
    group_members = defaultdict(set)
    if group_ids:
        for group_id, member_id in GroupMembership.objects.filter(group_id__in=group_ids).values_list("group_id", "member_id"):
            group_members[group_id].add(member_id)

    result = {}
    for send in sends:
        season_id = season_of[send.pk]
        ids = set()
        if send.club_wide:
            if season_id:
                ids |= active[season_id]
        else:
            if season_id:
                for team_id in teams[send.pk]:
                    ids |= roster[(team_id, season_id)]
            for group_id in groups[send.pk]:
                ids |= group_members[group_id]
        ids |= set(send.invited_members.values_list("pk", flat=True))
        ids -= set(send.excluded_members.values_list("pk", flat=True))
        result[send.pk] = ids
    return result


def effective_member_ids(send) -> set:
    return audience_ids_by_send([send])[send.pk]


def effective_members(send):
    """The ``Member`` queryset this send is addressed to."""
    return Member.objects.filter(pk__in=effective_member_ids(send))


def audience_count(send) -> int:
    return len(effective_member_ids(send))


def addressed_people(send, members) -> list:
    """The subset of ``members`` (one Member or an iterable) the send is addressed to, in the order given."""
    if isinstance(members, Member):
        members = [members]
    members = [m for m in members if m is not None]
    if not members:
        return []
    audience = effective_member_ids(send)
    return [m for m in members if m.pk in audience]


def addressed_to(send, members) -> bool:
    """Whether the audience includes ``members`` (one Member, or any one of an iterable)."""
    return bool(addressed_people(send, members))


def members_not_yet_submitted(send):
    """``effective_members`` minus anyone with a submission to this send: who still needs to respond (the reminder job)."""
    submitted = send.submissions.exclude(member__isnull=True).values_list("member_id", flat=True)
    return effective_members(send).exclude(pk__in=submitted)


def is_send_open(send, when=None) -> bool:
    """Whether the send accepts submissions now: its own is_active / opens_at / closes_at window."""
    when = when or timezone.now()
    if not send.is_active:
        return False
    if send.opens_at is not None and when < send.opens_at:
        return False
    return not (send.closes_at is not None and when > send.closes_at)


def send_status(send, when=None) -> str:
    """``inactive`` (switched off), ``closed`` (past closes_at), ``scheduled`` (before opens_at) or ``open``."""
    when = when or timezone.now()
    if not send.is_active:
        return "inactive"
    if send.closes_at is not None and when > send.closes_at:
        return "closed"
    if send.opens_at is not None and when < send.opens_at:
        return "scheduled"
    return "open"


def guardian_ids_of(member) -> set:
    """Ids of the parents / guardians in the families where ``member`` is a child (a login is not required)."""
    families = FamilyMembership.objects.filter(member=member, role=FamilyMembership.FamilyRole.CHILD).values("family_id")
    return set(FamilyMembership.objects.filter(family_id__in=families, role__in=FamilyMembership.GUARDIAN_ROLES).exclude(member=member).values_list("member_id", flat=True))


def is_guardian_of(guardian, child) -> bool:
    return guardian is not None and child is not None and guardian.pk in guardian_ids_of(child)


def managed_children(guardian) -> list:
    """The children of the families ``guardian`` is a parent / guardian in."""
    families = FamilyMembership.objects.filter(member=guardian, role__in=FamilyMembership.GUARDIAN_ROLES).values("family_id")
    return list(Member.objects.filter(family_memberships__family_id__in=families, family_memberships__role=FamilyMembership.FamilyRole.CHILD).exclude(pk=guardian.pk).distinct())


def fillable_members(send, viewer) -> list:
    """Who ``viewer`` (a Member) may fill ``send`` in for: themself and their children, restricted to the audience."""
    return addressed_people(send, [viewer, *managed_children(viewer)])
