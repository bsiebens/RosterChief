"""Celery tasks for events -- the `extend-event-series` beat schedule entry
(see rosterchief/settings.CELERY_BEAT_SCHEDULE and features/jobs.py), and
notifying members when a new event needs their RSVP.
"""

from celery import shared_task
from django.utils import timezone
from django.utils.translation import gettext as _

from events.models import Attendance, Event, EventSeries
from events.services import generate_occurrences, horizon
from features.models import Maintenance
from members.models import Member
from notifications.services import notify_members


@shared_task(name="events.tasks.extend_event_series")
def extend_event_series():
    if Maintenance.is_on():
        # Loud, not silent: a job that quietly skips itself while the platform is closed is
        # how a rolling horizon quietly runs dry. Raising here is what turns it into a
        # Failure on the control panel's Jobs tab instead of nothing happening at all.
        raise RuntimeError("Platform is in maintenance mode; this job stood down.")

    until = horizon()
    total = 0
    for series in EventSeries.objects.all():
        total += len(generate_occurrences(series, until))

    return f"Generated {total} occurrence(s) across {EventSeries.objects.count()} series."


@shared_task(name="events.tasks.notify_new_event")
def notify_new_event(event_id):
    """Scheduled from management.views.EventCreateView.form_valid -- a staff
    member deliberately planning one new event, not every Event row that
    happens to get created (a recurring series' rolling-horizon extension
    via extend_event_series above, or a bulk fixture import, would otherwise
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
