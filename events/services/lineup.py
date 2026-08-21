"""Placing players into a Lineup's slots and publishing it -- coach mode's C3
screen (mobile/coach_views.py's CoachLineupView/CoachLineupPlaceView). Mirrors
events.services.attendance's shape: small, focused functions the view writes
through rather than touching the models directly.
"""

from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from events.models import Attendance, LineupSlot
from members.models import Member
from notifications.services import notify_members

#: Attendance statuses that mean "not actually available" -- these members
#: stay exactly as they are on publish, never flipped to NOT_SELECTED, and
#: aren't offered as placeable in the available-players pool.
UNAVAILABLE_STATUSES = [Attendance.AttendanceStatus.ABSENT, Attendance.AttendanceStatus.EXCUSED, Attendance.AttendanceStatus.NO_RESPONSE]


def place_member(lineup, slot, member):
    """Tap-to-place: ``member`` moves into ``slot``, vacating any other slot
    of theirs in the same ``lineup`` first (a member is only ever in one slot
    at a time). Whoever was already in ``slot``, if anyone, is simply bumped
    back to the available pool -- the tap equivalent of the mock's "slots
    accept one player and swap on drop", without true drag-and-drop's two-way
    swap (see LineupSlot's own docstring for why tap-to-place instead of
    drag-and-drop at all)."""
    LineupSlot.objects.filter(unit__lineup=lineup, member=member).exclude(pk=slot.pk).update(member=None)
    slot.member = member
    slot.save(update_fields=["member"])


def clear_slot(slot):
    slot.member = None
    slot.save(update_fields=["member"])


def publish_lineup(lineup):
    """Marks the lineup published and writes it into the game record via
    Attendance -- the reason AttendanceStatus.SELECTED/NOT_SELECTED exist,
    previously unused anywhere. Every slotted member becomes SELECTED; every
    other member with an Attendance row for this event who was actually
    available (not out/silent, see UNAVAILABLE_STATUSES) becomes
    NOT_SELECTED. Notifies only the selected players."""
    lineup.published_at = timezone.now()
    lineup.save(update_fields=["published_at"])

    selected_member_ids = set(LineupSlot.objects.filter(unit__lineup=lineup, member__isnull=False).values_list("member_id", flat=True))

    Attendance.objects.filter(event=lineup.event, member_id__in=selected_member_ids).update(status=Attendance.AttendanceStatus.SELECTED)
    Attendance.objects.filter(event=lineup.event).exclude(member_id__in=selected_member_ids).exclude(status__in=UNAVAILABLE_STATUSES).update(status=Attendance.AttendanceStatus.NOT_SELECTED)

    selected_members = Member.objects.filter(pk__in=selected_member_ids)
    if selected_members:
        body = _("You're in the line-up for %(event)s.") % {"event": lineup.event.title}
        notify_members(selected_members, club=lineup.event.club, title=_("Line-up published"), body=body, source=lineup.event)

    return lineup
