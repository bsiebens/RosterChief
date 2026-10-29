"""Reading and writing a job's schedule, which lives in the database (django_celery_beat.PeriodicTask)
so the control panel can edit it. ``features.jobs.JOB_REGISTRY`` only carries each job's *default*.

Beat's DatabaseScheduler notices a saved PeriodicTask by itself (django_celery_beat bumps
PeriodicTasks.last_update on save), so there is nothing to reload or restart after an edit.
"""

from celery.schedules import crontab_parser
from django.conf import settings
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy, ngettext
from django_celery_beat.models import CrontabSchedule, IntervalSchedule

CRON_FIELDS = ("minute", "hour", "day_of_month", "month_of_year", "day_of_week")
CRON_DEFAULTS = dict.fromkeys(CRON_FIELDS, "*")

#: Field -> (crontab_parser max, min), used only to validate what an admin typed.
CRON_RANGES = {"minute": (60, 0), "hour": (24, 0), "day_of_month": (32, 1), "month_of_year": (13, 1), "day_of_week": (7, 0)}

#: Celery's cron weekday numbers: 0 is Sunday.
WEEKDAYS = {
    "0": gettext_lazy("Sunday"),
    "1": gettext_lazy("Monday"),
    "2": gettext_lazy("Tuesday"),
    "3": gettext_lazy("Wednesday"),
    "4": gettext_lazy("Thursday"),
    "5": gettext_lazy("Friday"),
    "6": gettext_lazy("Saturday"),
}


def validate_cron_field(field, value):
    """Raise ValueError unless `value` is a valid cron expression for `field` (``*``, ``5``, ``*/15``, ``1-5``, ``1,3``)."""
    maximum, minimum = CRON_RANGES[field]
    try:
        crontab_parser(maximum, minimum).parse(value)
    except Exception as exc:
        raise ValueError(str(exc)) from exc


def schedule_for(spec):
    """The (PeriodicTask field name, schedule row) for a registry ``default_schedule``-shaped spec,
    creating the schedule row if it does not exist yet."""
    if "interval" in spec:
        return "interval", IntervalSchedule.objects.get_or_create(**spec["interval"])[0]

    fields = {**CRON_DEFAULTS, **spec["crontab"]}
    # filter().first() rather than get_or_create: django_celery_beat's own migrations (and earlier admin
    # edits) can already hold identical rows, which get() would reject.
    schedule = CrontabSchedule.objects.filter(timezone=settings.TIME_ZONE, **fields).first()
    if schedule is None:
        schedule = CrontabSchedule.objects.create(timezone=settings.TIME_ZONE, **fields)
    return "crontab", schedule


def set_schedule(periodic_task, spec):
    """Point `periodic_task` at the schedule `spec` describes (and only that one) and save it."""
    field, schedule = schedule_for(spec)
    periodic_task.interval = schedule if field == "interval" else None
    periodic_task.crontab = schedule if field == "crontab" else None
    periodic_task.save()


def describe_schedule(periodic_task):
    """A human label for the schedule. Common shapes read as sentences; anything else falls back to the raw cron fields."""
    if periodic_task is None:
        return _("Not scheduled")

    interval = periodic_task.interval
    if interval is not None:
        every = interval.every
        if interval.period == IntervalSchedule.SECONDS:
            label = ngettext("Every second", "Every %(n)s seconds", every)
        elif interval.period == IntervalSchedule.MINUTES:
            label = ngettext("Every minute", "Every %(n)s minutes", every)
        elif interval.period == IntervalSchedule.HOURS:
            label = ngettext("Every hour", "Every %(n)s hours", every)
        elif interval.period == IntervalSchedule.DAYS:
            label = ngettext("Every day", "Every %(n)s days", every)
        else:
            return f"every {every} {interval.period}"
        return label % {"n": every}

    cron = periodic_task.crontab
    if cron is None:
        return _("Not scheduled")

    minute, hour, dom, moy, dow = cron.minute, cron.hour, cron.day_of_month, cron.month_of_year, cron.day_of_week
    if minute.isdigit() and hour.isdigit() and moy == "*":
        at = f"{int(hour):02d}:{int(minute):02d}"
        if dom == "*" and dow == "*":
            return _("Daily at %(at)s") % {"at": at}
        if dom == "*" and dow in WEEKDAYS:
            return _("Weekly, %(day)s at %(at)s") % {"day": WEEKDAYS[dow], "at": at}
        if dow == "*" and dom.isdigit():
            return _("Monthly, day %(day)s at %(at)s") % {"day": dom, "at": at}
    return f"cron {minute} {hour} {dom} {moy} {dow}"
