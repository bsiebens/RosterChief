"""Read side of the delivery log: per-provider health, the retry queue and the outbox. A handful of grouped queries; p50/p95 are
nearest-rank over the latest ``DURATION_SAMPLE`` durations of the window."""

import math
from datetime import timedelta

from django.db.models import Count, Max, Min, Q
from django.utils import timezone

from .models import NotificationDelivery, OutboxEntry, PushSettings

Status = NotificationDelivery.Status
PROVIDERS = ("apns", "fcm", "webpush", "email")
DURATION_SAMPLE = 2000
OVERDUE_AFTER = timedelta(minutes=10)

OK, DEGRADED, DOWN, QUIET = "ok", "degraded", "down", "quiet"


def health_state(requests: int, success_rate: float | None, ps: PushSettings | None = None) -> str:
    ps = ps or PushSettings.get()
    if requests < ps.health_min_requests or success_rate is None:
        return QUIET
    if success_rate < ps.down_below_percent:
        return DOWN
    if success_rate < ps.degraded_below_percent:
        return DEGRADED
    return OK


def percentile(sorted_values: list[int], fraction: float) -> int | None:
    if not sorted_values:
        return None
    return sorted_values[max(1, math.ceil(fraction * len(sorted_values))) - 1]


def _base(since, club):
    """Delivery rows in the window. HELD rows are not send attempts yet, so they stay out of rates and lists."""
    queryset = NotificationDelivery.objects.filter(created__gte=since).exclude(status=Status.HELD)
    return queryset.filter(club=club) if club else queryset


def provider_health(since, club=None) -> dict[str, dict]:
    ps = PushSettings.get()
    grouped: dict = {}
    for row in _base(since, club).values("provider", "status").annotate(n=Count("pk"), last=Max("created")):
        grouped.setdefault(row["provider"], {})[row["status"]] = row
    retrying_rows = NotificationDelivery.objects.filter(status=Status.RETRYING)
    if club:
        retrying_rows = retrying_rows.filter(club=club)
    retrying_now = dict(retrying_rows.values_list("provider").annotate(n=Count("pk")))
    cards = {}
    for provider in PROVIDERS:
        by_status = grouped.get(provider, {})
        total = sum(row["n"] for row in by_status.values())
        sent = by_status.get(Status.SENT, {"n": 0})["n"]
        dead = by_status.get(Status.DEAD, {"n": 0})["n"]
        judged = total - dead
        rate = round(100 * sent / judged, 1) if judged > 0 else None
        failure_times = [by_status[s]["last"] for s in (Status.FAILED, Status.DEAD) if s in by_status]
        durations = sorted(_base(since, club).filter(provider=provider, duration_ms__isnull=False).order_by("-created").values_list("duration_ms", flat=True)[:DURATION_SAMPLE])
        cards[provider] = {
            "requests": total,
            "sent": sent,
            "failed": by_status.get(Status.FAILED, {"n": 0})["n"],
            "dead": dead,
            "retrying": retrying_now.get(provider, 0),
            "success_rate": rate,
            "p50_ms": percentile(durations, 0.5),
            "p95_ms": percentile(durations, 0.95),
            "last_success": by_status.get(Status.SENT, {}).get("last"),
            "last_failure": max(failure_times) if failure_times else None,
            "state": health_state(total, rate, ps),
        }
    return cards


def retry_queue(now=None) -> dict:
    now = now or timezone.now()
    waiting_rows = NotificationDelivery.objects.filter(status=Status.RETRYING)
    stats = waiting_rows.aggregate(waiting=Count("pk"), oldest=Min("next_attempt_at"))
    overdue = NotificationDelivery.objects.filter(status__in=[Status.RETRYING, Status.HELD], next_attempt_at__lt=now - OVERDUE_AFTER).count()
    reasons = []
    if overdue:
        reasons.append(f"{overdue} retries or held pushes are overdue by more than 10 minutes; the retry job may not be running.")
    return {"waiting": stats["waiting"], "oldest_next_attempt": stats["oldest"], "overdue": overdue, "stuck": bool(reasons), "reasons": reasons}


def outbox_stats() -> dict:
    stats = OutboxEntry.objects.aggregate(pending=Count("pk", filter=Q(status=OutboxEntry.Status.PENDING)), failed=Count("pk", filter=Q(status=OutboxEntry.Status.FAILED)), oldest=Min("available_at", filter=Q(status=OutboxEntry.Status.PENDING)))
    return {"pending": stats["pending"], "failed": stats["failed"], "oldest_pending": stats["oldest"]}
