"""Season calendar: which seasons a club should have, derived from its season start and duration settings."""

import calendar
import datetime

from .models import Season

MAX_CREATED = 12  # safety cap per run (a club with very old seasons catches up gradually)


def add_months(day: datetime.date, months: int) -> datetime.date:
    month_index = day.month - 1 + months
    year, month = day.year + month_index // 12, month_index % 12 + 1
    return datetime.date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def anchor(year: int, month: int, day: int) -> datetime.date:
    return datetime.date(year, month, min(day, calendar.monthrange(year, month)[1]))


def season_end(start: datetime.date, duration_months: int) -> datetime.date:
    """Last day of a season that starts on ``start``: the day before the same date ``duration_months`` later."""
    return add_months(start, duration_months) - datetime.timedelta(days=1)


def ensure_seasons(today: datetime.date, *, start_month: int, start_day: int, duration_months: int, lookahead: int = 1) -> list[Season]:
    """Create the missing seasons in the current club schema and return them.

    Ensures a season covers ``today`` and that ``lookahead`` seasons follow it. New seasons chain on the end of the
    latest existing one (so existing data is never overlapped or re-cut); a club without any season starts at the
    latest season-start date that is not after ``today``.
    """
    created: list[Season] = []
    last = Season.objects.order_by("-end_date").first()
    if last is None:
        year = today.year if anchor(today.year, start_month, start_day) <= today else today.year - 1
        next_start = anchor(year, start_month, start_day)
    else:
        next_start = last.end_date + datetime.timedelta(days=1)

    def covered_ahead() -> bool:
        current = Season.objects.filter(start_date__lte=today, end_date__gte=today).first()
        if current is None:
            return False
        return Season.objects.filter(start_date__gt=current.end_date).count() >= lookahead

    while not covered_ahead() and len(created) < MAX_CREATED:
        season = Season.objects.create(start_date=next_start, end_date=season_end(next_start, duration_months))
        created.append(season)
        next_start = season.end_date + datetime.timedelta(days=1)
    return created
