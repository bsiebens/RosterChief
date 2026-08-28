"""One PDF per registration batch, covering every entry in it -- not one
per membership. club.services.invoicing.DuesInvoice is strictly a
OneToOneField to a single ClubMembership (staff sends one per member's own
season fee), which can't represent "everyone a parent registered in one go,
on one document" the way this needs to.

Reuses RegistrationBatch's own subtotal/discount_amount/total and each
entry's own price/discount_amount -- both already the source of truth for
what was charged at submission time (registration.services.submission.
submit_registration), nothing here recomputes anything.

Cached to disk, same shape as club.services.invoicing.invoice_pdf/
shop.services.invoices.render_invoice_pdf -- unlike either of those, a
batch's own pricing fields never change after submission, so this needs no
invalidation path at all. render_pdf/its error type are this module's own
copy, not imported from club/shop's versions -- see club.services.
invoicing.render_pdf's own docstring for why an app's two-line PDF renderer
isn't worth coupling to another app's.
"""

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.template.loader import render_to_string
from django.utils.translation import gettext_lazy as _

from club.services.invoicing import resolve_document_address


class RegistrationInvoicePDFError(Exception):
    """Raised when WeasyPrint's native libraries aren't available."""


def render_pdf(html: str) -> bytes:
    try:
        from weasyprint import HTML
    except (ImportError, OSError) as error:
        raise RegistrationInvoicePDFError(_("PDF rendering needs the native pango/cairo libraries (on macOS: brew install pango).")) from error

    return HTML(string=html).write_pdf()


def _cache_path(batch) -> str:
    return f"clubs/{batch.club.slug}/registration/invoices/cache/{batch.pk}.pdf"


def batch_invoice_pdf(batch) -> bytes:
    cache_path = _cache_path(batch)
    if default_storage.exists(cache_path):
        with default_storage.open(cache_path, "rb") as cached:
            return cached.read()

    document_address = resolve_document_address(batch.club)
    entries = list(batch.entries.select_related("membership__member", "product_variant__product").order_by("membership__member__last_name", "membership__member__first_name"))
    html = render_to_string("registration/batch_invoice_pdf.html", {"club": batch.club, "batch": batch, "entries": entries, "document_address": document_address})
    pdf_bytes = render_pdf(html)
    default_storage.save(cache_path, ContentFile(pdf_bytes))
    return pdf_bytes
