import os

from celery import Celery
from celery.signals import task_prerun

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "rosterchief.settings")

import django

django.setup()

app = Celery("rosterchief")

app.config_from_object("django.conf:settings", namespace="CELERY")

# Load all task modules from registered Django apps
app.autodiscover_tasks()


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f"Request: {self.request!r}")
