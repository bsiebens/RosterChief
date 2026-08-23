"""Celery task behind the `generate-seasons` beat schedule entry (see
rosterchief/settings.CELERY_BEAT_SCHEDULE and features/jobs.py).

Mirrors `manage.py generate_seasons`'s default behaviour (generate, not --resync) exactly --
that command still exists, unchanged, for manual use from a shell, including --resync, which
this task deliberately does not run unattended (see club/management/commands/generate_seasons.py:
--resync can delete rows, so it isn't something a beat schedule should do on its own).
"""

from celery import shared_task
from dateutil.relativedelta import relativedelta
from django.utils import timezone

from club.models import Club
from club.services.seasons import generate_seasons as generate_seasons_for_club
from features.models import JobToggle, Maintenance

#: How far ahead to generate, matching the management command's own default.
YEARS_AHEAD = 2


@shared_task(name="club.tasks.generate_seasons")
def generate_seasons():
    if Maintenance.is_on():
        raise RuntimeError("Platform is in maintenance mode; this job stood down.")
    if not JobToggle.is_enabled("club.tasks.generate_seasons"):
        raise RuntimeError("This job is disabled in the control panel.")

    until = timezone.localdate() + relativedelta(years=YEARS_AHEAD)
    clubs = Club.objects.active()

    total = 0
    for club in clubs:
        total += len(generate_seasons_for_club(club, until))

    return f"Generated {total} season(s) across {clubs.count()} club(s)."
