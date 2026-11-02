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
from . import BillingError

NUMBER_MAX_LENGTH = 32
AUTO_FORMAT = re.compile(r"^INV-\d{4}-\d+$")


def next_automatic_number(year: int) -> str:
    """Allocate the next ``INV-YYYY-00001`` number. Must run inside the transaction that creates the invoice."""
    InvoiceSequence.objects.get_or_create(year=year)
    seq = InvoiceSequence.objects.select_for_update().get(year=year)
    seq.last_number += 1
    seq.save(update_fields=["last_number"])
    return f"INV-{year}-{seq.last_number:05d}"


def clean_number(raw: str) -> str:
    """Validate a custom invoice number: trimmed, not blank, at most 32 characters, not the automatic format, unique.

    ``ValueError`` = invalid format (422); ``BillingError`` = already taken (409).
    """
    number = (raw or "").strip()
    if not number:
        raise ValueError("The invoice number cannot be blank.")
    if len(number) > NUMBER_MAX_LENGTH:
        raise ValueError(f"The invoice number can be at most {NUMBER_MAX_LENGTH} characters.")
    if AUTO_FORMAT.match(number):
        raise ValueError("Numbers like INV-YYYY-00001 are reserved for the automatic sequence; use another format.")
    if Invoice.objects.filter(number=number).exists():
        raise BillingError(f"Invoice number {number} is already in use.")
    return number


def build_snapshot(due: Due) -> dict:
    """Everything printed on the invoice, frozen: issuer, bill-to, line and amounts."""
    settings = BillingSettings.current()
    club = due.club
    return {
        "issuer": {
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
        },
        "bill_to": {
            "name": club.legal_name or club.name,
            "address": club.legal_address,
            "zip_code": club.legal_zip_code,
            "city": club.legal_city,
            "country": str(club.legal_country or ""),
            "country_name": club.legal_country.name if club.legal_country else "",
            "vat_id": club.vat_id,
        },
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
    existing = Invoice.objects.filter(due=due).first()
    if existing:
        return existing
    custom = clean_number(number) if number is not None else None
    today = timezone.localdate()
    snapshot = build_snapshot(due)
    try:
        with transaction.atomic():
            invoice = Invoice.objects.create(due=due, number=custom or next_automatic_number(today.year), custom_number=custom is not None, issued_at=today, snapshot=snapshot)
    except IntegrityError:
        existing = Invoice.objects.filter(due=due).first()
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
    return {
        "invoice": invoice,
        "snap": snap,
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


def mark_invoice_sent_manually(invoice: Invoice, *, actor=None) -> Invoice:
    invoice.sent_at, invoice.sent_method, invoice.sent_by = timezone.now(), Invoice.MANUAL, actor
    invoice.save(update_fields=["sent_at", "sent_method", "sent_by", "modified"])
    return invoice
