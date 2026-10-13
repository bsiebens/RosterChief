"""The delivery log (notifications.models.NotificationDelivery): what every channel sent, what
failed and why, and the retry of the transient failures.

Channels call ``record`` once per send. A failure the channel classifies as transient
(``retryable=True``: throttling, a server error, a network error) becomes ``RETRYING`` with the
``payload`` the channel needs to send again; ``retry_due`` (the ``retry_failed_deliveries`` job)
hands each due row back to its channel (``Channel.retry``), which finishes it with
``mark_sent`` / ``mark_dead`` / ``mark_failed``. Logging never raises: a log write failing must
not stop a delivery.
"""

import logging
import time
from datetime import timedelta

from django.utils import timezone

from .models import NotificationDelivery
from .policy import policy_for

logger = logging.getLogger(__name__)

Status = NotificationDelivery.Status

#: Delay before retry 1, 2, 3 ... (attempts includes the first send, so MAX_ATTEMPTS=4 means three retries).
RETRY_DELAYS = [timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=30)]
MAX_ATTEMPTS = len(RETRY_DELAYS) + 1
#: How long rows are kept: successes briefly, problems longer for debugging.
RETENTION_SENT = timedelta(days=14)
RETENTION_OTHER = timedelta(days=60)


def _truncate(error) -> str:
    return str(error)[:500]


def elapsed_ms(started: float) -> int:
    """Milliseconds since ``started`` (a ``time.monotonic()`` reading)."""
    return max(0, round((time.monotonic() - started) * 1000))


def record(*, club, channel: str, target, outcome: str, notification=None, user=None, error="", payload=None, provider: str = "", duration_ms: int | None = None, hold_until=None) -> NotificationDelivery | None:
    """Log one send. ``outcome`` is ``sent``, ``failed`` (refused, don't retry), ``dead`` (the target is
    gone for good), ``retry`` (transient: tried again later, needs ``payload``) or ``held`` (a push
    kept back for the account's quiet hours: sent by ``retry_due`` from ``hold_until`` on, needs ``payload``). ``provider``
    (apns/fcm/webpush/email) and ``duration_ms`` (how long the provider call took) are for monitoring."""
    try:
        status = {"sent": Status.SENT, "failed": Status.FAILED, "dead": Status.DEAD, "retry": Status.RETRYING, "held": Status.HELD}[outcome]
        retrying = status in (Status.RETRYING, Status.HELD)
        return NotificationDelivery.objects.create(
            club=club,
            notification=notification,
            user=user,
            channel=channel,
            provider=provider,
            duration_ms=duration_ms,
            target=str(target)[:255],
            status=status,
            last_error=_truncate(error),
            next_attempt_at=(hold_until if status == Status.HELD else timezone.now() + RETRY_DELAYS[0]) if retrying else None,
            payload=(payload or {}) if retrying else {},
        )
    except Exception:
        logger.exception("delivery_log.record_failed channel=%s", channel)
        return None


def _timing(delivery: NotificationDelivery, provider: str, duration_ms: int | None) -> list[str]:
    """Update the monitoring fields from a retry when it knows them; returns the changed field names."""
    fields = []
    if provider and delivery.provider != provider:
        delivery.provider = provider
        fields.append("provider")
    if duration_ms is not None:
        delivery.duration_ms = duration_ms
        fields.append("duration_ms")
    return fields


def mark_sent(delivery: NotificationDelivery, *, provider: str = "", duration_ms: int | None = None) -> None:
    _finish(delivery, Status.SENT, "", provider, duration_ms)


def mark_dead(delivery: NotificationDelivery, error="", *, provider: str = "", duration_ms: int | None = None) -> None:
    _finish(delivery, Status.DEAD, error, provider, duration_ms)


def mark_failed(delivery: NotificationDelivery, error="", *, retryable: bool = True, provider: str = "", duration_ms: int | None = None) -> None:
    """A retry did not work out: schedule the next one, or give up (not retryable, or out of attempts)."""
    if retryable and delivery.attempts < MAX_ATTEMPTS:
        delivery.attempts += 1
        delivery.status = Status.RETRYING
        delivery.last_error = _truncate(error)
        delivery.next_attempt_at = timezone.now() + RETRY_DELAYS[min(delivery.attempts - 1, len(RETRY_DELAYS) - 1)]
        extra = _timing(delivery, provider, duration_ms)
        delivery.save(update_fields=["attempts", "status", "last_error", "next_attempt_at", "modified", *extra])
    else:
        _finish(delivery, Status.FAILED, error, provider, duration_ms)


def _finish(delivery: NotificationDelivery, status: str, error, provider: str = "", duration_ms: int | None = None) -> None:
    extra = _timing(delivery, provider, duration_ms)
    delivery.status = status
    delivery.last_error = _truncate(error)
    delivery.next_attempt_at = None
    delivery.payload = {}
    delivery.save(update_fields=["status", "last_error", "next_attempt_at", "payload", "modified", *extra])


def _still_worth_sending(delivery: NotificationDelivery, now) -> bool:
    """A held push is not sent when it went stale in the meantime (past its category's time to live) or
    was already read in the app; it is then finished as failed with a distinct reason."""
    notification = delivery.notification
    if notification is not None:
        policy = policy_for(notification.category, notification.priority)
        if now > delivery.created + timedelta(seconds=policy.ttl_seconds):
            mark_failed(delivery, "expired in quiet hours", retryable=False)
            return False
        if notification.read_at is not None:
            mark_failed(delivery, "read before release", retryable=False)
            return False
    return True


def retry_due(now=None) -> int:
    """Retry every ``RETRYING`` delivery whose time has come, and release every ``HELD`` one whose quiet
    hours have ended (the same ``channel.retry`` sends both); returns how many were attempted.
    A delivery whose channel is gone, or whose retry raises, is given the next backoff step."""
    from .dispatch import channels

    now = now or timezone.now()
    by_name = {channel.name: channel for channel in channels()}
    attempted = 0
    for delivery in NotificationDelivery.objects.filter(status__in=[Status.RETRYING, Status.HELD], next_attempt_at__lte=now).select_related("club", "notification", "user").order_by("next_attempt_at")[:500]:
        channel = by_name.get(delivery.channel)
        if channel is None:
            mark_failed(delivery, "channel not available", retryable=False)
            continue
        if delivery.status == Status.HELD and not _still_worth_sending(delivery, now):
            continue
        attempted += 1
        try:
            channel.retry(delivery)
        except Exception as error:
            logger.exception("delivery_log.retry_failed delivery=%s", delivery.pk)
            mark_failed(delivery, error)
    return attempted


def purge(now=None) -> int:
    """Delete rows past their retention. Rows still RETRYING are never purged."""
    now = now or timezone.now()
    sent, _ = NotificationDelivery.objects.filter(status=Status.SENT, created__lt=now - RETENTION_SENT).delete()
    other, _ = NotificationDelivery.objects.filter(status__in=[Status.FAILED, Status.DEAD], created__lt=now - RETENTION_OTHER).delete()
    return sent + other
