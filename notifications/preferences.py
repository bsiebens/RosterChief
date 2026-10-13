"""Who wants what: the per-account switches (notifications.models.NotificationPreference) behind
"don't push me news" / "don't email me reminders".

The rule everything shares: **no row means on**; a club-specific row beats the all-clubs row
(``club=None``). Channels ask ``users_with_disabled`` once per batch, never per recipient.
"""

from django.db.models import Q

from .models import NotificationCategory, NotificationPreference

Channel = NotificationPreference.Channel


def users_with_disabled(user_ids, club, category: str, channel: str) -> set:
    """Of ``user_ids``, the accounts that switched ``category`` off on ``channel`` for ``club``."""
    user_ids = {user_id for user_id in user_ids if user_id}
    if not user_ids:
        return set()
    club_id = getattr(club, "pk", club)
    # ``club_id__in=[x, None]`` would never match the NULL (all-clubs) rows in SQL.
    rows = NotificationPreference.objects.filter(user_id__in=user_ids, category=category, channel=channel).filter(Q(club_id=club_id) | Q(club__isnull=True) if club_id else Q(club__isnull=True))
    decided: dict = {}
    for row in rows:
        # A club row wins over the all-clubs row, whatever order they arrive in.
        if row.user_id not in decided or row.club_id is not None:
            decided[row.user_id] = row.enabled
    return {user_id for user_id, enabled in decided.items() if not enabled}


def is_enabled(user, club, category: str, channel: str) -> bool:
    return user.pk not in users_with_disabled([user.pk], club, category, channel)


def matrix(user, club=None) -> dict:
    """``{category: {channel: bool}}`` as ``user`` has it for ``club`` (``None`` = the all-clubs defaults)."""
    result = {category: dict.fromkeys(Channel.values, True) for category in NotificationCategory.values}
    rows = list(NotificationPreference.objects.filter(user=user, club__isnull=True))
    if club is not None:
        rows += list(NotificationPreference.objects.filter(user=user, club=club))  # later = club rows override
    for row in rows:
        result[row.category][row.channel] = row.enabled
    return result


def overrides(user, club=None) -> dict:
    """Only the explicitly stored switches for exactly this scope: ``{category: {channel: bool}}``."""
    found: dict = {}
    for row in NotificationPreference.objects.filter(user=user, club=club):
        found.setdefault(row.category, {})[row.channel] = row.enabled
    return found


def set_preference(user, club, category: str, channel: str, enabled: bool) -> NotificationPreference:
    if category not in NotificationCategory.values or channel not in Channel.values:
        raise ValueError(f"unknown category/channel: {category}/{channel}")
    row, _created = NotificationPreference.objects.update_or_create(user=user, club=club, category=category, channel=channel, defaults={"enabled": enabled})
    return row


def reset(user, club=None, category: str | None = None) -> int:
    """Forget the stored switches for this scope (back to the inherited default)."""
    rows = NotificationPreference.objects.filter(user=user, club=club)
    if category:
        rows = rows.filter(category=category)
    count, _deleted = rows.delete()
    return count
