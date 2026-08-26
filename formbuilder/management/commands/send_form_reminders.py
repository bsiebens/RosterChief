import datetime

from django.utils import timezone
from django.utils.translation import gettext as _

from features.commands import ScheduledJobCommand
from formbuilder.models import FormSend
from formbuilder.services.audience import members_not_yet_submitted
from notifications.services import notify_members

#: How long before a send's closes_at this nudges whoever still hasn't
#: submitted -- shorter than events' own 7-day DEADLINE_REMINDER_LEAD_TIME:
#: forms tend to be asked closer to their deadline than a scheduling
#: decision is, so a week's notice is usually moot by the time one goes out.
FORM_REMINDER_LEAD_TIME = datetime.timedelta(days=3)


class Command(ScheduledJobCommand):
    help = "One reminder push per FormSend, FORM_REMINDER_LEAD_TIME before it closes, to whoever still hasn't submitted."
    job_name = "formbuilder.tasks.send_form_reminders"

    def handle(self, *args, **options):
        """Same shape as events.management.commands.send_deadline_reminders:
        idempotent via FormSend.reminder_sent_at (safe to run as often as
        cron likes without double-notifying), and the reminder window itself
        (closes_at - LEAD_TIME <= now < closes_at) is pushed into the query
        rather than fetched-then-filtered in Python, for the same reason --
        "not yet reminded, still open" can be a large set across a whole
        platform; "actually inside its lead-time window right now" isn't.

        A send with no closes_at is never picked up here at all -- there's
        no deadline to count backwards from, so "3 days before never" isn't
        a real window to reason about."""
        now = timezone.now()
        sends_reminded = 0
        members_notified = 0

        candidates = (
            FormSend.objects.filter(is_active=True, reminder_sent_at__isnull=True, closes_at__isnull=False)
            .filter(closes_at__gt=now, closes_at__lte=now + FORM_REMINDER_LEAD_TIME)
            .select_related("club", "form")
            .iterator(chunk_size=200)
        )
        for send in candidates:
            members = members_not_yet_submitted(send)
            if members:
                body = _("Reminder: “%(form)s” still needs your response.") % {"form": send.form.title}
                notify_members(members, club=send.club, title=send.form.title, body=body, source=send)
                members_notified += len(members)

            # Marked processed even when nobody still needed a nudge -- same
            # "the window only opens once" reasoning as send_deadline_reminders.
            send.reminder_sent_at = now
            send.save(update_fields=["reminder_sent_at", "modified"])
            sends_reminded += 1

        return f"Reminded {members_notified} member(s) across {sends_reminded} send(s)."
