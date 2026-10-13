"""Who wants what: the per-account switches (notifications.models.NotificationPreference) behind
"don't push me news" / "don't email me reminders".

The rule everything shares: **no row means on**. Three scopes, most specific wins:

1. one *person* in one club (``member`` set: "for Lou only");
2. one club (``club`` set, ``member`` empty);
3. every club (both empty).

Channels ask ``users_with_disabled`` once per batch, never per recipient.
"""

from django.db.models import Q

from .models import NotificationCategory, NotificationPreference

Channel = NotificationPreference.Channel


def users_with_disabled(user_ids, club, category: str, channel: str, member=None) -> set:
    """Of ``user_ids``, the accounts that switched ``category`` off on ``channel`` for ``club`` --
    for notifications *about* ``member`` when given (their per-person rows win over the club's)."""
    user_ids = {user_id for user_id in user_ids if user_id}
    if not user_ids:
        return set()
    club_id = getattr(club, "pk", club)
    member_id = getattr(member, "pk", member)
    scope = Q(club__isnull=True, member__isnull=True)
    if club_id:
        scope |= Q(club_id=club_id, member__isnull=True)
        if member_id:
            scope |= Q(club_id=club_id, member_id=member_id)
    decided: dict = {}  # user_id -> (specificity, enabled)
    for row in NotificationPreference.objects.filter(scope, user_id__in=user_ids, category=category, channel=channel):
        specificity = 2 if row.member_id else 1 if row.club_id else 0
        if row.user_id not in decided or specificity > decided[row.user_id][0]:
            decided[row.user_id] = (specificity, row.enabled)
    return {user_id for user_id, (_specificity, enabled) in decided.items() if not enabled}


def users_with_disabled_by_member(user_ids, club, category: str, channel: str, member_ids) -> dict:
    """``{member_id: {user_id, ...}}``: ``users_with_disabled`` for several subject people at once --
    ONE query however many members the batch is about (a club-wide notification makes one per child)."""
    user_ids = {user_id for user_id in user_ids if user_id}
    member_ids = {member_id for member_id in member_ids if member_id}
    if not user_ids or not member_ids:
        return {member_id: set() for member_id in member_ids}
    club_id = getattr(club, "pk", club)
    scope = Q(club__isnull=True, member__isnull=True)
    if club_id:
        scope |= Q(club_id=club_id, member__isnull=True) | Q(club_id=club_id, member_id__in=member_ids)
    base: dict = {}  # user_id -> (specificity, enabled), club/all-clubs rows
    personal: dict = {}  # member_id -> {user_id: enabled}
    for row in NotificationPreference.objects.filter(scope, user_id__in=user_ids, category=category, channel=channel):
        if row.member_id:
            personal.setdefault(row.member_id, {})[row.user_id] = row.enabled
            continue
        specificity = 1 if row.club_id else 0
        if row.user_id not in base or specificity > base[row.user_id][0]:
            base[row.user_id] = (specificity, row.enabled)
    base_off = {user_id for user_id, (_specificity, enabled) in base.items() if not enabled}
    result = {}
    for member_id in member_ids:
        own = personal.get(member_id, {})
        result[member_id] = {user_id for user_id in base_off if user_id not in own} | {user_id for user_id, enabled in own.items() if not enabled}
    return result


def is_enabled(user, club, category: str, channel: str, member=None) -> bool:
    return user.pk not in users_with_disabled([user.pk], club, category, channel, member=member)


def matrix(user, club=None, member=None) -> dict:
    """``{category: {channel: bool}}`` as ``user`` has it for ``club`` (``None`` = the all-clubs defaults),
    for notifications about ``member`` when given."""
    result = {category: dict.fromkeys(Channel.values, True) for category in NotificationCategory.values}
    rows = list(NotificationPreference.objects.filter(user=user, club__isnull=True, member__isnull=True))
    if club is not None:
        rows += list(NotificationPreference.objects.filter(user=user, club=club, member__isnull=True))
        if member is not None:
            rows += list(NotificationPreference.objects.filter(user=user, club=club, member=member))  # later = more specific
    for row in rows:
        result[row.category][row.channel] = row.enabled
    return result


def overrides(user, club=None, member=None) -> dict:
    """Only the explicitly stored switches for exactly this scope: ``{category: {channel: bool}}``."""
    found: dict = {}
    for row in NotificationPreference.objects.filter(user=user, club=club, member=member):
        found.setdefault(row.category, {})[row.channel] = row.enabled
    return found


def set_preference(user, club, category: str, channel: str, enabled: bool, member=None) -> NotificationPreference:
    if category not in NotificationCategory.values or channel not in Channel.values:
        raise ValueError(f"unknown category/channel: {category}/{channel}")
    if member is not None and club is None:
        raise ValueError("a per-person switch needs a club")
    row, _created = NotificationPreference.objects.update_or_create(user=user, club=club, member=member, category=category, channel=channel, defaults={"enabled": enabled})
    return row


def reset(user, club=None, category: str | None = None, member=None) -> int:
    """Forget the stored switches for this exact scope (back to the inherited default)."""
    rows = NotificationPreference.objects.filter(user=user, club=club, member=member)
    if category:
        rows = rows.filter(category=category)
    count, _deleted = rows.delete()
    return count
