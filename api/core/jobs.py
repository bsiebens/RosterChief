"""Long and bulk actions as Celery jobs (API.md §17.3).

An endpoint that would block (sending invoices, importing a spreadsheet, building an export)
enqueues a task and answers ``202`` with the job; the client polls ``GET /manage/jobs/{id}``.

Writing a job::

    from api.core.jobs import JobContext, job_task, start_job

    @job_task(name="shop.send-dues-invoices")
    def send_dues(ctx: JobContext, season_id: str):
        sent = 0
        for i, member in enumerate(members, 1):
            ...
            ctx.progress(i, total)            # optional, 0-100
        return {"sent": sent}                 # JSON-serialisable; becomes ``result``

    @router.post("/manage/invoices/send", response={202: JobOut, **ERRORS})
    def send(request, payload: SendIn):
        club = panel_club(request)
        return 202, start_job(request, send_dues, str(payload.season_id))

* Arguments must be JSON-serialisable (UUIDs as strings); the club and user come from the job,
  read them from ``ctx.club`` / ``ctx.user``.
* Raise ``ApiError`` (or any exception) to fail the job: an ``ApiError`` keeps its code and
  message, anything else becomes ``job_failed`` with a generic message (the traceback is logged).
* The task is enqueued on commit, so it never runs before the job row is visible; in tests
  (``CELERY_TASK_ALWAYS_EAGER``) wrap the request in ``self.captureOnCommitCallbacks(execute=True)``.
* A job is visible only to its club and starting user (anyone else gets 404).
"""

import logging

from celery import shared_task
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from api.core.errors import ApiError
from api.models import Job

logger = logging.getLogger(__name__)


class JobContext:
    """Handed to a job function: who it runs for, and a way to report progress."""

    def __init__(self, job: Job):
        self.job = job

    @property
    def club(self):
        return self.job.club

    @property
    def user(self):
        return self.job.user

    def progress(self, done: int, total: int | None = None) -> None:
        """Report progress: a percentage, or ``done`` of ``total`` items."""
        percent = done if total is None else (round(100 * done / total) if total else 100)
        self.job.progress = max(0, min(100, int(percent)))
        self.job.save(update_fields=["progress", "modified"])


def job_task(*, name: str):
    """Decorator: turn ``fn(ctx, *args)`` into a Celery task that keeps its ``Job`` row current.
    Start it with ``start_job``."""

    def decorate(fn):
        @shared_task(name=name, ignore_result=True)
        def runner(job_id, *args):
            job = Job.objects.select_related("club", "user").filter(pk=job_id).first()
            if job is None:
                logger.warning("Job %s vanished before task %s ran", job_id, name)
                return
            job.status = Job.Status.RUNNING
            job.save(update_fields=["status", "modified"])
            try:
                result = fn(JobContext(job), *args)
            except ApiError as exc:
                _fail(job, exc.code, str(exc.message or exc.code))
            except Exception:
                logger.exception("Job %s (%s) failed", job_id, name)
                _fail(job, "job_failed", _("This action could not be completed."))
            else:
                job.status = Job.Status.SUCCEEDED
                job.progress = 100
                job.result = result
                job.finished_at = timezone.now()
                job.save(update_fields=["status", "progress", "result", "finished_at", "modified"])

        runner.job_function = fn
        return runner

    return decorate


def _fail(job: Job, code: str, message: str) -> None:
    job.status = Job.Status.FAILED
    job.error_code = code
    job.error_message = message
    job.finished_at = timezone.now()
    job.save(update_fields=["status", "error_code", "error_message", "finished_at", "modified"])


def job_out(job: Job) -> dict:
    return {
        "id": str(job.pk),
        "status": job.status,
        "progress": job.progress,
        "result": job.result if job.status == Job.Status.SUCCEEDED else None,
        "error": {"code": job.error_code, "message": job.error_message} if job.status == Job.Status.FAILED else None,
    }


def start_job(request, task, *args) -> dict:
    """Create the job for the signed-in user in the request's club and enqueue ``task`` (a
    ``job_task``) once the transaction commits. Returns the ``JobOut`` dict; answer it with 202."""
    club = getattr(request, "club", None)
    if club is None:
        raise ApiError(404, "not_found", _("No club found for this host."))
    job = Job.objects.create(club=club, user=request.user, name=task.name)
    transaction.on_commit(lambda: task.delay(str(job.pk), *args))
    return job_out(job)
