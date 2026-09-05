"""Order invoices: composed fresh from the order's own current line items,
then cached to disk under a per-invoice path so the (comparatively slow)
WeasyPrint render only happens once per invoice -- see ``render_invoice_pdf``.
The ``Invoice`` row itself carries no ``pdf`` field; the cache lives purely
in storage, keyed by ``invoice.pk``, and is never exposed as a public
URL -- every read goes through ``default_storage.open()`` inside an
authenticated view (InvoicePdfView/mobile ShopInvoiceView), the same way a
club logo's *upload* uses this storage without the PDF ever getting a
public-facing link the way a logo does.

Cache invalidation is event-driven, not time-based: ``shop.signals`` deletes
an order's cached PDF the moment ``Order.status`` actually changes (paid,
delivered, refunded, ... all change what the PDF itself says), so the next
request regenerates it. Nothing here decides *when* to invalidate; this
module only knows how to render, cache, and drop a cached copy.

Not shared with club.services.invoicing's own ``render_pdf``/PDF error type --
same "an app depending on another app's PDF error type for a two-line
function isn't worth the coupling" reasoning that module's own docstring
gives for not sharing with management.pdf/billing.services.invoices either.
"""

import base64
import io

import qrcode
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.template.loader import render_to_string
from django.urls import reverse
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
        "vat_id": club.vat_id,
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


def _order_url(order) -> str:
    """Absolute link to this order's own management page -- not request-
    bound (render_invoice_pdf has no request to build one from; this can run
    from a background email-attachment path just as easily as a live
    download), so built by hand from the club's subdomain + ROSTERCHIEF_BASE_DOMAIN,
    same tenancy convention as club.tenancy's own subdomain resolution."""
    path = reverse("management:order_detail", kwargs={"pk": order.pk})
    return f"https://{order.club.slug}.{settings.ROSTERCHIEF_BASE_DOMAIN}{path}"


def _order_qr_data_uri(order) -> str | None:
    """A QR code scannable at pickup -- lands staff straight on this order's
    own management page (mark paid/ready/delivered) without hunting for it
    by number. Never fatal: a club running before ROSTERCHIEF_BASE_DOMAIN is
    configured (local dev without it set) just prints without one rather
    than encoding a broken link."""
    if not settings.ROSTERCHIEF_BASE_DOMAIN:
        return None

    image = qrcode.make(_order_url(order))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return f"data:image/png;base64,{base64.b64encode(buffer.getvalue()).decode('ascii')}"


def _cache_path(invoice) -> str:
    return f"clubs/{invoice.club.slug}/shop/invoices/cache/{invoice.pk}.pdf"


def invalidate_cached_invoice_pdf(invoice) -> None:
    """Drop invoice's cached PDF, if any -- called from shop.signals the
    moment its order's status changes. A no-op when nothing was cached yet
    (a PDF nobody has ever downloaded), which is the common case."""
    path = _cache_path(invoice)
    if default_storage.exists(path):
        default_storage.delete(path)


def render_invoice_pdf(invoice) -> bytes:
    cache_path = _cache_path(invoice)
    if default_storage.exists(cache_path):
        with default_storage.open(cache_path, "rb") as cached:
            return cached.read()

    order = invoice.order
    lines = order.order_items.select_related("product", "variant", "beneficiary")
    html = render_to_string(
        "shop/order_invoice_pdf.html",
        {
            "club": invoice.club,
            "invoice": invoice,
            "order": order,
            "lines": lines,
            "logo_data_uri": _logo_data_uri(invoice.club),
            "qr_data_uri": _order_qr_data_uri(order),
        },
    )
    pdf_bytes = render_pdf(html)
    default_storage.save(cache_path, ContentFile(pdf_bytes))
    return pdf_bytes


def render_pdf(html: str) -> bytes:
    try:
        from weasyprint import HTML
    except (ImportError, OSError) as error:
        raise ShopInvoicePDFError(_("PDF rendering needs the native pango/cairo libraries (on macOS: brew install pango).")) from error

    return HTML(string=html).write_pdf()
