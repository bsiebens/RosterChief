"""Invoices: numbering, PDF rendering, and sending."""

import datetime
import re
from decimal import Decimal

from django.core.mail import EmailMessage
from django.db import IntegrityError, transaction
from django.template.loader import render_to_string
from django.utils import timezone

from rosterchief.mail import send_message

from ..models import BillingSettings, Due, Invoice, InvoiceSequence
from . import BillingError, numbering

NUMBER_MAX_LENGTH = 32
LEGACY_AUTO_FORMAT = re.compile(r"^INV-\d{4}-\d+$")  # the original fixed format; always reserved, whatever the current pattern


def next_automatic_number(on_date: datetime.date | None = None) -> str:
    """Allocate the next automatic invoice number from the configured pattern. Must run inside the transaction that
    creates the invoice (see ``numbering.allocate``)."""
    on_date = on_date or timezone.localdate()
    pattern = BillingSettings.current().invoice_number_pattern
    return numbering.allocate(InvoiceSequence, pattern, on_date, lambda n: Invoice.objects.filter(number=n).exists())


def peek_next_automatic_number(pattern: str | None = None, on_date: datetime.date | None = None) -> str:
    """The number the next automatic invoice would get right now. Writes nothing."""
    pattern = pattern or BillingSettings.current().invoice_number_pattern
    return numbering.peek(InvoiceSequence, pattern, on_date or timezone.localdate(), lambda n: Invoice.objects.filter(number=n).exists())[0]


def clean_number(raw: str) -> str:
    """Validate a custom invoice number: trimmed, not blank, at most 32 characters, not in the automatic format, unique.

    Reserved (automatic-looking) are numbers matching the CURRENT invoice number pattern and the legacy ``INV-YYYY-n``
    format. Unique across ALL invoices, voided ones included.

    ``ValueError`` = invalid format (422); ``BillingError`` = already taken (409).
    """
    number = (raw or "").strip()
    if not number:
        raise ValueError("The invoice number cannot be blank.")
    if len(number) > NUMBER_MAX_LENGTH:
        raise ValueError(f"The invoice number can be at most {NUMBER_MAX_LENGTH} characters.")
    pattern = BillingSettings.current().invoice_number_pattern
    if numbering.matches_pattern(pattern, number) or LEGACY_AUTO_FORMAT.match(number):
        raise ValueError(f"This format is reserved for the automatic sequence (pattern {pattern}); use another format.")
    if Invoice.objects.filter(number=number).exists():
        raise BillingError(f"Invoice number {number} is already in use.")
    return number


def issuer_snapshot(settings: BillingSettings | None = None) -> dict:
    settings = settings or BillingSettings.current()
    return {
        "legal_name": settings.legal_name,
        "address": settings.address,
        "zip_code": settings.zip_code,
        "city": settings.city,
        "country": str(settings.country or ""),
        "country_name": settings.country.name if settings.country else "",
        "vat_id": settings.vat_id,
        "iban": settings.iban,
        "bic": settings.bic,
        "contact_email": settings.contact_email,
        "website": settings.website,
        "invoice_footer": settings.invoice_footer,
        "payment_terms": settings.payment_terms,
    }


def bill_to_snapshot(club) -> dict:
    return {
        "name": club.legal_name or club.name,
        "address": club.legal_address,
        "zip_code": club.legal_zip_code,
        "city": club.legal_city,
        "country": str(club.legal_country or ""),
        "country_name": club.legal_country.name if club.legal_country else "",
        "vat_id": club.vat_id,
    }


def split_description(description: str) -> tuple[str, str]:
    """``"Pro - platform subscription"`` -> ``("Pro", "platform subscription")``: the plan name is shown bold, the rest as a sub-line."""
    name, _, detail = description.partition(" - ")
    return name.strip(), detail.strip()


def build_snapshot(due: Due) -> dict:
    """Everything printed on the invoice, frozen: issuer, bill-to, line and amounts."""
    return {
        "issuer": issuer_snapshot(),
        "bill_to": bill_to_snapshot(due.club),
        "description": f"{due.plan.name} - platform subscription",
        "period_start": due.period_start.isoformat(),
        "period_end": due.period_end.isoformat(),
        "due_date": due.grace_until.isoformat(),
        "net": f"{due.amount:.2f}",
        "vat": f"{due.vat_amount:.2f}",
        "total": f"{due.total:.2f}",
        "vat_rate": f"{due.vat_rate:.2f}",
        "reverse_charge": due.reverse_charge,
    }


