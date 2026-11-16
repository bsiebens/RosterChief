"""The ready-for-pickup workflow: the orders whose items are all fulfilled, confirmed in bulk.

An order is waiting for confirmation when it is not ready or closed yet and every item is there: it has no made-to-order lines
(everything was in stock), or all of them are received. Confirming marks the selected orders ready for pickup, stores the pickup
instructions on them and notifies each purchaser (payment instructions are added for orders that are not fully paid). The payment
text given at confirmation is for those notifications only: the club-level default stays as it is."""

from django.db import transaction
from django.db.models import Exists, OuterRef, Q

from shop.models import Order, OrderLine, Product, ProductionStatus
from shop.services import orders as order_service
from shop.settings_models import ShopSettings


def waiting_orders():
    production = OrderLine.objects.filter(order=OuterRef("pk"), product__product_type=Product.ProductType.MERCHANDISE, product__inventory_managed=False)
    return (
        Order.objects.filter(kind=Order.Kind.SHOP, fulfillment_status=Order.FulfillmentStatus.NOT_READY)
        .annotate(has_production=Exists(production))
        .filter(Q(has_production=False) | Q(production_status=ProductionStatus.RECEIVED))
        .select_related("purchaser")
        .prefetch_related("lines__beneficiary")
        .order_by("created")
    )


def is_candidate(order) -> bool:
    """The same rule as ``waiting_orders`` for one order: not ready or closed yet, and every item is there."""
    return order.fulfillment_status == Order.FulfillmentStatus.NOT_READY and (not order.has_production_lines or order.production_status == ProductionStatus.RECEIVED)


@transaction.atomic
def confirm(order_ids, *, pickup_instructions: str, payment_instructions: str, save_as_default: bool = False) -> dict:
    """Mark the selected waiting orders ready and notify their purchasers; the rest are counted as skipped."""
    ids = list(dict.fromkeys(order_ids))
    waiting = list(waiting_orders().filter(pk__in=ids))
    for order in waiting:
        order_service.mark_ready(order, pickup_instructions=pickup_instructions, payment_instructions=payment_instructions)
    if save_as_default and pickup_instructions.strip():
        settings = ShopSettings.current()
        settings.pickup_instructions = pickup_instructions.strip()
        settings.save(update_fields=["pickup_instructions", "modified"])
    return {"count": len(waiting), "skipped": len(ids) - len(waiting)}
