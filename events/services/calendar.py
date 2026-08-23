"""Date-range math and grid layout for the Events page's Week/Month/Season calendar
views (management/views.py's EventListView, template event_list.html). Kept separate
from the view itself since none of this touches the request/queryset-scoping layer --
it only turns "an anchor date + a list of already-visible events" into the shapes each
template needs.

Three grids, one per granularity:
- week_grid: a 7-day x hour-gutter layout with absolutely-positioned blocks (top/height
  as a percentage of the visible hour span), including simple side-by-side column
  layout for events that overlap in time on the same day.
- month_grid: a standard 6-row calendar grid, each day carrying its own event list
  (title + kind, no time-of-day positioning -- there's no room for it at that scale).
- season_grid: one month_grid per month spanning the season, but day cells carry only
  a count (a full title list is illegible at that scale) -- see D5-alike "Season" view.
"""

import calendar as _calendar
import datetime
import itertools

from django.utils import timezone

#: The week grid's visible hours -- the full day, always, so nothing is ever
#: scrolled out of view regardless of what time events happen to fall at (this
#: used to default to a narrower 08:00-22:00 window, only widening for events
#: outside it -- a very early or very late one still left the grid clipped at
#: the other end, e.g. midnight..22:00 rather than the full day).
DEFAULT_DAY_START_HOUR = 0
DEFAULT_DAY_END_HOUR = 24

#: Floor on a block's rendered height, in percent of the visible hour span -- a very
#: short event (a 15-minute weigh-in) would otherwise render as a sliver too thin to
#: click or read.
MIN_BLOCK_HEIGHT_PCT = 4.0


def week_bounds(anchor: datetime.date) -> tuple[datetime.date, datetime.date]:
    """Monday..Sunday of the week containing ``anchor``."""
    start = anchor - datetime.timedelta(days=anchor.weekday())
    return start, start + datetime.timedelta(days=6)


def month_bounds(anchor: datetime.date) -> tuple[datetime.date, datetime.date]:
    """First..last day of the month containing ``anchor``."""
    last_day = _calendar.monthrange(anchor.year, anchor.month)[1]
    return anchor.replace(day=1), anchor.replace(day=last_day)


def add_months(anchor: datetime.date, months: int) -> datetime.date:
    """``anchor`` shifted by whole months, clamped to day 1 -- only ever used to
    step between month-starts (month_bounds/season month list), so the
    day-of-month is never meaningful to preserve."""
    month_index = anchor.month - 1 + months
    year = anchor.year + month_index // 12
    month = month_index % 12 + 1
    return datetime.date(year, month, 1)


def agenda_groups(items, *, start_of=lambda item: item.start, show_past=False, today=None):
    """Group already start-sorted ``items`` the way every agenda-style
    schedule in this app presents itself: "This week"/"Next week", then
    everything further out under its own month divider -- shared by
    mobile.views.CalendarView (the member app's own Calendar), management.
    views.EventListView (the desktop's "List" view), and mobile.coach_views.
    CoachScheduleView (the coach app's "Schedule"), so the three stay
    behaviourally identical rather than three hand-rolled copies of the same
    grouping drifting apart over time.

    ``start_of`` extracts an item's start datetime -- the default assumes a
    bare ``Event``-like object; pass a lambda for anything else (e.g.
    CalendarView's own rows, each a dict wrapping one).

    ``show_past`` is for a caller already querying in descending order (most
    recent first) -- "This week"/"Next week" only make sense for what's
    ahead, so past mode skips that split and groups straight into months
    instead, newest month first, oldest last.

    Returns ``(this_week, next_week, later_months)`` -- ``later_months`` is
    ``[{"month_start": date, "items": [...]}, ...]``; ``this_week``/
    ``next_week`` are always ``[]`` in show_past mode, with everything
    carried in ``later_months`` instead.
    """
    if not items:
        return [], [], []

    def _month_start(item):
        return timezone.localtime(start_of(item)).date().replace(day=1)

    if show_past:
        months = [{"month_start": month_start, "items": list(month_items)} for month_start, month_items in itertools.groupby(items, key=_month_start)]
        return [], [], months

    today = today or timezone.localdate()
    _this_week_start, this_week_end = week_bounds(today)
    next_week_end = this_week_end + datetime.timedelta(days=7)

    this_week, next_week, later = [], [], []
    for item in items:
        item_date = timezone.localtime(start_of(item)).date()
        if item_date <= this_week_end:
            this_week.append(item)
        elif item_date <= next_week_end:
            next_week.append(item)
        else:
            later.append(item)

    later_months = [{"month_start": month_start, "items": list(month_items)} for month_start, month_items in itertools.groupby(later, key=_month_start)]
    return this_week, next_week, later_months


