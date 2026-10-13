"""Read side of the delivery log for the platform control panel's "Push monitoring" page.

Everything here is a handful of grouped queries (``.values().annotate()``) over
``NotificationDelivery`` -- never a per-row Python loop over the window -- so the page stays
quick with 100k+ rows; the ``(created, provider, status)`` index serves the window scans.

Percentiles: SQLite has no percentile function and PostgreSQL's differs, so p50/p95 are computed
in Python from a *bounded sample* -- the ``DURATION_SAMPLE`` most recent durations per provider in
the window (nearest-rank). Exact for small windows, a close estimate for huge ones.
"""

import math
from datetime import timedelta

from django.conf import settings
from django.db.models import Count, Max, Min, Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from . import sandbox
from .models import NotificationDelivery

Status = NotificationDelivery.Status

PROVIDERS = ["apns", "fcm", "webpush", "email"]
PROVIDER_LABELS = {"apns": "APNs (iOS)", "fcm": "FCM (Android)", "webpush": "Web push", "email": "Email"}

#: Health rules. Success rate = sent / (requests - dead tokens): a gone device is not the provider's fault.
DEGRADED_BELOW = 95.0
DOWN_BELOW = 50.0
#: Fewer requests than this in the window and we do not judge (state "quiet").
MIN_VOLUME = 5

OK, DEGRADED, DOWN, QUIET = "ok", "degraded", "down", "quiet"
STATE_LABELS = {OK: _("OK"), DEGRADED: _("Degraded"), DOWN: _("Down"), QUIET: _("Not enough data")}

#: A retry due this long ago and still RETRYING means the retry job is stuck.
STUCK_AFTER = timedelta(minutes=10)
RETRY_JOB = "notifications.tasks.retry_failed_deliveries"
#: A device not seen for this long is "stale".
STALE_DEVICE_AFTER = timedelta(days=60)
DURATION_SAMPLE = 2000
PROBLEM_ROWS = 50
TOP_REASONS = 10

#: window key -> (label, total span, bucket size)
WINDOWS = {
    "1h": (_("Last hour"), timedelta(hours=1), timedelta(minutes=5)),
    "24h": (_("Last 24 hours"), timedelta(hours=24), timedelta(hours=1)),
    "7d": (_("Last 7 days"), timedelta(days=7), timedelta(days=1)),
}
DEFAULT_WINDOW = "24h"


def health_state(requests: int, success_rate: float | None) -> str:
    """Pure rule: ok / degraded / down, or quiet below MIN_VOLUME requests."""
    if requests < MIN_VOLUME or success_rate is None:
        return QUIET
    if success_rate < DOWN_BELOW:
        return DOWN
    if success_rate < DEGRADED_BELOW:
        return DEGRADED
    return OK


def percentile(sorted_values: list[int], fraction: float) -> int | None:
    """Nearest-rank percentile of an ascending list."""
    if not sorted_values:
        return None
    rank = max(1, math.ceil(fraction * len(sorted_values)))
    return sorted_values[rank - 1]


def mask_target(channel: str, target: str) -> str:
    """Emails are personal data: ``alice@example.com`` -> ``a***@example.com``. Other targets (install / subscription ids) are truncated."""
    if "@" in target:
        local, _sep, domain = target.rpartition("@")
        return f"{local[:1]}***@{domain}"
    return target if len(target) <= 24 else target[:24] + "…"


def _count(by_status, status):
    return by_status.get(status, {"n": 0})["n"]


def _base(since, club):
    """Delivery rows in the window. HELD rows (a push waiting for the end of someone's quiet hours) are
    not send attempts yet: leaving them out keeps success rates, charts and failure lists honest."""
    queryset = NotificationDelivery.objects.filter(created__gte=since).exclude(status=Status.HELD)
    return queryset.filter(club=club) if club else queryset


def provider_health(since, club=None, now=None) -> list[dict]:
    now = now or timezone.now()
    grouped = {}
    for row in _base(since, club).exclude(provider="").values("provider", "status").annotate(n=Count("pk"), last=Max("created")):
        grouped.setdefault(row["provider"], {})[row["status"]] = row
    retrying_now = dict(NotificationDelivery.objects.filter(status=Status.RETRYING).filter(**({"club": club} if club else {})).values_list("provider").annotate(n=Count("pk")))
    cards = []
    for provider in PROVIDERS:
        by_status = grouped.get(provider, {})
        total = sum(row["n"] for row in by_status.values())
        sent, dead = _count(by_status, Status.SENT), _count(by_status, Status.DEAD)
        judged = total - dead
        rate = round(100 * sent / judged, 1) if judged > 0 else None
        failure_times = [by_status[status]["last"] for status in (Status.FAILED, Status.DEAD) if status in by_status]
        durations = sorted(
            _base(since, club).filter(provider=provider, duration_ms__isnull=False).order_by("-created").values_list("duration_ms", flat=True)[:DURATION_SAMPLE],
        )
        state = health_state(total, rate)
        cards.append(
            {
                "provider": provider,
                "label": PROVIDER_LABELS[provider],
                "requests": total,
                "sent": sent,
                "success_rate": rate,
                "failed": _count(by_status, Status.FAILED),
                "dead": dead,
                "retrying": retrying_now.get(provider, 0),
                "p50": percentile(durations, 0.5),
                "p95": percentile(durations, 0.95),
                "last_success": by_status.get(Status.SENT, {}).get("last"),
                "last_failure": max(failure_times) if failure_times else None,
                "state": state,
                "state_label": STATE_LABELS[state],
            }
        )
    return cards


