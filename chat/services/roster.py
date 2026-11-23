"""Team chats follow the rosters: an ``AudienceRule`` says whose participants a chat has (staff / players / parents of a team in the
current season). ``sync`` adds new people and sets ``left_at`` for roster people who dropped out. People added by hand are never removed,
and nobody an admin removed comes back."""

import logging

from django.db import transaction
from django.utils import timezone

from chat.models import AudienceRule, Conversation, Participant
from chat.services import people
from formbuilder.services.audience import guardian_ids_of
from members.models import GroupMembership, Member
from teams.models import StaffAssignment, TeamMembership

log = logging.getLogger(__name__)


def _reachable(ids: set) -> set:
    """Who can actually be written to: members with a login; for those without one, their guardians that have a login. A minor with a login
    stays only when a guardian with a login comes along (the minors rule: a child is not in a club-wide chat without a parent)."""
    found = list(Member.objects.filter(pk__in=ids).select_related("user"))
    out = set()
    for m in found:
        has_login = m.user_id is not None and m.user.is_active
        guardians = set(Member.objects.filter(pk__in=guardian_ids_of(m), user__isnull=False, user__is_active=True).values_list("pk", flat=True)) if (not has_login or people.is_minor(m)) else set()
        if has_login and (not people.is_minor(m) or guardians):
            out.add(m.pk)
        out |= guardians
    return out


def _desired_club(rule, season) -> tuple[set, set]:
    """Club-wide rule: nobody is a chat admin through it (the creator is). Without a team: everybody reachable when all three groups are
    on, else the staff / players / parents of any team (and club staff) this season."""
    staff_ids = set(StaffAssignment.objects.filter(season=season).values_list("member_id", flat=True)) | set(Member.objects.filter(club_staff=True).values_list("pk", flat=True))
    player_ids = set(TeamMembership.objects.filter(season=season).values_list("member_id", flat=True))
    if rule.include_staff and rule.include_players and rule.include_parents:
        wanted = set(Member.objects.values_list("pk", flat=True))
    else:
        wanted = (staff_ids if rule.include_staff else set()) | (player_ids if rule.include_players else set())
        if rule.include_parents:
            for child in Member.objects.filter(pk__in=player_ids):
                wanted |= guardian_ids_of(child)
    return _reachable(wanted), set()


def desired(rule) -> tuple[set, set]:
    """``(member ids, staff ids)`` the rule wants in the chat right now."""
    season = people.current_season()
    if season is None:
        return set(), set()
    if rule.group_id is not None:
        return _desired_group(rule)
    if rule.team_id is None:
        return _desired_club(rule, season)
    if rule.conversation.kind == Conversation.Kind.GROUP:  # a club-wide chat narrowed to teams
        ids, _staff = _desired_team(rule, season)
        return _reachable(ids), set()
    return _desired_team(rule, season)


def _desired_group(rule) -> tuple[set, set]:
    """A members group: its admins count as staff (and chat admins), the other members as players; parents are the guardians of the members."""
    rows = list(GroupMembership.objects.filter(group_id=rule.group_id).values_list("member_id", "is_admin"))
    staff = {m for m, admin in rows if admin} if rule.include_staff else set()
    members = {m for m, admin in rows if not admin} if rule.include_players else set()
    parents = set()
    if rule.include_parents:
        for child in Member.objects.filter(pk__in=[m for m, _a in rows]):
            parents |= guardian_ids_of(child)
    return _reachable(staff | members | parents), staff & _reachable(staff)


def _desired_team(rule, season) -> tuple[set, set]:
    staff = set(StaffAssignment.objects.filter(team=rule.team, season=season).values_list("member_id", flat=True)) if rule.include_staff else set()
    player_ids = set(TeamMembership.objects.filter(team=rule.team, season=season).values_list("member_id", flat=True))
    players = player_ids if rule.include_players else set()
    parents = set()
    if rule.include_parents:
        for child in Member.objects.filter(pk__in=player_ids):
            parents |= guardian_ids_of(child)
    return staff | players | parents, staff


@transaction.atomic
def sync(conversation) -> dict:
    """Bring a team chat's roster-sourced participants in line with its rules. Returns ``{"added": n, "left": n}``."""
    wanted, staff = set(), set()
    for rule in conversation.rules.select_related("team", "conversation"):
        ids, staff_ids = desired(rule)
        wanted |= ids
        staff |= staff_ids
    now = timezone.now()
    existing = {p.member_id: p for p in conversation.participants.all()}
    added = left = 0
    for member_id in wanted:
        p = existing.get(member_id)
        if p is None:
            Participant.objects.create(conversation=conversation, member_id=member_id, source=Participant.Source.RULE, is_admin=member_id in staff, last_read_seq=conversation.last_seq)
            added += 1
        elif p.left_at is not None and not p.removed_by_admin and p.source == Participant.Source.RULE:
            p.left_at, p.joined_at = None, now
            p.save(update_fields=["left_at", "joined_at", "modified"])
            added += 1
        elif p.left_at is None and p.source == Participant.Source.RULE and p.is_admin != (member_id in staff):
            p.is_admin = member_id in staff
            p.save(update_fields=["is_admin", "modified"])
    for member_id, p in existing.items():
        if p.source == Participant.Source.RULE and p.left_at is None and member_id not in wanted:
            p.left_at = now
            p.save(update_fields=["left_at", "modified"])
            left += 1
    return {"added": added, "left": left}


def sync_team(team) -> int:
    """Sync every chat that has a rule for ``team`` (called when its roster changes)."""
    n = 0
    for conversation in Conversation.objects.filter(pk__in=AudienceRule.objects.filter(team=team).values("conversation_id")):
        sync(conversation)
        n += 1
    return n


def sync_all() -> dict:
    total = {"conversations": 0, "added": 0, "left": 0}
    for conversation in Conversation.objects.filter(rules__isnull=False, archived=False).distinct():
        try:
            result = sync(conversation)
        except Exception:
            log.exception("chat.sync_failed %s", conversation.pk)
            continue
        total["conversations"] += 1
        total["added"] += result["added"]
        total["left"] += result["left"]
    return total
