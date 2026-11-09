"""Order invoices: one per order, numbered ``INV-<year>-<seq>`` (per club schema). The PDF is rendered on demand from the
order's current lines and discounts (WeasyPrint, club branding) and cached in the private invoice cache; the cache is
dropped whenever the order changes (``invalidate_cache``). No VAT, voiding or credit notes (the old shop had none)."""

import base64
import datetime
import io

from django.db import transaction
from django.template.loader import render_to_string
from django.utils import timezone

from formbuilder.services.management import Conflict
from rosterchief.storage import drop_cached_invoice_pdf, read_cached_invoice_pdf, write_cached_invoice_pdf
from shop.models import Invoice, Order
from shop.settings_models import ShopSettings

_LOGO_TYPES = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif", "webp": "image/webp", "svg": "image/svg+xml"}


class PDFUnavailable(Exception):
    pass


def _club():
    from events.services.attendance import current_club

    return current_club()


def billing_snapshot(order) -> dict:
    club = _club()
    snap = {"club": {}, "purchaser": {"name": str(order.purchaser), "email": order.purchaser.contact_email}}
    if club is not None:
        snap["club"] = {
            "name": club.legal_name or club.name, "address": club.legal_address, "zip_code": club.legal_zip_code, "city": club.legal_city,
            "country": str(club.legal_country or ""), "contact_email": club.contact_email, "vat_id": club.vat_id,
            "payment_instructions": club.payment_instructions,
        }  # fmt: skip
    return snap


@transaction.atomic
def create_invoice(order, *, due_date: datetime.date | None = None) -> Invoice:
    order = Order.objects.select_for_update().get(pk=order.pk)
    if order.fulfillment_status == Order.FulfillmentStatus.CANCELLED:
        raise Conflict("This order is cancelled.", code="order_cancelled")
    if Invoice.objects.filter(order=order).exists():
        raise Conflict("This order already has an invoice.", code="invoice_exists")
    due = due_date or timezone.localdate() + datetime.timedelta(days=ShopSettings.current().invoice_due_days)
    return Invoice.objects.create(order=order, due_date=due, billing_snapshot=billing_snapshot(order))


def ensure_invoice(order) -> Invoice:
    """The order's invoice, issued now (number, due date after the shop's invoice terms) when it has none yet: an order that is ready
    for pickup always has one. Never raises for an existing invoice; a cancelled order is the caller's business."""
    existing = Invoice.objects.filter(order=order).first()
    return existing if existing is not None else create_invoice(order)


def _cache_name(invoice) -> str:
    club = _club()
    return f"{club.schema_name if club else 'public'}/shop/{invoice.pk}.pdf"


def invalidate_cache(order) -> None:
    invoice = Invoice.objects.filter(order=order).first()
    if invoice is not None:
        drop_cached_invoice_pdf(_cache_name(invoice))


def lines_for(order) -> list[dict]:
    return [
        {"description": ln.product.name + (f" - {ln.variant.name}" if ln.variant else ""), "quantity": ln.quantity, "unit_price": ln.unit_price, "line_total": ln.line_total}
        for ln in order.lines.select_related("product", "variant")
    ]  # fmt: skip


def payments_for(order) -> list[dict]:
    """The confirmed payments shown on the invoice (a voucher is named by its number), oldest first."""
    from shop.models import Payment

    rows = []
    for p in order.payments.filter(status=Payment.PaymentStatus.CONFIRMED).select_related("voucher").order_by("paid_at", "created"):
        if p.voucher_id:
            label = f"Voucher {p.voucher.number}"
        elif p.provider:
            label = "Online payment"
        else:
            label = p.get_method_display()
        rows.append({"label": label, "reference": p.reference, "date": p.paid_at or p.created, "amount": p.amount})
    return rows


def _logo_uri(club):
    if not club or not club.logo:
        return None
    try:
        with club.logo.open("rb") as f:
            data = f.read()
    except FileNotFoundError, ValueError, OSError:
        return None
    mime = _LOGO_TYPES.get(club.logo.name.rsplit(".", 1)[-1].lower(), "image/png")
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def _qr_uri(order):
    """QR to the order's management page (scan at pickup); skipped when the club has no domain or qrcode is missing."""
    try:
        import qrcode

        from club.models import Domain

        club = _club()
        domain = Domain.objects.filter(tenant=club, is_primary=True).first() if club else None
        if domain is None:
            return None
        buffer = io.BytesIO()
        qrcode.make(f"https://{domain.domain}/shop/orders/{order.pk}").save(buffer, format="PNG")
        return f"data:image/png;base64,{base64.b64encode(buffer.getvalue()).decode('ascii')}"
    except Exception:
        return None


def recreate_pdf(invoice) -> None:
    """Drop the cached PDF and build it again from the order as it is now (the stored billing details stay as issued)."""
    invalidate_cache(invoice.order)
    render_pdf(invoice)


def render_pdf(invoice) -> bytes:
    cached = read_cached_invoice_pdf(_cache_name(invoice))
    if cached is not None:
        return cached
    try:
        from weasyprint import HTML
    except (ImportError, OSError) as error:
        raise PDFUnavailable("PDF rendering is not available on this server.") from error
    from shop.services import payments

    order = invoice.order
    club = _club()
    html = render_to_string(
        "shop/order_invoice_pdf.html",
        {
            "club": club,
            "invoice": invoice,
            "order": order,
            "lines": order.lines.select_related("product", "variant", "beneficiary"),
            "discounts": order.applied_discounts.all(),
            "payments": payments_for(order),
            "amount_paid": payments.amount_paid(order),
            "amount_due": payments.amount_due(order),
            "snapshot": invoice.billing_snapshot or {},
            "logo_data_uri": _logo_uri(club),
            "qr_data_uri": _qr_uri(order),
        },
    )
    pdf = HTML(string=html).write_pdf()
    write_cached_invoice_pdf(_cache_name(invoice), pdf)
    return pdf
