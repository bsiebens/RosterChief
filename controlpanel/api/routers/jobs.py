"""Background jobs: the celery run history and a one-line health summary for the control panel's top bar."""

import datetime
import json

from django.shortcuts import get_object_or_404
from django.utils import timezone
from django_celery_beat.models import PeriodicTask
from django_celery_results.models import TaskResult
from ninja import Query, Router, Schema

from controlpanel import audit
from controlpanel.services import periodic_jobs as svc

from ..auth import superuser_auth
from ..errors import ApiError
from ..schemas import CronIn, JobDetailOut, JobEnabledIn, JobOut, JobQueuedOut, JobRunBrief, JobsStatusOut, PeriodicJobOut

router = Router(tags=["jobs"])

FAILURE_WINDOW_HOURS = 24
RUNNING = ("STARTED",)
PENDING = ("PENDING", "RECEIVED")
STATUS = {"SUCCESS": "success", "FAILURE": "failure", "STARTED": "running", "PENDING": "pending", "RECEIVED": "pending", "RETRY": "retry", "REVOKED": "revoked"}


class JobsQuery(Schema):
    limit: int = 15
    status: str | None = None  # success | failure | running | pending | retry | revoked


def _result_text(raw: str | None) -> str:
    """celery stores results as JSON, so a returned string arrives quoted ('"Hello, world!"'): show the value itself."""
    if not raw:
        return ""
    try:
        value = json.loads(raw)
    except ValueError:
        return raw
    return value if isinstance(value, str) else json.dumps(value, indent=2, ensure_ascii=False)


def _short(text: str | None, size: int = 200) -> str:
    line = next((ln.strip() for ln in reversed((text or "").strip().splitlines()) if ln.strip()), "")
    return line if len(line) <= size else line[: size - 1] + "…"


def _out(r: TaskResult) -> JobOut:
    failed = r.status == "FAILURE"
    started = r.date_created
    duration = (r.date_done - started).total_seconds() if r.status == "SUCCESS" or failed else None
    return JobOut(
        id=r.task_id,
        name=r.task_name or "(unknown task)",
        status=STATUS.get(r.status, r.status.lower()),
        started_at=started,
        finished_at=r.date_done if r.status in ("SUCCESS", "FAILURE", "REVOKED") else None,
        duration_seconds=duration,
        scheduled=bool(r.periodic_task_name),
        worker=r.worker,
        detail=_short(r.traceback if failed else _result_text(r.result)),
    )


@router.get("/jobs", response=list[JobOut], summary="Recent celery task runs, newest first")
def list_jobs(request, q: Query[JobsQuery]):
    runs = TaskResult.objects.order_by("-date_created")
    if q.status:
        codes = [code for code, name in STATUS.items() if name == q.status]
        runs = runs.filter(status__in=codes)
    return [_out(r) for r in runs[: max(1, min(q.limit, 100))]]


@router.get("/jobs/status", response=JobsStatusOut, summary="Health of the background jobs (failures in the last 24 hours, runs in progress)")
def jobs_status(request):
    since = timezone.now() - datetime.timedelta(hours=FAILURE_WINDOW_HOURS)
    failed = TaskResult.objects.filter(status="FAILURE", date_done__gte=since).count()
    running = TaskResult.objects.filter(status__in=RUNNING, date_created__gte=since).count()
    last = TaskResult.objects.order_by("-date_created").values_list("date_created", flat=True).first()
    return JobsStatusOut(state="failing" if failed else "busy" if running else "ok", failed=failed, running=running, window_hours=FAILURE_WINDOW_HOURS, last_run_at=last)


