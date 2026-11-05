"""Background work for members."""

import logging

from celery import shared_task
from django.db import connection
from django.utils import timezone
from django_tenants.utils import get_public_schema_name, schema_context

from members.models import ImportJob

log = logging.getLogger(__name__)
PROGRESS_EVERY = 10


@shared_task(name="members.run_member_import")
def run_member_import(job_id: str) -> str:
    """Create the members of an ``ImportJob`` (queued from the club's schema, so it runs in that schema). Progress is
    written to the job every few rows, the per-row results when done, and the person who started it gets an inbox note."""
    from club.models import Club
    from members.services import importing
    from notifications import inbox

    job = ImportJob.objects.filter(pk=job_id).first()
    if job is None or job.status not in (ImportJob.Status.QUEUED, ImportJob.Status.RUNNING):
        return "skipped"
    schema = connection.schema_name
    job.status = ImportJob.Status.RUNNING
    job.save(update_fields=["status", "modified"])

    def progress(done, total):
        if done % PROGRESS_EVERY == 0 or done == total:
            ImportJob.objects.filter(pk=job.pk).update(processed=done)

    try:
        club = _club(schema)
        result = importing.commit(job.created_by, club, job.rows, job.season_id, progress=progress)
    except Exception as e:  # noqa: BLE001  (a failed import must leave a readable trace on the job, not just in the worker log)
        log.exception("Member import %s failed", job_id)
        message = str(e) or e.__class__.__name__
        ImportJob.objects.filter(pk=job.pk).update(status=ImportJob.Status.FAILED, error=message[:2000], rows=[], finished_at=timezone.now())
        _tell(job, title="Import failed", body=message[:300], kind=inbox.Kind.WARNING)
        return "failed"
    c = result["counts"]
    ImportJob.objects.filter(pk=job.pk).update(status=ImportJob.Status.DONE, result=result, rows=[], processed=job.total, finished_at=timezone.now())
    _tell(
        job,
        title="Import finished",
        body=f"{c['created']} members were imported, {c['duplicate']} skipped as duplicates and {c['error']} left out because of errors.",
        kind=inbox.Kind.WARNING if c["error"] else inbox.Kind.SUCCESS,
    )
    return "done"


def _club(schema_name):
    from club.models import Club

    with schema_context(get_public_schema_name()):
        return Club.objects.get(schema_name=schema_name)


def _tell(job, *, title, body, kind):
    from notifications import inbox

    if job.created_by is None:
        return
    try:
        inbox.notify(job.created_by, _club(connection.schema_name), title=title, body=body, kind=kind, href="/members", push=True, category="admin")
    except Exception:  # noqa: BLE001
        log.exception("Import notification failed")
