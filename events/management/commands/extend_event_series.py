from events.models import EventSeries
from events.services import generate_occurrences, horizon
from features.commands import ScheduledJobCommand


class Command(ScheduledJobCommand):
    help = "Materialise recurring event occurrences up to the rolling horizon."
    job_name = "events.tasks.extend_event_series"

    def handle(self, *args, **options):
        until = horizon()
        total = 0

        for series in EventSeries.objects.all():
            created = generate_occurrences(series, until)
            total += len(created)
            if created:
                self.stdout.write(f"{series}: generated {len(created)} occurrence(s).")

        return f"Generated {total} occurrence(s) across {EventSeries.objects.count()} series."
