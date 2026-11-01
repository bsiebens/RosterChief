"""Scheduled billing work (Celery beat; schedules in settings.CELERY_BEAT_SCHEDULE). Every task is idempotent and takes
``dry_run`` to report what it would do. One club failing never stops the others; failures fail the task at the end."""

import logging

from celery import shared_task

from controlpanel import audit

from .services import dues as dues_svc
from .services import reminders as reminders_svc

log = logging.getLogger(__name__)


def _finish(label: str, done: list[str], failures: list[str]) -> str:
    summary = f"{label}: {len(done)} done" + (f", {len(failures)} failed" if failures else "")
    if failures:
        raise RuntimeError(summary + " -- " + "; ".join(failures))
    return summary


@shared_task(name="billing.renew_subscriptions")
def renew_subscriptions(dry_run: bool = False) -> str:
    """Open (and invoice) the next period for clubs whose renewal lead time has been reached."""
    done, failures = [], []
    for billing in dues_svc.subscriptions_due_for_renewal():
        club = billing.club
        try:
            if dry_run:
                done.append(f"would renew {club.slug}")
                continue
            due = dues_svc.renew(billing)
            audit.record(None, "billing.period_renewed", club=club, target=due)
            done.append(club.slug)
        except Exception as e:
            log.exception("renewal failed for %s", club.slug)
            failures.append(f"{club.slug}: {e}")
    return _finish("renew_subscriptions", done, failures)


@shared_task(name="billing.send_billing_reminders")
def send_billing_reminders(dry_run: bool = False, force: bool = False) -> str:
    """Email club administrators once per escalation level for every owing period."""
    done, failures = [], []
    for due, level in reminders_svc.reminders_to_send(force=force):
        try:
            if dry_run:
                done.append(f"would remind {due.club.slug} ({level})")
            elif reminders_svc.send_reminder(due, level):
                done.append(f"{due.club.slug} ({level})")
        except Exception as e:
            log.exception("reminder failed for %s", due.club.slug)
            failures.append(f"{due.club.slug}: {e}")
    return _finish("send_billing_reminders", done, failures)


@shared_task(name="billing.archive_overdue_clubs")
def archive_overdue_clubs(dry_run: bool = False) -> str:
    """Archive active clubs whose period stayed unpaid past its grace (when the club's billing allows it)."""
    done, failures = [], []
    for club in dues_svc.archivable_clubs():
        try:
            if dry_run:
                done.append(f"would archive {club.slug}")
                continue
            dues_svc.archive_overdue(club)
            audit.record(None, "billing.club_archived_overdue", club=club, target=club)
            done.append(club.slug)
        except Exception as e:
            log.exception("archiving failed for %s", club.slug)
            failures.append(f"{club.slug}: {e}")
    return _finish("archive_overdue_clubs", done, failures)
