"""Numbers for the overview card and the shop pages."""

from decimal import Decimal

from django.db.models import Sum

from shop.models import Order, OrderLine
from shop.services import payments, production

PAID = Order.PaymentStatus.PAID
CANCELLED = Order.FulfillmentStatus.CANCELLED


def shop_stats() -> dict:
    orders = Order.objects.all()
    live = orders.exclude(fulfillment_status=CANCELLED)
    paid = live.filter(payment_status=PAID)
    outstanding = sum((payments.amount_due(o) for o in live.exclude(payment_status__in=[PAID, Order.PaymentStatus.REFUNDED])), Decimal("0"))
    return {
        "orders_total": orders.count(),
        "orders_open": live.exclude(fulfillment_status=Order.FulfillmentStatus.DELIVERED, payment_status=PAID).count(),
        "revenue": paid.aggregate(t=Sum("total"))["t"] or Decimal("0"),
        "items_sold": OrderLine.objects.filter(order__in=paid).aggregate(t=Sum("quantity"))["t"] or 0,
        "outstanding": outstanding,
        "unpaid_count": live.filter(payment_status__in=[Order.PaymentStatus.PENDING, Order.PaymentStatus.PARTIALLY_PAID]).count(),
        "ready_for_pickup_count": live.filter(fulfillment_status=Order.FulfillmentStatus.READY_FOR_PICKUP).count(),
        "lines_to_order_count": production.lines_to_order().count(),
    }
