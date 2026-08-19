"""Keep waffle's flag cache honest when club targeting changes, and keep a JobRun history
of the scheduled platform jobs (see features/jobs.py).

waffle caches a flag's M2M ids and only flushes on ``save()``. Editing an M2M
does not call ``save()``, so adding or removing a club would otherwise leave a
stale cached set and the flag would keep answering with the old value.
"""

from celery.signals import task_failure, task_postrun, task_prerun
from django.db.models.signals import m2m_changed
from django.dispatch import receiver
from django.utils import timezone

from .jobs import JOB_REGISTRY
from .models import Flag, JobRun

FLUSH_ACTIONS = {"post_add", "post_remove", "post_clear"}


@receiver(m2m_changed, sender=Flag.clubs.through)
def flush_flag_club_cache(sender, instance, action, reverse, pk_set, **kwargs):
    if action not in FLUSH_ACTIONS:
        return

    if isinstance(instance, Flag):
        instance.flush()
    else:
        # Reverse edit (club.flags.add(flag)): flush each flag touched.
        for flag in Flag.objects.filter(pk__in=pk_set or []):
            flag.flush()


@task_prerun.connect
def record_job_start(sender=None, task_id=None, task=None, **kwargs):
    """One JobRun row per task execution, for the jobs in JOB_REGISTRY only -- an
    unregistered Celery task (should one ever be added without a job to show for it)
    is not the control panel Jobs tab's business."""
    if task is None or task.name not in JOB_REGISTRY:
        return

    JobRun.objects.create(task_id=task_id, name=task.name, status=JobRun.Status.STARTED, started_at=timezone.now())


@task_postrun.connect
def record_job_finish(sender=None, task_id=None, task=None, retval=None, state=None, **kwargs):
    """Closes the row record_job_start opened. Fires whether the task succeeded or raised --
    on success, ``retval`` is whatever the task returned (see billing/tasks.py, club/tasks.py,
    events/tasks.py: each returns a short human summary for this) and becomes JobRun.detail.
    On failure ``retval`` is not reliably the exception, so record_job_failure below (driven
    by the dedicated task_failure signal instead) fills in JobRun.error."""
    if task is None or task.name not in JOB_REGISTRY:
        return

    succeeded = state == "SUCCESS"
    JobRun.objects.filter(task_id=task_id).update(
        status=JobRun.Status.SUCCESS if succeeded else JobRun.Status.FAILURE,
        finished_at=timezone.now(),
        detail=str(retval)[:4000] if succeeded else "",
    )


@task_failure.connect
def record_job_failure(sender=None, task_id=None, exception=None, **kwargs):
    task_name = getattr(sender, "name", None)
    if task_name not in JOB_REGISTRY:
        return

    JobRun.objects.filter(task_id=task_id).update(error=str(exception)[:4000])
