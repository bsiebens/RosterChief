"""Read helpers for the calendar API. Everyone in the management app may view every event; whether the viewer may
edit one is computed by the API with ``members.services.access.can_manage_event``.

Series are not expanded here: their occurrences are real Event rows, generated up to a rolling horizon
(events.services.recurrence.HORIZON_DAYS ahead), so a range further out than that shows no occurrences of an
open-ended series yet. Cancelled events are included (``event.cancelled`` flags them) so the calendar can strike them
through.
"""

import datetime

from django.db.models import Exists, OuterRef, Prefetch, Q

from events.models import Event
from members.models import GroupMembership, Member
from teams.models import StaffAssignment, TeamMembership


def events_between(
    start: datetime.datetime,
    end: datetime.datetime,
    *,
    team_id=None,
    group_id=None,
    location_id=None,
    opponent_id=None,
    kind: str | None = None,
    mine_member: Member | None = None,
):
    """Events overlapping ``[start, end)`` (an event without an end counts as an instant at its start), ordered by
    start, with location / opponent / series / season joined and teams / groups prefetched (no N+1 for a list).

    Filters: ``team_id`` / ``group_id`` (the event lists that team / group), ``location_id``, ``opponent_id``,
    ``kind`` (an Event.EventKind value), ``mine_member``: events whose audience includes that member (on the roster or
    staff of one of its teams in the event's season, in one of its groups, invited, or club-wide; never when
    explicitly excluded).
    """
    qs = Event.objects.filter(start__lt=end).filter(Q(end__gt=start) | Q(end__isnull=True, start__gte=start))
    if team_id:
        qs = qs.filter(teams__pk=team_id)
    if group_id:
        qs = qs.filter(groups__pk=group_id)
    if location_id:
        qs = qs.filter(location_id=location_id)
    if opponent_id:
        qs = qs.filter(opponent_id=opponent_id)
    if kind:
        qs = qs.filter(kind=kind)
    if mine_member is not None:
        qs = qs.filter(audience_q(mine_member))
    return qs.select_related("location", "opponent", "series", "season").prefetch_related(Prefetch("teams"), Prefetch("groups")).order_by("start", "title").distinct()


def audience_q(member) -> Q:
    """Q for the events whose audience includes ``member`` (see events_between)."""
    member_id = getattr(member, "pk", member)
    on_roster = TeamMembership.objects.filter(member_id=member_id, team__scheduled_events=OuterRef("pk"), season=OuterRef("season"))
    on_staff = StaffAssignment.objects.filter(member_id=member_id, team__scheduled_events=OuterRef("pk"), season=OuterRef("season"))
    in_group = GroupMembership.objects.filter(member_id=member_id, group__scheduled_events=OuterRef("pk"))
    invited = Event.invited_members.through.objects.filter(event_id=OuterRef("pk"), member_id=member_id)
    excluded = Event.excluded_members.through.objects.filter(event_id=OuterRef("pk"), member_id=member_id)
    return (Q(club_wide=True) | Exists(on_roster) | Exists(on_staff) | Exists(in_group) | Exists(invited)) & ~Exists(excluded)
