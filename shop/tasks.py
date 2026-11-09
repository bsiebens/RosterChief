from celery import shared_task


@shared_task(name="shop.notify_order")
def notify_order(kind: str, order_id: str, extra: dict | None = None) -> str:
    """Runs in the club schema it was queued from."""
    from .models import Order
    from .services import notifications

    order = Order.objects.select_related("purchaser").filter(pk=order_id).first()
    return f"told {notifications.send(kind, order, extra or {}) if order else 0} account(s)"
