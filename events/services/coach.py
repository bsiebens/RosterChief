"""Rules the coach screens share between the member web app (mobile.coach_views) and the
app API (api.protected.routers.coach): which RSVP states count as in/out, which event is "the next
session", and the on-demand nudge to silent players."""

import datetime

from django.db.models import Case, F, When
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext as _

from events.models import Attendance, Event
from members.models import Member
from notifications.models import NotificationCategory
from notifications.services import notify_members

#: RSVP states that count as "in" for the stat tile -- present/selected are an
#: explicit yes, maybe is still a lean-in rather than silence.
IN_STATUSES = [Attendance.AttendanceStatus.PRESENT, Attendance.AttendanceStatus.SELECTED, Attendance.AttendanceStatus.MAYBE]

#: An explicit no -- declined or, for a published line-up, not selected.
#: Distinct from NO_RESPONSE ("silent"), which is a non-answer rather than a no.
OUT_STATUSES = [Attendance.AttendanceStatus.ABSENT, Attendance.AttendanceStatus.EXCUSED, Attendance.AttendanceStatus.NOT_SELECTED]

#: The event kinds the coach event forms offer -- social/other stay desktop-only.
COACH_EVENT_KINDS = [Event.EventKind.TRAINING, Event.EventKind.GAME, Event.EventKind.TOURNAMENT, Event.EventKind.MEETING]

#: Event kinds a line-up applies to -- a Tournament is a squad-selection event
#: exactly like a Game (who's travelling/dressed), unlike Training/Meeting.
LINEUP_EVENT_KINDS = [Event.EventKind.GAME, Event.EventKind.TOURNAMENT]

#: How long after an event's end (or assumed end) it still counts as "the current
#: session": past the end by this much it drops off the Today tile.
STILL_CURRENT_GRACE = datetime.timedelta(minutes=30)
#: Most training events carry no end time (see Event.end's own help text).
STILL_CURRENT_ASSUMED_DURATION = datetime.timedelta(minutes=90)


def still_current_events(team):
    """Events for ``team`` that haven't yet reached their "still current" cutoff."""
    cutoff = Case(
        When(end__isnull=False, then=F("end") + STILL_CURRENT_GRACE),
        default=F("start") + STILL_CURRENT_ASSUMED_DURATION,
    )
    return Event.objects.filter(teams=team, cancelled=False).annotate(still_current_cutoff=cutoff).filter(still_current_cutoff__gte=timezone.now()).order_by("start")


def remind_silent(event) -> int:
    """Nudge every player who hasn't answered for ``event`` (the same text as the daily
    deadline reminder, so nobody gets two differently worded pushes). Returns how many were
    notified."""
    member_ids = Attendance.objects.filter(event=event, status=Attendance.AttendanceStatus.NO_RESPONSE).values_list("member_id", flat=True)
    members = list(Member.objects.filter(id__in=member_ids))
    if not members:
        return 0

    def body():
        when = date_format(timezone.localtime(event.start), "D d M, H:i")
        return _("Reminder: %(kind)s on %(when)s still needs your answer.") % {"kind": event.get_kind_display(), "when": when}

    notify_members(members, club=event.club, title=event.title, body=body, source=event, category=NotificationCategory.COACH)
    return len(members)


def notify_event_changed(event) -> int:
    """Tell everybody invited to ``event`` that it changed (a coach asked for it). Returns how many
    were notified."""
    members = list(Member.objects.filter(attendances__event=event))
    if not members:
        return 0

    def body():
        when = date_format(timezone.localtime(event.start), "D d M, H:i")
        return _("%(kind)s changed: it is now on %(when)s.") % {"kind": event.get_kind_display(), "when": when}

    return len(
        notify_members(
            members, club=event.club, title=event.title, body=body, source=event, category=NotificationCategory.EVENT, priority="critical" if datetime.timedelta(0) <= event.start - timezone.now() <= datetime.timedelta(hours=24) else "normal"
        )
    )


def notify_event_cancelled(event, *, keep_link: bool) -> int:
    """Tell everybody invited to ``event`` that it is cancelled. When the event is about to be deleted
    (``keep_link`` false) the notification points at the calendar instead of at it."""
    members = list(Member.objects.filter(attendances__event=event))
    if not members:
        return 0

    def body():
        when = date_format(timezone.localtime(event.start), "D d M, H:i")
        return _("%(kind)s on %(when)s has been cancelled.") % {"kind": event.get_kind_display(), "when": when}

    title = event.title
    if keep_link:
        return len(notify_members(members, club=event.club, title=title, body=body, source=event, category=NotificationCategory.EVENT, priority="critical"))
    return len(notify_members(members, club=event.club, title=title, body=body, target_type="calendar", category=NotificationCategory.EVENT, priority="critical"))