def _local_span(event) -> tuple[datetime.datetime, datetime.datetime]:
    """An event's start/end in local time, end defaulting to +1h when unset
    (mirrors the "assumed duration" read-time fallback events.models.Event
    documents for non-GAME kinds -- this is a display concern, so it doesn't
    touch the stored field)."""
    start = timezone.localtime(event.start)
    end = timezone.localtime(event.end) if event.end else start + datetime.timedelta(hours=1)
    if end <= start:
        end = start + datetime.timedelta(hours=1)
    return start, end


def _assign_columns(blocks: list[dict]) -> None:
    """Side-by-side layout for same-day events that overlap in time -- sets
    ``left_pct``/``width_pct`` on each block dict in place. Greedy interval
    scheduling: sort by start, hand each event the lowest-numbered column
    whose previous occupant has already ended, and once a run of mutually
    overlapping events (a "cluster") is fully placed, every block in it shares
    that cluster's column count as its width divisor -- otherwise an event
    that only overlaps one neighbour would render at 1/3 width just because
    the *neighbour* also overlaps something else further along."""
    blocks.sort(key=lambda b: (b["start"], b["end"]))
    columns: list[datetime.datetime] = []  # end time currently occupying each column
    cluster: list[dict] = []
    cluster_end = None

    def flush(cluster_blocks):
        if not cluster_blocks:
            return
        width = max(b["column"] for b in cluster_blocks) + 1
        for b in cluster_blocks:
            b["width_pct"] = round(100 / width, 2)
            b["left_pct"] = round(b["column"] * 100 / width, 2)

    for block in blocks:
        if cluster_end is not None and block["start"] >= cluster_end:
            flush(cluster)
            cluster, columns, cluster_end = [], [], None

        placed = False
        for index, occupied_until in enumerate(columns):
            if block["start"] >= occupied_until:
                columns[index] = block["end"]
                block["column"] = index
                placed = True
                break
        if not placed:
            columns.append(block["end"])
            block["column"] = len(columns) - 1

        cluster.append(block)
        cluster_end = max(cluster_end, block["end"]) if cluster_end else block["end"]

    flush(cluster)


