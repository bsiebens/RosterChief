import datetime
import logging

from celery import shared_task
from django.utils import timezone
from django_tenants.utils import get_public_schema_name, get_tenant_model, schema_context

from .seasons import ensure_seasons

logger = logging.getLogger(__name__)


@shared_task(name="events.create_missing_seasons")
def create_missing_seasons(today: datetime.date | None = None) -> str:
    """Give every active club a season covering today plus the next one, using the club's season start/duration.

    Scheduled every Monday at 02:00. Idempotent: clubs that already have their seasons are left alone.
    """
    today = today or timezone.localdate()
    created = 0
    failed = []
    for club in get_tenant_model().objects.filter(archived_at__isnull=True).exclude(schema_name=get_public_schema_name()):
        try:
            with schema_context(club.schema_name):
                created += len(ensure_seasons(today, start_month=club.season_start.month, start_day=club.season_start.day, duration_months=club.season_duration_months))
        except Exception:  # one broken club must not stop the others
            logger.exception("Creating seasons failed for club %s", club.slug)
            failed.append(club.slug)
    return f"created {created} season(s)" + (f"; failed: {', '.join(failed)}" if failed else "")
