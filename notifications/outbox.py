"""The outbox: dispatch work that survives a restart.

``enqueue`` writes an ``OutboxEntry`` in the caller's transaction (so it exists if and only if the
thing it is about does); ``kick`` works it off right after commit on a background thread; the
``process_outbox`` job (``recover``) picks up anything a crash or restart left behind, and purges
old entries. Delivery is **at-least-once**: an entry is claimed atomically, but a crash while a
handler runs makes the sweeper run it again, so handlers must be idempotent or tolerate a repeat
of the sends they had not marked yet.

Handlers are looked up by kind through ``HANDLERS`` (dotted paths, imported lazily) so this app
never imports the apps that produce the work.
"""

import datetime
import logging

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.module_loading import import_string

from rosterchief.concurrency import run_in_background

from .models import OutboxEntry

logger = logging.getLogger(__name__)

#: kind -> dotted path of ``handler(payload: dict)``.
HANDLERS = {
    OutboxEntry.Kind.NOTIFICATIONS: "notifications.outbox.handle_notifications",
    OutboxEntry.Kind.GAME_MOMENTS: "notifications.outbox.handle_game_moments",
}

MAX_ATTEMPTS = 5
#: Delay before the next try after the 1st, 2nd, 3rd+ failure.
BACKOFF = [datetime.timedelta(minutes=1), datetime.timedelta(minutes=5), datetime.timedelta(minutes=30)]
STUCK_AFTER = datetime.timedelta(minutes=5)
SWEEP_AFTER = datetime.timedelta(seconds=30)
DONE_RETENTION = datetime.timedelta(days=3)
FAILED_RETENTION = datetime.timedelta(days=30)


def handle_notifications(payload: dict) -> None:
    from .dispatch import dispatch_notifications

    dispatch_notifications(payload["ids"], email=payload.get("email", True))


def handle_game_moments(payload: dict) -> None:
    from events.services.game_push import deliver_moments

    deliver_moments(payload["event_id"], payload["moment_ids"])


def enqueue(kind: str, payload: dict) -> OutboxEntry:
    """Writes the entry in the current transaction. Call ``kick`` with it afterwards."""
    return OutboxEntry.objects.create(kind=kind, payload=payload)


def kick(entry_ids) -> None:
    """Works the entries off after the surrounding transaction commits, on a background thread --
    or right now, inline, when ``NOTIFICATIONS_DISPATCH_EAGER`` is on (tests)."""
    ids = list(entry_ids)
    if not ids:
        return
    if getattr(settings, "NOTIFICATIONS_DISPATCH_EAGER", False):
        _process_all(ids)
        return
    transaction.on_commit(lambda: run_in_background(_process_all, ids))


def _process_all(entry_ids) -> None:
    for entry_id in entry_ids:
        process(entry_id)


def claim(entry) -> bool:
    """Atomically moves a due pending entry to processing; False when someone else got it first."""
    now = timezone.now()
    claimed = OutboxEntry.objects.filter(pk=entry.pk, status=OutboxEntry.Status.PENDING, available_at__lte=now).update(status=OutboxEntry.Status.PROCESSING, locked_at=now, modified=now)
    return claimed == 1


def process(entry_id) -> bool:
    """Claims and runs one entry. True when it ran to completion; False when it was not claimable
    (already taken, done, or not due yet) or its handler failed (then it is retried later)."""
    entry = OutboxEntry.objects.filter(pk=entry_id).first()
    if entry is None or not claim(entry):
        return False
    try:
        import_string(HANDLERS[entry.kind])(entry.payload)
    except Exception as error:
        logger.exception("outbox.failed entry=%s kind=%s", entry.pk, entry.kind)
        _record_failure(entry, error)
        return False
    OutboxEntry.objects.filter(pk=entry.pk).update(status=OutboxEntry.Status.DONE, locked_at=None, modified=timezone.now())
    return True


def _record_failure(entry, error) -> None:
    attempts = entry.attempts + 1
    now = timezone.now()
    if attempts >= MAX_ATTEMPTS:
        status, available_at = OutboxEntry.Status.FAILED, entry.available_at
    else:
        status, available_at = OutboxEntry.Status.PENDING, now + BACKOFF[min(attempts, len(BACKOFF)) - 1]
    OutboxEntry.objects.filter(pk=entry.pk).update(status=status, attempts=attempts, available_at=available_at, locked_at=None, last_error=f"{type(error).__name__}: {error}"[:500], modified=now)


def recover() -> int:
    """The sweeper. Entries stuck in processing (the worker died) go back to pending -- counting as
    an attempt, so a poison entry ends up failed -- then every due pending entry older than 30
    seconds (the post-commit thread had its chance) is processed. Returns how many ran."""
    now = timezone.now()
    for entry in OutboxEntry.objects.filter(status=OutboxEntry.Status.PROCESSING, locked_at__lt=now - STUCK_AFTER):
        _record_failure(entry, RuntimeError("worker died while processing"))
    due = OutboxEntry.objects.filter(status=OutboxEntry.Status.PENDING, available_at__lte=now, created__lt=now - SWEEP_AFTER).values_list("pk", flat=True)
    return sum(1 for entry_id in list(due) if process(entry_id))


def purge() -> int:
    """Deletes done entries older than 3 days and failed ones older than 30."""
    now = timezone.now()
    done = OutboxEntry.objects.filter(status=OutboxEntry.Status.DONE, modified__lt=now - DONE_RETENTION).delete()[0]
    failed = OutboxEntry.objects.filter(status=OutboxEntry.Status.FAILED, modified__lt=now - FAILED_RETENTION).delete()[0]
    return done + failed
