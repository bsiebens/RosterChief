"""Celery application (tenant-aware: tasks remember the schema they were queued from).

Platform tasks (billing, statistics) work on shared tables and run in the public schema; tasks queued from inside a
club schema switch back to that schema when they run.
"""

import os

from celery.schedules import crontab
from tenant_schemas_celery.app import CeleryApp

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "rosterchief.settings")

app = CeleryApp("rosterchief")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

# Periodic tasks. These are *defaults*: beat creates a database row (django-celery-beat) for each one that does not
# exist yet and never overwrites an existing row, so the time and the on/off switch can be edited in the database
# (Django admin, "Periodic tasks") and survive restarts. See `manage.py beat_schedule` to compare the database with
# these defaults and to reset an entry to them.
app.conf.beat_schedule = {
    "billing-renew-subscriptions": {"task": "billing.renew_subscriptions", "schedule": crontab(hour=4, minute=0)},
    "billing-send-reminders": {"task": "billing.send_billing_reminders", "schedule": crontab(hour=5, minute=0)},
    "billing-downgrade-overdue": {"task": "billing.downgrade_overdue_clubs", "schedule": crontab(hour=5, minute=30)},
    "controlpanel-hello-world": {"task": "controlpanel.hello_world", "schedule": crontab()},  # every minute; dummy, to see the job log work
    "notifications-process-outbox": {"task": "notifications.process_outbox", "schedule": crontab()},  # every minute
    "notifications-retry-deliveries": {"task": "notifications.retry_failed_deliveries", "schedule": crontab()},  # every minute
    "notifications-check-health": {"task": "notifications.check_push_health", "schedule": crontab(minute="*/5")},
    "notifications-cleanup": {"task": "notifications.cleanup", "schedule": crontab(hour=3, minute=30)},
    # Mondays 02:00.
    "events-create-missing-seasons": {"task": "events.create_missing_seasons", "schedule": crontab(hour=2, minute=0, day_of_week=1)},
    "events-publish-scheduled-lineups": {"task": "events.publish_scheduled_lineups", "schedule": crontab()},  # every minute
    "news-notify-due-news": {"task": "news.notify_due_news", "schedule": crontab()},  # every minute
    "formbuilder-notify-due-sends": {"task": "formbuilder.notify_due_sends", "schedule": crontab()},  # every minute: scheduled sends opening
    "formbuilder-send-form-reminders": {"task": "formbuilder.send_form_reminders", "schedule": crontab(minute=10)},  # hourly
    "events-extend-event-series": {"task": "events.extend_event_series", "schedule": crontab(hour=1, minute=30)},
    "events-poll-live-games": {"task": "events.poll_live_games", "schedule": crontab()},  # every minute
    "events-settle-game-timelines": {"task": "events.settle_game_timelines", "schedule": crontab(hour=1, minute=45)},
    "events-refresh-fixtures": {"task": "events.refresh_fixtures", "schedule": crontab(hour=6, minute=15, day_of_week=1)},  # Mondays
    "events-refresh-standings": {"task": "events.refresh_standings", "schedule": crontab(hour=4, minute=30)},
    "billing-archive-overdue": {"task": "billing.archive_overdue_clubs", "schedule": crontab(hour=6, minute=0)},
}
