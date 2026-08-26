from decimal import Decimal

from django.db.models import Sum

from ..models import Order, OrderLine

#: An order counts as "sold" once it's actually been paid for -- Order.payment_status,
#: not fulfillment_status: a delivered-but-unpaid order (see Order.is_closed's own
#: docstring) hasn't actually generated revenue yet.
PAID_STATUSES = (Order.PaymentStatus.PAID,)


def order_kpis(club):
    """Top-of-the-orders-page numbers: how many orders need attention, how
    many there have been in total, and what's actually been sold."""
    orders = Order.objects.filter(club=club)
    paid_orders = orders.filter(payment_status__in=PAID_STATUSES)

    return {
        "open_orders": orders.exclude(fulfillment_status=Order.FulfillmentStatus.DELIVERED, payment_status=Order.PaymentStatus.PAID).count(),
        "total_orders": orders.count(),
        "total_sold": paid_orders.aggregate(total=Sum("total"))["total"] or Decimal("0"),
        "items_sold": OrderLine.objects.filter(order__in=paid_orders).aggregate(total=Sum("quantity"))["total"] or 0,
    }


def quantity_sold_by_product(club):
    """{product_id: total quantity} across every paid order line for this
    club -- one query for the whole Products list, not one per row."""
    rows = OrderLine.objects.filter(order__club=club, order__payment_status__in=PAID_STATUSES).values("product_id").annotate(total=Sum("quantity"))
    return {row["product_id"]: row["total"] for row in rows}


def quantity_sold_by_variant(product):
    """{variant_id: total quantity} for one product's own variants -- the
    product edit page's own per-row breakdown."""
    rows = OrderLine.objects.filter(product=product, variant__isnull=False, order__payment_status__in=PAID_STATUSES).values("variant_id").annotate(total=Sum("quantity"))
    return {row["variant_id"]: row["total"] for row in rows}
