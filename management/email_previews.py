"""Sample renders of every branded email this app can send -- the Email tab
on the Club identity page (management/templates/management/club_settings.html),
so a club can see exactly what a member/parent receives without anything
actually being sent.

Each entry renders the *real* templates the real send functions use (see
club.services.invoicing.send_invoice_email/send_reminder_email,
notifications.services.notify_members, and allauth's own password-reset flow
via templates/account/email/password_reset_key_message.html) against a
hand-built sample context -- never a real Member/ClubMembership/DuesInvoice/
Notification row, so this needs nothing from the database beyond the current
club itself, and can't leak anything real. Adding a new branded email later
means adding one entry here, not touching the view or template.
"""

import datetime
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from types import SimpleNamespace

from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


@dataclass(frozen=True)
class EmailPreview:
    key: str
    label: str
    description: str
    subject_template: str
    text_template: str
    html_template: str
    build_context: Callable[..., dict]


def _dues_invoice_context(club, request, *, overdue):
    today = timezone.now().date()
    due_date = today - datetime.timedelta(days=5) if overdue else today + datetime.timedelta(days=14)
    invoice = SimpleNamespace(number="DUE-2026-00042", amount=Decimal("45.00"), due_date=due_date)
    membership = SimpleNamespace(season="2026-2027")
    member = SimpleNamespace(first_name="Jamie")
    return {"club": club, "invoice": invoice, "membership": membership, "member": member, "request": request}


def _notification_context(club, request):
    notification = SimpleNamespace(title="Training moved to Tuesday", body="This week's U16 training moves from Thursday 19:00 to Tuesday 19:00, same location. See you there!")
    return {"club": club, "notification": notification, "request": request}


def _password_reset_context(club, request):
    # Mirrors allauth.account.internal.flows.password_reset.request_password_reset's
    # own context -- current_site is only used by the *stock* .txt template's
    # base_message.txt wrapper (our .html override doesn't reference it, but the
    # .txt preview does), username only matters when ACCOUNT_LOGIN_METHODS
    # includes "username" (it doesn't here -- login is email-only), so it's left
    # out entirely rather than faked.
    return {
        "club": club,
        "current_site": SimpleNamespace(name="RosterChief", domain="rosterchief.app"),
        "password_reset_url": f"https://{club.slug}.rosterchief.app/accounts/password/reset/key/example/",
        "request": request,
    }


EMAIL_PREVIEWS = [
    EmailPreview(
        key="dues_invoice",
        label=_("Membership invoice"),
        description=_("Sent when a staff member clicks “Send invoice” for one or more members on the Dues & billing page."),
        subject_template="club/email/dues_invoice_subject.txt",
        text_template="club/email/dues_invoice.txt",
        html_template="club/email/dues_invoice.html",
        build_context=lambda club, request: _dues_invoice_context(club, request, overdue=False),
    ),
    EmailPreview(
        key="dues_invoice_reminder",
        label=_("Invoice reminder"),
        description=_("Sent by the “Send reminders” button, once per invoice that's still unpaid past its due date."),
        subject_template="club/email/dues_invoice_reminder_subject.txt",
        text_template="club/email/dues_invoice_reminder.txt",
        html_template="club/email/dues_invoice_reminder.html",
        build_context=lambda club, request: _dues_invoice_context(club, request, overdue=True),
    ),
    EmailPreview(
        key="notification",
        label=_("Member notification"),
        description=_("Sent to a member (and always their parent/guardian too) when staff notify them about something -- e.g. the “Notify linked members” option when publishing news."),
        subject_template="notifications/email/notification_subject.txt",
        text_template="notifications/email/notification.txt",
        html_template="notifications/email/notification.html",
        build_context=_notification_context,
    ),
    EmailPreview(
        key="password_reset",
        label=_("Password reset"),
        description=_("Sent by the “Forgot your password?” link on the sign-in page."),
        subject_template="account/email/password_reset_key_subject.txt",
        text_template="account/email/password_reset_key_message.txt",
        html_template="account/email/password_reset_key_message.html",
        build_context=_password_reset_context,
    ),
]

EMAIL_PREVIEWS_BY_KEY = {preview.key: preview for preview in EMAIL_PREVIEWS}


def render_preview(preview: EmailPreview, *, club, request) -> dict:
    context = preview.build_context(club, request)
    subject = " ".join(render_to_string(preview.subject_template, context).split())
    text_body = render_to_string(preview.text_template, context).strip()
    html_body = render_to_string(preview.html_template, context)
    return {"subject": subject, "text": text_body, "html": html_body}
