"""Registry of the platform jobs Celery Beat runs on a schedule (see
rosterchief/settings.CELERY_BEAT_SCHEDULE).

Keyed on each task's dotted Celery name -- the same string a JobRun row carries in `name`
-- so features/signals.py can tell a tracked platform job apart from any other Celery task
that might get added later without a job to show for it, and so the control panel's Jobs
tab can label a JobRun without importing the task function itself.
"""

from django.utils.translation import gettext_lazy as _

JOB_REGISTRY = {
    "events.tasks.extend_event_series": {
        "label": _("Extend event series"),
        "description": _("Materialises recurring event occurrences up to the rolling horizon, so the calendar never runs dry."),
        "schedule": _("Daily at 03:00"),
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
}
