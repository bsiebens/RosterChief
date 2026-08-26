"""Dues invoices: asking a member (or their parent/guardian) to pay an outstanding
membership fee, and chasing it if the due date passes unpaid.

Kept separate from club.services.fees on purpose: fees.py owns what's actually owed
and settled (fee_amount/amount_paid/fee_status), this module only owns the paper
trail of having asked for it. A DuesInvoice's own "paid" reading is always the live
membership.fee_status -- never a flag duplicated here that could drift out of step.
"""

from datetime import timedelta
from types import SimpleNamespace

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from club.models import ClubMembership, DuesInvoice
from club.services.fees import remaining_balance
from events.models import Location
from rosterchief.mail import send_message


class DuesInvoicePDFError(Exception):
    """Raised when WeasyPrint's native libraries aren't available."""


def recipient_for(member) -> tuple[str, bool]:
    """Best email to invoice ``member`` at: their own, else the first parent/guardian
    who has one. Empty string means nobody reachable at all -- the caller must not
    create or send an invoice in that case."""
    if member.contact_email:
        return member.contact_email, False

    for guardian in member.guardians.order_by("last_name", "first_name"):
        if guardian.contact_email:
            return guardian.contact_email, True

    return "", False


def create_or_resend_invoice(membership: ClubMembership, *, due_in_days: int, recipient_email: str, sent_to_guardian: bool) -> DuesInvoice:
    """Create the membership's one invoice, or re-snapshot it if it already has one.
    Never touches reminder_count/last_reminder_sent_at -- a fresh send earns a fresh
    reminder clock, but that's set by the reminder path itself, not reset here, since
    a resend before any reminder went out has nothing to reset."""
    invoice, _created = DuesInvoice.objects.get_or_create(
        club=membership.club,
        membership=membership,
        defaults={"amount": remaining_balance(membership), "due_date": timezone.now().date() + timedelta(days=due_in_days)},
    )
    invoice.amount = remaining_balance(membership)
    invoice.due_date = timezone.now().date() + timedelta(days=due_in_days)
    invoice.sent_at = timezone.now()
    invoice.sent_to_email = recipient_email
    invoice.sent_to_guardian = sent_to_guardian
    # get_or_create's own save (for a new row) already assigned invoice.number,
    # so it's always set by this point -- update_fields never needs to include it.
    invoice.save(update_fields=["amount", "due_date", "sent_at", "sent_to_email", "sent_to_guardian", "modified"])
    # A resend changes amount/due_date directly on this row -- a cached PDF
    # from before would serve stale figures forever otherwise. The
    # fee_status-change signal (club.signals) doesn't cover this path since
    # nothing on the membership itself changes.
    invalidate_cached_invoice_pdf(invoice)
    return invoice


def _email_context(invoice: DuesInvoice, *, request=None) -> dict:
    return {"club": invoice.club, "invoice": invoice, "membership": invoice.membership, "member": invoice.membership.member, "request": request}


def _attach_pdf(message: EmailMultiAlternatives, invoice: DuesInvoice) -> None:
    """Best-effort: a club running without WeasyPrint's native libraries still gets
    the invoice email itself, just without the PDF -- everything the PDF shows is
    already in the email body."""
    try:
        pdf_bytes = invoice_pdf(invoice)
    except DuesInvoicePDFError:
        return
    message.attach(f"{invoice.number}.pdf", pdf_bytes, "application/pdf")


def send_invoice_email(invoice: DuesInvoice, *, request=None) -> bool:
    """Mail the branded invoice to invoice.sent_to_email. Never fatal: the invoice
    row (and its sent_at stamp) exists whether or not the mail leaves the building --
    see members.services.claims.send_claim_approved_email for the same reasoning."""
    if not invoice.sent_to_email:
        return False

    context = _email_context(invoice, request=request)
    subject = " ".join(render_to_string("club/email/dues_invoice_subject.txt", context).split())
    text_body = render_to_string("club/email/dues_invoice.txt", context).strip() + "\n"
    html_body = render_to_string("club/email/dues_invoice.html", context)

    message = EmailMultiAlternatives(subject, text_body, settings.DEFAULT_FROM_EMAIL, [invoice.sent_to_email])
    message.attach_alternative(html_body, "text/html")
    _attach_pdf(message, invoice)

    try:
        return send_message(message, fail_silently=False)
    except OSError:
        return False


def invoices_due_for_reminder(club, today=None):
    """Sent, unpaid (and not waived -- nothing's owed there), past their own due
    date. Reminders are opt-in per club-wide button push, not a cron job, so there's
    no "already reminded today" guard here -- see MembershipSendInvoiceRemindersView."""
    today = today or timezone.now().date()
    return (
        DuesInvoice.objects.filter(club=club, sent_at__isnull=False, due_date__lt=today)
        .exclude(membership__fee_status__in=[ClubMembership.FeeStatus.PAID, ClubMembership.FeeStatus.WAIVED])
        .select_related("membership__member")
    )


