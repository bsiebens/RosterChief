"""Staff-side order handling: the lifecycle actions behind the management Orders screens.

One place for what the management views (``management.views.Order*``) and the management API
(``api/panel/routers/shop_orders.py``) both do, so the two can't drift: filtering the list, marking
orders paid / ready for pickup / delivered / cancelled (one or many), editing a line, removing a
payment, and building the manufacturer export. Payments themselves live in ``shop.services.payments``,
production tracking in ``shop.services.production``.
"""

from django.db import transaction
from django.db.models import Count, Exists, OuterRef, Q

from ..models import Order, OrderLine, Payment, Product, ProductionStatus
from .inventory import InventoryError, return_order_to_stock, sync_line_stock
from .notifications import dispatch_order_ready_for_pickup_notification
from .payments import PaymentError, amount_due, delete_payment, record_payment, sync_payment_status
from .pricing import order_total
from .production import mark_lines_in_production, pending_production_lines, sync_production_status

__all__ = [
    "InventoryError",
    "PaymentError",
    "bulk_mark_paid",
    "bulk_mark_ready_for_pickup",
    "cancel_order",
    "export_pending_production",
    "filter_orders",
    "mark_delivered",
    "mark_paid",
    "mark_ready_for_pickup",
    "merchandise_products",
    "production_products",
    "ready_for_pickup_orders",
    "remove_payment",
    "save_order_line",
]


def filter_orders(club, *, payment_status: str = "", fulfillment_status: str = "", show_all: bool = False, search: str = ""):
    """The Orders list: the club's orders, closed ones (delivered *and* paid) hidden unless ``show_all``
    or a status filter is set (picking a fulfillment status and silently dropping paid orders from it
    would be surprising). ``search`` matches the purchaser's first/last/family name or the order number.
    ``has_production_line_items`` is annotated so a page needs no query per row."""
    orders = Order.objects.filter(club=club).select_related("purchaser").annotate(has_production_line_items=Exists(OrderLine.objects.filter(order=OuterRef("pk"), product__product_type=Product.ProductType.MERCHANDISE)))
    if payment_status:
        orders = orders.filter(payment_status=payment_status)
    if fulfillment_status:
        orders = orders.filter(fulfillment_status=fulfillment_status)
    if not payment_status and not fulfillment_status and not show_all:
        orders = orders.exclude(fulfillment_status=Order.FulfillmentStatus.DELIVERED, payment_status=Order.PaymentStatus.PAID)

    search = search.strip()
    if search:
        orders = orders.filter(
            Q(purchaser__first_name__icontains=search)
            | Q(purchaser__last_name__icontains=search)
            | Q(purchaser__family_memberships__family__club=club, purchaser__family_memberships__family__name__icontains=search)
            | Q(purchaser__family_memberships__family__club=club, purchaser__family_memberships__family__memberships__member__last_name__icontains=search)
            | Q(number__icontains=search)
        ).distinct()
    return orders


def merchandise_products(club):
    """The club's merchandise products: what a manufacturer export can cover."""
    return Product.objects.filter(club=club, product_type=Product.ProductType.MERCHANDISE)


def production_products(club):
    """Every merchandise product with how many of its lines still wait to be sent (``pending_count``)
    and how many are out with a manufacturer (``in_production_count``), cancelled orders left out."""
    live = ~Q(order_items__order__fulfillment_status=Order.FulfillmentStatus.CANCELLED)
    return (
        merchandise_products(club)
        .annotate(
            pending_count=Count("order_items", filter=Q(order_items__production_status=ProductionStatus.PENDING) & live),
            in_production_count=Count("order_items", filter=Q(order_items__production_status=ProductionStatus.IN_PRODUCTION) & live),
        )
        .order_by("name")
    )


def ready_for_pickup_orders(club):
    """Orders whose merchandise is fully back from production but not yet marked ready, oldest first;
    ``has_personalized_lines`` flags those that needed the one-at-a-time receiving."""
    return (
        Order.objects.filter(club=club, production_status=ProductionStatus.RECEIVED, fulfillment_status=Order.FulfillmentStatus.NOT_READY)
        .select_related("purchaser")
        .annotate(has_personalized_lines=Exists(OrderLine.objects.filter(order=OuterRef("pk")).exclude(personalization_number="", personalization_name="")))
        .order_by("created")
    )


# --- payment ---------------------------------------------------------------------------------


