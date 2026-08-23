import datetime

from django.utils import timezone
from django.utils.translation import gettext as _

from events.models import Attendance, Event
from features.commands import ScheduledJobCommand
from members.models import Member
from notifications.services import notify_members

#: How long before an event's answer deadline (or, when it has none, its own
#: start) this nudges whoever still hasn't answered.
DEADLINE_REMINDER_LEAD_TIME = datetime.timedelta(days=7)


class Command(ScheduledJobCommand):
    help = "One reminder push per event, DEADLINE_REMINDER_LEAD_TIME before its answer deadline (or start), to whoever still hasn't answered."
    job_name = "events.tasks.send_deadline_reminders"

    def handle(self, *args, **options):
        """Unlike events/services/notifications.py's notify_new_event (fired once,
        on-demand, from a staff member manually planning a single event), this is the
        periodic sweep that also catches a recurring series' occurrences, which never
        go through that on-creation path at all -- bulk-generating a season's worth of
        practices in one go and notifying everyone about each individually would be
        exactly the flood notify_new_event's own docstring says to avoid. Idempotent
        via Event.deadline_reminder_sent_at: safe to run as often as cron likes without
        double-notifying, and if a run is ever missed, the next one still catches
        anything whose window hasn't fully closed yet."""
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
