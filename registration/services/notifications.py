"""Registration app emails: the confirmation sent right at submission (below),
and -- once staff has reviewed and confirmed a registration's own invoice on
the management Registrations screen -- the invoice itself and its overdue
reminder. All three are never fatal, same shape as members.services.claims.
send_claim_approved_email: the underlying record (batch, or the confirmed
invoice state) exists in the database whether or not the mail actually
leaves the building.

confirm_and_send_invoice/send_registration_reminders are the one entry point
each for "confirm state, then email" -- they live here, not in registration.
services.invoicing, so that module (which they both need, for
batch_invoice_pdf/registration_invoices_due_for_reminder) never has to
import this one back."""

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from rosterchief.mail import send_message

from .invoicing import RegistrationInvoicePDFError, active_batch_entries, batch_invoice_pdf, batch_totals, confirm_invoice, registration_invoices_due_for_reminder


def send_registration_confirmation_email(batch, *, request=None):
    path = reverse("registration:status", kwargs={"token": batch.status_token})
    status_url = request.build_absolute_uri(path) if request is not None else path

    # "request" rides along in the context for the .html template's own
    # {% absolute_media_url %} use (the club's logo), same as claims.
    # send_claim_approved_email's own context.
    context = {"club": batch.club, "batch": batch, "contact_first_name": batch.contact_first_name, "status_url": status_url, "request": request}
    subject = " ".join(render_to_string("registration/email/confirmation_subject.txt", context).split())
    text_body = render_to_string("registration/email/confirmation.txt", context).strip() + "\n"
    html_body = render_to_string("registration/email/confirmation.html", context)

    message = EmailMultiAlternatives(subject, text_body, settings.DEFAULT_FROM_EMAIL, [batch.contact_email])
    message.attach_alternative(html_body, "text/html")

    try:
        return send_message(message, fail_silently=False)
    except OSError:
        # Anything the mail backend raises for an unreachable server or a
        # refused connection. The batch and its status page stand either way.
        return False


def _attach_invoice_pdf(message, batch):
    # Best-effort, same as club.services.invoicing._attach_pdf: a missing
    # native WeasyPrint library means the email still goes out, just without
    # the attachment -- the family can still reach the PDF from their status
    # page/mobile app once it's confirmed.
    try:
        pdf = batch_invoice_pdf(batch)
    except RegistrationInvoicePDFError:
        return
    message.attach(f"{batch.invoice_number}.pdf", pdf, "application/pdf")


def _invoice_email_context(batch, *, request=None):
    path = reverse("registration:status", kwargs={"token": batch.status_token})
    status_url = request.build_absolute_uri(path) if request is not None else path
    _subtotal, _discount_amount, total = batch_totals(active_batch_entries(batch), batch.manual_discount_amount)
    return {"club": batch.club, "batch": batch, "contact_first_name": batch.contact_first_name, "total": total, "status_url": status_url, "request": request}


def send_registration_invoice_email(batch, *, request=None) -> bool:
    """The email confirm_and_send_invoice sends once staff has reviewed and
    finalised a registration's amounts -- same construction as club.services.
    invoicing.send_invoice_email (subject/text/html render, best-effort PDF
    attach, never raises). Recipient is simply batch.contact_email -- no
    guardian-resolution needed here (unlike DuesInvoice.recipient_for),
    since the batch already carries the one email its own submitter gave."""
    context = _invoice_email_context(batch, request=request)
    subject = " ".join(render_to_string("registration/email/invoice_subject.txt", context).split())
    text_body = render_to_string("registration/email/invoice.txt", context).strip() + "\n"
    html_body = render_to_string("registration/email/invoice.html", context)

    message = EmailMultiAlternatives(subject, text_body, settings.DEFAULT_FROM_EMAIL, [batch.contact_email])
    message.attach_alternative(html_body, "text/html")
    _attach_invoice_pdf(message, batch)

    try:
        return send_message(message, fail_silently=False)
    except OSError:
        return False


def send_registration_reminder_email(batch, *, request=None) -> bool:
    context = _invoice_email_context(batch, request=request)
    subject = " ".join(render_to_string("registration/email/invoice_reminder_subject.txt", context).split())
    text_body = render_to_string("registration/email/invoice_reminder.txt", context).strip() + "\n"
    html_body = render_to_string("registration/email/invoice_reminder.html", context)

    message = EmailMultiAlternatives(subject, text_body, settings.DEFAULT_FROM_EMAIL, [batch.contact_email])
    message.attach_alternative(html_body, "text/html")
    _attach_invoice_pdf(message, batch)

    try:
        sent = send_message(message, fail_silently=False)
    except OSError:
        return False
    if sent:
        batch.invoice_last_reminder_sent_at = timezone.now()
        batch.invoice_reminder_count += 1
        batch.save(update_fields=["invoice_last_reminder_sent_at", "invoice_reminder_count", "modified"])
    return sent


def confirm_and_send_invoice(batch, *, due_in_days, request=None) -> bool:
    """The Registrations review screen's one "Confirm & send invoice"
    action -- allocates the invoice number/due date (registration.services.
    invoicing.confirm_invoice) then emails it. The confirmation itself always
    happens regardless of whether the email actually sends -- same
    never-raises-on-mail-failure reasoning club.services.invoicing.
    send_invoice_email already uses; this returns whether the email went
    out, not whether confirmation succeeded (confirmation never fails short
    of a database error)."""
    confirm_invoice(batch, due_in_days=due_in_days)
    return send_registration_invoice_email(batch, request=request)


def send_registration_reminders(club, *, request=None) -> tuple[int, int]:
    """Same shape as club.services.invoicing.send_reminders -- loops every
    overdue, confirmed registration invoice and emails a reminder for it."""
    sent = failed = 0
    for batch in registration_invoices_due_for_reminder(club):
        if send_registration_reminder_email(batch, request=request):
            sent += 1
        else:
            failed += 1
    return sent, failed