def week_grid(events, week_start: datetime.date) -> dict:
    """``events`` (already club/visibility/season-scoped) laid out across the
    Monday..Sunday week starting ``week_start``. Returns the hour gutter
    bounds plus, per day, a list of blocks each carrying top/height/left/width
    percentages for absolute positioning against a single shared-height grid."""
    week_end = week_start + datetime.timedelta(days=6)
    day_start_hour, day_end_hour = DEFAULT_DAY_START_HOUR, DEFAULT_DAY_END_HOUR
    by_day: dict[datetime.date, list] = {week_start + datetime.timedelta(days=i): [] for i in range(7)}

    spans = []
    for event in events:
        start, end = _local_span(event)
        if not (week_start <= start.date() <= week_end):
            continue
        spans.append((event, start, end))
        day_start_hour = min(day_start_hour, start.hour)
        end_hour_frac = end.hour + end.minute / 60 + (1 if end.second or end.microsecond else 0)
        day_end_hour = max(day_end_hour, int(end_hour_frac) + (1 if end_hour_frac % 1 else 0))

    span_hours = max(day_end_hour - day_start_hour, 1)
    for event, start, end in spans:
        start_frac = max(0.0, (start.hour + start.minute / 60) - day_start_hour)
        end_frac = min(float(span_hours), (end.hour + end.minute / 60) - day_start_hour)
        by_day[start.date()].append(
            {
                "event": event,
                "start": start,
                "end": end,
                "top_pct": round(100 * start_frac / span_hours, 2),
                "height_pct": max(round(100 * (end_frac - start_frac) / span_hours, 2), MIN_BLOCK_HEIGHT_PCT),
            }
        )

    days = []
    for i in range(7):
        day = week_start + datetime.timedelta(days=i)
        blocks = by_day[day]
        _assign_columns(blocks)
        days.append({"date": day, "is_today": day == timezone.localdate(), "blocks": blocks})

    return {
        "week_start": week_start,
        "week_end": week_end,
        "days": days,
        "hours": list(range(day_start_hour, day_end_hour + 1)),
        "day_start_hour": day_start_hour,
        "day_end_hour": day_end_hour,
        # The grid always spans the full day (see DEFAULT_DAY_START_HOUR/END_HOUR's
        # own comment -- never clipped), so a plain "top of the grid" scroll position
        # would default to an empty 00:00 view most weeks. The template scrolls its
        # bounded viewport to just before this hour instead -- None when the week has
        # no events at all, since there's nothing to reveal either way.
        "first_event_hour": min((start.hour for _event, start, _end in spans), default=None),
    }


def month_grid(events, anchor: datetime.date) -> dict:
    """A standard calendar grid (always full weeks, so always a multiple of 7
    cells) for the month containing ``anchor``, each day carrying the events
    that start on it. Cells outside the month stay in the grid (so the week
    rows line up) but are flagged ``in_month=False`` for the template to dim."""
    month_start, month_end = month_bounds(anchor)
    grid_start = month_start - datetime.timedelta(days=month_start.weekday())
    weeks_needed = -(-((month_end - grid_start).days + 1) // 7)  # ceil div
    grid_end = grid_start + datetime.timedelta(days=weeks_needed * 7 - 1)

    by_day: dict[datetime.date, list] = {}
    for event in events:
        start, _end = _local_span(event)
        day = start.date()
        if grid_start <= day <= grid_end:
            by_day.setdefault(day, []).append(event)

    today = timezone.localdate()
    weeks = []
    day = grid_start
    while day <= grid_end:
        week = []
        for _ in range(7):
            week.append(
                {
                    "date": day,
                    "in_month": day.month == anchor.month,
                    "is_today": day == today,
                    "events": by_day.get(day, []),
                }
            )
            day += datetime.timedelta(days=1)
        weeks.append(week)

    return {"month_start": month_start, "month_end": month_end, "weeks": weeks}


def season_grid(events, season) -> list[dict]:
    """One compact month_grid per month spanning ``season``, day cells
    trimmed to just a count (see module docstring) -- events are split up
    front by month so each month's grid only scans its own slice, not the
    whole season's list."""
    events_by_month: dict[tuple[int, int], list] = {}
    for event in events:
        start, _end = _local_span(event)
        events_by_month.setdefault((start.year, start.month), []).append(event)

    months = []
    cursor = season.start_date.replace(day=1)
    end_month = season.end_date.replace(day=1)
    while cursor <= end_month:
        grid = month_grid(events_by_month.get((cursor.year, cursor.month), []), cursor)
        for week in grid["weeks"]:
            for cell in week:
                cell["count"] = len(cell["events"])
                del cell["events"]
        months.append({"label": cursor, "weeks": grid["weeks"]})
        cursor = add_months(cursor, 1)

    return months
