"""Member-facing shop notifications -- app + email, one function per event.
Kept out of checkout.py (cart -> Order is that module's own job) and out of
management/views.py (a view shouldn't own what an email says).
"""

from django.utils.translation import gettext_lazy as _

from notifications.services import notify_members

from .invoices import ShopInvoicePDFError, render_invoice_pdf


def _invoice_attachment(order):
    """Best-effort: a club running without WeasyPrint's native libraries still
    gets the notification itself, just without the PDF -- same reasoning as
    club.services.invoicing._attach_pdf."""
    invoice = getattr(order, "invoice", None)
    if invoice is None:
        return None
    try:
        pdf_bytes = render_invoice_pdf(invoice)
    except ShopInvoicePDFError:
        return None
    return [(f"{invoice.number}.pdf", pdf_bytes, "application/pdf")]


def dispatch_order_placed_notification(order) -> None:
    """The purchaser, and only the purchaser (not every shop admin), told
    their order is in and payable on pickup -- app + email, invoice PDF
    attached to the email."""
    title = _("Order %(number)s placed") % {"number": order.number}
    body = _("Your order is in — pay when you pick it up. Total: €%(total)s.") % {"total": order.total}
    notify_members([order.purchaser], club=order.club, title=title, body=body, source=order, attachments=_invoice_attachment(order))


def dispatch_order_ready_for_pickup_notification(order) -> None:
    """Told the moment a shop admin flips the order to READY_FOR_PICKUP
    (ManageOrderMarkReadyForPickupView) -- app + email. order.pickup_instructions,
    when set, is folded into the body rather than passed separately: it's
    exactly what the member needs to actually go collect the thing, not an
    aside."""
    title = _("Order %(number)s is ready for pickup") % {"number": order.number}
    body = _("Your order is ready to collect — pay when you pick it up.")
    if order.pickup_instructions:
        body = f"{body} {order.pickup_instructions}"
    notify_members([order.purchaser], club=order.club, title=title, body=body, source=order)
