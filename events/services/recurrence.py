"""Recurring event series: RRULE helpers and materialising concrete Event rows from an EventSeries.

Occurrences are real Event rows (attendance attaches to them directly); there are no virtual occurrences. A series holds
an RFC 5545 RRULE, an anchor ``dtstart``, an optional ``until`` and ``excluded_dates`` (EXDATEs, UTC ISO strings). Rows
are generated up to a rolling horizon (``HORIZON_DAYS`` ahead) by ``generate_occurrences``, run on create/update and
daily by the ``events.extend_event_series`` task. ``generated_until`` is an invariant: every slot up to it has already
been handled (generated, excluded or deliberately removed), so generation never re-creates anything before it.

Rules are expanded in the club's local time zone (settings.TIME_ZONE), so a 19:00 training stays at 19:00 across a DST
change (the old app expanded in UTC and drifted by an hour).

A single occurrence can be cancelled (EXDATE + row removed or flagged) or detached (edited on its own; series-wide
updates and regeneration skip it). The friendly recurrence input (daily/weekly/monthly, every N, weekdays, COUNT) is
built into and parsed back from the RRULE string here too (port of the old management/recurrence_ui.py).
"""

import datetime
import logging
import re

from dateutil.rrule import rrulestr
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from events.models import Event, EventSeries, Season

logger = logging.getLogger(__name__)

HORIZON_DAYS = 90

WEEKDAY_CODES = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
WEEKDAY_LABELS = {
    "MO": gettext_lazy("Mon"),
    "TU": gettext_lazy("Tue"),
    "WE": gettext_lazy("Wed"),
    "TH": gettext_lazy("Thu"),
    "FR": gettext_lazy("Fri"),
    "SA": gettext_lazy("Sat"),
    "SU": gettext_lazy("Sun"),
}
FREQUENCIES = ("daily", "weekly", "monthly")

_FRIENDLY_RE = re.compile(r"^FREQ=(DAILY|WEEKLY|MONTHLY);INTERVAL=(\d+)(?:;BYDAY=([A-Z,]+))?(?:;COUNT=(\d+))?$")


# -- the friendly recurrence input <-> RRULE --


def build_rrule(frequency: str, interval: int = 1, weekdays=None, count: int | None = None) -> str:
    """RRULE for the friendly fields. ``weekdays`` (two-letter codes) is used for "weekly" only, normalised to
    Monday..Sunday order so the same pattern always gives the same string."""
    interval = interval or 1
    if frequency not in FREQUENCIES:
        raise ValueError(f"Unknown frequency: {frequency!r}")
    rule = f"FREQ={frequency.upper()};INTERVAL={interval}"
    if frequency == "weekly":
        ordered = [code for code in WEEKDAY_CODES if code in (weekdays or [])]
        rule += f";BYDAY={','.join(ordered)}"
    if count:
        rule += f";COUNT={count}"
    return rule


def parse_rrule(rrule: str) -> dict | None:
    """The friendly fields that produced ``rrule`` (``{"frequency", "interval", "weekdays", "count"}``), or None if it
    is not one of build_rrule's shapes (hand-written / imported rules are edited as raw text)."""
    match = _FRIENDLY_RE.match((rrule or "").strip())
    if not match:
        return None
    freq, interval, days, count = match.groups()
    frequency = freq.lower()
    if (frequency == "weekly") != bool(days):
        return None
    weekdays = days.split(",") if days else []
    if any(code not in WEEKDAY_CODES for code in weekdays):
        return None
    return {"frequency": frequency, "interval": int(interval), "weekdays": weekdays, "count": int(count) if count else None}


def describe_rrule(rrule: str) -> str:
    """Human summary such as "Every week on Mon, Wed" or "Every 2 months, 10 times"; the raw rule if not one of ours."""
    parsed = parse_rrule(rrule)
    if parsed is None:
        return rrule
    n = parsed["interval"]
    if parsed["frequency"] == "daily":
        text = _("Every day") if n == 1 else _("Every %(n)d days") % {"n": n}
    elif parsed["frequency"] == "weekly":
        days = ", ".join(str(WEEKDAY_LABELS[code]) for code in parsed["weekdays"])
        text = _("Every week on %(days)s") % {"days": days} if n == 1 else _("Every %(n)d weeks on %(days)s") % {"n": n, "days": days}
    else:
        text = _("Every month") if n == 1 else _("Every %(n)d months") % {"n": n}
    if parsed["count"]:
        text += ", " + (_("%(n)d times") % {"n": parsed["count"]})
    return text


