"""How each notification category is delivered: how long a push may wait, whether a newer one replaces an older one, how loud it
is, and whether it is held during quiet hours. One table feeds the APNs headers, the FCM message and the quiet-hours rule."""

from dataclasses import dataclass

from .models import Category, Priority

DAY = 24 * 3600


@dataclass(frozen=True)
class PushPolicy:
    ttl_seconds: int
    collapse: bool  # a newer push for the same thread replaces the older one (live game updates)
    android_channel_id: str  # one channel per category: ``rc_<category>``
    android_importance: str  # "high" makes sound and pops up
    apns_priority: int  # 10 = deliver now, 5 = may be batched
    hold_in_quiet_hours: bool  # held until the window ends (True) or dropped because it would be stale (False)


def _policy(category: str, ttl_seconds: int, *, collapse=False, importance="default", apns_priority=5, hold=True) -> PushPolicy:
    return PushPolicy(ttl_seconds, collapse, f"rc_{category}", importance, apns_priority, hold)


POLICIES = {
    Category.NEWS: _policy("news", 7 * DAY),
    Category.EVENT: _policy("event", 3 * DAY, importance="high", apns_priority=10),
    Category.REMINDER: _policy("reminder", DAY),
    Category.LINEUP: _policy("lineup", DAY, importance="high", apns_priority=10),
    Category.CREW: _policy("crew", 2 * DAY, importance="high", apns_priority=10),
    Category.FORM: _policy("form", 3 * DAY),
    Category.SHOP: _policy("shop", 2 * DAY),
    Category.COACH: _policy("coach", DAY, importance="high", apns_priority=10),
    Category.ADMIN: _policy("admin", 3 * DAY),
    Category.ANNOUNCEMENT: _policy("announcement", 7 * DAY),
    Category.CHAT: _policy("chat", DAY, collapse=True, importance="high", apns_priority=10),
    Category.GAME: _policy("game", 15 * 60, collapse=True, importance="high", apns_priority=10, hold=False),
    Category.GENERAL: _policy("general", 3 * DAY),
}


def policy_for(category: str, priority: str = Priority.NORMAL) -> PushPolicy:
    """The policy of ``category``; a critical notification is always loud, immediate and lives at least a day."""
    base = POLICIES.get(category) or POLICIES[Category.GENERAL]
    if priority == Priority.CRITICAL:
        return PushPolicy(max(base.ttl_seconds, DAY), base.collapse, base.android_channel_id, "high", 10, True)
    return base
