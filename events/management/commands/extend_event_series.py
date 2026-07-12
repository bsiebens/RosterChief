from django.core.management.base import BaseCommand

from events.models import EventSeries
from events.services import generate_occurrences, horizon


class Command(BaseCommand):
    help = "Materialise recurring event occurrences up to the rolling horizon."

    def handle(self, *args, **options):
        until = horizon()
        total = 0

        for series in EventSeries.objects.all():
            created = generate_occurrences(series, until)
            total += len(created)
            if created:
                self.stdout.write(f"{series}: generated {len(created)} occurrence(s).")

        self.stdout.write(self.style.SUCCESS(f"Done. Generated {total} occurrence(s) across {EventSeries.objects.count()} series."))
