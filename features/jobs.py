"""Registry of the platform jobs the in-process scheduler runs on a schedule (see
features/scheduler.py, and DEPLOYMENT.md's "Scheduled jobs" for the operational picture;
each entry here corresponds to a features.commands.ScheduledJobCommand-based management
command). This is the single source of truth for *when* a job runs -- there is no longer a
separate crontab to keep in sync with it.

Keyed on an arbitrary but stable string, matching what each job's own ``job_name`` sets
and what a JobRun row carries in `name` -- so the control panel's Jobs tab can label a
JobRun without importing the command itself. These predate the Celery-to-cron-to-in-process
migrations and still read as dotted Celery task paths (e.g. "events.tasks.extend_event_series")
for that reason -- purely historical continuity with already-written JobRun rows, not a
claim that a `tasks` module still exists.

``command``/``args``: the control panel's own "Run now" button (controlpanel.views.
JobRunNowView) needs the actual manage.py command name to invoke -- deliberately NOT
derived from the registry key itself (e.g. by taking its last dotted segment), because
that guess is wrong for notify_news_published (command is notify_published_news, word
order doesn't match its own historical key -- see above). ``args`` mirrors what the
scheduler passes on its own scheduled tick, so a manual run does precisely what a
scheduled one would (the two billing jobs that default to a dry run and need --commit to
actually act, included).

``schedule``: a human-readable label for the control panel's Jobs tab -- translated, so
NOT machine-parseable. ``trigger``: the machine-readable counterpart features.scheduler
actually schedules from -- kwargs for APScheduler's ``add_job(trigger=..., **kwargs)``,
either ``{"trigger": "cron", ...}`` (APScheduler's own CronTrigger kwargs: hour/minute/day/
day_of_week, all in the server's configured timezone) or ``{"trigger": "interval", ...}``
for a fixed-cadence job like the once-a-minute live-score poll. Keep ``schedule`` and
``trigger`` in sync by hand when either changes -- there is no cross-check that the label
still describes the trigger, the same way there was none between the label and the old
crontab entry.
"""

from django.utils.translation import gettext_lazy as _

JOB_REGISTRY = {
    "events.tasks.extend_event_series": {
        "label": _("Extend event series"),
        "description": _("Materialises recurring event occurrences up to the rolling horizon, so the calendar never runs dry."),
        "schedule": _("Daily at 03:00"),
        "trigger": {"trigger": "cron", "hour": 3, "minute": 0},
        "command": "extend_event_series",
        "args": [],
    },
    "events.tasks.send_deadline_reminders": {
        "label": _("Send deadline reminders"),
        "description": _("Nudges whoever still hasn't answered an event, one week before its answer deadline (or its start, when no deadline is set)."),
        "schedule": _("Daily at 07:00"),
        "trigger": {"trigger": "cron", "hour": 7, "minute": 0},
        "command": "send_deadline_reminders",
        "args": [],
    },
    "events.tasks.publish_scheduled_lineups": {
        "label": _("Publish scheduled line-ups"),
        "description": _("Publishes any line-up whose coach-picked publish time has arrived."),
        "schedule": _("Every 15 minutes"),
        "trigger": {"trigger": "interval", "minutes": 15},
        "command": "publish_scheduled_lineups",
        "args": [],
    },
    "events.tasks.poll_live_game_results": {
        "label": _("Poll live game results"),
        "description": _("Checks each game due to start soon or still within its post-game window for a fresh score/live status from its competition's data source."),
        "schedule": _("Every minute"),
        "trigger": {"trigger": "interval", "seconds": 60},
        "command": "poll_live_game_results",
        "args": [],
    },
    "billing.tasks.renew_subscriptions": {
        "label": _("Renew subscriptions"),
        "description": _("Opens the next billing period for clubs whose current one is running out."),
        "schedule": _("Daily at 04:00"),
        "trigger": {"trigger": "cron", "hour": 4, "minute": 0},
        "command": "renew_subscriptions",
        "args": [],
    },
    "billing.tasks.send_billing_reminders": {
        "label": _("Send billing reminders"),
        "description": _("Emails club admins about outstanding platform fees, once per escalation level."),
        "schedule": _("Daily at 05:00"),
        "trigger": {"trigger": "cron", "hour": 5, "minute": 0},
        "command": "send_billing_reminders",
        "args": ["--commit"],
    },
    "billing.tasks.archive_overdue_clubs": {
        "label": _("Archive overdue clubs"),
        "description": _("Archives clubs unpaid past their grace period."),
        "schedule": _("Daily at 06:00"),
        "trigger": {"trigger": "cron", "hour": 6, "minute": 0},
        "command": "archive_overdue_clubs",
        "args": ["--commit"],
    },
    "club.tasks.generate_seasons": {
        "label": _("Generate seasons"),
        "description": _("Generates the next two years of season rows for every active club, so signups and rosters never hit a missing season."),
        "schedule": _("Monthly, 1st at 05:00"),
        "trigger": {"trigger": "cron", "day": 1, "hour": 5, "minute": 0},
        "command": "generate_seasons",
        "args": [],
    },
    "news.tasks.notify_news_published": {
        "label": _("Notify published news"),
        "description": _("Sends the notification for any news item whose publish time has arrived (including one scheduled ahead of time) and hasn't been notified yet."),
        "schedule": _("Every 15 minutes"),
        "trigger": {"trigger": "interval", "minutes": 15},
        "command": "notify_published_news",
        "args": [],
    },
    "announcements.tasks.publish_scheduled_announcements": {
        "label": _("Publish scheduled announcements"),
        "description": _("Pushes any platform announcement whose scheduled time has arrived."),
        "schedule": _("Every 5 minutes"),
        "trigger": {"trigger": "interval", "minutes": 5},
        "command": "publish_scheduled_announcements",
        "args": [],
    },
    "formbuilder.tasks.send_form_reminders": {
        "label": _("Send form reminders"),
        "description": _("Nudges whoever hasn't submitted a form send yet, a few days before it closes."),
        "schedule": _("Daily at 07:30"),
        "trigger": {"trigger": "cron", "hour": 7, "minute": 30},
        "command": "send_form_reminders",
        "args": [],
    },
}
