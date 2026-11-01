"""Creating a club builds a database schema and runs every migration in it, which is too slow for a web request.
The API records a ``ClubProvisioning`` and a Celery task does the work; callers poll the record.
"""

from django.db import connection, transaction
from django.utils import timezone
from django_tenants.utils import get_public_schema_name, schema_context

from club.models import Club
from club.services import provision_club
from controlpanel import audit
from controlpanel.models import ClubProvisioning


def request_club(*, name: str, owner, sport=None, club_fields: dict | None = None, requested_by=None) -> ClubProvisioning:
    """Record a request and queue the build. Returns the (pending) record."""
    from controlpanel.tasks import provision_club_task

    job = ClubProvisioning.objects.create(name=name, owner=owner, sport=sport, club_fields=club_fields or {}, requested_by=requested_by)
    transaction.on_commit(lambda: provision_club_task.delay(str(job.pk)))
    return job


def _drop_leftover_schema(schema_name: str) -> None:
    """A failed build must not leave a half-made schema behind (the club row itself is rolled back with the transaction)."""
    if not schema_name or schema_name == get_public_schema_name() or Club.objects.filter(schema_name=schema_name).exists():
        return
    with connection.cursor() as cursor:
        cursor.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')


def run_provisioning(job_id) -> ClubProvisioning:
    """Build the club for a request. Safe to call twice: only a pending request is started."""
    with schema_context(get_public_schema_name()):
        with transaction.atomic():
            job = ClubProvisioning.objects.select_for_update(of=("self",)).select_related("owner", "sport").get(pk=job_id)
            if job.status != ClubProvisioning.PENDING:
                return job
            job.status, job.started_at = ClubProvisioning.RUNNING, timezone.now()
            job.save(update_fields=["status", "started_at", "modified"])
        club = Club(name=job.name, owner=job.owner, sport=job.sport, **job.club_fields)
        try:
            provision_club(club)
        except Exception as e:
            _drop_leftover_schema(club.schema_name)
            job.status, job.error, job.finished_at = ClubProvisioning.FAILED, f"{type(e).__name__}: {e}", timezone.now()
            job.save(update_fields=["status", "error", "finished_at", "modified"])
            audit.record(job.requested_by, "club.create_failed", club=None, data={"name": job.name, "error": job.error[:500]})
            return job
        job.status, job.club, job.finished_at = ClubProvisioning.SUCCEEDED, club, timezone.now()
        job.save(update_fields=["status", "club", "finished_at", "modified"])
        audit.record(job.requested_by, "club.created", target=club, club=club, data={"name": club.name, "slug": club.slug, "owner": job.owner.email})
        return job