def mark_paid(order, *, method: str, reference: str = "") -> Payment | None:
    """Settles whatever is still due with one payment (a voucher that already covered part is not
    charged twice). ``None`` when nothing is due."""
    due = amount_due(order)
    if due <= 0:
        return None
    return record_payment(order, amount=due, method=method, reference=reference)


def bulk_mark_paid(club, order_ids, *, method: str) -> int:
    """``mark_paid`` for each of ``order_ids`` that belongs to ``club`` and isn't cancelled; orders with
    nothing due are skipped. Returns how many got a payment."""
    orders = Order.objects.filter(pk__in=order_ids, club=club).exclude(fulfillment_status=Order.FulfillmentStatus.CANCELLED)
    count = 0
    with transaction.atomic():
        for order in orders:
            if mark_paid(order, method=method) is not None:
                count += 1
    return count


def remove_payment(payment) -> bool:
    """Deletes a payment (a voucher payment gives its amount back) and recomputes the order's payment
    status, unless the order is refunded (a terminal state a vanishing row must not resurrect).
    Returns True when the order fell back to pending."""
    order = payment.order
    delete_payment(payment)
    if order.payment_status == Order.PaymentStatus.REFUNDED:
        return False
    sync_payment_status(order)
    order.refresh_from_db(fields=["payment_status"])
    return order.payment_status == Order.PaymentStatus.PENDING


# --- fulfilment ------------------------------------------------------------------------------


def mark_ready_for_pickup(order, *, pickup_instructions: str = "") -> None:
    """Flags the order ready (regardless of its payment state), keeps the instructions on it and
    notifies the purchaser."""
    order.pickup_instructions = pickup_instructions
    order.fulfillment_status = Order.FulfillmentStatus.READY_FOR_PICKUP
    order.save(update_fields=["fulfillment_status", "pickup_instructions"])
    dispatch_order_ready_for_pickup_notification(order.pk)


def bulk_mark_ready_for_pickup(club, order_ids, *, pickup_instructions: str = "") -> int:
    """``mark_ready_for_pickup`` with one shared note for each of ``order_ids`` that belongs to ``club``
    and isn't cancelled; each purchaser is notified about their own order. Returns how many."""
    orders = Order.objects.filter(pk__in=order_ids, club=club).exclude(fulfillment_status=Order.FulfillmentStatus.CANCELLED)
    count = 0
    for order in orders:
        mark_ready_for_pickup(order, pickup_instructions=pickup_instructions)
        count += 1
    return count


def mark_delivered(order) -> None:
    """Plain status set, no state machine: a pickup before payment happens in real life."""
    order.fulfillment_status = Order.FulfillmentStatus.DELIVERED
    order.save(update_fields=["fulfillment_status"])


def cancel_order(order, *, user=None) -> None:
    """Cancels the order and puts what it took back in stock."""
    with transaction.atomic():
        order.fulfillment_status = Order.FulfillmentStatus.CANCELLED
        order.save(update_fields=["fulfillment_status"])
        return_order_to_stock(order, user=user)


# --- lines -----------------------------------------------------------------------------------


def save_order_line(line, *, old_variant_id, user=None) -> None:
    """Persists an edited (not yet saved) line: price and total are recomputed from quantity and the
    possibly new variant, stock moves to match, then the order's total, payment status and production
    rollup follow. Raises ``InventoryError`` -- changing nothing -- when the new size lacks stock."""
    line.unit_price = line.variant.effective_price if line.variant_id else line.product.price
    line.line_total = line.unit_price * line.quantity
    with transaction.atomic():
        line.save()
        sync_line_stock(line, old_variant_id=old_variant_id, user=user)

    order = line.order
    order.total = order_total(order)
    order.save(update_fields=["total"])
    sync_payment_status(order)
    sync_production_status(order)


# --- production export -----------------------------------------------------------------------


def export_pending_production(club, products):
    """Builds the manufacturer spreadsheet of every not-yet-submitted line of ``products`` and marks
    those lines in production -- only once the file is built, so a failed export never marks lines sent
    that weren't. The file is stashed under a one-time token (``management.shop_export``). Returns
    ``(token, filename, line_count)``, or ``None`` when nothing is pending."""
    # Lazy: the spreadsheet builder lives in the management app, which imports this package.
    from management.shop_export import build_production_export, stash_production_export

    lines = list(pending_production_lines(products))
    if not lines:
        return None
    filename = "order-production-list.xlsx"
    workbook = build_production_export(lines)
    mark_lines_in_production(lines)
    return stash_production_export(club, workbook, filename), filename, len(lines)
