"""Bust a Due's invoice's cached PDF the moment its status/amount_paid
actually changes -- a payment recorded or removed (both funnel through
billing.services.dues._resettle), or the period waived. Same "pre_save,
compare against what's still in the database" shape as club.signals' own
invalidate_dues_pdf_on_fee_status_change, for the same reason: the PDF
renders the running payment list and current balance/status, not just the
period's own frozen amount/dates.
"""

from django.db.models.signals import pre_save
from django.dispatch import receiver

from .models import Due, Invoice
from .services.invoices import invalidate_cached_invoice_pdf


@receiver(pre_save, sender=Due)
def invalidate_invoice_pdf_on_due_change(sender, instance, **kwargs):
    if not instance.pk:
        return

    previous = Due.objects.filter(pk=instance.pk).values("status", "amount_paid").first()
    if previous is None or (previous["status"] == instance.status and previous["amount_paid"] == instance.amount_paid):
        return

    invoice = Invoice.objects.filter(due_id=instance.pk).first()
    if invoice is not None:
        invalidate_cached_invoice_pdf(invoice)
