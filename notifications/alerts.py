"""Tell the platform admins when push (or its plumbing) breaks -- once per outage.

``evaluate()`` runs from the ``check_push_health`` job every few minutes. It computes the problems
that exist *now* (a provider down or degraded over the last hour, a stuck retry queue, a provider
that has devices but no credentials, a backed-up outbox) and reconciles them with ``PushAlert``
rows: a new problem opens an alert and mails the platform admins, a still-open one is repeated at
most every ``REMINDER_INTERVAL``, a worse one (degraded -> down) is mailed at once, and one that is
gone is resolved with a "recovered" mail. The mail goes out through email, never push: push is the
thing that may be broken.
"""

from dataclasses import dataclass, field
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import EmailMultiAlternatives
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from rosterchief.mail import send_message

from . import monitoring
from .models import OutboxEntry, PushAlert

REMINDER_INTERVAL = timedelta(hours=6)
HEALTH_WINDOW = timedelta(hours=1)
OUTBOX_BACKLOG_AFTER = timedelta(minutes=5)

WARNING, CRITICAL = "warning", "critical"
_RANK = {WARNING: 1, CRITICAL: 2}


@dataclass
class Problem:
    key: str
    severity: str
    title: str
    details: list = field(default_factory=list)


def _provider_problems(now) -> list[Problem]:
    problems = []
    for card in monitoring.provider_health(now - HEALTH_WINDOW, now=now):
        if card["state"] not in (monitoring.DOWN, monitoring.DEGRADED):
            continue
        down = card["state"] == monitoring.DOWN
        label = card["label"]
        details = [
            _("Success rate over the last hour: %(rate)s%% (%(sent)s of %(requests)s requests sent, %(failed)s failed, %(dead)s dead tokens).")
            % {"rate": card["success_rate"], "sent": card["sent"], "requests": card["requests"], "failed": card["failed"], "dead": card["dead"]},
        ]
        if card["last_failure"]:
            details.append(_("Last failure: %(when)s.") % {"when": timezone.localtime(card["last_failure"]).strftime("%d/%m %H:%M")})
        title = _("%(provider)s is down") % {"provider": label} if down else _("%(provider)s is degraded") % {"provider": label}
        problems.append(Problem(f"provider:{card['provider']}", CRITICAL if down else WARNING, title, details))
    return problems


def _queue_problems(now) -> list[Problem]:
    queue = monitoring.retry_queue(now=now)
    if not queue["stuck"]:
        return []
    details = [str(reason) for reason in queue["stuck_reasons"]]
    details.append(_("Deliveries waiting for a retry: %(waiting)s (%(overdue)s overdue).") % {"waiting": queue["waiting"], "overdue": queue["overdue"]})
    return [Problem("retry_queue", CRITICAL, _("The push retry queue is stuck"), details)]


def _config_problems() -> list[Problem]:
    config = monitoring.configuration()
    problems = []
    for key, label, mismatch in (
        ("apns", monitoring.PROVIDER_LABELS["apns"], _device_mismatch(config, "ios")),
        ("fcm", monitoring.PROVIDER_LABELS["fcm"], _device_mismatch(config, "android")),
        ("webpush", monitoring.PROVIDER_LABELS["webpush"], config["web_mismatch"]),
    ):
        if mismatch:
            problems.append(
                Problem(
                    f"config:{key}",
                    WARNING,
                    _("%(provider)s is not configured but devices are registered for it") % {"provider": label},
                    [_("Notifications to those devices cannot be delivered until the credentials are set on the server.")],
                )
            )
    return problems


def _device_mismatch(config, platform) -> bool:
    return any(row["platform"] == platform and row.get("mismatch") for row in config["devices"])


def _outbox_problems(now) -> list[Problem]:
    stale = OutboxEntry.objects.filter(status=OutboxEntry.Status.PENDING, available_at__lt=now - OUTBOX_BACKLOG_AFTER)
    count = stale.count()
    if not count:
        return []
    return [Problem("outbox", CRITICAL, _("The notification outbox is backed up"), [_("%(count)s entries have been waiting for more than 5 minutes; the outbox job may not be running.") % {"count": count}])]


def current_problems(now=None) -> list[Problem]:
    now = now or timezone.now()
    return [*_provider_problems(now), *_queue_problems(now), *_config_problems(), *_outbox_problems(now)]


def recipients() -> list[str]:
    """Platform admins: active superusers with an email address."""
    emails = get_user_model().objects.filter(is_active=True, is_superuser=True).exclude(email="").values_list("email", flat=True)
    return sorted(set(emails))


def monitoring_url() -> str:
    path = reverse("controlpanel:push_monitoring")
    base = getattr(settings, "ROSTERCHIEF_BASE_DOMAIN", "")
    return f"https://{base}{path}" if base and base != "localhost" else path


def _send(subject: str, lines: list[str]) -> bool:
    to = recipients()
    if not to:
        return False
    body = "\n".join([*lines, "", _("Push monitoring: %(url)s") % {"url": monitoring_url()}])
    message = EmailMultiAlternatives(subject, body, settings.DEFAULT_FROM_EMAIL, to)
    return send_message(message, fail_silently=True)


def _since(alert) -> str:
    return timezone.localtime(alert.first_seen).strftime("%d/%m/%Y %H:%M")


def _send_alert(alert: PushAlert, problem: Problem, *, reminder: bool, now) -> None:
    subject = "[RosterChief] " + (_("Push problem (still open): %(title)s") if reminder else _("Push problem: %(title)s")) % {"title": problem.title}
    lines = [problem.title + ".", "", _("Since: %(since)s") % {"since": _since(alert)}, *problem.details]
    if alert.notifications_sent:
        lines += ["", _("Alerts sent so far: %(count)s") % {"count": alert.notifications_sent}]
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
        message = problem.title[:500]
        alert = open_alerts.get(key)
        if alert is None:
            alert, _created = PushAlert.objects.update_or_create(key=key, defaults={"severity": problem.severity, "message": message, "first_seen": now, "last_notified_at": None, "notifications_sent": 0, "resolved_at": None})
            _send_alert(alert, problem, reminder=False, now=now)
            counts["opened"] += 1
            continue
        escalated = _RANK.get(problem.severity, 0) > _RANK.get(alert.severity, 0)
        if escalated or alert.message != message or alert.severity != problem.severity:
            alert.severity, alert.message = problem.severity, message
            alert.save(update_fields=["severity", "message"])
        if escalated:
            _send_alert(alert, problem, reminder=False, now=now)
            counts["escalated"] += 1
        elif alert.last_notified_at is None or now - alert.last_notified_at >= REMINDER_INTERVAL:
            _send_alert(alert, problem, reminder=alert.last_notified_at is not None, now=now)
            counts["reminded"] += 1

    for key, alert in open_alerts.items():
        if key in problems:
            continue
        alert.resolved_at = now
        alert.save(update_fields=["resolved_at"])
        if alert.notifications_sent:
            _send(
                "[RosterChief] " + _("Push recovered: %(title)s") % {"title": alert.message},
                [_("%(title)s -- this is back to normal.") % {"title": alert.message}, _("Since: %(since)s") % {"since": _since(alert)}, _("Resolved: %(when)s") % {"when": timezone.localtime(now).strftime("%d/%m/%Y %H:%M")}],
            )
        counts["resolved"] += 1
    return counts
