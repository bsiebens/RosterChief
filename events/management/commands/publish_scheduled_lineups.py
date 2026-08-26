from django.utils import timezone

from events.models import Lineup
from events.services.lineup import publish_lineup
from features.commands import ScheduledJobCommand


class Command(ScheduledJobCommand):
    help = "Publish any line-up whose coach-picked scheduled_publish_at has arrived."
    job_name = "events.tasks.publish_scheduled_lineups"

    def handle(self, *args, **options):
        """The periodic sweep behind a coach's "schedule for later" option on the
        Publish action (mobile/coach_views.py's CoachLineupPublishView, events.
        services.lineup.schedule_lineup_publish) -- catches any line-up whose
        scheduled_publish_at has arrived and actually publishes it. Runs frequently
        (see DEPLOYMENT.md's "Scheduled jobs"), unlike this app's other daily jobs,
        since a schedule set for a specific time should take effect close to it, not
        up to a day late."""
        due = Lineup.objects.filter(published_at__isnull=True, scheduled_publish_at__isnull=False, scheduled_publish_at__lte=timezone.now()).iterator(chunk_size=200)
        count = 0
        for lineup in due:
            publish_lineup(lineup)
            count += 1

        return f"Published {count} scheduled line-up(s)."
