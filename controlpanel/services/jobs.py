"""Read side of the scheduled-job history for the control panel's Jobs tab and the
Platform dashboard's job log / failed-jobs tile.

``features.jobs.JOB_REGISTRY`` is what a job *is* (label, description, schedule);
``features.models.JobRun`` is what actually happened, written by features.commands.
ScheduledJobCommand around each cron-invoked command's own execution. This module just
joins the two for a template.
"""

from datetime import timedelta

from django.utils import timezone

from features.jobs import JOB_REGISTRY
from features.models import JobRun, JobToggle

#: Runs shown per job on the Jobs tab -- enough to see a pattern (a job that fails every
#: third day, say) without the page turning into a full audit log.
RECENT_RUNS = 10

#: What counts as "recent" for the dashboard's failed-jobs KPI tile.
FAILURE_WINDOW_HOURS = 24

#: Rows in the Platform dashboard's job log card.
JOB_LOG_ROWS = 8


def job_overview():
    """One entry per registered job, its most recent runs, and a shortcut to the latest."""
    return [
        {
            "name": name,
            "label": meta["label"],
            "description": meta["description"],
            "schedule": meta["schedule"],
            "enabled": JobToggle.is_enabled(name),
            "runs": (runs := list(JobRun.objects.filter(name=name)[:RECENT_RUNS])),
            "latest": runs[0] if runs else None,
        }
        for name, meta in JOB_REGISTRY.items()
    ]


def recent_job_failures(hours=FAILURE_WINDOW_HOURS):
    """Failures in the last `hours` -- the platform-health "failed jobs" signal. A number
    that sits here is exactly what a dead beat schedule or a broken task looks like."""
    since = timezone.now() - timedelta(hours=hours)
    return JobRun.objects.filter(status=JobRun.Status.FAILURE, started_at__gte=since)


def recent_job_runs(limit=JOB_LOG_ROWS):
    """Every job's runs, most recent first, for the dashboard's Job log card."""
    return JobRun.objects.all()[:limit]
