"""Bust an order's cached invoice PDF the moment its status actually
changes -- the PDF's paid/owed styling and status line are derived from
Order.status (see shop/templates/shop/order_invoice_pdf.html), so a stale
cached copy would otherwise keep showing a status the order no longer has.
Registered from ShopConfig.ready.
"""

from django.db.models.signals import pre_save
from django.dispatch import receiver

from .models import Invoice, Order
from .services.invoices import invalidate_cached_invoice_pdf


@receiver(pre_save, sender=Order)
def invalidate_invoice_pdf_on_status_change(sender, instance, **kwargs):
    if not instance.pk:
        return

    previous_status = Order.objects.filter(pk=instance.pk).values_list("status", flat=True).first()
    if previous_status is None or previous_status == instance.status:
        return

    invoice = Invoice.objects.filter(order_id=instance.pk).first()
    if invoice is not None:
        invalidate_cached_invoice_pdf(invoice)
