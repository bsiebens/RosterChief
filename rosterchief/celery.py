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
    "controlpanel-hello-world": {"task": "controlpanel.hello_world", "schedule": crontab()},  # every minute; dummy, to see the job log work
    "billing-archive-overdue": {"task": "billing.archive_overdue_clubs", "schedule": crontab(hour=6, minute=0)},
}
