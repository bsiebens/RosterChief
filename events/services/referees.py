"""Assigning referees to home games.

Eligibility comes from teams.RefereeProfile -- a per-member level + validity
(unrelated to members.Group), with the *level* (teams.RefereeLevel) owning
which teams it qualifies for. A profile with no level, or one whose validity
has lapsed, is not eligible for anything -- see RefereeProfile.eligible_teams,
the single definition this module reads through. Availability conflicts (the
member is expected elsewhere at an overlapping time) are surfaced as a
warning only -- never a hard block, since a human may know the two
commitments don't actually clash (enough travel time, one is optional).
Capacity (Event.max_referees) *is* a hard ceiling, enforced here for both
staff-assignment and any future self-service sign-up.

A team can opt out of all of this entirely (Team.referee_management =
FEDERATION): its home games never need club-arranged referees, so they're
excluded from eligibility, assignment, and every referee-facing screen --
see needs_referee_management().

Not every referee is a club member -- add_external_referee logs one by name
only (still counts against Event.max_referees, still capacity-checked), for
e.g. a federation-appointed referee the club still needs to pay.
"""

import datetime
from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from events.models import Event, EventReferee
from events.services.attendance import effective_members
from members.models import Member
from teams.models import Team

# Used only to give an event with no explicit `end` a time window for the
# overlap check below -- never written back to the event itself.
ASSUMED_EVENT_DURATION = datetime.timedelta(hours=2)


class RefereeAssignmentError(Exception):
    """A referee could not be assigned to a game."""


def event_window(event):
    """(start, end) for overlap purposes -- events.end may be blank."""
    return event.start, event.end or (event.start + ASSUMED_EVENT_DURATION)


def needs_referee_management(event) -> bool:
    """Whether this event is one the club should be arranging referees for at
    all: a home game with at least one club-managed team. A federation-managed
    team's home games are entirely out of scope for the referee tools -- the
    federation appoints referees for those, not the club."""
    return event.is_home_game and event.teams.filter(referee_management=Team.RefereeManagement.CLUB).exists()


def eligible_referees(event):
    """Members who could referee `event`: their RefereeProfile has a level
    qualifying for one of its club-managed teams, and is currently valid,
    minus whoever is already assigned. Empty unless needs_referee_management."""
    if not needs_referee_management(event):
        return Member.objects.none()

    team_ids = list(event.teams.filter(referee_management=Team.RefereeManagement.CLUB).values_list("id", flat=True))
    assigned_ids = event.referees.values_list("member_id", flat=True)
    today = timezone.localdate()

    return (
        Member.objects.filter(referee_profile__level__teams__id__in=team_ids, referee_profile__valid_until__gte=today)
        .exclude(pk__in=assigned_ids)
        .distinct()
    )


def conflicting_events(member, event):
    """Other events in this club overlapping `event`'s time window where
    `member` is part of the expected audience -- informational only, never
    blocks an assignment."""
    start, end = event_window(event)
    candidates = Event.objects.filter(club=event.club).exclude(pk=event.pk).filter(start__lt=end)

    conflicts = []
    for candidate in candidates:
        _candidate_start, candidate_end = event_window(candidate)
        if candidate_end > start and effective_members(candidate).filter(pk=member.pk).exists():
            conflicts.append(candidate)
    return conflicts


def _lock_and_check_capacity(event):
    """Row-locks `event` and raises RefereeAssignmentError if it's not a
    club-managed home game or is already at Event.max_referees. Shared by
    assign_referee/add_external_referee so two admins acting at the same
    moment can't both squeeze past the ceiling."""
    event = Event.objects.select_for_update().get(pk=event.pk)

    if not needs_referee_management(event):
        raise RefereeAssignmentError(_("Referees can only be assigned to home games for club-managed teams."))

    if event.referees.count() >= event.max_referees:
        raise RefereeAssignmentError(_("This game already has its maximum of %(max)s referee(s).") % {"max": event.max_referees})

    return event


@transaction.atomic
def assign_referee(event, member, *, assigned_by):
    """Assign `member` to referee `event`. Raises RefereeAssignmentError if
    it's not a club-managed home game, the game is already at
    Event.max_referees, or `member` is already assigned."""
    event = _lock_and_check_capacity(event)

    if event.referees.filter(member=member).exists():
        raise RefereeAssignmentError(_("%(member)s is already assigned to this game.") % {"member": member})

    return EventReferee.objects.create(event=event, member=member, assigned_by=assigned_by)


@transaction.atomic
def add_external_referee(event, name, *, assigned_by):
    """Log a non-member referee (e.g. federation-appointed) by name only.
    Same capacity/home-game rules as assign_referee -- an external slot still
    counts against Event.max_referees."""
    name = name.strip()
    if not name:
        raise RefereeAssignmentError(_("A name is required for an external referee."))

    event = _lock_and_check_capacity(event)

    return EventReferee.objects.create(event=event, external_name=name, assigned_by=assigned_by)


def remove_referee(referee):
    referee.delete()


def set_referee_fee(referee, *, fee=None, km=None, km_rate=None):
    """Update one referee assignment's payment details -- what the club owes
    for this game, split into a flat fee and a mileage component. Any
    argument left as None clears that field rather than leaving it
    unchanged, matching how the edit form always submits all three."""
    referee.fee = fee if fee is not None else Decimal("0.00")
    referee.km = km
    referee.km_rate = km_rate
    referee.save(update_fields=["fee", "km", "km_rate"])
    return referee