def retry_queue(club=None, now=None) -> dict:
    from features.models import JobRun, JobToggle

    now = now or timezone.now()
    queryset = NotificationDelivery.objects.filter(status=Status.RETRYING)
    if club:
        queryset = queryset.filter(club=club)
    stats = queryset.aggregate(waiting=Count("pk"), oldest=Min("next_attempt_at"), overdue=Count("pk", filter=Q(next_attempt_at__lt=now - STUCK_AFTER)))
    last_run = JobRun.objects.filter(name=RETRY_JOB).order_by("-started_at").first()
    enabled = JobToggle.is_enabled(RETRY_JOB)
    job_silent = last_run is None or last_run.started_at < now - STUCK_AFTER
    reasons = []
    if not enabled:
        reasons.append(_("The retry job is paused."))
    if job_silent:
        reasons.append(_("The retry job has not run in the last 10 minutes."))
    if stats["overdue"]:
        reasons.append(_("Retries are overdue by more than 10 minutes."))
    return {**stats, "job_enabled": enabled, "last_run": last_run, "stuck": bool(reasons), "stuck_reasons": reasons}


def time_series(since, window: str, club=None, now=None) -> list[dict]:
    """sent vs problem (failed/dead/retrying) per bucket, in ONE query of conditional counts (<= 24 buckets)."""
    now = now or timezone.now()
    _label, span, step = WINDOWS[window]
    count = int(span / step)
    start = now - span
    aggregates = {}
    edges = []
    for i in range(count):
        low, high = start + step * i, start + step * (i + 1)
        edges.append(low)
        in_bucket = Q(created__gte=low, created__lt=high) if i < count - 1 else Q(created__gte=low)
        aggregates[f"s{i}"] = Count("pk", filter=in_bucket & Q(status=Status.SENT))
        aggregates[f"f{i}"] = Count("pk", filter=in_bucket & ~Q(status=Status.SENT))
    totals = _base(max(since, start), club).aggregate(**aggregates)
    buckets = [{"start": edges[i], "sent": totals[f"s{i}"], "failed": totals[f"f{i}"]} for i in range(count)]
    peak = max([b["sent"] + b["failed"] for b in buckets] + [1])
    for bucket in buckets:
        bucket["peak"] = peak
        bucket["sent_pct"] = round(100 * bucket["sent"] / peak, 1)
        bucket["failed_pct"] = round(100 * bucket["failed"] / peak, 1)
    return buckets


def top_reasons(since, club=None) -> list[dict]:
    return list(_base(since, club).exclude(status=Status.SENT).values("provider", "last_error").annotate(count=Count("pk"), last_seen=Max("created")).order_by("-count", "-last_seen")[:TOP_REASONS])


def recent_problems(since, club=None) -> list:
    rows = list(_base(since, club).exclude(status=Status.SENT).select_related("club").order_by("-created")[:PROBLEM_ROWS])
    for row in rows:
        row.masked_target = mask_target(row.channel, row.target)
    return rows


def configuration() -> dict:
    from appapi.models import PushDevice
    from appapi.push import apns_configured, fcm_configured
    from features.models import EmailSuppression
    from mobile.models import PushSubscription

    stale_before = timezone.now() - STALE_DEVICE_AFTER
    devices = {}
    for platform in PushDevice.Platform.values:
        devices[platform] = {"enabled": 0, "disabled": 0, "stale": 0}
    for row in PushDevice.objects.values("platform", "enabled").annotate(n=Count("pk"), stale=Count("pk", filter=Q(last_seen__lt=stale_before))):
        bucket = devices.setdefault(row["platform"], {"enabled": 0, "disabled": 0, "stale": 0})
        bucket["enabled" if row["enabled"] else "disabled"] += row["n"]
        bucket["stale"] += row["stale"]
    # What is really set: the sandbox reports everything as configured so sends can run, which would hide a missing key here.
    apns, fcm = apns_configured(real=True), fcm_configured(real=True)
    vapid = bool(settings.VAPID_PRIVATE_KEY)
    subscriptions = PushSubscription.objects.count()
    for platform, configured in (("ios", apns), ("android", fcm)):
        devices[platform]["mismatch"] = not configured and (devices[platform]["enabled"] + devices[platform]["disabled"]) > 0
    return {
        "sandbox": sandbox.enabled(),
        "apns": apns,
        "fcm": fcm,
        "vapid": vapid,
        "email_backend": settings.EMAIL_BACKEND.rsplit(".", 1)[-1],
        "email_paused": EmailSuppression.is_on(),
        "devices": [{"platform": platform, "label": dict(PushDevice.Platform.choices)[platform], **counts} for platform, counts in devices.items()],
        "subscriptions": subscriptions,
        "web_mismatch": not vapid and subscriptions > 0,
        "any_mismatch": devices["ios"]["mismatch"] or devices["android"]["mismatch"] or (not vapid and subscriptions > 0),
    }


def build(window: str, club=None, now=None) -> dict:
    now = now or timezone.now()
    if window not in WINDOWS:
        window = DEFAULT_WINDOW
    since = now - WINDOWS[window][1]
    series = time_series(since, window, club, now)
    return {
        "window": window,
        "since": since,
        "health": provider_health(since, club, now),
        "queue": retry_queue(club, now),
        "series": series,
        "x_ticks": [series[0]["start"], series[len(series) // 2]["start"], now],
        "reasons": top_reasons(since, club),
        "problems": recent_problems(since, club),
        "config": configuration(),
    }
