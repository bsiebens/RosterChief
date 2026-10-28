"""Answering an event invitation (RSVP) -- the one set of rules behind the member app's
RSVP buttons (mobile.views.EventDetailView.post) and the native app API
(api.app.routers.events), so the two can't drift apart.

Invitations are the Attendance rows events.services.attendance.sync_event_attendances
creates: an RSVP only ever updates one, never invites itself.
"""

from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from events.models import Attendance
from events.services.lineup import notify_dropout

#: What a member may answer. "dropout" isn't an AttendanceStatus: it's a SELECTED member
#: pulling out of a published line-up, stored as ABSENT (see submit_rsvp).
DROPOUT = "dropout"
ANSWERABLE_STATUSES = (Attendance.AttendanceStatus.PRESENT, Attendance.AttendanceStatus.ABSENT, Attendance.AttendanceStatus.MAYBE, DROPOUT)


class RsvpError(Exception):
    """Why an answer was refused. ``code`` is stable (the app API returns it as the error
    code); the message is translated and safe to show."""

    def __init__(self, code: str, message):
        super().__init__(message)
        self.code = code
        self.message = message


def rsvp_is_closed(event, now=None) -> bool:
    """The reply deadline has passed. A line-up dropout is still allowed after it."""
    now = now or timezone.now()
    return event.deadline is not None and event.deadline < now


def has_started(event, now=None) -> bool:
    return event.start <= (now or timezone.now())


def submit_rsvp(attendance: Attendance, status: str, note: str = "", *, now=None) -> Attendance:
    """Record ``status`` (plus an optional ``note``) on an existing invitation.

    * Nothing changes once the event has started.
    * ``dropout`` is only for a member SELECTED in the published line-up; it is stored as
      ABSENT, needs a reason, skips the deadline and tells the event's managers.
    * Otherwise the reply deadline applies.
    * Absent needs a reason (not just punctuation); Maybe may carry one; Present clears it.
    """
    now = now or timezone.now()
    event = attendance.event
    if status not in ANSWERABLE_STATUSES:
        raise RsvpError("invalid_status", _("Unknown RSVP status."))
    if has_started(event, now):
        raise RsvpError("event_started", _("“%(title)s” has already started, so answers can no longer be changed.") % {"title": event.title})

    is_dropout = status == DROPOUT
    if is_dropout:
        if attendance.status != Attendance.AttendanceStatus.SELECTED:
            raise RsvpError("not_in_lineup", _("You're not in the published line-up for this event."))
    elif rsvp_is_closed(event, now):
        raise RsvpError("rsvp_closed", _("Replies are closed for this event."))

    # A reason only means something next to Out/Maybe -- clearing it on In avoids a stale
    # "sick" note hanging around under an answer it no longer explains. Private by
    # construction: only the member's own family and the team's coaches ever read it.
    note = (note or "").strip()
    if is_dropout or status == Attendance.AttendanceStatus.ABSENT:
        if not any(char.isalnum() for char in note):
            raise RsvpError("note_required", _("Please enter a reason."))
    elif status != Attendance.AttendanceStatus.MAYBE:
        note = ""

    attendance.status = Attendance.AttendanceStatus.ABSENT if is_dropout else status
    attendance.note = note
    attendance.answered_by_coach = False  # the player's own word again
    attendance.save(update_fields=["status", "note", "answered_by_coach", "modified"])
    if is_dropout:
        notify_dropout(event, attendance.member, note)
    return attendance
