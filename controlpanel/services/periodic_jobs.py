"""Periodic (celery beat) jobs: the schedule lives in the database (django-celery-beat), the defaults in code.

``rosterchief/celery.py`` seeds a row per code-defined job when it is missing and never overwrites it afterwards, so
editing a schedule here (or in the Django admin) survives restarts. ``default_schedule`` is what the code would seed.
"""

import datetime
import json
import re

from celery.schedules import crontab
from django.utils import timezone
from django_celery_beat.models import CrontabSchedule, PeriodicTask
from django_celery_results.models import TaskResult

from rosterchief.celery import app as current_app

FIELDS = ("minute", "hour", "day_of_month", "month_of_year", "day_of_week")
FIELD_PATTERN = re.compile(r"^[0-9*/,\-]+$")
RECENT = 12


class JobError(Exception):
    pass


def get_task(name: str) -> PeriodicTask:
    try:
        return PeriodicTask.objects.select_related("crontab").get(name=name)
    except PeriodicTask.DoesNotExist as e:
        raise JobError("Unknown job.") from e


def schedule_of(task: PeriodicTask) -> dict | None:
    c = task.crontab
    return {**{f: str(getattr(c, f)) for f in FIELDS}, "timezone": str(c.timezone)} if c else None


def default_schedule(name: str) -> dict | None:
    entry = current_app.conf.beat_schedule.get(name)
    sched = entry["schedule"] if entry else None
    if not isinstance(sched, crontab):
        return None
    return {f: str(getattr(sched, f"_orig_{f}")) for f in FIELDS}


def validate_schedule(values: dict) -> dict:
    """Return the five cron fields cleaned, or raise ``JobError`` naming the field that is wrong."""
    clean = {}
    for f in FIELDS:
        v = str(values.get(f, "")).strip().replace(" ", "")
        if not v or not FIELD_PATTERN.match(v):
            raise JobError(f"{f.replace('_', ' ')}: use numbers with * , - /, e.g. */5 or 1,15.")
        clean[f] = v
    try:
        crontab(**clean)
    except Exception as e:  # celery raises ParseException/ValueError for out-of-range values
        raise JobError(f"Not a valid schedule: {e}") from e
    return clean


def set_schedule(task: PeriodicTask, values: dict) -> PeriodicTask:
    tz = task.crontab.timezone if task.crontab else timezone.get_current_timezone()
    cron, _ = CrontabSchedule.objects.get_or_create(**values, timezone=tz)
    task.crontab, task.interval, task.solar, task.clocked = cron, None, None, None
    task.save()  # also tells a running beat to reload its schedule
    return task


def reset_schedule(task: PeriodicTask) -> PeriodicTask:
    default = default_schedule(task.name)
    if default is None:
        raise JobError("This job has no default schedule in code.")
    return set_schedule(task, default)


def next_run(task: PeriodicTask) -> datetime.datetime | None:
    """The next time the schedule fires, as an aware datetime.

    Worked out from the cron fields in the schedule's own timezone. (celery's ``remaining_estimate`` is off by the zone
    offset when the schedule's zone differs from the one it is asked in, which showed 06:00 Brussels as 07:00.)
    """
    if not task.enabled or not task.crontab:
        return None
    sched = task.schedule
    tz = task.crontab.timezone
    now = timezone.now().astimezone(tz)
    minutes, hours = sorted(sched.minute), sorted(sched.hour)
    for offset in range(0, 366 * 2):
        day = (now + datetime.timedelta(days=offset)).date()
        if day.month not in sched.month_of_year or day.day not in sched.day_of_month or (day.isoweekday() % 7) not in sched.day_of_week:
            continue
        for h in hours:
            for m in minutes:
                candidate = datetime.datetime(day.year, day.month, day.day, h, m, tzinfo=tz)
                if candidate > now:
                    return candidate.astimezone(datetime.UTC)
    return None


def label_of(task: PeriodicTask) -> str:
    return task.task.rsplit(".", 1)[-1].replace("_", " ").capitalize()


def description_of(task: PeriodicTask) -> str:
    current_app.loader.import_default_modules()  # tasks are registered lazily; make sure this process has them (cheap after the first call)
    fn = current_app.tasks.get(task.task)
    doc = (fn.__doc__ or "").strip() if fn else ""
    return " ".join(doc.split("\n\n")[0].split()) or (task.description or "")


def runs_of(task: PeriodicTask) -> list[TaskResult]:
    return list(TaskResult.objects.filter(task_name=task.task).order_by("-date_created")[:RECENT])


def state_of(task: PeriodicTask, runs: list[TaskResult]) -> str:
    if not task.enabled:
        return "paused"
    if any(r.status in ("STARTED", "PENDING", "RECEIVED") and r.date_created > timezone.now() - datetime.timedelta(hours=1) for r in runs):
        return "running"
    if not runs:
        return "never"
    return "error" if runs[0].status == "FAILURE" else "ok"


def run_now(task: PeriodicTask) -> str:
    """Queue one run now, with the arguments the schedule uses. The worker records the result like any other run."""
    try:
        result = current_app.send_task(task.task, args=json.loads(task.args or "[]"), kwargs=json.loads(task.kwargs or "{}"))
    except Exception as e:
        raise JobError("The task could not be queued. Is the message broker (Redis) running?") from e
    return result.id
