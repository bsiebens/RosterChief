"""The delivery desk: orders marked ready for pickup that were not handed over yet, for whoever runs the shop counter.

Handing over can take the payment on the spot (cash, bank transfer or card, recorded like any manual payment) and marks the order
delivered, all in one transaction. Handing over an order that still has an amount due needs an explicit ``allow_unpaid``."""

from decimal import Decimal

from django.db import transaction
from django.db.models import Q

from formbuilder.services.management import Conflict
from members.services.errors import Invalid
from shop.models import Order
from shop.services import orders as order_service
from shop.services import payments

MANUAL_METHODS = {"cash", "bank_transfer", "credit_card"}


def waiting_queryset(q: str = ""):
    qs = Order.objects.filter(fulfillment_status=Order.FulfillmentStatus.READY_FOR_PICKUP).select_related("purchaser").prefetch_related("lines__product", "lines__variant", "lines__beneficiary")
    q = (q or "").strip()
    if q:
        qs = qs.filter(
            Q(number__icontains=q)
            | Q(purchaser__first_name__icontains=q)
            | Q(purchaser__last_name__icontains=q)
            | Q(lines__beneficiary__first_name__icontains=q)
            | Q(lines__beneficiary__last_name__icontains=q)
        ).distinct()
    return qs.order_by("modified")


def split_by_payment(orders) -> dict:
    """Counts and the due amounts per order, for the tabs (``unpaid`` = something is still due)."""
    rows = {o.pk: max(Decimal("0"), o.total - payments.amount_paid(o)) for o in orders}
    return {"due": rows, "all": len(rows), "unpaid": sum(1 for d in rows.values() if d > 0), "paid": sum(1 for d in rows.values() if d <= 0)}


@transaction.atomic
def hand_over(order, *, method: str | None, reference: str = "", allow_unpaid: bool = False, actor=None) -> Order:
    """Take the payment (when a method is given and something is due), then mark the order delivered."""
    order = Order.objects.select_for_update().get(pk=order.pk)
    if order.fulfillment_status != Order.FulfillmentStatus.READY_FOR_PICKUP:
        raise Conflict("This order is not waiting to be picked up.", code="not_ready_for_pickup")
    if method:
        if method not in MANUAL_METHODS:
            raise Invalid("Unknown payment method.", {"method": ["Choose cash, bank transfer or card."]})
        payments.mark_paid(order, method=method, reference=reference, actor=actor)
    due = max(Decimal("0"), order.total - payments.amount_paid(order))
    if due > 0 and not allow_unpaid:
        raise Conflict(f"EUR {due:.2f} is still due. Collect the payment or confirm handing over unpaid.", code="unpaid")
    return order_service.mark_delivered(order)
