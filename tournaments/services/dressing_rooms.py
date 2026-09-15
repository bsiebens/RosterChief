"""Booking a dressing room for a tournament team. A room can legitimately go
to more than one team at once (shared rooms, or a team split across two age
groups sharing kit) -- and a team can legitimately hold more than one room --
so overlapping bookings are *allowed*, not rejected: the only thing this
module still blocks is the exact same (room, team) pair being booked twice,
a plain duplicate rather than an intentional multi-booking.
``windows_conflict`` stays here (not private) since the management view also
uses it to flag overlaps in the assignment table for staff to notice, rather
than the service silently allowing something nobody can see.
"""

from django.db import transaction
from django.utils.translation import gettext_lazy as _

from ..models import DressingRoom, DressingRoomAssignment


class DressingRoomAssignmentError(Exception):
    """A dressing room could not be assigned."""


def windows_conflict(a_start, a_end, b_start, b_end) -> bool:
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
    Raises DressingRoomAssignmentError only if that team already has this
    exact room -- an overlapping booking with a *different* team (same room,
    or the same team already holding a different room) is allowed and left
    for the "Dressing rooms" tab to flag visually, not rejected here."""
    dressing_room = DressingRoom.objects.select_for_update().get(pk=dressing_room.pk)

    if DressingRoomAssignment.objects.filter(dressing_room=dressing_room, tournament_team=tournament_team).exists():
        raise DressingRoomAssignmentError(_("%(team)s is already assigned to this room.") % {"team": tournament_team})

    return DressingRoomAssignment.objects.create(dressing_room=dressing_room, tournament_team=tournament_team, assigned_from=assigned_from, assigned_until=assigned_until, note=note)


def release_dressing_room(assignment: DressingRoomAssignment):
    assignment.delete()
