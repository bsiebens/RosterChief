"""Scheduled work refuses to run while the platform is locked down.

Deliberately opt-in, per command, rather than a blanket guard on BaseCommand: maintenance is
usually declared IN ORDER to run `migrate` or `collectstatic`, and a guard that blocked those
would make the mode useless — you would have to turn it off to do the work you turned it on
for. Only the domain jobs (which write club data, archive clubs, or import members) stand
down.
"""

import uuid

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from features.models import JobRun, JobToggle, Maintenance


class MaintenanceAwareCommand(BaseCommand):
    """A command that must not run while the platform is closed."""

    def execute(self, *args, **options):
        if Maintenance.is_on() and not options.get("ignore_maintenance"):
            raise CommandError("The platform is in maintenance mode; this command stands down. Pass --ignore-maintenance to override.")

        return super().execute(*args, **options)

    def create_parser(self, prog_name, subcommand, **kwargs):
        parser = super().create_parser(prog_name, subcommand, **kwargs)
        parser.add_argument(
            "--ignore-maintenance",
            action="store_true",
            help="Run even though the platform is in maintenance mode.",
        )
        return parser


class ScheduledJobCommand(MaintenanceAwareCommand):
    """A MaintenanceAwareCommand that's also one of features.jobs.JOB_REGISTRY's scheduled
    platform jobs -- cron invokes these directly (see DEPLOYMENT.md's "Scheduled jobs"),
    there is no Celery worker/beat left to provide task_prerun/task_postrun/task_failure
    signals, so this writes the same JobRun bookkeeping those signals used to (see the now-
    deleted features/signals.py task handlers) directly around its own execute().

    Also stands down via JobToggle -- a per-job switch, narrower than Maintenance's platform-
    wide one -- checked here rather than in MaintenanceAwareCommand.execute() itself so a
    disabled job still gets a JobRun(FAILURE) row instead of silently doing nothing: the same
    "loud, not silent" reasoning events.tasks.extend_event_series's own Maintenance check used
    to give for raising instead of quietly skipping -- a job that goes quiet is indistinguishable
    from a job that was never scheduled at all.

    Subclasses set ``job_name`` to their features.jobs.JOB_REGISTRY key and implement
    ``handle()`` as normal, returning a short result string -- it becomes JobRun.detail on
    success. Maintenance blocking (raised inside super().execute(), before handle() ever
    runs) and any exception handle() itself raises both land here as JobRun(FAILURE) via the
    same except clause -- one wrapping point covers both failure shapes.
    """

    job_name: str = ""

    def execute(self, *args, **options):
        job_run = JobRun.objects.create(task_id=str(uuid.uuid4()), name=self.job_name, status=JobRun.Status.STARTED, started_at=timezone.now())
        try:
            if not JobToggle.is_enabled(self.job_name):
                raise CommandError("This job is disabled in the control panel.")
            result = super().execute(*args, **options)
        except Exception as exc:
            job_run.status = JobRun.Status.FAILURE
            job_run.finished_at = timezone.now()
            job_run.error = str(exc)[:4000]
            job_run.save(update_fields=["status", "finished_at", "error"])
            raise
        else:
            job_run.status = JobRun.Status.SUCCESS
            job_run.finished_at = timezone.now()
            job_run.detail = str(result or "")[:4000]
            job_run.save(update_fields=["status", "finished_at", "detail"])
            return result
