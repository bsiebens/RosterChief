"""Quiet hours (notifications.models.QuietHours): when a push should not buzz a phone.

``quiet_until`` answers "is this account in its quiet window right now, and if so when does it
end?" -- the moment a held push is released. Pure time arithmetic in the account's own time zone,
so a window crossing midnight and daylight-saving changes work.
"""

import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.utils import timezone

from .models import QuietHours


def _zone(name: str):
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError, ValueError:
        return ZoneInfo("UTC")


def window_end(hours: QuietHours, now: datetime.datetime | None = None) -> datetime.datetime | None:
    """The end (aware datetime) of the window ``now`` is inside, or None when ``now`` is outside it
    or the window is off/empty."""
    if not hours.enabled or hours.start == hours.end:
        return None
    zone = _zone(hours.timezone)
    local = (now or timezone.now()).astimezone(zone)
    today = local.date()
    start, end = hours.start, hours.end
    if start < end:  # same day, e.g. 13:00-15:00
        if start <= local.time() < end:
            return datetime.datetime.combine(today, end, tzinfo=zone)
        return None
    # across midnight, e.g. 22:00-07:00
    if local.time() >= start:
        return datetime.datetime.combine(today + datetime.timedelta(days=1), end, tzinfo=zone)
    if local.time() < end:
        return datetime.datetime.combine(today, end, tzinfo=zone)
    return None


def quiet_until(user_ids, now: datetime.datetime | None = None) -> dict:
    """``{user_id: end}`` for the accounts of ``user_ids`` that are in their quiet hours right now."""
    user_ids = {user_id for user_id in user_ids if user_id}
    if not user_ids:
        return {}
    now = now or timezone.now()
    result = {}
    for hours in QuietHours.objects.filter(user_id__in=user_ids, enabled=True):
        end = window_end(hours, now)
        if end is not None:
            result[hours.user_id] = end
    return result


def get(user) -> QuietHours | None:
    return QuietHours.objects.filter(user=user).first()


def set_quiet_hours(user, *, enabled: bool, start: datetime.time, end: datetime.time, tz: str) -> QuietHours:
    if tz not in _known_zones():
        raise ValueError(f"unknown time zone: {tz}")
    hours, _created = QuietHours.objects.update_or_create(user=user, defaults={"enabled": enabled, "start": start, "end": end, "timezone": tz})
    return hours


def _known_zones():
    from zoneinfo import available_timezones

    return available_timezones()
