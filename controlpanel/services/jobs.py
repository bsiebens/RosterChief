"""Read side of the scheduled-job history for the control panel's Jobs tab and the
Platform dashboard's job log / failed-jobs tile.

``features.jobs.JOB_REGISTRY`` is what a job *is* (label, description, schedule);
``features.models.JobRun`` is what actually happened, written by features.commands.
ScheduledJobCommand around each scheduler-invoked command's own execution. This module
just joins the two for a template.
"""

from datetime import timedelta

from django.utils import timezone

from features.jobs import JOB_REGISTRY
from features.models import JobRun, JobToggle

#: Runs shown per job on the Jobs tab -- enough to see a pattern (a job that fails every
#: third day, say) without the page turning into a full audit log.
RECENT_RUNS = 10

#: What counts as "recent" for the failed-jobs count (command bar indicator, dashboard tile).
FAILURE_WINDOW_HOURS = 24

#: Rows in the Platform dashboard's job log card.
JOB_LOG_ROWS = 8


def job_overview():
    """One entry per registered job, its most recent runs, and a shortcut to the latest.

    Ordered failing first (latest run failed), then running, then everything else -- each
    group in JOB_REGISTRY order, so the table only reshuffles when something needs looking
    at. One JobRun query per job; fine at a dozen jobs.
    """
    jobs = []
    for name, meta in JOB_REGISTRY.items():
        runs = list(JobRun.objects.filter(name=name)[:RECENT_RUNS])
        latest = runs[0] if runs else None
        enabled = JobToggle.is_enabled(name)
        is_failing = latest is not None and latest.status == JobRun.Status.FAILURE
        is_running = latest is not None and latest.status == JobRun.Status.STARTED
        if not enabled:
            state = "paused"
        elif latest is None:
            state = "never"
        else:
            state = {JobRun.Status.SUCCESS: "ok", JobRun.Status.FAILURE: "error"}.get(latest.status, "running")
        jobs.append(
            {
                "name": name,
                "label": meta["label"],
                "description": meta["description"],
                "schedule": meta["schedule"],
                "enabled": enabled,
                "runs": runs,
                "runs_oldest_first": runs[::-1],
                "latest": latest,
                "state": state,
                "is_failing": is_failing,
                "is_running": is_running,
            }
        )
    # sorted() is stable, so registry order survives within each group.
    return sorted(jobs, key=lambda job: 0 if job["is_failing"] else 1 if job["is_running"] else 2)


def job_summary(jobs):
    """Counts for the Jobs tab's breadcrumb strip, from an already-built job_overview()."""
    return {
        "scheduled": len(jobs),
        "failing": sum(job["is_failing"] for job in jobs),
        "paused": sum(not job["enabled"] for job in jobs),
    }


def recent_job_failures(hours=FAILURE_WINDOW_HOURS):
    """Failures in the last `hours` -- the platform-health "failed jobs" signal. A number
    that sits here is exactly what a dead beat schedule or a broken task looks like."""
    since = timezone.now() - timedelta(hours=hours)
    return JobRun.objects.filter(status=JobRun.Status.FAILURE, started_at__gte=since)


def recent_job_runs(limit=JOB_LOG_ROWS):
    """Every job's runs, most recent first, for the dashboard's Job log card."""
    return JobRun.objects.all()[:limit]
