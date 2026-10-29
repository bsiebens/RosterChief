"""Registry of the platform jobs that Celery runs on a schedule (see DEPLOYMENT.md's "Scheduled
jobs"). Each entry is one features.commands.ScheduledJobCommand-based management command, run by the
Celery task of the same name in that app's ``tasks.py``.

What this registry is, and is not: it says what a job *is* (label, description, command, task name) and
what its **default** schedule is. The schedule that actually runs lives in the database
(django_celery_beat's PeriodicTask), is created from ``default_schedule`` by ``manage.py install_tasks``
(never overwriting an existing one) and is edited from the control panel's Jobs tab. Results live in
django_celery_results' TaskResult.

Keyed on an arbitrary but stable string, matching what each job's own ``job_name`` sets and what a
JobRun row carries in `name`. These predate Celery and still read as dotted paths (e.g.
"events.tasks.extend_event_series") for historical continuity with already-written JobRun rows.

``command``/``args``: the manage.py command the Celery task runs, and the arguments it passes --
deliberately NOT derived from the registry key (the guess is wrong for notify_news_published, whose
command is notify_published_news). The two billing jobs that default to a dry run need --commit to
actually act, so it is in their ``args``.

``task``: the Celery task name (``@shared_task(name=...)``), the join key to PeriodicTask.task and
TaskResult.task_name. ``default_schedule``: ``{"interval": {"every": n, "period": "minutes"}}`` or
``{"crontab": {...}}`` (cron fields as django_celery_beat names them; unset fields mean ``*``), in the
server's configured timezone.
"""

from django.utils.translation import gettext_lazy as _

