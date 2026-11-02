"""Tell the platform admins when push (or its plumbing) breaks, once per outage. ``evaluate`` runs from the ``check_push_health`` task:
it computes the problems that exist now and reconciles them with ``PushAlert`` rows. A new problem opens an alert and mails the
recipients, a still-open one is repeated at most every 6 hours (or at once when it got worse), a gone one is resolved with a recovery
mail. Mail goes out through email, never push: push is what may be broken.

Alert keys: ``provider:<name>``, ``retry_queue``, ``outbox_backlog``, ``credentials:<name>``, ``sandbox_left_on``."""

import re
from dataclasses import dataclass
from datetime import timedelta

from django.utils import timezone

from . import gateways, health, sandbox
from .models import OutboxEntry, PushAlert, PushDevice, PushSettings, SandboxEvent, WebPushSubscription

REMINDER_INTERVAL = timedelta(hours=6)
HEALTH_WINDOW = timedelta(hours=1)
OUTBOX_BACKLOG_AFTER = timedelta(minutes=5)
SANDBOX_LEFT_ON_AFTER = timedelta(hours=1)
LABELS = {"apns": "APNs (iOS)", "fcm": "FCM (Android)", "webpush": "Web push", "email": "Email"}


@dataclass
class Problem:
    key: str
    title: str
    details: list


def _provider_problems(now) -> list[Problem]:
    problems = []
    for provider, card in health.provider_health(now - HEALTH_WINDOW).items():
        if card["state"] not in (health.DOWN, health.DEGRADED):
            continue
        word = "down" if card["state"] == health.DOWN else "degraded"
        details = [f"Success rate over the last hour: {card['success_rate']}% ({card['sent']} of {card['requests']} requests sent, {card['failed']} failed, {card['dead']} dead tokens)."]
        if card["last_failure"]:
            details.append(f"Last failure: {timezone.localtime(card['last_failure']):%d/%m %H:%M}.")
        problems.append(Problem(f"provider:{provider}", f"{LABELS[provider]} is {word}", details))
    return problems


def _queue_problems(now) -> list[Problem]:
    queue = health.retry_queue(now)
    if not queue["stuck"]:
        return []
    return [Problem("retry_queue", "The push retry queue is stuck", [*queue["reasons"], f"Deliveries waiting for a retry: {queue['waiting']} ({queue['overdue']} overdue)."])]


def _outbox_problems(now) -> list[Problem]:
    count = OutboxEntry.objects.filter(status=OutboxEntry.Status.PENDING, available_at__lt=now - OUTBOX_BACKLOG_AFTER).count()
    if not count:
        return []
    return [Problem("outbox_backlog", "The notification outbox is backed up", [f"{count} entries have been waiting for more than 5 minutes; the outbox job may not be running."])]


def _credential_problems() -> list[Problem]:
    """Devices or subscriptions exist for a provider that is enabled, but its credentials are not configured (so nothing can be delivered)."""
    if sandbox.is_on():
        return []
    ps = PushSettings.get()
    in_use = {
        "apns": ps.apns_enabled and PushDevice.objects.filter(enabled=True, platform=PushDevice.Platform.IOS).exists(),
        "fcm": ps.fcm_enabled and PushDevice.objects.filter(enabled=True, platform=PushDevice.Platform.ANDROID).exists(),
        "webpush": ps.webpush_enabled and WebPushSubscription.objects.exists(),
    }
    problems = []
    for provider, used in in_use.items():
        status = gateways.credentials_status(provider)
        if used and not status["configured"]:
            problems.append(Problem(f"credentials:{provider}", f"{LABELS[provider]} has no credentials", [f"Missing environment variables: {', '.join(status['missing'])}. Notifications through it cannot be delivered."]))
    return problems


def _sandbox_problems(now) -> list[Problem]:
    if not sandbox.is_on(now):
        return []
    event = SandboxEvent.objects.filter(action="sandbox_on").order_by("-created").first()
    if event is not None and event.created > now - SANDBOX_LEFT_ON_AFTER:
        return []
    return [Problem("sandbox_left_on", "The sending sandbox is on: nothing is delivered to users", ["Pushes and notification emails are captured instead of sent. Switch back to live on the control panel."])]


def current_problems(now=None) -> list[Problem]:
    now = now or timezone.now()
    return [*_provider_problems(now), *_queue_problems(now), *_outbox_problems(now), *_credential_problems(), *_sandbox_problems(now)]


def recipients() -> list[str]:
    """``PushSettings.alert_emails`` (comma/space/semicolon separated), else the superusers of the platform."""
    configured = [e for e in re.split(r"[,;\s]+", PushSettings.get().alert_emails or "") if "@" in e]
    if configured:
        return sorted(set(configured))
    from controlpanel.services.platform_admins import platform_admins

    return sorted({p.profile.email for p in platform_admins() if p.is_superuser and p.profile.is_active and p.profile.email})


def _send(subject: str, lines: list[str]) -> bool:
    from django.core.mail import EmailMessage

    from rosterchief.mail import send_message

    to = recipients()
    if not to:
        return False
    try:
        return send_message(EmailMessage(f"[RosterChief] {subject}", "\n".join(lines), to=to), fail_silently=True)
    except Exception:
        return False


def _since(alert) -> str:
    return f"{timezone.localtime(alert.first_seen):%d/%m/%Y %H:%M}"


def _notify(alert: PushAlert, problem: Problem, *, reminder: bool, now) -> None:
    subject = f"Push problem (still open): {problem.title}" if reminder else f"Push problem: {problem.title}"
    lines = [f"{problem.title}.", "", f"Since: {_since(alert)}", *problem.details]
    if _send(subject, lines):
        alert.last_notified_at = now
        alert.notifications_sent += 1
        alert.save(update_fields=["last_notified_at", "notifications_sent"])


def evaluate(now=None) -> dict:
    """Reconcile the current problems with the PushAlert rows; returns counts for the job log."""
    now = now or timezone.now()
    problems = {problem.key: problem for problem in current_problems(now)}
    open_alerts = {alert.key: alert for alert in PushAlert.objects.filter(resolved_at__isnull=True)}
    counts = {"opened": 0, "reminded": 0, "escalated": 0, "resolved": 0}
    for key, problem in problems.items():
        alert = open_alerts.get(key)
        if alert is None:
            alert = PushAlert.objects.create(key=key, message=problem.title[:500])
            _notify(alert, problem, reminder=False, now=now)
            counts["opened"] += 1
        elif alert.message != problem.title[:500]:  # it got worse (degraded -> down): tell at once
            alert.message = problem.title[:500]
            alert.save(update_fields=["message"])
            _notify(alert, problem, reminder=False, now=now)
            counts["escalated"] += 1
        elif alert.last_notified_at is None or now - alert.last_notified_at >= REMINDER_INTERVAL:
            _notify(alert, problem, reminder=alert.last_notified_at is not None, now=now)
            counts["reminded"] += 1
    for key, alert in open_alerts.items():
        if key in problems:
            continue
        alert.resolved_at = now
        alert.save(update_fields=["resolved_at"])
        if alert.notifications_sent:
            _send(f"Push recovered: {alert.message}", [f"{alert.message} -- this is back to normal.", f"Since: {_since(alert)}", f"Resolved: {timezone.localtime(now):%d/%m/%Y %H:%M}"])
        counts["resolved"] += 1
    return counts
