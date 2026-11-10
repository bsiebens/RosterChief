"""Scheduled billing work (Celery beat; schedules in settings.CELERY_BEAT_SCHEDULE). Every task is idempotent and takes
``dry_run`` to report what it would do. One club failing never stops the others; failures fail the task at the end."""

import logging

from celery import shared_task

from controlpanel import audit

from .services import dues as dues_svc
from .services import invoicing_mode
from .services import reminders as reminders_svc

log = logging.getLogger(__name__)


def _finish(label: str, done: list[str], failures: list[str]) -> str:
    summary = f"{label}: {len(done)} done" + (f", {len(failures)} failed" if failures else "")
    if failures:
        raise RuntimeError(summary + " -- " + "; ".join(failures))
    return summary


@shared_task(name="billing.renew_subscriptions")
def renew_subscriptions(dry_run: bool = False) -> str:
    """Open (and invoice) the next period for clubs whose renewal lead time has been reached. Does nothing while automatic invoicing is off
    (``BillingSettings.auto_invoicing``): then the periods are opened and invoiced by hand and the admins are warned instead."""
    if not invoicing_mode.auto_invoicing_enabled():
        return "renew_subscriptions: skipped, automatic invoicing is off"
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


@shared_task(name="billing.downgrade_overdue_clubs")
def downgrade_overdue_clubs(dry_run: bool = False) -> str:
    """Move clubs with an overdue period to the default plan's entitlements, and lift that once nothing is overdue.

    Independent of ``auto_archive``: downgrading is not archiving. Idempotent (``downgraded_at`` is only set once).
    """
    done, failures = [], []
    for club in dues_svc.clubs_to_downgrade():
        try:
            if dry_run:
                done.append(f"would downgrade {club.slug}")
                continue
            if dues_svc.downgrade_club(club):
                audit.record(None, "billing.club_downgraded", club=club, target=club)
                done.append(f"{club.slug} downgraded")
        except Exception as e:
            log.exception("downgrading failed for %s", club.slug)
            failures.append(f"{club.slug}: {e}")
    for club in dues_svc.clubs_to_restore():
        try:
            if dry_run:
                done.append(f"would restore {club.slug}")
            elif dues_svc.restore_if_settled(club):  # audits billing.club_restored_after_payment
                done.append(f"{club.slug} restored")
        except Exception as e:
            log.exception("restoring failed for %s", club.slug)
            failures.append(f"{club.slug}: {e}")
    return _finish("downgrade_overdue_clubs", done, failures)


@shared_task(name="billing.archive_overdue_clubs")
def archive_overdue_clubs(dry_run: bool = False) -> str:
    """Archive active clubs that stayed unpaid for ``archive_after_days`` after being downgraded (when billing allows it)."""
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


@shared_task(name="billing.send_invoicing_digest")
def send_invoicing_digest() -> str:
    """Weekly overview of the clubs that need an invoice (only while automatic invoicing is off)."""
    if invoicing_mode.auto_invoicing_enabled():
        return "send_invoicing_digest: skipped, automatic invoicing is on"
    return f"send_invoicing_digest: {invoicing_mode.send_digest()} club(s) listed"


@shared_task(name="billing.send_invoicing_alerts")
def send_invoicing_alerts() -> str:
    """Daily: mail the clubs that entered their invoicing window today (once per club and period; only while automatic invoicing is off)."""
    if invoicing_mode.auto_invoicing_enabled():
        return "send_invoicing_alerts: skipped, automatic invoicing is on"
    return f"send_invoicing_alerts: {invoicing_mode.send_window_alerts()} club(s) alerted"
