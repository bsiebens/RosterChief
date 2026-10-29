import json

from django.core.management import BaseCommand
from django.utils import translation
from django_celery_beat.models import PeriodicTask

from features.jobs import JOB_REGISTRY
from features.scheduling import schedule_for


class Command(BaseCommand):
    help = "Installs the default Celery Beat schedule for every job in features.jobs.JOB_REGISTRY, without touching schedules that already exist"

    def handle(self, *args, **kwargs):
        for meta in JOB_REGISTRY.values():
            # Matched on the task, not the name: the schedule is editable (and the name could have been
            # renamed) in the control panel, and one task must never end up scheduled twice.
            if PeriodicTask.objects.filter(task=meta["task"]).exists():
                continue

            field, schedule = schedule_for(meta["default_schedule"])
            with translation.override("en"):
                name = str(meta["label"])
            PeriodicTask.objects.create(name=name, task=meta["task"], args=json.dumps([]), kwargs=json.dumps({}), **{field: schedule})
            self.stdout.write(self.style.SUCCESS(f"Scheduled: {name} ({schedule})"))
