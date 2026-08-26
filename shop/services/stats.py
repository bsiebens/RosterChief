from decimal import Decimal

from django.db.models import Sum

from ..models import Order, OrderLine

#: An order counts as "sold" once it's actually been paid for -- matches
#: controlpanel/services/statistics.py's own PAID_STATUSES for the platform
#: dashboard's Shop "Revenue" figure (kept as an independent definition here
#: rather than imported, same reasoning as shop/services/invoices.py's own
#: standalone PDF error handling: this app doesn't reach into controlpanel).
PAID_STATUSES = (Order.OrderStatus.PAID, Order.OrderStatus.DELIVERED)

#: Still needs staff attention -- payment collection or handover -- as
#: opposed to a settled (delivered) or dead (cancelled/refunded) order.
OPEN_STATUSES = (Order.OrderStatus.PENDING, Order.OrderStatus.PAID, Order.OrderStatus.PARTIALLY_PAID)


def order_kpis(club):
    """Top-of-the-orders-page numbers: how many orders need attention, how
    many there have been in total, and what's actually been sold."""
    orders = Order.objects.filter(club=club)
    paid_orders = orders.filter(status__in=PAID_STATUSES)

    return {
        "open_orders": orders.filter(status__in=OPEN_STATUSES).count(),
        "total_orders": orders.count(),
        "total_sold": paid_orders.aggregate(total=Sum("total"))["total"] or Decimal("0"),
        "items_sold": OrderLine.objects.filter(order__in=paid_orders).aggregate(total=Sum("quantity"))["total"] or 0,
    }


def quantity_sold_by_product(club):
    """{product_id: total quantity} across every paid order line for this
    club -- one query for the whole Products list, not one per row."""
    rows = OrderLine.objects.filter(order__club=club, order__status__in=PAID_STATUSES).values("product_id").annotate(total=Sum("quantity"))
    return {row["product_id"]: row["total"] for row in rows}


def quantity_sold_by_variant(product):
    """{variant_id: total quantity} for one product's own variants -- the
    product edit page's per-row breakdown."""
    rows = OrderLine.objects.filter(product=product, variant__isnull=False, order__status__in=PAID_STATUSES).values("variant_id").annotate(total=Sum("quantity"))
    return {row["variant_id"]: row["total"] for row in rows}
