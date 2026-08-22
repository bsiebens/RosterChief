"""Celery tasks for events -- the `extend-event-series` beat schedule entry
(see rosterchief/settings.CELERY_BEAT_SCHEDULE and features/jobs.py), and
notifying members when a new event needs their RSVP.
"""

import datetime

from celery import shared_task
from django.utils import timezone
from django.utils.translation import gettext as _

from events.models import Attendance, Event, EventSeries, Lineup
from events.services import generate_occurrences, horizon
from events.services.lineup import publish_lineup
from features.models import Maintenance
from members.models import Member
from notifications.services import notify_members

#: How long before an event's answer deadline (or, when it has none, its own
#: start) send_deadline_reminders below nudges whoever still hasn't answered.
DEADLINE_REMINDER_LEAD_TIME = datetime.timedelta(days=7)


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


@shared_task(name="events.tasks.send_deadline_reminders")
def send_deadline_reminders():
    """One reminder push per event, DEADLINE_REMINDER_LEAD_TIME before whichever
    cutoff matters -- the event's own answer deadline, or its start when no
    deadline is set -- to whoever still hasn't answered.

    Unlike notify_new_event above (fired once, on-demand, from a staff
    member manually planning a single event), this is the periodic sweep
    that also catches a recurring series' occurrences, which never go
    through that on-creation path at all -- bulk-generating a season's
    worth of practices in one go and notifying everyone about each
    individually would be exactly the flood notify_new_event's own
    docstring says to avoid. Idempotent via Event.deadline_reminder_sent_at:
    safe to run as often as CELERY_BEAT_SCHEDULE likes without
    double-notifying, and if a run is ever missed, the next one still
    catches anything whose window hasn't fully closed yet.
    """
    if Maintenance.is_on():
        raise RuntimeError("Platform is in maintenance mode; this job stood down.")

    now = timezone.now()
    events_reminded = 0
    members_notified = 0

    candidates = Event.objects.filter(cancelled=False, deadline_reminder_sent_at__isnull=True, start__gt=now).select_related("club")
    for event in candidates:
        cutoff = event.deadline or event.start
        reminder_at = cutoff - DEADLINE_REMINDER_LEAD_TIME
        if not (reminder_at <= now < cutoff):
            continue

        member_ids = Attendance.objects.filter(event=event, status=Attendance.AttendanceStatus.NO_RESPONSE).values_list("member_id", flat=True)
        members = Member.objects.filter(id__in=member_ids)
        if members:
            when = timezone.localtime(event.start).strftime("%a %d %b, %H:%M")
            body = _("Reminder: %(kind)s on %(when)s still needs your answer.") % {"kind": event.get_kind_display(), "when": when}
            notify_members(members, club=event.club, title=event.title, body=body, source=event)
            members_notified += len(members)

        # Marked processed even when nobody was NO_RESPONSE at the time -- the
        # window only opens once per event, not "keep checking until someone
        # answers" (that would just mean it fires the moment they stop being
        # NO_RESPONSE for an unrelated reason, e.g. answering after the window).
        event.deadline_reminder_sent_at = now
        event.save(update_fields=["deadline_reminder_sent_at", "modified"])
        events_reminded += 1

    return f"Reminded {members_notified} member(s) across {events_reminded} event(s)."


@shared_task(name="events.tasks.publish_scheduled_lineups")
def publish_scheduled_lineups():
    """The periodic sweep behind a coach's "schedule for later" option on the
    Publish action (mobile/coach_views.py's CoachLineupPublishView, events.
    services.lineup.schedule_lineup_publish) -- catches any line-up whose
    scheduled_publish_at has arrived and actually publishes it. Runs
    frequently (see CELERY_BEAT_SCHEDULE), unlike this module's other daily
    jobs, since a schedule set for a specific time should take effect close
    to it, not up to a day late."""
    if Maintenance.is_on():
        raise RuntimeError("Platform is in maintenance mode; this job stood down.")

    due = Lineup.objects.filter(published_at__isnull=True, scheduled_publish_at__isnull=False, scheduled_publish_at__lte=timezone.now())
    count = 0
    for lineup in due:
        publish_lineup(lineup)
        count += 1

    return f"Published {count} scheduled line-up(s)."
