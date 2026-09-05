"""Invoice PDFs.

The PDF is rendered on demand from the Due's frozen snapshot (plan, amount, dates), so it
carries no state of its own beyond the number. Only the number is stored — an accountant
reconciles against it, so it is allocated once, never recomputed.
"""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import EmailMultiAlternatives
from django.db.models import Q
from django.template.loader import render_to_string
from django.utils import timezone

from billing.models import Due, Invoice
from billing.services import BillingError
from billing.services.reminders import admin_emails
from rosterchief.mail import send_message


def issue_invoice(due: Due) -> Invoice:
    """One invoice per due, allocated once. Re-issuing returns the existing one rather than
    burning a number — a gap in an invoice series is a question you do not want to answer.

    The moment one is actually created (not re-fetched), platform admins get told about it --
    see notify_admins_of_new_invoice. This is the one choke point both the daily renewal
    command and a platform admin's manual "Open period"/"Reactivate" click go through
    (billing.services.dues.open_period), so it's the right place for that, not either caller."""
    invoice, created = Invoice.objects.get_or_create(due=due)
    if created:
        notify_admins_of_new_invoice(invoice)

    return invoice


def _platform_admin_emails() -> list[str]:
    User = get_user_model()
    return list(User.objects.filter(Q(is_staff=True) | Q(is_superuser=True)).exclude(email="").order_by("email").values_list("email", flat=True))


def notify_admins_of_new_invoice(invoice: Invoice) -> None:
    """Tell every platform admin a club's invoice is ready to send out by hand -- see
    Invoice.needs_sending and billing/templates/controlpanel's "Owed" table (Send/Mark as
    sent buttons). Skipped for a zero-amount period: open_period() auto-marks those PAID
    immediately, so there's nothing to invoice.

    fail_silently -- unlike send_invoice below, a hiccup here must never block opening the
    period itself (this runs inside open_period()'s own transaction, from both the daily
    renewal cron and a platform admin's manual click)."""
    if not invoice.needs_sending:
        return

    recipients = _platform_admin_emails()
    if not recipients:
        return

    due = invoice.due
    context = {"club": due.club, "due": due, "invoice": invoice}
    subject = render_to_string("billing/email/new_period_subject.txt", context).strip()
    text_body = render_to_string("billing/email/new_period.txt", context)

    message = EmailMultiAlternatives(subject=subject, body=text_body, from_email=settings.DEFAULT_FROM_EMAIL, to=recipients)
    send_message(message, fail_silently=True)


def send_invoice(invoice: Invoice, *, actor=None) -> None:
    """Email the invoice (PDF attached) to the club's own admins, then record it as sent.
    Raises BillingError if there's nobody reachable to send it to -- surfaced to the admin
    who clicked "Send", not silently dropped."""
    due = invoice.due
    recipients = admin_emails(due.club)
    if not recipients:
        raise BillingError(f"{due.club} has no reachable admin to send the invoice to.")

    context = {"club": due.club, "due": due, "invoice": invoice, "billing_contact": settings.BILLING_CONTACT_EMAIL}
    subject = render_to_string("billing/email/invoice_subject.txt", context).strip()
    text_body = render_to_string("billing/email/invoice.txt", context)

    message = EmailMultiAlternatives(subject=subject, body=text_body, from_email=settings.DEFAULT_FROM_EMAIL, to=recipients)
    message.attach(f"{invoice.number}.pdf", invoice_pdf(invoice), "application/pdf")
    send_message(message, fail_silently=False)

    invoice.sent_at = timezone.now()
    invoice.sent_method = Invoice.SentMethod.EMAIL
    invoice.sent_by = actor
    invoice.save(update_fields=["sent_at", "sent_method", "sent_by"])


def mark_invoice_sent_manually(invoice: Invoice, *, actor=None) -> None:
    """Record that this invoice went out some other way (e.g. e-invoicing) -- no email sent
    from here, just the same "sent" bookkeeping send_invoice leaves behind."""
    invoice.sent_at = timezone.now()
    invoice.sent_method = Invoice.SentMethod.MANUAL
    invoice.sent_by = actor
    invoice.save(update_fields=["sent_at", "sent_method", "sent_by"])


def render_pdf(html: str) -> bytes:
    """HTML to PDF.

    WeasyPrint is imported here, not at module scope: it binds to native pango/cairo
    libraries, and a machine without them must still be able to run the app, the tests and
    every other page — it should only fail when someone actually asks for a PDF, and say why.
    """
    try:
        from weasyprint import HTML
    except (ImportError, OSError) as error:
        raise BillingError("PDF rendering needs the native pango/cairo libraries (on macOS: brew install pango).") from error

    return HTML(string=html).write_pdf()


def invoice_pdf(invoice: Invoice, base_url: str | None = None) -> bytes:
    html = render_to_string("billing/invoice.html", {"invoice": invoice, "due": invoice.due, "club": invoice.due.club, "payments": invoice.due.payments.all()})

    return render_pdf(html)
