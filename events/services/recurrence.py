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


def occurrence_datetimes(series, until):
    """Expand the series' RRULE from its anchor up to ``until``, minus EXDATEs."""
    rule = rrulestr(series.rrule, dtstart=series.dtstart)
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
    event.end = event.start + series.duration if series.duration is not None else None
    event.gathering = event.start - series.gathering_offset if series.gathering_offset is not None else None
    event.deadline = event.start - series.deadline_offset if series.deadline_offset is not None else None
    event.save()

    event.teams.set(series.teams.all())
    event.invited_members.set(series.invited_members.all())
    event.excluded_members.set(series.excluded_members.all())


def generate_occurrences(series, until=None):
    """Materialise any missing occurrences up to ``until`` (default: horizon)."""
    until = until or horizon()
    existing = set(series.occurrences.values_list("start", flat=True))

    created = []
    for start in occurrence_datetimes(series, until):
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
