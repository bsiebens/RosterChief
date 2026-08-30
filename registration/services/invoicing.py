"""One PDF per registration batch, covering every entry in it -- not one
per membership. club.services.invoicing.DuesInvoice is strictly a
OneToOneField to a single ClubMembership (staff sends one per member's own
season fee), which can't represent "everyone a parent registered in one go,
on one document" the way this needs to.

Reuses RegistrationBatch's own subtotal/discount_amount/total and each
entry's own price/discount_amount -- both already the source of truth for
what was charged at submission time (registration.services.submission.
submit_registration), nothing here recomputes anything.

Not cached to disk, unlike club.services.invoicing.invoice_pdf/shop.services.
invoices.render_invoice_pdf: this now also shows each membership's early-
payment offer (club.services.fees.early_payment_offer), which is time-
sensitive -- whether it's still live changes on its own the day the deadline
passes, with nothing that would trigger a cache bust. A batch invoice is a
rarely-downloaded document (one family, occasionally), so re-rendering every
time costs little and can never go stale. render_pdf/its error type are this
module's own copy, not imported from club/shop's versions -- see club.
services.invoicing.render_pdf's own docstring for why an app's two-line PDF
renderer isn't worth coupling to another app's.
"""

from django.template.loader import render_to_string
from django.utils.translation import gettext_lazy as _

from club.services.fees import early_payment_offer
from club.services.invoicing import resolve_document_address


class RegistrationInvoicePDFError(Exception):
    """Raised when WeasyPrint's native libraries aren't available."""


def render_pdf(html: str) -> bytes:
    try:
        from weasyprint import HTML
    except (ImportError, OSError) as error:
        raise RegistrationInvoicePDFError(_("PDF rendering needs the native pango/cairo libraries (on macOS: brew install pango).")) from error

    return HTML(string=html).write_pdf()


def batch_invoice_pdf(batch) -> bytes:
    document_address = resolve_document_address(batch.club)
    entries = list(batch.entries.select_related("membership__member", "product_variant__product").order_by("membership__member__last_name", "membership__member__first_name"))

    # One row per membership (not per entry -- two entries for the same
    # person share one offer), in the same order as the entries above.
    seen_memberships = {}
    for entry in entries:
        seen_memberships.setdefault(entry.membership_id, entry.membership)
    early_payment_rows = [{"membership": membership, "offer": offer} for membership in seen_memberships.values() if (offer := early_payment_offer(membership)) is not None]

    html = render_to_string(
        "registration/batch_invoice_pdf.html",
        {"club": batch.club, "batch": batch, "entries": entries, "document_address": document_address, "early_payment_rows": early_payment_rows},
    )
    return render_pdf(html)
