"""Emailing a club's admins about money it owes the platform.

Built on the same BillingNotice the on-screen banner uses (notices.py), so the email and the
banner can never disagree about how much is owed or how long is left.

**Sent once per escalation level, not once per run.** The command is on a daily cron; a club
that owes money for a month must not receive thirty identical emails. ``Due.last_reminder_level``
records the level last mailed, so an escalation (info -> warning -> error) always gets through
and a repeat of the same level never does.
"""

from dataclasses import dataclass

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.translation import gettext as _

from billing.services.notices import BillingNotice, club_billing_notice
from club.models import ClubRole
from rosterchief.mail import send_message


@dataclass(frozen=True)
class ReminderResult:
    club: object
    notice: BillingNotice
    recipients: list[str]
    sent: bool
    skipped_reason: str = ""


def admin_emails(club) -> list[str]:
    """Every club admin we can actually reach, de-duplicated and order-stable.

    A club with admins but no email addresses returns empty — the caller reports that rather
    than silently counting it as reminded.
    """
    roles = ClubRole.objects.filter(club=club, role=ClubRole.Roles.ADMIN).select_related("member", "member__user").order_by("member__last_name", "member__first_name")

    seen, emails = set(), []
    for role in roles:
        email = role.member.contact_email
        if email and email not in seen:
            seen.add(email)
            emails.append(email)

    return emails


def needs_reminder(due, notice: BillingNotice) -> bool:
    """True when this due has not yet been mailed at its current level."""
    return due.last_reminder_level != notice.level


def send_reminder(club, notice: BillingNotice, *, recipients: list[str]) -> None:
    """Render and send one reminder, then record the level so it is not repeated."""
    context = {
        "club": club,
        "notice": notice,
        "due": notice.due,
        "billing_contact": settings.BILLING_CONTACT_EMAIL,
    }
    subject = render_to_string("billing/email/reminder_subject.txt", context).strip()
    text_body = render_to_string("billing/email/reminder.txt", context)

    message = EmailMultiAlternatives(subject=subject, body=text_body, from_email=settings.DEFAULT_FROM_EMAIL, to=recipients)
    if not send_message(message, fail_silently=False):
        return

    notice.due.last_reminder_level = notice.level
    notice.due.last_reminder_sent_at = timezone.now()
    notice.due.save(update_fields=["last_reminder_level", "last_reminder_sent_at", "modified"])


def reminders_to_send(clubs, today=None, *, force: bool = False) -> list[ReminderResult]:
    """Work out who would be reminded, without sending anything.

    Returned whether or not each one is actually sendable, so the command can report a club
    with no reachable admin instead of skipping it in silence — an unreachable club is exactly
    the one that gets archived without ever having been told.
    """
    results = []
    for club in clubs:
        notice = club_billing_notice(club, today)
        if notice is None:
            continue

        recipients = admin_emails(club)
        if not recipients:
            results.append(ReminderResult(club=club, notice=notice, recipients=[], sent=False, skipped_reason=_("no club admin with an email address")))
            continue
        if not force and not needs_reminder(notice.due, notice):
            results.append(ReminderResult(club=club, notice=notice, recipients=recipients, sent=False, skipped_reason=_("already reminded at this level")))
            continue

        results.append(ReminderResult(club=club, notice=notice, recipients=recipients, sent=True))

    return results
