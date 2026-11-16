"""Payment reminders: one email per escalation level per period."""

import datetime

from django.utils import timezone

from billing.emails import send_branded, texts

from ..models import BillingSettings, Due
from .dues import dues_overdue, owing_dues, today
from .invoices import club_recipients

INFO, WARNING, ERROR = "info", "warning", "error"
RANK = {"": 0, INFO: 1, WARNING: 2, ERROR: 3}
URGENT_DAYS = 7  # this close to the end of grace the reminder escalates to "error"


def level_for(due: Due, day: datetime.date | None = None) -> str:
    day = day or today()
    if due.is_issued_ahead(day):
        return INFO
    if due.grace_until < day or (due.grace_until - day).days <= URGENT_DAYS:
        return ERROR
    return WARNING


def reminders_to_send(day: datetime.date | None = None, *, force: bool = False):
    """``[(due, level)]`` for owing periods of active clubs whose level is higher than the last reminder sent."""
    day = day or today()
    out = []
    for due in owing_dues().filter(club__archived_at__isnull=True).exclude(amount=0, vat_amount=0):
        level = level_for(due, day)
        if force or RANK[level] > RANK.get(due.last_reminder_level, 0):
            out.append((due, level))
    return out


def send_reminder(due: Due, level: str) -> bool:
    recipients = club_recipients(due.club)
    if not recipients:
        return False
    subject = {INFO: "Upcoming invoice", WARNING: "Payment due", ERROR: "Payment overdue"}[level]
    body = f"{subject}: EUR {due.balance} is open for {due.club.name} ({due.period_start} to {due.period_end}). Grace ends {due.grace_until}."
    extra = ""
    if level == ERROR:
        if due.club.billing.auto_archive:
            days = BillingSettings.current().archive_after_days
            extra = f"If it stays unpaid the club is first moved to the free plan and archived {days} days later."
        else:
            extra = "If it stays unpaid the club is moved to the free plan."
        body += f" {extra}"
    summary = [("Club", due.club.name), ("Period", f"{due.period_start} to {due.period_end}"), ("Grace ends", str(due.grace_until)), ("Open balance", f"EUR {due.balance}")]
    ctx = {"t": texts(), "title": subject, "intro": f"{subject}: EUR {due.balance} is open for {due.club.name}.", "rows": summary, "extra": extra}
    if not send_branded(subject=f"{subject} - {due.club.name}", text=body, template="billing/email/reminder.html", ctx=ctx, to=recipients, fail_silently=True):
        return False
    due.last_reminder_level, due.last_reminder_sent_at = level, timezone.now()
    due.save(update_fields=["last_reminder_level", "last_reminder_sent_at", "modified"])
    return True


__all__ = ["dues_overdue", "level_for", "reminders_to_send", "send_reminder"]