def _periodic(task: PeriodicTask) -> PeriodicJobOut:
    runs = svc.runs_of(task)
    schedule, default = svc.schedule_of(task), svc.default_schedule(task.name)
    latest = _out(runs[0]) if runs else None
    return PeriodicJobOut(
        name=task.name,
        task=task.task,
        label=svc.label_of(task),
        description=svc.description_of(task),
        builtin=task.task.startswith("celery."),
        enabled=task.enabled,
        state=svc.state_of(task, runs),
        schedule=schedule,
        default_schedule=default,
        edited=bool(default and schedule and {f: schedule[f] for f in svc.FIELDS} != default),
        next_run_at=svc.next_run(task),
        last_run_at=task.last_run_at,
        total_run_count=task.total_run_count,
        latest=JobRunBrief(status=latest.status, started_at=latest.started_at, duration_seconds=latest.duration_seconds, detail=latest.detail) if latest else None,
        recent=[STATUS.get(r.status, r.status.lower()) for r in reversed(runs)],
    )


def _task(name: str) -> PeriodicTask:
    try:
        return svc.get_task(name)
    except svc.JobError as e:
        raise ApiError(404, "not_found", str(e)) from e


@router.get("/jobs/scheduled", response=list[PeriodicJobOut], summary="The periodic jobs: schedule, state and recent runs")
def list_scheduled(request):
    return [_periodic(t) for t in PeriodicTask.objects.select_related("crontab").order_by("name")]


@router.put("/jobs/scheduled/{name}/schedule", response=PeriodicJobOut, auth=superuser_auth, summary="Change when a job runs (stored in the database; beat reloads it)")
def put_schedule(request, name: str, payload: CronIn):
    task = _task(name)
    before = svc.schedule_of(task)
    try:
        svc.set_schedule(task, svc.validate_schedule(payload.dict()))
    except svc.JobError as e:
        raise ApiError(422, "validation_error", str(e), {"schedule": [str(e)]}) from e
    task.refresh_from_db()
    audit.record(request.user, "job.schedule_changed", target=task, data={"name": name, "from": before, "to": svc.schedule_of(task)})
    return _periodic(task)


@router.post("/jobs/scheduled/{name}/reset", response=PeriodicJobOut, auth=superuser_auth, summary="Put a job back on the schedule defined in code")
def reset_schedule(request, name: str):
    task = _task(name)
    before = svc.schedule_of(task)
    try:
        svc.reset_schedule(task)
    except svc.JobError as e:
        raise ApiError(409, "no_default", str(e)) from e
    task.refresh_from_db()
    audit.record(request.user, "job.schedule_reset", target=task, data={"name": name, "from": before, "to": svc.schedule_of(task)})
    return _periodic(task)


@router.put("/jobs/scheduled/{name}/enabled", response=PeriodicJobOut, auth=superuser_auth, summary="Pause or resume a job")
def put_enabled(request, name: str, payload: JobEnabledIn):
    task = _task(name)
    task.enabled = payload.enabled
    task.save()
    audit.record(request.user, "job.resumed" if payload.enabled else "job.paused", target=task, data={"name": name})
    return _periodic(task)


@router.post("/jobs/scheduled/{name}/run", response={202: JobQueuedOut}, auth=superuser_auth, summary="Queue one run of a job now (a paused job can still be run by hand)")
def run_now(request, name: str):
    task = _task(name)
    try:
        task_id = svc.run_now(task)
    except svc.JobError as e:
        raise ApiError(503, "broker_unavailable", str(e)) from e
    audit.record(request.user, "job.run_now", target=task, data={"name": name, "task_id": task_id})
    return 202, JobQueuedOut(task_id=task_id)


@router.get("/jobs/{task_id}", response=JobDetailOut, summary="One task run in full, including the traceback of a failure")
def job_detail(request, task_id: str):
    r = get_object_or_404(TaskResult, task_id=task_id)
    return JobDetailOut(
        **_out(r).dict(),
        traceback=r.traceback,
        result=_result_text(r.result) if r.status != "FAILURE" else r.result,
        args=r.task_args,
        kwargs=r.task_kwargs,
        periodic_task_name=r.periodic_task_name,
    )
