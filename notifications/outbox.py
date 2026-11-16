"""The outbox: notification work that survives a restart and is delivered at least once.

``enqueue`` writes an ``OutboxEntry`` in the caller's transaction (it exists if and only if the thing that caused it does).
``claim`` moves due entries to PROCESSING with ``select_for_update(skip_locked)``, so concurrent workers never share one; entries stuck
in PROCESSING for 5 minutes (a worker died) count as a failed attempt and go back to PENDING. A failed handler is retried with a
backoff (``PushSettings.retry_delays_minutes``) and given up (FAILED) after ``PushSettings.outbox_max_attempts``."""

import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .models import OutboxEntry, PushSettings

logger = logging.getLogger(__name__)

STUCK_AFTER = timedelta(minutes=5)
BATCH = 50
DONE_RETENTION = timedelta(days=3)
FAILED_RETENTION = timedelta(days=30)
Status = OutboxEntry.Status


def enqueue(payload: dict, kind: str = "notification", available_at=None) -> OutboxEntry:
    return OutboxEntry.objects.create(kind=kind, payload=payload, available_at=available_at or timezone.now())


def _record_failure(entry: OutboxEntry, error, now, ps: PushSettings) -> None:
    attempts = entry.attempts + 1
    delays = ps.retry_delays_minutes or list(PushSettings.DEFAULT_RETRY_DELAYS)
    if attempts >= ps.outbox_max_attempts:
        status, available_at = Status.FAILED, entry.available_at
    else:
        status, available_at = Status.PENDING, now + timedelta(minutes=delays[min(attempts, len(delays)) - 1])
    OutboxEntry.objects.filter(pk=entry.pk).update(status=status, attempts=attempts, available_at=available_at, locked_at=None, last_error=f"{type(error).__name__}: {error}"[:500], modified=now)


def recover_stuck(now=None) -> int:
    now = now or timezone.now()
    ps = PushSettings.get()
    count = 0
    with transaction.atomic():
        for entry in OutboxEntry.objects.select_for_update(skip_locked=True).filter(status=Status.PROCESSING, locked_at__lt=now - STUCK_AFTER):
            _record_failure(entry, RuntimeError("worker died while processing"), now, ps)
            count += 1
    return count


def claim(limit: int = BATCH, now=None) -> list[OutboxEntry]:
    """Atomically take up to ``limit`` due pending entries (oldest first) and mark them PROCESSING."""
    now = now or timezone.now()
    with transaction.atomic():
        entries = list(OutboxEntry.objects.select_for_update(skip_locked=True).filter(status=Status.PENDING, available_at__lte=now).order_by("available_at")[:limit])
        OutboxEntry.objects.filter(pk__in=[e.pk for e in entries]).update(status=Status.PROCESSING, locked_at=now, modified=now)
    return entries


def handle_notification(payload: dict) -> None:
    from club.models import Club

    from .delivery import deliver_to_user, get_user

    user = get_user(payload["user_id"])
    if user is None:
        return  # the account is gone: nothing to deliver
    club = Club.objects.filter(pk=payload["club_id"]).first() if payload.get("club_id") else None
    deliver_to_user(
        user,
        title=payload["title"],
        body=payload["body"],
        category=payload.get("category", "general"),
        priority=payload.get("priority", "normal"),
        data=payload.get("data") or {},
        club=club,
        notification_id=payload.get("notification_id"),
        send_email=payload.get("send_email", False),
    )


HANDLERS = {"notification": handle_notification}


def process(entry: OutboxEntry, now=None) -> bool:
    """Run one claimed entry's handler. True when it completed."""
    ps = PushSettings.get()
    try:
        HANDLERS[entry.kind](entry.payload)
    except Exception as error:
        logger.exception("outbox.failed entry=%s kind=%s", entry.pk, entry.kind)
        _record_failure(entry, error, now or timezone.now(), ps)
        return False
    OutboxEntry.objects.filter(pk=entry.pk).update(status=Status.DONE, locked_at=None, last_error="", modified=timezone.now())
    return True


def process_outbox(limit: int = BATCH, max_batches: int = 20, now=None) -> dict:
    """Recover stuck entries, then claim and run batches until nothing is due. Returns counts for the job log."""
    counts = {"recovered": recover_stuck(now), "done": 0, "failed": 0}
    for _batch in range(max_batches):
        entries = claim(limit, now)
        if not entries:
            break
        for entry in entries:
            counts["done" if process(entry, now) else "failed"] += 1
    return counts


def purge(now=None) -> int:
    now = now or timezone.now()
    done, _ = OutboxEntry.objects.filter(status=Status.DONE, modified__lt=now - DONE_RETENTION).delete()
    failed, _ = OutboxEntry.objects.filter(status=Status.FAILED, modified__lt=now - FAILED_RETENTION).delete()
    return done + failed
