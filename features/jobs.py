"""Registry of the platform jobs cron runs on a schedule (see DEPLOYMENT.md's "Scheduled
jobs" for the actual crontab; each entry here corresponds to a features.commands.
ScheduledJobCommand-based management command).

Keyed on an arbitrary but stable string, matching what each job's own ``job_name`` sets
and what a JobRun row carries in `name` -- so the control panel's Jobs tab can label a
JobRun without importing the command itself. These predate the Celery-to-cron migration
and still read as dotted Celery task paths (e.g. "events.tasks.extend_event_series") for
that reason -- purely historical continuity with already-written JobRun rows, not a
claim that a `tasks` module still exists.
"""

from django.utils.translation import gettext_lazy as _

JOB_REGISTRY = {
    "events.tasks.extend_event_series": {
        "label": _("Extend event series"),
        "description": _("Materialises recurring event occurrences up to the rolling horizon, so the calendar never runs dry."),
        "schedule": _("Daily at 03:00"),
    },
    "events.tasks.send_deadline_reminders": {
        "label": _("Send deadline reminders"),
        "description": _("Nudges whoever still hasn't answered an event, one week before its answer deadline (or its start, when no deadline is set)."),
        "schedule": _("Daily at 07:00"),
    },
    "events.tasks.publish_scheduled_lineups": {
        "label": _("Publish scheduled line-ups"),
        "description": _("Publishes any line-up whose coach-picked publish time has arrived."),
        "schedule": _("Every 15 minutes"),
    },
    "billing.tasks.renew_subscriptions": {
        "label": _("Renew subscriptions"),
        "description": _("Opens the next billing period for clubs whose current one is running out."),
        "schedule": _("Daily at 04:00"),
    },
    "billing.tasks.send_billing_reminders": {
        "label": _("Send billing reminders"),
        "description": _("Emails club admins about outstanding platform fees, once per escalation level."),
        "schedule": _("Daily at 05:00"),
    },
    "billing.tasks.archive_overdue_clubs": {
        "label": _("Archive overdue clubs"),
        "description": _("Archives clubs unpaid past their grace period."),
        "schedule": _("Daily at 06:00"),
    },
    "club.tasks.generate_seasons": {
        "label": _("Generate seasons"),
        "description": _("Generates the next two years of season rows for every active club, so signups and rosters never hit a missing season."),
        "schedule": _("Monthly, 1st at 05:00"),
    },
    "news.tasks.notify_news_published": {
        "label": _("Notify published news"),
        "description": _("Sends the notification for any news item whose publish time has arrived (including one scheduled ahead of time) and hasn't been notified yet."),
        "schedule": _("Every 15 minutes"),
    },
}
