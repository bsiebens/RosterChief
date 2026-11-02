"""Who wants what. No row means on; the most specific scope wins: the club's row, then the all-clubs row (``club`` empty).
A master switch ("all push" / "all email") sits on top of the category switches. Quiet hours and the email mode live here too."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.db.models import Q
from django.utils import timezone

from .models import Category, Channel, MasterSwitch, NotificationPreference, PushDevice, PushSettings, QuietHours, UserNotificationSettings

EmailMode = UserNotificationSettings.EmailMode


def _club_id(club):
    return getattr(club, "pk", club)


def _scope(club_id) -> Q:
    return Q(club_id=club_id) | Q(club__isnull=True) if club_id else Q(club__isnull=True)


def _resolve(rows, club_id) -> bool:
    """``rows`` already filtered to the user, channel (and category): club row > all-clubs row > on."""
    by_scope = {row.club_id: row.enabled for row in rows}
    if club_id is not None and club_id in by_scope:
        return by_scope[club_id]
    return by_scope.get(None, True)


def master_enabled(user, channel: str, club=None) -> bool:
    club_id = _club_id(club)
    rows = MasterSwitch.objects.filter(_scope(club_id), user=user, channel=channel)
    return _resolve(rows, club_id)


def is_enabled(user, category: str, channel: str, club=None) -> bool:
    """Whether ``user`` wants ``category`` on ``channel`` ("push" or "email") for ``club`` (None = no particular club)."""
    if channel not in Channel.values:
        raise ValueError(f"unknown channel: {channel}")
    if not master_enabled(user, channel, club):
        return False
    club_id = _club_id(club)
    rows = NotificationPreference.objects.filter(_scope(club_id), user=user, category=category, channel=channel)
    return _resolve(rows, club_id)


def set_preference(user, category: str, channel: str, enabled: bool, club=None) -> NotificationPreference:
    if category not in Category.values or channel not in Channel.values:
        raise ValueError(f"unknown category/channel: {category}/{channel}")
    row, _created = NotificationPreference.objects.update_or_create(user=user, club=club, category=category, channel=channel, defaults={"enabled": enabled})
    return row


def set_master_switch(user, channel: str, enabled: bool, club=None) -> MasterSwitch:
    if channel not in Channel.values:
        raise ValueError(f"unknown channel: {channel}")
    row, _created = MasterSwitch.objects.update_or_create(user=user, club=club, channel=channel, defaults={"enabled": enabled})
    return row


# --- email mode -------------------------------------------------------------------------------------------------------------


def get_email_mode(user) -> str:
    row = UserNotificationSettings.objects.filter(user=user).first()
    return row.email_mode if row else EmailMode.ALWAYS.value


def set_email_mode(user, mode: str) -> UserNotificationSettings:
    if mode not in EmailMode.values:
        raise ValueError(f"unknown email mode: {mode}")
    row, _created = UserNotificationSettings.objects.update_or_create(user=user, defaults={"email_mode": mode})
    return row


def has_active_app(user, now=None) -> bool:
    """An enabled native device seen within ``PushSettings.stale_device_days``: the app can notify this account."""
    now = now or timezone.now()
    cutoff = now - timedelta(days=PushSettings.get().stale_device_days)
    return PushDevice.objects.filter(user=user, enabled=True, last_seen__gte=cutoff).exists()


def should_email(user, category: str, club=None, now=None) -> bool:
    """Email switches, then the email mode: ``unless_app`` skips email while the app can reach the account."""
    if not is_enabled(user, category, Channel.EMAIL, club):
        return False
    return not (get_email_mode(user) == EmailMode.UNLESS_APP and has_active_app(user, now))


# --- quiet hours --------------------------------------------------------------------------------------------------------------


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError, ValueError:
        return ZoneInfo("UTC")


def set_quiet_hours(user, start, end, timezone_name: str = "Europe/Brussels", enabled: bool = True) -> QuietHours:
    if start == end:
        raise ValueError("The window can't be empty.")
    try:
        ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ValueError(f"unknown time zone: {timezone_name}") from error
    row, _created = QuietHours.objects.update_or_create(user=user, defaults={"start": start, "end": end, "timezone": timezone_name, "enabled": enabled})
    return row


def clear_quiet_hours(user) -> bool:
    count, _deleted = QuietHours.objects.filter(user=user).delete()
    return bool(count)


def quiet_state(user, now=None) -> tuple[bool, datetime | None]:
    """``(active, until)``: whether ``now`` is inside the account's quiet hours, and when the window ends (an aware datetime).
    The window may cross midnight and is evaluated in its own time zone."""
    row = QuietHours.objects.filter(user=user, enabled=True).first()
    if row is None:
        return False, None
    zone = _zone(row.timezone)
    local = (now or timezone.now()).astimezone(zone)
    time_now = local.timetz().replace(tzinfo=None)
    if row.start < row.end:
        active = row.start <= time_now < row.end
        end_day = local.date()
    else:  # crosses midnight
        active = time_now >= row.start or time_now < row.end
        end_day = local.date() + timedelta(days=1) if time_now >= row.start else local.date()
    if not active:
        return False, None
    return True, datetime.combine(end_day, row.end, tzinfo=zone)
