"""Tell the platform admins when push (or its plumbing) breaks -- once per outage.

``evaluate()`` runs from the ``check_push_health`` job every few minutes. It computes the problems
that exist *now* (a provider down or degraded over the last hour, a stuck retry queue, a provider
that had credentials and lost them, a backed-up outbox) and reconciles them with ``PushAlert``
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
from .models import OutboxEntry, ProviderConfigState, PushAlert

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


_CONFIG_PROVIDERS = (("apns", "apns"), ("fcm", "fcm"), ("webpush", "vapid"))


def track_configuration(config=None, now=None) -> None:
    """Remember which providers have credentials, and when one that did loses them. A provider that has never
    been configured stays quiet; only the configured -> not configured switch is an incident, and it lasts
    until the credentials are back."""
    config = config or monitoring.configuration()
    now = now or timezone.now()
    for provider, config_key in _CONFIG_PROVIDERS:
        configured = bool(config[config_key])
        state, created = ProviderConfigState.objects.get_or_create(provider=provider, defaults={"configured": configured, "changed_at": now})
        if created:
            continue
        if configured and (not state.configured or state.lost_at):
            state.configured, state.lost_at, state.changed_at = True, None, now
            state.save(update_fields=["configured", "lost_at", "changed_at"])
        elif not configured and state.configured:
            state.configured, state.lost_at, state.changed_at = False, now, now
            state.save(update_fields=["configured", "lost_at", "changed_at"])


def _config_problems() -> list[Problem]:
    problems = []
    for state in ProviderConfigState.objects.filter(lost_at__isnull=False):
        label = monitoring.PROVIDER_LABELS.get(state.provider, state.provider)
        problems.append(
            Problem(
                f"config:{state.provider}",
                WARNING,
                _("%(provider)s is no longer configured") % {"provider": label},
                [
                    _("Its credentials were set before and are gone since %(when)s, so notifications through it cannot be delivered. Restore them on the server.") % {"when": timezone.localtime(state.lost_at).strftime("%d/%m %H:%M")},
                ],
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


def _sandbox_problems(now) -> list[Problem]:
    """While the sandbox is on nothing reaches anybody: an admin must know it is not a quiet day."""
    from . import sandbox

    if not sandbox.enabled(now):
        return []
    state = sandbox.status(now)
    details = [_("Pushes and notification emails are captured instead of sent. Switch back to live on the control panel (Push monitoring > Sending sandbox).")]
    if state["expires_at"]:
        details.append(_("It switches back to live by itself at %(when)s.") % {"when": timezone.localtime(state["expires_at"]).strftime("%d/%m/%Y %H:%M")})
    if state["changed_by"]:
        details.append(_("Switched on by %(who)s.") % {"who": state["changed_by"]})
    return [Problem("sandbox", WARNING, _("The sending sandbox is on: nothing is delivered to users"), details)]


def current_problems(now=None) -> list[Problem]:
    now = now or timezone.now()
    return [*_provider_problems(now), *_queue_problems(now), *_config_problems(), *_outbox_problems(now), *_sandbox_problems(now)]


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
    from . import sandbox

    now = now or timezone.now()
    sandbox.expire_if_due(now)  # a sandbox period that ran out is recorded and ends here
    track_configuration(now=now)
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
