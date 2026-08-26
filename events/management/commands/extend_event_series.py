from events.models import EventSeries
from events.services import generate_occurrences, horizon
from features.commands import ScheduledJobCommand


class Command(ScheduledJobCommand):
    help = "Materialise recurring event occurrences up to the rolling horizon."
    job_name = "events.tasks.extend_event_series"

    def handle(self, *args, **options):
        until = horizon()
        total = 0
        series_count = 0

        # .iterator(): this runs across every club's every series in one
        # sweep -- without it, Django caches the whole queryset in memory
        # before the loop even starts, which grows with the platform, not
        # with any one run's actual work.
        for series in EventSeries.objects.iterator(chunk_size=200):
            series_count += 1
            created = generate_occurrences(series, until)
            total += len(created)
            if created:
                self.stdout.write(f"{series}: generated {len(created)} occurrence(s).")

        return f"Generated {total} occurrence(s) across {series_count} series."