def issue_invoice(due: Due, *, number: str | None = None) -> Invoice:
    """Create the invoice for a period (idempotent). Automatic numbers come from the gapless counter; ``number`` sets a
    custom one (only possible here, numbers never change afterwards) without touching the counter."""
    existing = Invoice.objects.filter(due=due, voided_at__isnull=True).first()
    if existing:
        return existing
    custom = clean_number(number) if number is not None else None
    today = timezone.localdate()
    snapshot = build_snapshot(due)
    try:
        with transaction.atomic():
            invoice = Invoice.objects.create(due=due, number=custom or next_automatic_number(today), custom_number=custom is not None, issued_at=today, snapshot=snapshot)
    except IntegrityError:
        existing = Invoice.objects.filter(due=due, voided_at__isnull=True).first()
        if existing:
            return existing
        raise BillingError(f"Invoice number {custom} is already in use.") from None
    _notify_platform_admins(invoice)
    return invoice


def _notify_platform_admins(invoice: Invoice) -> None:
    if invoice.due.total == 0:
        return
    from controlpanel.services.platform_admins import platform_admins

    emails = [p.profile.email for p in platform_admins() if p.profile.email]
    if not emails:
        return
    due = invoice.due
    message = EmailMessage(
        subject=f"New invoice {invoice.number} for {due.club.name}",
        body=f"Invoice {invoice.number} was issued for {due.club.name}: EUR {due.total} for {due.period_start} to {due.period_end}.",
        to=emails,
    )
    send_message(message, fail_silently=True)


def invoice_context(invoice: Invoice) -> dict:
    due = invoice.due
    snap = invoice.snapshot or build_snapshot(due)  # invoices from before snapshots render from live data
    payments = list(due.payments.all())
    paid = sum((p.amount for p in payments), Decimal("0"))
    replaced_by = invoice.replaced_by if invoice.replaced_by_id else None
    replaces = invoice.replaces
    void_note = invoice.credit_notes.filter(kind="void").first() if invoice.voided_at else None
    return {
        "voided": invoice.voided_at is not None,
        "voided_on": timezone.localtime(invoice.voided_at).date() if invoice.voided_at else None,
        "replaced_by_number": replaced_by.number if replaced_by else None,
        "void_credit_note_number": void_note.number if void_note else None,
        "replaces_number": replaces.number if replaces else None,
        "invoice": invoice,
        "snap": snap,
        "line_name": split_description(snap["description"])[0],
        "line_detail": split_description(snap["description"])[1],
        "issuer": snap["issuer"],
        "bill_to": snap["bill_to"],
        "period_start": datetime.date.fromisoformat(snap["period_start"]),
        "period_end": datetime.date.fromisoformat(snap["period_end"]),
        "due_date": datetime.date.fromisoformat(snap["due_date"]),
        "net": Decimal(snap["net"]),
        "vat": Decimal(snap["vat"]),
        "total": Decimal(snap["total"]),
        "vat_rate": Decimal(snap["vat_rate"]),
        "payments": payments,
        "balance": Decimal(snap["total"]) - paid,
    }


def invoice_html(invoice: Invoice) -> str:
    return render_to_string("billing/invoice.html", invoice_context(invoice))


def invoice_pdf(invoice: Invoice) -> bytes:
    try:
        from weasyprint import HTML
    except (ImportError, OSError) as e:  # system libraries (pango) missing
        raise BillingError("PDF rendering is not available on this server.") from e
    return HTML(string=invoice_html(invoice)).write_pdf()


def club_recipients(club) -> list[str]:
    from club.services import club_admins

    return sorted({u.email for u in club_admins(club) if u.email})


@transaction.atomic
def send_invoice(invoice: Invoice, *, actor=None) -> Invoice:
    """Email the PDF to the club's administrators."""
    _refuse_if_voided(invoice)
    recipients = club_recipients(invoice.due.club)
    if not recipients:
        raise BillingError("This club has no administrator with an email address.")
    message = EmailMessage(
        subject=f"Invoice {invoice.number}",
        body=f"Please find attached invoice {invoice.number} for {invoice.due.club.name}.",
        to=recipients,
    )
    message.attach(f"{invoice.number}.pdf", invoice_pdf(invoice), "application/pdf")
    if not send_message(message):
        raise BillingError("Automated email is paused, so the invoice was not sent.")
    invoice.sent_at, invoice.sent_method, invoice.sent_by = timezone.now(), Invoice.EMAIL, actor
    invoice.save(update_fields=["sent_at", "sent_method", "sent_by", "modified"])
    return invoice


def _refuse_if_voided(invoice: Invoice) -> None:
    if invoice.voided_at is not None:
        raise BillingError(f"Invoice {invoice.number} was voided; it can no longer be sent.")


def mark_invoice_sent_manually(invoice: Invoice, *, actor=None) -> Invoice:
    _refuse_if_voided(invoice)
    invoice.sent_at, invoice.sent_method, invoice.sent_by = timezone.now(), Invoice.MANUAL, actor
    invoice.save(update_fields=["sent_at", "sent_method", "sent_by", "modified"])
    return invoice
