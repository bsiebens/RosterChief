from celery.schedules import crontab
from django.core.management.base import BaseCommand, CommandError
from django_celery_beat.models import PeriodicTask
from django_celery_beat.schedulers import ModelEntry

from rosterchief.celery import app

CRON_FIELDS = (("minute", "_orig_minute"), ("hour", "_orig_hour"), ("day_of_month", "_orig_day_of_month"), ("month_of_year", "_orig_month_of_year"), ("day_of_week", "_orig_day_of_week"))


def _code_cron(entry) -> dict | None:
    schedule = entry["schedule"]
    return {field: str(getattr(schedule, attr)) for field, attr in CRON_FIELDS} if isinstance(schedule, crontab) else None


def _db_cron(task) -> dict | None:
    c = task.crontab
    return {field: str(getattr(c, field)) for field, _ in CRON_FIELDS} if c else None


def _describe(task) -> str:
    if task.crontab:
        c = task.crontab
        return f"{c.minute} {c.hour} {c.day_of_month} {c.month_of_year} {c.day_of_week} ({c.timezone})"
    return str(task.interval or task.clocked or task.solar or "?")


class Command(BaseCommand):
    help = "Show the periodic tasks: what the database holds (editable in the admin under 'Periodic tasks') compared with the defaults in rosterchief/celery.py. Use --sync NAME to reset an entry's schedule to the code default."

    def add_arguments(self, parser):
        parser.add_argument("--sync", nargs="*", metavar="NAME", help="Reset these code-defined entries (or all, if no name is given) to their default schedule. The on/off switch is kept.")

    def handle(self, *args, **options):
        code = app.conf.beat_schedule
        if options["sync"] is not None:
            return self._sync(code, options["sync"])
        rows = []
        db = {t.name: t for t in PeriodicTask.objects.select_related("crontab", "interval", "clocked", "solar").order_by("name")}
        for name in sorted(set(code) | set(db)):
            task, entry = db.get(name), code.get(name)
            if task is None:
                status = "not in the database yet (created on the next beat start)"
            elif entry is None:
                status = "built into Celery" if name.startswith("celery.") else "database only"
            elif task.task != entry["task"] or _db_cron(task) != _code_cron(entry):
                status = "EDITED in the database (differs from the code default)"
            else:
                status = "default"
            rows.append(
                (
                    name,
                    (entry or {}).get("task") or task.task,
                    _describe(task) if task else "-",
                    "-" if task is None else ("on" if task.enabled else "OFF"),
                    task.last_run_at.strftime("%Y-%m-%d %H:%M") if task and task.last_run_at else "never",
                    status,
                )
            )
        header = ("name", "task", "schedule (m h dom moy dow)", "enabled", "last run", "status")
        widths = [max(len(str(r[i])) for r in [header, *rows]) for i in range(len(header))]
        for r in [header, *rows]:
            self.stdout.write("  ".join(str(v).ljust(w) for v, w in zip(r, widths, strict=True)).rstrip())

    def _sync(self, code, names):
        names = names or sorted(code)
        unknown = [n for n in names if n not in code]
        if unknown:
            raise CommandError(f"Not defined in rosterchief/celery.py: {', '.join(unknown)}")
        for name in names:
            ModelEntry.from_entry(name, app=app, **code[name])  # rewrites the schedule; 'enabled' is not touched
            self.stdout.write(f"reset {name} to its default schedule")
