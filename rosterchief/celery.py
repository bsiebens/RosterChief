"""Celery application (tenant-aware: tasks remember the schema they were queued from).

Platform tasks (billing, statistics) work on shared tables and run in the public schema; tasks queued from inside a
club schema switch back to that schema when they run.
"""

import os

from tenant_schemas_celery.app import CeleryApp

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "rosterchief.settings")

app = CeleryApp("rosterchief")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
