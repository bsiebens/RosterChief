"""Invoices: numbering, PDF rendering, and sending."""

from django.core.mail import EmailMessage
from django.db import IntegrityError, transaction
from django.template.loader import render_to_string
from django.utils import timezone

from rosterchief.mail import send_message

from ..models import Due, Invoice
from . import BillingError


def _next_number(year: int) -> str:
    prefix = f"INV-{year}-"
    last = Invoice.objects.filter(number__startswith=prefix).order_by("-number").first()
    n = int(last.number.rsplit("-", 1)[1]) + 1 if last else 1
    return f"{prefix}{n:05d}"


def issue_invoice(due: Due) -> Invoice:
    """Create the invoice for a period (idempotent). One platform-wide gapless sequence per year."""
    existing = Invoice.objects.filter(due=due).first()
    if existing:
        return existing
    today = timezone.localdate()
    for _attempt in range(5):
        try:
            with transaction.atomic():
                invoice = Invoice.objects.create(due=due, number=_next_number(today.year), issued_at=today)
            break
        except IntegrityError:
            existing = Invoice.objects.filter(due=due).first()
            if existing:
                return existing
    else:  # pragma: no cover
        raise BillingError("Could not allocate an invoice number.")
    _notify_platform_admins(invoice)
    return invoice


def _notify_platform_admins(invoice: Invoice) -> None:
    if invoice.due.amount == 0:
        return
    from controlpanel.services.platform_admins import platform_admins

    emails = [p.profile.email for p in platform_admins() if p.profile.email]
    if not emails:
        return
    due = invoice.due
    message = EmailMessage(
        subject=f"New invoice {invoice.number} for {due.club.name}",
        body=f"Invoice {invoice.number} was issued for {due.club.name}: EUR {due.amount} for {due.period_start} to {due.period_end}.",
        to=emails,
    )
    send_message(message, fail_silently=True)


def invoice_context(invoice: Invoice) -> dict:
    due = invoice.due
    club = due.club
    return {
        "invoice": invoice,
        "due": due,
        "club": club,
        "club_name": club.legal_name or club.name,
        "payments": list(due.payments.all()),
        "balance": due.balance,
        "line": f"{due.plan.name} - platform subscription",
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