def describe_recurrence(series: EventSeries) -> str:
    """describe_rrule plus the series' end date, e.g. "Every week on Tue, until 30/06/2027"."""
    text = describe_rrule(series.rrule)
    if series.until is not None:
        text += ", " + (_("until %(date)s") % {"date": timezone.localtime(series.until).date().strftime("%d/%m/%Y")})
    return text


def recurrence_values(series: EventSeries) -> dict:
    """The editing shape of a series' recurrence: ``{"frequency", "interval", "weekdays", "count", "until", "rrule",
    "friendly"}``. ``friendly`` is False for a raw rule the picker cannot show (frequency etc. are then None)."""
    parsed = parse_rrule(series.rrule)
    until = timezone.localtime(series.until).date() if series.until is not None else None
    if parsed is None:
        return {"frequency": None, "interval": None, "weekdays": [], "count": None, "until": until, "rrule": series.rrule, "friendly": False}
    return {**parsed, "until": until, "rrule": series.rrule, "friendly": True}


def end_of_day(day: datetime.date) -> datetime.datetime:
    """The last instant of ``day`` in local time (a series ``until`` given as a date)."""
    return timezone.make_aware(datetime.datetime.combine(day, datetime.time.max))


def make_rule(rrule: str, dtstart: datetime.datetime):
    """dateutil rule for ``rrule`` anchored at ``dtstart`` (localised). Raises ValueError for a bad rule."""
    rule = rrulestr(rrule, dtstart=timezone.localtime(dtstart))
    if not hasattr(rule, "_freq"):  # an rruleset: several lines / RDATEs are not supported
        raise ValueError("Only a single RRULE is supported.")
    return rule


# -- expansion and generation --


def horizon(now: datetime.datetime | None = None) -> datetime.datetime:
    return (now or timezone.now()) + datetime.timedelta(days=HORIZON_DAYS)


def iso(dt: datetime.datetime) -> str:
    """The canonical EXDATE form of an occurrence start (UTC ISO)."""
    return dt.astimezone(datetime.UTC).isoformat()


def excluded_set(series: EventSeries) -> set[str]:
    out = set()
    for value in series.excluded_dates or []:
        try:
            out.add(iso(datetime.datetime.fromisoformat(value)))
        except TypeError, ValueError:
            logger.warning("Ignoring malformed EXDATE %r on series %s", value, series.pk)
    return out


def occurrence_datetimes(series: EventSeries, until: datetime.datetime, after: datetime.datetime | None = None) -> list[datetime.datetime]:
    """The rule's occurrences from ``after`` (default: the series' own dtstart) up to ``until``, capped by the series'
    own ``until``, minus EXDATEs. ``after`` re-anchors the walk at an occurrence already known to be phase-aligned."""
    if series.until is not None and series.until < until:
        until = series.until
    rule = make_rule(series.rrule, after or series.dtstart)
    excluded = excluded_set(series)
    result = []
    for occurrence in rule:
        if occurrence > until:
            break
        if iso(occurrence) not in excluded:
            result.append(occurrence)
    return result


def _audience(series: EventSeries) -> dict:
    return {
        "teams": list(series.teams.all()),
        "groups": list(series.groups.all()),
        "invited_members": list(series.invited_members.all()),
        "excluded_members": list(series.excluded_members.all()),
    }


