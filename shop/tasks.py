from celery import shared_task


@shared_task(name="shop.notify_order")
def notify_order(kind: str, order_id: str, extra: dict | None = None) -> str:
    """Runs in the club schema it was queued from."""
    from .models import Order
    from .services import notifications

    order = Order.objects.select_related("purchaser").filter(pk=order_id).first()
    return f"told {notifications.send(kind, order, extra or {}) if order else 0} account(s)"


@shared_task(name="shop.send_charge_reminders")
def send_charge_reminders() -> str:
    """Remind the members of every active club about charge invoices that are overdue (Finance -> Invoices). Scheduled daily; each
    invoice is only reminded now and then, on the club's own schedule (``shop.services.charges.reminder_due``)."""
    import logging

    from django_tenants.utils import get_public_schema_name, get_tenant_model, schema_context

    from .services import charges

    log = logging.getLogger(__name__)
    reminders = 0
    failed = []
    for club in get_tenant_model().objects.filter(archived_at__isnull=True).exclude(schema_name=get_public_schema_name()):
        try:
            with schema_context(club.schema_name):
                reminders += charges.send_due_reminders()["reminders"]
        except Exception:
            log.exception("Charge reminders failed for club %s", club.slug)
            failed.append(club.slug)
    return f"{reminders} reminder(s)" + (f"; failed: {', '.join(failed)}" if failed else "")
