"""Materialise concrete Event rows from a recurring EventSeries.

Occurrences are real Event rows (so attendance attaches directly). The series
holds the RRULE, an anchor ``dtstart``, and a set of ``excluded_dates``
(EXDATEs). Rows are generated up to a rolling horizon; a single occurrence can
be cancelled (adds an EXDATE + removes the row) or detached (edited
independently, so series-wide updates skip it).
"""

from datetime import timedelta

from dateutil.rrule import rrulestr
from django.utils import timezone

from events.models import Event

HORIZON_DAYS = 90


def horizon():
    return timezone.now() + timedelta(days=HORIZON_DAYS)


def occurrence_datetimes(series, until, after=None):
    """Expand the series' RRULE from ``after`` (default: its own anchor) up
    to ``until``, minus EXDATEs.

    ``after``, when given, re-anchors the walk at a later point without
    changing the rule's own cadence: dateutil finds the next valid
    occurrence *from* that point on, following the same BYDAY/interval
    pattern, so this safely resumes generation instead of re-walking a
    series' entire history from its original start every time (see
    generate_occurrences' own docstring).

    ``until`` is the generation horizon; the series' own ``until`` (its formal
    end) further caps it, whichever comes first.
    """
    if series.until is not None and series.until < until:
        until = series.until

    rule = rrulestr(series.rrule, dtstart=after or series.dtstart)
    excluded = set(series.excluded_dates)

    result = []
    for occurrence in rule:
        if occurrence > until:
            break
        if occurrence.isoformat() in excluded:
            continue
        result.append(occurrence)
    return result


def apply_template(series, event):
    """Copy the series template (and audience) onto ``event`` and save it.

    Setting the audience M2M fires the sync signal, so attendance follows.
    """
    event.kind = series.kind
    event.title = series.title
    event.location = series.location
    event.opponent = series.opponent
    event.club_wide = series.club_wide
    event.end = event.start + series.duration if series.duration is not None else None
    event.gathering = event.start - series.gathering_offset if series.gathering_offset is not None else None
    event.deadline = event.start - series.deadline_offset if series.deadline_offset is not None else None
    event.save()

    event.teams.set(series.teams.all())
    event.groups.set(series.groups.all())
    event.invited_members.set(series.invited_members.all())
    event.excluded_members.set(series.excluded_members.all())


def generate_occurrences(series, until=None):
    """Materialise any missing occurrences up to ``until`` (default: horizon).

    Resumes from the series' own last-generated occurrence rather than
    re-walking the RRULE from ``series.dtstart`` every time -- for a
    long-running series, that's the difference between expanding a handful
    of new dates each run and re-expanding its entire history every single
    time, forever. Deliberately *not* ``series.generated_until`` as the
    resume point, tempting as that field looks for this: it's an arbitrary
    horizon timestamp, not a genuine occurrence of the rule's own cadence,
    and re-anchoring dateutil's dtstart there breaks phase alignment for
    anything but a bare FREQ=...;INTERVAL=1 rule -- it'll happily hand back
    that arbitrary point itself as a spurious "occurrence" purely because
    it's now the dtstart. The last real occurrence is, by construction,
    already phase-aligned.

    Skipped for a COUNT=-bounded rule: COUNT is counted from the rule's own
    dtstart, not from wherever iteration happens to resume, so re-anchoring
    would silently grant it N *more* occurrences past its original limit.
    Harmless to just walk those from scratch every time instead -- a
    COUNT-bounded rule can never produce more than COUNT occurrences ever,
    so it was never the unbounded, grows-with-the-series'-age case this
    resume logic exists for in the first place.

    ``existing`` re-checks the resume point itself (so re-anchoring there
    doesn't recreate it) as a safety net -- scoped to the resume window, not
    the whole series, so it stays cheap regardless of how old the series is.
    """
    until = until or horizon()
    last_start = series.occurrences.order_by("-start").values_list("start", flat=True).first()
    can_resume = last_start is not None and "COUNT=" not in series.rrule.upper()
    resume_from = last_start if can_resume else series.dtstart
    existing = set(series.occurrences.filter(start__gte=resume_from).values_list("start", flat=True))

    created = []
    for start in occurrence_datetimes(series, until, after=resume_from):
        if start in existing:
            continue
        event = Event(club=series.club, series=series, start=start)
        apply_template(series, event)
        created.append(event)

    series.generated_until = until
    series.save(update_fields=["generated_until"])
    return created


def cancel_occurrence(event, *, hard_delete=True):
    """Drop a single occurrence and record an EXDATE so it isn't regenerated."""
    series = event.series
    if series is not None:
        iso = event.start.isoformat()
        if iso not in series.excluded_dates:
            series.excluded_dates = [*series.excluded_dates, iso]
            series.save(update_fields=["excluded_dates"])

    if hard_delete:
        event.delete()
    else:
        event.cancelled = True
        event.save(update_fields=["cancelled"])


def detach_occurrence(event):
    """Mark an occurrence as edited independently of the series."""
    event.detached = True
    event.save(update_fields=["detached"])


def propagate_series(series):
    """Re-apply the series template to its non-detached future occurrences."""
    now = timezone.now()
    updated = []
    for event in series.occurrences.filter(detached=False, start__gte=now):
        apply_template(series, event)
        updated.append(event)
    return updated