def apply_template(series: EventSeries, event: Event, audience: dict | None = None) -> None:
    """Copy the series template (fields and audience) onto ``event`` and save it. The audience goes through the m2m
    ``.set()`` so the attendance-sync signals fire by themselves."""
    event.series = series
    event.kind = series.kind
    event.title = series.title
    event.location_id = series.location_id
    event.opponent_id = series.opponent_id
    event.club_wide = series.club_wide
    event.end = event.start + series.duration if series.duration is not None else None
    event.gathering = event.start - series.gathering_offset if series.gathering_offset is not None else None
    event.deadline = event.start - series.deadline_offset if series.deadline_offset is not None else None
    event.season = Season.get_current(timezone.localtime(event.start).date())
    event.save()
    audience = audience if audience is not None else _audience(series)
    for name, objs in audience.items():
        getattr(event, name).set(objs)


def generate_occurrences(series: EventSeries, until: datetime.datetime | None = None, *, since: datetime.datetime | None = None, resume: bool = True) -> list[Event]:
    """Create the missing occurrences up to ``until`` (default: the horizon) and move ``generated_until`` there.

    Only slots after the previous ``generated_until`` are created (or, with ``since``, slots at/after ``since``: used
    by regenerate_series after a rule change), so a deleted or moved occurrence never comes back.

    The walk resumes from the last non-detached occurrence instead of re-expanding the whole history (old behaviour),
    but only when that start is still phase-aligned (same local time of day as dtstart) and the rule has no COUNT
    (COUNT counts from dtstart, so resuming would grant extra occurrences).
    """
    until = until or horizon()
    if series.until is not None and series.generated_until is not None and series.generated_until >= min(until, series.until):
        return []
    anchor = series.occurrences.filter(detached=False).order_by("-start").values_list("start", flat=True).first()
    can_resume = resume and anchor is not None and anchor >= series.dtstart and "COUNT=" not in series.rrule.upper() and timezone.localtime(anchor).time() == timezone.localtime(series.dtstart).time()
    resume_from = anchor if can_resume else series.dtstart
    floor = since
    floor_exclusive = series.generated_until if since is None else None
    existing = set(series.occurrences.filter(start__gte=resume_from).values_list("start", flat=True))

    created = []
    audience = None
    for start in occurrence_datetimes(series, until, after=resume_from):
        if start in existing:
            continue
        if floor is not None and start < floor:
            continue
        if floor_exclusive is not None and start <= floor_exclusive:
            continue
        if audience is None:
            audience = _audience(series)
        event = Event(series=series, start=start)
        apply_template(series, event, audience)
        created.append(event)

    if series.generated_until is None or until > series.generated_until:
        series.generated_until = until
        series.save(update_fields=["generated_until", "modified"])
    return created


def exclude_date(series: EventSeries, start: datetime.datetime) -> None:
    """Add ``start`` to the series' EXDATEs (idempotent)."""
    value = iso(start)
    if value not in excluded_set(series):
        series.excluded_dates = [*series.excluded_dates, value]
        series.save(update_fields=["excluded_dates", "modified"])


def cancel_occurrence(event: Event, *, hard_delete: bool = True) -> None:
    """Drop one occurrence: EXDATE so it is never regenerated, then delete the row (or only flag it cancelled)."""
    if event.series_id is not None:
        exclude_date(event.series, event.start)
    if hard_delete:
        event.delete()
    else:
        event.cancelled = True
        event.save(update_fields=["cancelled", "modified"])


def detach_occurrence(event: Event) -> None:
    """Mark an occurrence as edited independently of the series."""
    if not event.detached:
        event.detached = True
        event.save(update_fields=["detached", "modified"])


def propagate_series(series: EventSeries, *, since: datetime.datetime | None = None) -> list[Event]:
    """Re-apply the series template to its non-detached occurrences starting at/after ``since`` (default: now)."""
    since = since or timezone.now()
    audience = _audience(series)
    updated = []
    for event in series.occurrences.filter(detached=False, start__gte=since).order_by("start"):
        apply_template(series, event, audience)
        updated.append(event)
    return updated


def _with_time(dt: datetime.datetime, new_time: datetime.time) -> datetime.datetime:
    return timezone.make_aware(datetime.datetime.combine(timezone.localtime(dt).date(), new_time))


