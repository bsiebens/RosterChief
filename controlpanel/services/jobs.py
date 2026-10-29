"""Read side of the scheduled jobs for the control panel's Jobs tab and the Platform dashboard's
job log / failed-jobs tile.

``features.jobs.JOB_REGISTRY`` is what a job *is* (label, description, Celery task name);
``django_celery_beat.PeriodicTask`` is its schedule and on/off switch (editable, see
features.scheduling); ``django_celery_results.TaskResult`` is what actually happened, written by the
Celery worker. This module joins the three for a template.
"""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.conf import settings
from django.utils import timezone
from django.utils.translation import gettext as _
from django_celery_beat.models import PeriodicTask
from django_celery_results.models import TaskResult

from features.jobs import JOB_REGISTRY
from features.scheduling import describe_schedule

#: Runs shown per job on the Jobs tab -- enough to see a pattern (a job that fails every
#: third day, say) without the page turning into a full audit log.
RECENT_RUNS = 10

#: What counts as "recent" for the failed-jobs count (command bar indicator, dashboard tile).
FAILURE_WINDOW_HOURS = 24

#: Rows in the Platform dashboard's job log card.
JOB_LOG_ROWS = 8

#: A task still "started" this long after it began was lost (its worker died or was killed): Celery's own
#: hard time limit would have ended it by now, and nothing ever writes a result for a killed worker.
STALE_AFTER = timedelta(seconds=settings.CELERY_TASK_TIME_LIMIT + 60)

KEY_BY_TASK = {meta["task"]: key for key, meta in JOB_REGISTRY.items()}

STARTED, SUCCESS, FAILURE = "started", "success", "failure"
FINISHED_STATES = {"SUCCESS": SUCCESS, "FAILURE": FAILURE, "REVOKED": FAILURE}


@dataclass
class JobRunRecord:
    """One TaskResult shaped for the templates: the same attributes the old JobRun row had."""

    name: str  # the registry key
    label: str
    status: str  # started | success | failure
    started_at: datetime
    finished_at: datetime | None = None
    detail: str = ""
    error: str = ""

    @property
    def duration(self):
        return None if self.finished_at is None else self.finished_at - self.started_at


def _payload(text):
    try:
        return json.loads(text) if text else None
    except ValueError:
        return text


def _detail(result):
    payload = _payload(result)
    if isinstance(payload, dict):
        return str(payload.get("result", ""))[:4000]
    return str(payload or "")[:4000]


def _error(result, traceback):
    """ "ExcType: message" from the exception Celery stored, else the traceback's last line."""
    payload = _payload(result)
    if isinstance(payload, dict) and payload.get("exc_type"):
        message = payload.get("exc_message")
        message = " ".join(str(part) for part in message) if isinstance(message, list) else str(message or "")
        return f"{payload['exc_type']}: {message}"[:4000]
    lines = [line for line in (traceback or "").strip().splitlines() if line.strip()]
    return (lines[-1] if lines else str(payload or ""))[:4000]


def to_record(result):
    key = KEY_BY_TASK.get(result.task_name, result.task_name)
    label = str(JOB_REGISTRY[key]["label"]) if key in JOB_REGISTRY else result.task_name
    status = FINISHED_STATES.get(result.status, STARTED)
    started = result.date_created

    if status == STARTED:
        if timezone.now() - started > STALE_AFTER:
            return JobRunRecord(key, label, FAILURE, started, started + STALE_AFTER, error=_("The worker stopped before this run finished."))
        return JobRunRecord(key, label, STARTED, started)
    if status == SUCCESS:
        return JobRunRecord(key, label, SUCCESS, started, result.date_done, detail=_detail(result.result))
    return JobRunRecord(key, label, FAILURE, started, result.date_done, error=_error(result.result, result.traceback))


def job_overview():
    """One entry per registered job, its most recent runs, and a shortcut to the latest.

    Ordered failing first (latest run failed), then running, then everything else -- each
    group in JOB_REGISTRY order, so the table only reshuffles when something needs looking
    at. One TaskResult query per job; fine at a dozen jobs.
    """
    periodic = {task.task: task for task in PeriodicTask.objects.select_related("interval", "crontab").filter(task__in=KEY_BY_TASK)}
    jobs = []
    for key, meta in JOB_REGISTRY.items():
        task = periodic.get(meta["task"])
        results = TaskResult.objects.filter(task_name=meta["task"]).order_by("-date_created")[:RECENT_RUNS]
        runs = [to_record(result) for result in results]
        latest = runs[0] if runs else None
        enabled = task is not None and task.enabled
        is_failing = latest is not None and latest.status == FAILURE
        is_running = latest is not None and latest.status == STARTED
        if task is None:
            state = "unscheduled"
        elif not enabled:
            state = "paused"
        elif latest is None:
            state = "never"
        else:
            state = {SUCCESS: "ok", FAILURE: "error"}.get(latest.status, "running")
        jobs.append(
            {
                "name": key,
                "label": meta["label"],
                "description": meta["description"],
                "task": meta["task"],
                "schedule": describe_schedule(task),
                "scheduled": task is not None,
                "enabled": enabled,
                "last_scheduled_run": task.last_run_at if task is not None else None,
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
        "scheduled": sum(job["scheduled"] for job in jobs),
        "failing": sum(job["is_failing"] for job in jobs),
        "paused": sum(job["scheduled"] and not job["enabled"] for job in jobs),
        "unscheduled": sum(not job["scheduled"] for job in jobs),
    }


def recent_job_failures(hours=FAILURE_WINDOW_HOURS):
    """Failures in the last `hours` -- the platform-health "failed jobs" signal. A number
    that sits here is exactly what a dead beat schedule or a broken task looks like."""
    since = timezone.now() - timedelta(hours=hours)
    return TaskResult.objects.filter(status="FAILURE", task_name__in=KEY_BY_TASK, date_done__gte=since)


def recent_job_runs(limit=JOB_LOG_ROWS):
    """Every job's runs, most recent first, for the dashboard's Job log card."""
    return [to_record(result) for result in TaskResult.objects.filter(task_name__in=KEY_BY_TASK).order_by("-date_created")[:limit]]
