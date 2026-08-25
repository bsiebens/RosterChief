"""Order invoices: rendered on demand, never stored -- same pattern as
club/services/invoicing.py's dues invoices (see that module's own docstring
for the reasoning). The ``Invoice`` row still exists and still gets a real
``INV-<year>-<seq>`` number the moment an order is placed, it just never
carries a ``pdf`` file; ``render_invoice_pdf`` regenerates the document fresh
from the order's own current line items every time someone views/prints it.

Not shared with club.services.invoicing's own ``render_pdf``/PDF error type --
same "an app depending on another app's PDF error type for a two-line
function isn't worth the coupling" reasoning that module's own docstring
gives for not sharing with management.pdf/billing.services.invoices either.
"""

import base64

from django.template.loader import render_to_string
from django.utils.translation import gettext_lazy as _

from club.services.invoicing import resolve_document_address

from ..models import Invoice

_LOGO_MIMETYPES = {"svg": "image/svg+xml", "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif", "webp": "image/webp"}


class ShopInvoicePDFError(Exception):
    """Raised when WeasyPrint's native libraries aren't available."""


def create_invoice_for_order(order):
    """One Invoice per Order, created the moment the order is placed -- see
    shop.services.checkout.place_order. billing_snapshot freezes the club's
    name/address/contact as they were *then*, so a later club-identity edit
    doesn't rewrite history on an invoice already handed to a member."""
    club = order.club
    document_address = resolve_document_address(club)
    snapshot = {
        "club_name": club.official_name,
        "address": document_address.address if document_address else "",
        "zip_code": document_address.zip_code if document_address else "",
        "city": document_address.city if document_address else "",
        "contact_email": club.contact_email,
    }
    return Invoice.objects.create(club=club, order=order, billing_snapshot=snapshot)


def _logo_data_uri(club) -> str | None:
    """Base64-embedded, not a URL -- WeasyPrint renders server-side with no
    browser session and no guarantee MEDIA_URL is even reachable from wherever
    this runs, so a data: URI is the only embedding that works regardless of
    storage backend. Never fatal: a club with no logo, or a logo file that's
    gone missing from storage, just prints without one."""
    if not club.logo:
        return None
    try:
        with club.logo.open("rb") as f:
            data = f.read()
    except (FileNotFoundError, ValueError, OSError):
        return None

    extension = club.logo.name.rsplit(".", 1)[-1].lower()
    mimetype = _LOGO_MIMETYPES.get(extension, "image/png")
    return f"data:{mimetype};base64,{base64.b64encode(data).decode('ascii')}"


def render_invoice_pdf(invoice) -> bytes:
    order = invoice.order
    lines = order.order_items.select_related("product", "beneficiary")
    html = render_to_string(
        "shop/order_invoice_pdf.html",
        {
            "club": invoice.club,
            "invoice": invoice,
            "order": order,
            "lines": lines,
            "logo_data_uri": _logo_data_uri(invoice.club),
        },
    )
    return render_pdf(html)


def render_pdf(html: str) -> bytes:
    try:
        from weasyprint import HTML
    except (ImportError, OSError) as error:
        raise ShopInvoicePDFError(_("PDF rendering needs the native pango/cairo libraries (on macOS: brew install pango).")) from error

    return HTML(string=html).write_pdf()
