from celery import shared_task


@shared_task(name="sponsoring.run_billing")
def run_billing() -> str:
    """In every active club: invoice the sponsor installments whose invoice date has come, then suspend sponsors that are past their grace period
    (and reactivate those that paid up). Scheduled daily."""
    import logging

    from django_tenants.utils import get_public_schema_name, get_tenant_model, schema_context

    from . import billing

    log = logging.getLogger(__name__)
    invoiced, suspended, reactivated = 0, 0, 0
    for club in get_tenant_model().objects.filter(archived_at__isnull=True).exclude(schema_name=get_public_schema_name()):
        try:
            with schema_context(club.schema_name):
                invoiced += billing.invoice_due()
                result = billing.enforce_grace()
                suspended += result["suspended"]
                reactivated += result["reactivated"]
        except Exception:  # one club's problem (or a club without the sponsoring tables) must not stop the others
            log.exception("sponsoring.billing_failed", extra={"club": club.slug})
    return f"invoiced {invoiced}, suspended {suspended}, reactivated {reactivated}"