JOB_REGISTRY = {
    "events.tasks.extend_event_series": {
        "label": _("Extend event series"),
        "description": _("Materialises recurring event occurrences up to the rolling horizon, so the calendar never runs dry."),
        "command": "extend_event_series",
        "task": "events.extend-event-series",
        "default_schedule": {"crontab": {"hour": "3", "minute": "0"}},
        "args": [],
    },
    "events.tasks.send_deadline_reminders": {
        "label": _("Send deadline reminders"),
        "description": _("Nudges whoever still hasn't answered an event, one week before its answer deadline (or its start, when no deadline is set)."),
        "command": "send_deadline_reminders",
        "task": "events.send-deadline-reminders",
        "default_schedule": {"crontab": {"hour": "7", "minute": "0"}},
        "args": [],
    },
    "events.tasks.publish_scheduled_lineups": {
        "label": _("Publish scheduled line-ups"),
        "description": _("Publishes any line-up whose coach-picked publish time has arrived."),
        "command": "publish_scheduled_lineups",
        "task": "events.publish-scheduled-lineups",
        "default_schedule": {"interval": {"every": 15, "period": "minutes"}},
        "args": [],
    },
    "events.tasks.sync_crew_invites": {
        "label": _("Sync referee and official invites"),
        "description": _("Invites everyone currently eligible to the upcoming home games that still need a referee or official -- a safety net for eligibility that changed after a game was scheduled."),
        "command": "sync_crew_invites",
        "task": "events.sync-crew-invites",
        "default_schedule": {"interval": {"every": 60, "period": "minutes"}},
        "args": [],
    },
    "events.tasks.poll_live_game_results": {
        "label": _("Poll live game results"),
        "description": _("Checks each game due to start soon or still within its post-game window for a fresh score/live status from its competition's data source."),
        "command": "poll_live_game_results",
        "task": "events.poll-live-game-results",
        "default_schedule": {"interval": {"every": 1, "period": "minutes"}},
        "args": [],
    },
    "events.tasks.check_fixture_sources": {
        "label": _("Check fixture sources"),
        "description": _("Re-checks every team's stored federation fixture lists for the current season and notifies club admins of moved, new or removed games, or a list that could not be fetched."),
        "command": "check_fixture_sources",
        "task": "events.check-fixture-sources",
        "default_schedule": {"crontab": {"day_of_week": "1", "hour": "6", "minute": "15"}},
        "args": [],
    },
    "events.tasks.refresh_standings": {
        "label": _("Refresh league standings"),
        "description": _("Recomputes the league standings of every team imported from RBIHF for the current season, for the public API. A team's standings are also refreshed as soon as one of its games finishes."),
        "command": "refresh_standings",
        "task": "events.refresh-standings",
        "default_schedule": {"crontab": {"hour": "1", "minute": "0"}},
        "args": [],
    },
    "events.tasks.settle_game_timelines": {
        "label": _("Settle game timelines"),
        "description": _("Re-fetches the timelines (goals, penalties, ...) of games that finished in the last week until they stop changing, since federations correct them after the final whistle. Only for clubs with game timelines switched on."),
        "command": "settle_game_timelines",
        "task": "events.settle-game-timelines",
        "default_schedule": {"crontab": {"hour": "1", "minute": "30"}},
        "args": [],
    },
    "billing.tasks.renew_subscriptions": {
        "label": _("Renew subscriptions"),
        "description": _("Opens the next billing period for clubs whose current one is running out."),
        "command": "renew_subscriptions",
        "task": "billing.renew-subscriptions",
        "default_schedule": {"crontab": {"hour": "4", "minute": "0"}},
        "args": [],
    },
    "billing.tasks.send_billing_reminders": {
        "label": _("Send billing reminders"),
        "description": _("Emails club admins about outstanding platform fees, once per escalation level."),
        "command": "send_billing_reminders",
        "task": "billing.send-billing-reminders",
        "default_schedule": {"crontab": {"hour": "5", "minute": "0"}},
        "args": ["--commit"],
    },
    "billing.tasks.archive_overdue_clubs": {
        "label": _("Archive overdue clubs"),
        "description": _("Archives clubs unpaid past their grace period."),
        "command": "archive_overdue_clubs",
        "task": "billing.archive-overdue-clubs",
        "default_schedule": {"crontab": {"hour": "6", "minute": "0"}},
        "args": ["--commit"],
    },
    "club.tasks.generate_seasons": {
        "label": _("Generate seasons"),
        "description": _("Generates the next two years of season rows for every active club, so signups and rosters never hit a missing season."),
        "command": "generate_seasons",
        "task": "club.generate-seasons",
        "default_schedule": {"crontab": {"day_of_month": "1", "hour": "5", "minute": "0"}},
        "args": [],
    },
    "news.tasks.notify_news_published": {
        "label": _("Notify published news"),
        "description": _("Sends the notification for any news item whose publish time has arrived (including one scheduled ahead of time) and hasn't been notified yet."),
        "command": "notify_published_news",
        "task": "news.notify-published-news",
        "default_schedule": {"interval": {"every": 15, "period": "minutes"}},
        "args": [],
    },
    "announcements.tasks.publish_scheduled_announcements": {
        "label": _("Publish scheduled announcements"),
        "description": _("Pushes any platform announcement whose scheduled time has arrived."),
        "command": "publish_scheduled_announcements",
        "task": "announcements.publish-scheduled-announcements",
        "default_schedule": {"interval": {"every": 5, "period": "minutes"}},
        "args": [],
    },
    "formbuilder.tasks.send_form_reminders": {
        "label": _("Send form reminders"),
        "description": _("Nudges whoever hasn't submitted a form send yet, a few days before it closes."),
        "command": "send_form_reminders",
        "task": "formbuilder.send-form-reminders",
        "default_schedule": {"crontab": {"hour": "7", "minute": "30"}},
        "args": [],
    },
    "notifications.tasks.retry_failed_deliveries": {
        "label": _("Retry failed deliveries"),
        "description": _("Sends push notifications and emails again that failed for a transient reason (throttling, a server or network error), with a growing delay, and purges old delivery-log rows."),
        "command": "retry_failed_deliveries",
        "task": "notifications.retry-failed-deliveries",
        "default_schedule": {"interval": {"every": 2, "period": "minutes"}},
        "args": [],
    },
    "notifications.tasks.process_outbox": {
        "label": _("Process the notification outbox"),
        "description": _("Delivers notifications and live game pushes that a restart or crash interrupted, retrying with a growing delay, and purges old outbox entries."),
        "command": "process_outbox",
        "task": "notifications.process-outbox",
        "default_schedule": {"interval": {"every": 1, "period": "minutes"}},
        "args": [],
    },
    "notifications.tasks.check_push_health": {
        "label": _("Check push health"),
        "description": _("Emails the platform admins when a push provider is down or degraded, the retry queue or the outbox is stuck, or a provider is not configured -- once per outage, with a reminder every 6 hours and a recovery notice."),
        "command": "check_push_health",
        "task": "notifications.check-push-health",
        "default_schedule": {"interval": {"every": 5, "period": "minutes"}},
        "args": [],
    },
}
