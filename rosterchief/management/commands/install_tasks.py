import json

from django.core.management import BaseCommand
from django_celery_beat.models import IntervalSchedule, PeriodicTask


class Command(BaseCommand):
    help = "Installs default Celery Beat tasks without overwriting existing schedules"

    def handle(self, *args, **kwargs):
        every_5_minutes, created = IntervalSchedule.objects.get_or_create(every=5, period=IntervalSchedule.MINUTES)

        if created:
            self.stdout.write(self.style.SUCCESS("Created 5-minute interval schedule"))

        tasks = [
            {"name": "Publish scheduled announcements", "path": "announcements.publish-scheduled-announcements", "interval": every_5_minutes},
        ]

        for task in tasks:
            periodic_task, created = PeriodicTask.objects.get_or_create(
                name=task["name"],
                task=task["path"],
                defaults={
                    "interval": task["interval"],
                    "args": json.dumps([]),
                    "kwargs": json.dumps([]),
                },
            )

            if created:
                self.stdout.write(self.style.SUCCESS(f"Successfully installed task: '{task['name']}'"))
            else:
                self.stdout.write(self.style.WARNING(f"Task '{task['name']}' already exists ... skipping installation to prevent overwriting"))
