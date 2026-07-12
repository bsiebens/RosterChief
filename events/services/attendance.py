"""Keep an event's attendance rows in sync with its effective audience.

The audience of an event is the union of the current rosters of its ``teams``
(for the event's season) plus any individually ``invited_members``, minus any
``excluded_members``. Attendance rows are reconciled against that set, but only
for events that are still in the future — history is never rewritten.
"""

from django.utils import timezone

from club.models import Season
from events.models import Attendance
from members.models import Member
from teams.models import TeamMembership


def resolve_season(event):
    """The season whose rosters define the event's audience."""
    if event.season_id is not None:
        return event.season
    return Season.covering(event.club, event.start.date())


def effective_members(event):
    """Return the ``Member`` queryset invited to ``event``."""
    member_ids: set = set()

    season = resolve_season(event)
    if season is not None:
        team_ids = list(event.teams.values_list("id", flat=True))
        if team_ids:
            member_ids.update(TeamMembership.objects.filter(team_id__in=team_ids, season=season).values_list("member_id", flat=True))

    member_ids.update(event.invited_members.values_list("id", flat=True))
    member_ids.difference_update(event.excluded_members.values_list("id", flat=True))

    return Member.objects.filter(id__in=member_ids)


def sync_event_attendances(event):
    """Reconcile ``event``'s attendance rows with its effective audience.

    No-op for events that have already started. New members get a
    ``NO_RESPONSE`` row; members no longer invited have their row removed
    outright (hard reconcile).
    """
    if event.start < timezone.now():
        return

    desired_ids = set(effective_members(event).values_list("id", flat=True))
    existing_ids = set(event.attendances.values_list("member_id", flat=True))

    to_add = desired_ids - existing_ids
    if to_add:
        Attendance.objects.bulk_create([Attendance(event=event, member_id=member_id) for member_id in to_add])

    to_remove = existing_ids - desired_ids
    if to_remove:
        event.attendances.filter(member_id__in=to_remove).delete()