def shift_time_of_day(series: EventSeries, new_time: datetime.time, *, since: datetime.datetime | None = None) -> None:
    """Move the series to another (local) time of day, keeping the dates: dtstart, EXDATEs and the non-detached
    occurrences from ``since`` (default now) are moved in place, so their attendance is kept. Call
    propagate_series afterwards to recompute end / gathering / deadline."""
    since = since or timezone.now()
    new_time = new_time.replace(tzinfo=None)
    series.dtstart = _with_time(series.dtstart, new_time)
    series.excluded_dates = [iso(_with_time(datetime.datetime.fromisoformat(v), new_time)) for v in series.excluded_dates]
    series.save(update_fields=["dtstart", "excluded_dates", "modified"])
    for event in series.occurrences.filter(detached=False, start__gte=since):
        event.start = _with_time(event.start, new_time)
        event.save(update_fields=["start", "modified"])


def regenerate_series(series: EventSeries, *, now: datetime.datetime | None = None) -> tuple[int, list[Event]]:
    """After a recurrence change (rule, first date, end): remove future non-detached occurrences the rule no longer
    produces and create the new ones from now on. Past and detached occurrences are kept. Returns (removed, created).

    (The old app left stale occurrences for a manual cancel each; future occurrences are removed here instead so the
    calendar never shows both the old and the new pattern.)"""
    now = now or timezone.now()
    until = max(horizon(now), series.generated_until or now)
    valid = set(occurrence_datetimes(series, until))
    stale = [e.pk for e in series.occurrences.filter(detached=False, start__gte=now) if e.start not in valid]
    removed = Event.objects.filter(pk__in=stale).delete()[1].get("events.Event", 0) if stale else 0
    created = generate_occurrences(series, until, since=now, resume=False)
    return removed, created


def split_series(series: EventSeries, at: datetime.datetime) -> EventSeries:
    """Split ``series`` at ``at`` ("this and following"): the original ends just before ``at``; a copy (same template,
    audience, rule) starts at the first rule slot at/after ``at`` and takes over every occurrence (detached ones too)
    and EXDATE from ``at`` on. A COUNT is reduced by the slots that stay with the original. Returns the new series."""
    rule = make_rule(series.rrule, series.dtstart)
    first_slot = rule.after(timezone.localtime(at) - datetime.timedelta(microseconds=1), inc=False)
    used = 0
    for slot in rule:
        if slot >= at:
            break
        used += 1
    rrule = series.rrule
    if match := re.search(r"COUNT=(\d+)", rrule, flags=re.IGNORECASE):
        rrule = rrule[: match.start(1)] + str(max(int(match.group(1)) - used, 1)) + rrule[match.end(1) :]

    excluded = [v for v in series.excluded_dates if datetime.datetime.fromisoformat(v) >= at]
    new = EventSeries.objects.create(
        rrule=rrule,
        dtstart=first_slot or at,
        until=series.until,
        duration=series.duration,
        gathering_offset=series.gathering_offset,
        deadline_offset=series.deadline_offset,
        excluded_dates=excluded,
        generated_until=series.generated_until,
        kind=series.kind,
        title=series.title,
        location_id=series.location_id,
        opponent_id=series.opponent_id,
        club_wide=series.club_wide,
    )
    for name, objs in _audience(series).items():
        getattr(new, name).set(objs)
    Event.objects.filter(series=series, start__gte=at).update(series=new)

    series.until = at - datetime.timedelta(microseconds=1)
    series.excluded_dates = [v for v in series.excluded_dates if datetime.datetime.fromisoformat(v) < at]
    series.save(update_fields=["until", "excluded_dates", "modified"])
    return new


def extend_all_series(until: datetime.datetime | None = None) -> tuple[int, int]:
    """Generate occurrences up to the horizon for every series of the current schema. Returns (series, created)."""
    until = until or horizon()
    series_count = created = 0
    for series in EventSeries.objects.iterator(chunk_size=200):
        series_count += 1
        try:
            created += len(generate_occurrences(series, until))
        except ValueError:  # a broken (imported / hand-written) rule must not stop the others
            logger.exception("Could not expand series %s (%r)", series.pk, series.rrule)
    return series_count, created
