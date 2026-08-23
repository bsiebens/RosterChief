"""Notifying members about a newly-created event -- fire-and-forget, dispatched from a
live web request (management.views.EventCreateView.form_valid, mobile.coach_views.
CoachCreateEventView.post), not scheduled. Previously a Celery task; no worker/beat left
to hand it off to (see DEPLOYMENT.md's "Scheduled jobs"), so dispatch_notify_new_event
below runs it on a plain background thread instead -- the request that created the event
returns immediately either way, just without a broker's persistence if the process were to
crash mid-send (acceptable for a one-off, low-stakes notification; see the migration's own
discussion for why this was chosen over a synchronous call).
"""

import threading

from django.db import connections
from django.utils import timezone
from django.utils.translation import gettext as _

from events.models import Attendance, Event
from members.models import Member
from notifications.services import notify_members


def notify_new_event(event_id):
    """Scheduled from management.views.EventCreateView.form_valid -- a staff
    member deliberately planning one new event, not every Event row that
    happens to get created (a recurring series' rolling-horizon extension via
    the extend_event_series command, or a bulk fixture import, would otherwise
    flood everyone with one push per occurrence; those are intentionally not
    wired to this).

    Notifies only whoever is still NO_RESPONSE -- right after creation every
    invited member starts there (events.services.attendance.sync_event_attendances
    just ran via the Event post_save signal), so this is exactly "everyone
    who needs to respond", not the full invited list.
    """
    event = Event.objects.filter(pk=event_id, cancelled=False).select_related("club").first()
    if event is None:
        return "Skipped: event no longer exists or was cancelled."

    member_ids = Attendance.objects.filter(event=event, status=Attendance.AttendanceStatus.NO_RESPONSE).values_list("member_id", flat=True)
    members = Member.objects.filter(id__in=member_ids)
    if not members:
        return "Skipped: no one to notify."

    when = timezone.localtime(event.start).strftime("%a %d %b, %H:%M")
    body = _("New %(kind)s: %(when)s. Let us know if you can make it.") % {"kind": event.get_kind_display(), "when": when}
    notifications = notify_members(members, club=event.club, title=event.title, body=body, source=event)
    return f"Notified {len(notifications)} member(s)."


def dispatch_notify_new_event(event_id):
    """Runs notify_new_event on a daemon background thread so the request that just
    created the event doesn't wait on it. connections.close_all() in the finally is
    load-bearing: a manually-spawned thread doesn't get Django's usual per-request
    connection teardown, so skipping it leaks one DB connection per dispatch."""

    def _run():
        try:
            notify_new_event(event_id)
        finally:
            connections.close_all()

    threading.Thread(target=_run, daemon=True).start()
