"""Celery task behind the `extend-event-series` beat schedule entry (see
rosterchief/settings.CELERY_BEAT_SCHEDULE and features/jobs.py).

Mirrors `manage.py extend_event_series` exactly -- that command still exists, unchanged, for
manual use from a shell (see events/management/commands/extend_event_series.py).
"""

from celery import shared_task

from events.models import EventSeries
from events.services import generate_occurrences, horizon
from features.models import Maintenance


@shared_task(name="events.tasks.extend_event_series")
def extend_event_series():
    if Maintenance.is_on():
        # Loud, not silent: a job that quietly skips itself while the platform is closed is
        # how a rolling horizon quietly runs dry. Raising here is what turns it into a
        # Failure on the control panel's Jobs tab instead of nothing happening at all.
        raise RuntimeError("Platform is in maintenance mode; this job stood down.")

    until = horizon()
    total = 0
    for series in EventSeries.objects.all():
        total += len(generate_occurrences(series, until))

    return f"Generated {total} occurrence(s) across {EventSeries.objects.count()} series."
