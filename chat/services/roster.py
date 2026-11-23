"""Team chats follow the rosters: an ``AudienceRule`` says whose participants a chat has (staff / players / parents of a team in the
current season). ``sync`` adds new people and sets ``left_at`` for roster people who dropped out. People added by hand are never removed,
and nobody an admin removed comes back."""

import logging

from django.db import transaction
from django.utils import timezone

from chat.models import AudienceRule, Conversation, Participant
from chat.services import people
from formbuilder.services.audience import guardian_ids_of
from members.models import Member
from teams.models import StaffAssignment, TeamMembership

log = logging.getLogger(__name__)


def desired(rule) -> tuple[set, set]:
    """``(member ids, staff ids)`` the rule wants in the chat right now."""
    season = people.current_season()
    if season is None:
        return set(), set()
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
    for rule in conversation.rules.select_related("team"):
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