def send_reminder_email(invoice: DuesInvoice, *, request=None) -> bool:
    if not invoice.sent_to_email:
        return False

    context = _email_context(invoice, request=request)
    subject = " ".join(render_to_string("club/email/dues_invoice_reminder_subject.txt", context).split())
    text_body = render_to_string("club/email/dues_invoice_reminder.txt", context).strip() + "\n"
    html_body = render_to_string("club/email/dues_invoice_reminder.html", context)

    message = EmailMultiAlternatives(subject, text_body, settings.DEFAULT_FROM_EMAIL, [invoice.sent_to_email])
    message.attach_alternative(html_body, "text/html")
    _attach_pdf(message, invoice)

    try:
        sent = send_message(message, fail_silently=False)
    except OSError:
        return False
    if not sent:
        return False

    invoice.last_reminder_sent_at = timezone.now()
    invoice.reminder_count += 1
    invoice.save(update_fields=["last_reminder_sent_at", "reminder_count", "modified"])
    return True


def send_reminders(club, *, request=None) -> tuple[int, int]:
    """Push-button "remind everyone past due" -- returns (sent, failed)."""
    sent = failed = 0
    for invoice in invoices_due_for_reminder(club):
        if send_reminder_email(invoice, request=request):
            sent += 1
        else:
            failed += 1
    return sent, failed


def render_pdf(html: str) -> bytes:
    """Same lazy-import shape as management.pdf.render_pdf/billing.services.invoices.render_pdf
    -- WeasyPrint binds to native pango/cairo libraries, and a machine without them
    must still be able to run the app; this only fails when someone actually asks
    for a PDF. Not shared with either of those: an app depending on another app's
    PDF error type for a two-line function isn't worth the coupling."""
    try:
        from weasyprint import HTML
    except (ImportError, OSError) as error:
        raise DuesInvoicePDFError(_("PDF rendering needs the native pango/cairo libraries (on macOS: brew install pango).")) from error

    return HTML(string=html).write_pdf()


def resolve_document_address(club):
    """The address to print on an official document header (a dues invoice,
    the referee payment form) -- the club's own ``legal_address`` when set,
    else its home ground (``events.models.Location``, ``is_home=True``), so
    a club that hasn't set a legal address yet still gets *something* rather
    than a blank header.

    ``Location.is_home`` itself is purely about telling a home game from an
    away one -- this is the one place its address doubles as a stand-in for
    an actual registered/mailing address, and only when the club hasn't set
    one of its own. Returns an object exposing ``.address``/``.zip_code``/
    ``.city`` either way (a plain namespace for the legal-address branch, the
    real ``Location`` for the fallback), or ``None`` when neither is set.
    """
    if club.legal_address:
        return SimpleNamespace(address=club.legal_address, zip_code=club.legal_zip_code, city=club.legal_city)
    return Location.objects.filter(club=club, is_home=True).first()


def _cache_path(invoice: DuesInvoice) -> str:
    return f"clubs/{invoice.club.slug}/dues/invoices/cache/{invoice.pk}.pdf"


def invalidate_cached_invoice_pdf(invoice: DuesInvoice) -> None:
    """Drop invoice's cached PDF, if any -- called both from club.signals
    (the moment the membership's fee_status changes -- paid/owed is the one
    thing the PDF itself renders differently) and from create_or_resend_invoice
    directly (a resend changes amount/due_date on this same row). A no-op
    when nothing was cached yet."""
    path = _cache_path(invoice)
    if default_storage.exists(path):
        default_storage.delete(path)


def invoice_pdf(invoice: DuesInvoice) -> bytes:
    # Cached to disk, same reasoning/shape as shop.services.invoices.
    # render_invoice_pdf -- see that module's own docstring. Never exposed
    # as a public URL: every read goes through default_storage.open() inside
    # an authenticated view (DuesInvoicePdfView).
    cache_path = _cache_path(invoice)
    if default_storage.exists(cache_path):
        with default_storage.open(cache_path, "rb") as cached:
            return cached.read()

    # Same header convention as management/event_referee_form_pdf.html: the club's
    # legal name (official_name falls back to the everyday name when unset) and its
    # document address -- never an event-specific location, since a dues invoice
    # isn't tied to any one event.
    document_address = resolve_document_address(invoice.club)
    html = render_to_string("club/dues_invoice_pdf.html", {"club": invoice.club, "invoice": invoice, "membership": invoice.membership, "member": invoice.membership.member, "document_address": document_address})
    pdf_bytes = render_pdf(html)
    default_storage.save(cache_path, ContentFile(pdf_bytes))
    return pdf_bytes
