"""Celery application for RosterChief's scheduled platform jobs.

Replaces the host crontab described in DEPLOYMENT.md's old "Scheduled jobs" section: the
same Redis instance django-redis already uses for caching (``DJANGO_REDIS_URL``) doubles as
the broker and result backend, so there is nothing new to deploy except the `worker` and
`beat` processes themselves (see compose.yaml) -- no RabbitMQ, no separate broker to run,
monitor or back up.

``autodiscover_tasks()`` with no arguments relies on Celery's Django integration (active
because DJANGO_SETTINGS_MODULE is set below): it walks INSTALLED_APPS and imports each
app's ``tasks.py`` if present -- see billing/tasks.py, club/tasks.py, events/tasks.py.
"""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "rosterchief.settings")

app = Celery("rosterchief")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
