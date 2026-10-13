"""How each notification category is delivered by the push providers: how long a push may wait,
whether a newer one replaces an older one, how loud it is, and whether it is held during quiet hours.

One table (``POLICIES``) is the single source for the APNs headers (``apns-expiration``,
``apns-collapse-id``, ``apns-priority``), the FCM message (``ttl``, ``collapse_key``, ``priority``,
Android channel) and the quiet-hours rule, so the two providers behave alike. The Android channel
ids/importance are also served to the Android app (app API ``categories``) so it can create one
system notification channel per category: people can then tune each in Android's own settings.
"""

from dataclasses import dataclass

from .models import NotificationCategory, NotificationPriority

DAY = 24 * 3600


@dataclass(frozen=True)
class PushPolicy:
    ttl_seconds: int
    #: A newer push for the same thread replaces the older one still waiting/shown (live game updates).
    collapse: bool
    #: Android notification channel id (one per category) and its importance: "high" makes sound and pops up.
    android_channel_id: str
    android_importance: str
    #: APNs priority: 10 = deliver now, 5 = may be batched to save battery.
    apns_priority: int
    #: Held until the quiet hours end (True) or dropped because it would be stale by then (False).
    hold_in_quiet_hours: bool


def _policy(ttl_seconds, *, collapse=False, importance="default", apns_priority=5, hold=True, category=""):
    return PushPolicy(ttl_seconds, collapse, f"rc_{category}", importance, apns_priority, hold)


POLICIES = {
    NotificationCategory.NEWS: _policy(7 * DAY, category="news"),
    NotificationCategory.EVENT: _policy(3 * DAY, importance="high", apns_priority=10, category="event"),
    NotificationCategory.REMINDER: _policy(DAY, category="reminder"),
    NotificationCategory.LINEUP: _policy(DAY, importance="high", apns_priority=10, category="lineup"),
    NotificationCategory.CREW: _policy(2 * DAY, importance="high", apns_priority=10, category="crew"),
    NotificationCategory.FORM: _policy(3 * DAY, category="form"),
    NotificationCategory.SHOP: _policy(2 * DAY, category="shop"),
    NotificationCategory.COACH: _policy(DAY, importance="high", apns_priority=10, category="coach"),
    NotificationCategory.ADMIN: _policy(3 * DAY, category="admin"),
    NotificationCategory.ANNOUNCEMENT: _policy(7 * DAY, category="announcement"),
    NotificationCategory.GAME: _policy(15 * 60, collapse=True, importance="high", apns_priority=10, hold=False, category="game"),
    NotificationCategory.GENERAL: _policy(3 * DAY, category="general"),
}


def policy_for(category: str, priority: str = NotificationPriority.NORMAL) -> PushPolicy:
    """The delivery policy of ``category``; a critical notification is always loud and immediate."""
    base = POLICIES.get(category) or POLICIES[NotificationCategory.GENERAL]
    if priority == NotificationPriority.CRITICAL:
        return PushPolicy(max(base.ttl_seconds, DAY), base.collapse, base.android_channel_id, "high", 10, True)
    return base


def android_channels() -> list[dict]:
    """What the Android app creates at startup: one channel per category."""
    return [{"id": policy.android_channel_id, "category": str(category), "importance": policy.android_importance} for category, policy in POLICIES.items()]
