"""The one fan-out for everything that reaches people: a notification (or a live game push)
is handed to every registered *channel* -- native push (APNs/FCM), web push, email -- instead of
each feature wiring itself to the channels it happens to know.

``notifications`` stays channel-agnostic: channels live in their own apps and register
themselves in ``AppConfig.ready`` (``register_channel``), so notifications never imports
api/app/mobile. The in-app inbox is the ``Notification`` row itself, written by
``notify_members`` before anything is dispatched.

Two entry points:

* ``schedule_notifications`` -- for stored ``Notification`` rows: after the surrounding
  transaction commits, off the request thread, every channel's ``deliver_notifications``;
* ``dispatch_to_user`` -- for a message with no inbox row (live game updates): every channel's
  ``deliver_to_user`` for one account.

Channels are best-effort and independent: one failing never stops the others.
"""

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


class Channel:
    """A delivery channel. Subclass, set ``name`` and register an instance in ``AppConfig.ready``."""

    name = ""

    def deliver_notifications(self, notifications, *, email: bool = True) -> None:
        """Deliver stored notifications (a ``notify_members`` batch). ``email`` is False when the
        caller asked for no email -- only the email channel looks at it."""

    def retry(self, delivery) -> None:
        """Send a ``RETRYING`` ``NotificationDelivery`` again from its stored ``payload`` and finish
        it with ``delivery_log.mark_sent`` / ``mark_dead`` / ``mark_failed``. A channel that never
        logs ``retry`` outcomes needn't implement this."""
        raise NotImplementedError

    def deliver_to_user(self, *, club, user, texts, data: dict, url: str, thread_id: str | None = None, category: str = "general") -> int:
        """Deliver one message to one account. ``texts(language)`` -> ``(title, body)`` in that
        language; ``data`` is the structured pointer native apps get, ``url`` the page a web
        notification opens. Returns how many devices/subscriptions it was handed to."""
        return 0


_channels: list[Channel] = []


def register_channel(channel: Channel) -> None:
    """Idempotent per channel name (``AppConfig.ready`` may run more than once in tests)."""
    _channels[:] = [existing for existing in _channels if existing.name != channel.name]
    _channels.append(channel)


def channels() -> list[Channel]:
    return list(_channels)


def dispatch_notifications(notification_ids, *, email: bool = True) -> None:
    from .models import Notification

    notifications = list(Notification.objects.filter(pk__in=notification_ids).select_related("club", "member", "member__user").order_by("created"))
    if not notifications:
        return
    for channel in channels():
        try:
            channel.deliver_notifications(notifications, email=email)
        except Exception:
            logger.exception("notifications.channel_failed channel=%s", channel.name)


def schedule_notifications(notifications, *, email: bool = True) -> None:
    """Durable: an outbox entry is written in this transaction, then worked off after it commits (the
    rows must be visible to the worker thread), off the request thread -- and by the
    ``process_outbox`` job if a restart got in between. ``NOTIFICATIONS_DISPATCH_EAGER`` (on in the
    test suite) delivers right away instead, so tests don't race a thread."""
    if not notifications:
        return
    from . import outbox
    from .models import OutboxEntry

    ids = [str(notification.pk) for notification in notifications]
    entry = outbox.enqueue(OutboxEntry.Kind.NOTIFICATIONS, {"ids": ids, "email": email})
    outbox.kick([entry.pk])


@dataclass
class UserDelivery:
    """What ``dispatch_to_user`` did, per channel name."""

    reached: dict = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.reached.values())


def dispatch_to_user(*, club, user, texts, data: dict, url: str, thread_id: str | None = None, category: str = "general") -> UserDelivery:
    """One message to one account on every channel (no inbox row). Never raises."""
    result = UserDelivery()
    for channel in channels():
        try:
            result.reached[channel.name] = channel.deliver_to_user(club=club, user=user, texts=texts, data=data, url=url, thread_id=thread_id, category=category)
        except Exception:
            logger.exception("notifications.channel_failed channel=%s", channel.name)
            result.reached[channel.name] = 0
    return result
