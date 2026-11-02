"""Queries behind the push monitoring page: series buckets, top failures, recent problems, device counts and masking helpers."""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

from django.db.models import Count, Max, Q
from django.db.models.functions import TruncDay, TruncHour, TruncMinute
from django.utils import timezone

from notifications.models import NotificationDelivery, PushAlert, PushDevice, PushSettings, WebPushSubscription

WINDOWS = {"1h": timedelta(hours=1), "24h": timedelta(hours=24), "7d": timedelta(days=7)}
DEFAULT_WINDOW = "24h"
Status = NotificationDelivery.Status


# --- masking ----------------------------------------------------------------------------------------------------------


def mask_email(email: str | None) -> str | None:
    """``alice@example.com`` -> ``a***@example.com``."""
    if not email:
        return None
    local, sep, domain = email.partition("@")
    if not sep:
        return truncate(email)
    return f"{local[:1]}***@{domain}"


def truncate(value: str | None, length: int = 24) -> str:
    value = value or ""
    return value if len(value) <= length else value[: length - 1] + "…"


def mask_target(target: str | None) -> str:
    """Emails are masked, every other id (installation id, endpoint) is cut to 24 characters."""
    target = target or ""
    return mask_email(target) if "@" in target else truncate(target)


# --- parameters -------------------------------------------------------------------------------------------------------


def parse_window(value: str | None) -> str:
    return value if value in WINDOWS else DEFAULT_WINDOW


def parse_club(value: str | None):
    """The Club for a valid, existing uuid; anything else is ignored (None)."""
    from club.models import Club

    try:
        pk = UUID(str(value))
    except TypeError, ValueError:
        return None
    return Club.objects.filter(pk=pk).only("id", "name").first()


# --- series -----------------------------------------------------------------------------------------------------------


def bucket_starts(window: str, now: datetime) -> list[datetime]:
    """1h -> 12 five-minute buckets, 24h -> 24 hourly, 7d -> 7 daily; the last one contains ``now``."""
    local = timezone.localtime(now)
    if window == "1h":
        last, step, n = local.replace(minute=local.minute - local.minute % 5, second=0, microsecond=0), timedelta(minutes=5), 12
    elif window == "24h":
        last, step, n = local.replace(minute=0, second=0, microsecond=0), timedelta(hours=1), 24
    else:
        last, step, n = local.replace(hour=0, minute=0, second=0, microsecond=0), None, 7
    if step is None:  # calendar days (DST safe)
        from datetime import time

        days = [last.date() - timedelta(days=i) for i in range(n - 1, -1, -1)]
        return [timezone.make_aware(datetime.combine(d, time.min), last.tzinfo) for d in days]
    return [last - step * i for i in range(n - 1, -1, -1)]


def series(window: str, now: datetime, club=None) -> list[dict]:
    starts = bucket_starts(window, now)
    trunc = {"1h": TruncMinute, "24h": TruncHour, "7d": TruncDay}[window]("created")
    qs = NotificationDelivery.objects.filter(created__gte=starts[0]).exclude(status=Status.HELD)
    if club is not None:
        qs = qs.filter(club=club)
    rows = qs.annotate(b=trunc).values("b").annotate(sent=Count("id", filter=Q(status=Status.SENT)), other=Count("id", filter=~Q(status=Status.SENT)))
    out = [{"bucket": s, "sent": 0, "not_sent": 0} for s in starts]
    for row in rows:
        b = timezone.localtime(row["b"])
        # the last bucket whose start is <= b
        for i in range(len(starts) - 1, -1, -1):
            if starts[i] <= b:
                out[i]["sent"] += row["sent"]
                out[i]["not_sent"] += row["other"]
                break
    return out


# --- problems ---------------------------------------------------------------------------------------------------------


def _scoped(since: datetime, club=None):
    qs = NotificationDelivery.objects.filter(created__gte=since)
    return qs.filter(club=club) if club is not None else qs


def top_failures(since: datetime, club=None, limit: int = 10) -> list[dict]:
    rows = _scoped(since, club).filter(status__in=[Status.FAILED, Status.DEAD, Status.RETRYING]).exclude(last_error="").values("provider", "last_error").annotate(count=Count("id"), last_seen=Max("created")).order_by("-count", "-last_seen")[:limit]
    return [{"provider": r["provider"], "error": r["last_error"], "count": r["count"], "last_seen": r["last_seen"]} for r in rows]


def recent_problems(since: datetime, club=None, limit: int = 50) -> list[dict]:
    rows = _scoped(since, club).exclude(status__in=[Status.SENT, Status.HELD]).select_related("club", "user").order_by("-created")[:limit]
    return [
        {
            "id": d.pk,
            "created": d.created,
            "provider": d.provider,
            "club": {"id": d.club_id, "name": d.club.name} if d.club_id else None,
            "target_masked": mask_target(d.target),
            "status": d.status,
            "attempts": d.attempts,
            "error": d.last_error,
            "user_id": str(d.user_id) if d.user_id else None,
            "user_email_masked": mask_email(d.user.email) if d.user_id else None,
        }
        for d in rows
    ]


def open_alerts() -> list[dict]:
    return [{"id": a.pk, "message": a.message, "first_seen": a.first_seen, "notifications_sent": a.notifications_sent} for a in PushAlert.objects.filter(resolved_at__isnull=True).order_by("first_seen")]


# --- devices ----------------------------------------------------------------------------------------------------------


def device_counts(settings_row: PushSettings, now: datetime, club=None) -> dict:
    stale_before = now - timedelta(days=settings_row.stale_device_days)
    rows = {
        r["platform"]: r for r in PushDevice.objects.values("platform").annotate(n_enabled=Count("id", filter=Q(enabled=True)), n_disabled=Count("id", filter=Q(enabled=False)), n_stale=Count("id", filter=Q(enabled=True, last_seen__lt=stale_before)))
    }
    out = {}
    for platform in (PushDevice.Platform.IOS, PushDevice.Platform.ANDROID):
        r = rows.get(platform.value, {})
        out[platform.value] = {"enabled": r.get("n_enabled", 0), "disabled": r.get("n_disabled", 0), "stale": r.get("n_stale", 0)}
    subs = WebPushSubscription.objects.filter(club=club) if club is not None else WebPushSubscription.objects.all()
    out["web"] = {"subscriptions": subs.count()}
    return out
