"""Booking a dressing room for a tournament team -- a capacity-style
select-for-update lock, the same reasoning ``events.services.referees``
uses for its own assignment ceiling: two admins confirming a room at the
same moment can't both squeeze a team into an already-double-booked slot.
``DressingRoomAssignment``'s own ``UniqueConstraint`` only stops the exact
same (room, team) pair being inserted twice -- a genuine overlap between
two *different* teams needs a query, not a constraint, hence this module.
"""

from django.db import transaction
from django.utils.translation import gettext_lazy as _

from ..models import DressingRoom, DressingRoomAssignment


class DressingRoomAssignmentError(Exception):
    """A dressing room could not be assigned."""


def _windows_conflict(a_start, a_end, b_start, b_end) -> bool:
    """Either side missing a window means "for the whole tournament" --
    that always conflicts with anything else on the same room. Two
    time-boxed windows conflict only if they actually overlap."""
    if a_start is None or a_end is None or b_start is None or b_end is None:
        return True
    return a_start < b_end and b_start < a_end


@transaction.atomic
def assign_dressing_room(dressing_room: DressingRoom, tournament_team, *, assigned_from=None, assigned_until=None, note: str = "") -> DressingRoomAssignment:
    """Books `dressing_room` for `tournament_team`, for `assigned_from`..
    `assigned_until` (or the whole tournament, if either is left unset).
    Raises DressingRoomAssignmentError if that team already has this room,
    or if the requested window conflicts with another team's booking of
    the same room."""
    dressing_room = DressingRoom.objects.select_for_update().get(pk=dressing_room.pk)

    if DressingRoomAssignment.objects.filter(dressing_room=dressing_room, tournament_team=tournament_team).exists():
        raise DressingRoomAssignmentError(_("%(team)s is already assigned to this room.") % {"team": tournament_team})

    others = DressingRoomAssignment.objects.filter(dressing_room=dressing_room).exclude(tournament_team=tournament_team)
    for other in others:
        if _windows_conflict(assigned_from, assigned_until, other.assigned_from, other.assigned_until):
            raise DressingRoomAssignmentError(_("This room is already booked by another team for that time."))

    return DressingRoomAssignment.objects.create(dressing_room=dressing_room, tournament_team=tournament_team, assigned_from=assigned_from, assigned_until=assigned_until, note=note)


def release_dressing_room(assignment: DressingRoomAssignment):
    assignment.delete()
