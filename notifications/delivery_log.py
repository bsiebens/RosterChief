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
from datetime import timedelta

from django.utils import timezone

from .models import NotificationDelivery

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


def record(*, club, channel: str, target, outcome: str, notification=None, user=None, error="", payload=None) -> NotificationDelivery | None:
    """Log one send. ``outcome`` is ``sent``, ``failed`` (refused, don't retry), ``dead`` (the target is
    gone for good) or ``retry`` (transient: tried again later, needs ``payload``)."""
    try:
        status = {"sent": Status.SENT, "failed": Status.FAILED, "dead": Status.DEAD, "retry": Status.RETRYING}[outcome]
        retrying = status == Status.RETRYING
        return NotificationDelivery.objects.create(
            club=club,
            notification=notification,
            user=user,
            channel=channel,
            target=str(target)[:255],
            status=status,
            last_error=_truncate(error),
            next_attempt_at=timezone.now() + RETRY_DELAYS[0] if retrying else None,
            payload=(payload or {}) if retrying else {},
        )
    except Exception:
        logger.exception("delivery_log.record_failed channel=%s", channel)
        return None


def mark_sent(delivery: NotificationDelivery) -> None:
    _finish(delivery, Status.SENT, "")


def mark_dead(delivery: NotificationDelivery, error="") -> None:
    _finish(delivery, Status.DEAD, error)


def mark_failed(delivery: NotificationDelivery, error="", *, retryable: bool = True) -> None:
    """A retry did not work out: schedule the next one, or give up (not retryable, or out of attempts)."""
    if retryable and delivery.attempts < MAX_ATTEMPTS:
        delivery.attempts += 1
        delivery.status = Status.RETRYING
        delivery.last_error = _truncate(error)
        delivery.next_attempt_at = timezone.now() + RETRY_DELAYS[min(delivery.attempts - 1, len(RETRY_DELAYS) - 1)]
        delivery.save(update_fields=["attempts", "status", "last_error", "next_attempt_at", "modified"])
    else:
        _finish(delivery, Status.FAILED, error)


def _finish(delivery: NotificationDelivery, status: str, error) -> None:
    delivery.status = status
    delivery.last_error = _truncate(error)
    delivery.next_attempt_at = None
    delivery.payload = {}
    delivery.save(update_fields=["status", "last_error", "next_attempt_at", "payload", "modified"])


def retry_due(now=None) -> int:
    """Retry every ``RETRYING`` delivery whose time has come; returns how many were attempted.
    A delivery whose channel is gone, or whose retry raises, is given the next backoff step."""
    from .dispatch import channels

    now = now or timezone.now()
    by_name = {channel.name: channel for channel in channels()}
    attempted = 0
    for delivery in NotificationDelivery.objects.filter(status=Status.RETRYING, next_attempt_at__lte=now).select_related("club", "notification", "user").order_by("next_attempt_at")[:500]:
        channel = by_name.get(delivery.channel)
        if channel is None:
            mark_failed(delivery, "channel not available", retryable=False)
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
