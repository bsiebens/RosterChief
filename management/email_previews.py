"""Sample renders of every branded email this app can send -- the Settings >
Email previews page (management/templates/management/email_previews.html),
so a club can see exactly what a member/parent receives without anything
actually being sent.

Each entry renders the *real* templates the real send functions use (see
members.services.claims.send_claim_approved_email and
club.services.invoicing.send_invoice_email/send_reminder_email) against a
hand-built sample context -- never a real Member/ClubMembership/DuesInvoice
row, so this needs nothing from the database beyond the current club itself,
and can't leak anything real. Adding a new branded email later means adding
one entry here, not touching the view or template.
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


def _claim_approved_context(club, request):
    return {
        "club": club,
        "child": "Jamie Doe",
        "parent_first_name": "Alex",
        "set_password_url": f"https://{club.slug}.rosterchief.app/accounts/password/reset/key/example/",
        "request": request,
    }


def _dues_invoice_context(club, request, *, overdue):
    today = timezone.now().date()
    due_date = today - datetime.timedelta(days=5) if overdue else today + datetime.timedelta(days=14)
    invoice = SimpleNamespace(number="DUE-2026-00042", amount=Decimal("45.00"), due_date=due_date)
    membership = SimpleNamespace(season="2026-2027")
    member = SimpleNamespace(first_name="Jamie")
    return {"club": club, "invoice": invoice, "membership": membership, "member": member, "request": request}


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
        key="claim_approved",
        label=_("Parent claim approved"),
        description=_("Sent when a staff member approves a parent or guardian's claim to a child, from the Parent claims page."),
        subject_template="members/email/claim_approved_subject.txt",
        text_template="members/email/claim_approved.txt",
        html_template="members/email/claim_approved.html",
        build_context=_claim_approved_context,
    ),
]


def render_preview(preview: EmailPreview, *, club, request) -> dict:
    context = preview.build_context(club, request)
    subject = " ".join(render_to_string(preview.subject_template, context).split())
    text_body = render_to_string(preview.text_template, context).strip()
    html_body = render_to_string(preview.html_template, context)
    return {"subject": subject, "text": text_body, "html": html_body}
