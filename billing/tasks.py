"""Celery tasks behind the billing beat schedule entries (see
rosterchief/settings.CELERY_BEAT_SCHEDULE and features/jobs.py).

Each mirrors its management command's *acting* behaviour exactly -- manage.py's own
--dry-run/--commit flags exist for a human at a terminal to preview first (see
billing/management/commands/), which a beat schedule has no terminal to do. These always
act, the same as the crontab entries they replace always passed --commit.
"""

from celery import shared_task
from django.utils import timezone

from billing.services import BillingError
from billing.services.dues import archivable_clubs, renew, subscriptions_due_for_renewal
from billing.services.reminders import reminders_to_send, send_reminder
from club.models import Club
from features.models import JobToggle, Maintenance


def _stand_down():
    # Loud, not silent -- see events/tasks.py for why these raise instead of skipping quietly.
    raise RuntimeError("Platform is in maintenance mode; this job stood down.")


def _check_enabled(name):
    if not JobToggle.is_enabled(name):
        raise RuntimeError("This job is disabled in the control panel.")


@shared_task(name="billing.tasks.renew_subscriptions")
def renew_subscriptions():
    if Maintenance.is_on():
        _stand_down()
    _check_enabled("billing.tasks.renew_subscriptions")

    due_for_renewal = subscriptions_due_for_renewal()
    if not due_for_renewal:
        return "Nothing to renew."

    renewed, failures = 0, []
    for subscription in due_for_renewal:
        try:
            renew(subscription)
            renewed += 1
        except BillingError as error:
            # One unpriced plan must not stop every other club from being billed.
            failures.append(f"{subscription.club}: {error}")

    if failures:
        raise RuntimeError(f"Renewed {renewed} club(s), {len(failures)} failed:\n  " + "\n  ".join(failures))

    return f"Renewed {renewed} club(s)."


@shared_task(name="billing.tasks.send_billing_reminders")
def send_billing_reminders():
    if Maintenance.is_on():
        _stand_down()
    _check_enabled("billing.tasks.send_billing_reminders")

    clubs = Club.objects.active().select_related("subscription", "subscription__plan").order_by("name")
    sendable = [result for result in reminders_to_send(clubs) if result.sent]

    if not sendable:
        return "Nothing owing. No reminders to send."

    sent, failures = 0, []
    for result in sendable:
        try:
            send_reminder(result.club, result.notice, recipients=result.recipients)
            sent += 1
        except OSError as error:
            # One bad address or a momentary SMTP failure must not stop the rest of the run.
            failures.append(f"{result.club}: {error}")

    if failures:
        raise RuntimeError(f"Sent {sent} reminder(s), {len(failures)} failed:\n  " + "\n  ".join(failures))

    return f"Sent {sent} reminder(s)."


@shared_task(name="billing.tasks.archive_overdue_clubs")
def archive_overdue_clubs():
    if Maintenance.is_on():
        _stand_down()
    _check_enabled("billing.tasks.archive_overdue_clubs")

    overdue = list(archivable_clubs(timezone.localdate()))
    for due in overdue:
        due.club.archive()

    return f"Archived {len(overdue)} club(s)."
