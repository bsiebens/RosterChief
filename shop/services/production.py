"""Tracking whether a merchandise OrderLine has been sent to (and received
back from) a manufacturer -- independent from payment_status/
fulfillment_status, which are both about the member's own side of the
order, not the club's manufacturing pipeline.
"""

from django.db import transaction

from ..models import Order, OrderLine, Product, ProductionStatus


def pending_production_lines(products):
    """Every not-yet-submitted OrderLine for these products, on a
    non-cancelled order -- what a manufacturer export actually includes."""
    return (
        OrderLine.objects.filter(product__in=products, production_status=ProductionStatus.PENDING)
        .exclude(order__fulfillment_status=Order.FulfillmentStatus.CANCELLED)
        .select_related("order", "order__purchaser", "beneficiary", "variant", "product")
        .order_by("product__name", "order__number")
    )


def in_production_lines(products):
    """Every OrderLine for these products currently in production -- already
    sent to a manufacturer, not yet received back. What "redownload the
    in-production list" reprints, unchanged: no mutation, just a fresh copy
    of what's already out (e.g. the original export got lost)."""
    return (
        OrderLine.objects.filter(product__in=products, production_status=ProductionStatus.IN_PRODUCTION)
        .exclude(order__fulfillment_status=Order.FulfillmentStatus.CANCELLED)
        .select_related("order", "order__purchaser", "beneficiary", "variant", "product")
        .order_by("product__name", "order__number")
    )


def sync_production_status(order):
    """Recomputes Order.production_status from its own merchandise
    OrderLines: PENDING while every one is, RECEIVED once every one is,
    IN_PRODUCTION for anything in between. A no-op (leaves the PENDING
    default alone) for an order with no merchandise lines at all -- see
    Order.has_production_lines, which gates whether that default is ever
    actually shown anywhere."""
    statuses = set(order.order_items.filter(product__product_type=Product.ProductType.MERCHANDISE).values_list("production_status", flat=True))
    if not statuses:
        return

    if statuses == {ProductionStatus.RECEIVED}:
        new_status = ProductionStatus.RECEIVED
    elif statuses == {ProductionStatus.PENDING}:
        new_status = ProductionStatus.PENDING
    else:
        new_status = ProductionStatus.IN_PRODUCTION

    if order.production_status != new_status:
        order.production_status = new_status
        order.save(update_fields=["production_status"])


@transaction.atomic
def mark_line_received(line):
    """Flips a single OrderLine straight to RECEIVED and resyncs its order's
    own rollup -- the "Mark received" quick action on a line already
    IN_PRODUCTION (order_detail.html), for advancing the common case without
    opening the full edit modal just to pick RECEIVED from a dropdown."""
    line.production_status = ProductionStatus.RECEIVED
    line.save(update_fields=["production_status"])
    sync_production_status(line.order)


@transaction.atomic
def mark_lines_in_production(lines):
    """Flips every given OrderLine to IN_PRODUCTION and resyncs each
    affected order's own rollup -- the state-changing half of exporting a
    manufacturer order list (management.shop_export builds the file itself;
    call this only once that file is safely in hand, so a failed export
    never marks anything sent that wasn't)."""
    orders = {line.order for line in lines}
    OrderLine.objects.filter(pk__in=[line.pk for line in lines]).update(production_status=ProductionStatus.IN_PRODUCTION)
    for order in orders:
        sync_production_status(order)
