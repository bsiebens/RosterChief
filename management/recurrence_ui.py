"""A friendly weekly/monthly recurrence picker on top of EventSeries.rrule (a raw
RFC 5545 string, e.g. "FREQ=WEEKLY;INTERVAL=1;BYDAY=MO,WE") -- covers the common
club-scheduling case (every N weeks on these weekdays, or every N months on the
same day of month) without building a full RRULE UI.

dateutil's rrule recurs on dtstart's own day-of-month for FREQ=MONTHLY with no
BYMONTHDAY needed, which is what keeps monthly this cheap to support alongside
weekly.

Anything that isn't exactly one of the two shapes build_rrule() produces --
hand-written, imported, or from a future frequency this module doesn't cover --
is left alone: parse_rrule() returns None, and EventSeriesForm falls back to
showing the raw string in an "Advanced" field instead of the friendly picker.
"""

import re

from django.utils.translation import gettext_lazy as _

WEEKDAY_CODES = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]

WEEKDAY_LABELS = {
    "MO": _("Mon"),
    "TU": _("Tue"),
    "WE": _("Wed"),
    "TH": _("Thu"),
    "FR": _("Fri"),
    "SA": _("Sat"),
    "SU": _("Sun"),
}

WEEKDAY_CHOICES = [(code, WEEKDAY_LABELS[code]) for code in WEEKDAY_CODES]

FREQUENCY_CHOICES = [
    ("weekly", _("Weekly")),
    ("monthly", _("Monthly")),
]

_WEEKLY_RE = re.compile(r"^FREQ=WEEKLY;INTERVAL=(\d+);BYDAY=([A-Z,]+)$")
_MONTHLY_RE = re.compile(r"^FREQ=MONTHLY;INTERVAL=(\d+)$")


def build_rrule(frequency, interval, weekdays=None):
    """Compose an RRULE string from the friendly fields. ``weekdays`` is a list
    of two-letter codes (e.g. ["MO", "WE"]), required for "weekly", ignored for
    "monthly"."""
    interval = interval or 1
    if frequency == "weekly":
        # Preserve WEEKDAY_CODES order regardless of the order the caller passed
        # them in, so the same pattern always serializes to the same string.
        ordered = [code for code in WEEKDAY_CODES if code in (weekdays or [])]
        return f"FREQ=WEEKLY;INTERVAL={interval};BYDAY={','.join(ordered)}"
    if frequency == "monthly":
        return f"FREQ=MONTHLY;INTERVAL={interval}"
    raise ValueError(f"Unknown frequency: {frequency!r}")


def parse_rrule(rrule):
    """The friendly fields that produced ``rrule``, or None if it doesn't match
    one of build_rrule()'s two shapes."""
    if match := _WEEKLY_RE.match(rrule):
        interval, days = match.groups()
        return {"frequency": "weekly", "interval": int(interval), "weekdays": days.split(",")}
    if match := _MONTHLY_RE.match(rrule):
        return {"frequency": "monthly", "interval": int(match.group(1)), "weekdays": []}
    return None


def describe_rrule(rrule):
    """A human-readable summary for list/detail pages, e.g. "Every week on Mon,
    Wed" or "Every 2 months" -- or the raw string verbatim if it isn't one of
    ours (an imported/hand-written RRULE)."""
    parsed = parse_rrule(rrule)
    if parsed is None:
        return rrule

    interval = parsed["interval"]
    if parsed["frequency"] == "weekly":
        days = ", ".join(str(WEEKDAY_LABELS[code]) for code in parsed["weekdays"] if code in WEEKDAY_LABELS)
        if not days:
            return _("Every week") if interval == 1 else _("Every %(n)d weeks") % {"n": interval}
        return _("Every week on %(days)s") % {"days": days} if interval == 1 else _("Every %(n)d weeks on %(days)s") % {"n": interval, "days": days}

    return _("Every month") if interval == 1 else _("Every %(n)d months") % {"n": interval}
