"""Tracking whether a merchandise OrderLine has been sent to (and received
back from) a manufacturer -- independent from payment_status/
fulfillment_status, which are both about the member's own side of the
order, not the club's manufacturing pipeline.
"""

from dataclasses import dataclass

from django.db import transaction
from django.db.models import Q

from ..models import Order, OrderLine, Product, ProductionStatus

#: A line whose personalization_number/_name is set can't be received in
#: bulk by counting boxes -- there's no telling *which* member's jersey a
#: given unit is without reading the name/number actually printed on it, so
#: it always needs the one-at-a-time OrderLineMarkReceivedView action
#: instead (see receive_production/production_receiving_summary below,
#: both of which exclude these lines from their own counting).
_NOT_PERSONALIZED = Q(personalization_number="") & Q(personalization_name="")


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


def production_receiving_summary():
    """Every (product, variant) combo with at least one non-personalized
    OrderLine currently IN_PRODUCTION, with the total *quantity* expected
    (not line count -- a single line can be several units) -- one row of
    the "Receive production" screen's own entry form. Grouped by product,
    each carrying its ``personalized_count`` (in-production personalized
    lines for that product, never folded into ``expected_quantity`` or
    touched by ``receive_production`` below) so an order needing individual
    handling is flagged there rather than silently left off entirely.

    Returns ``[{"product": Product, "variants": [{"variant": ProductVariant
    | None, "expected_quantity": int}, ...], "personalized_count": int}, ...]``,
    products with nothing at all pending (no plain lines and no
    personalized ones) left out."""
    merch_in_production = OrderLine.objects.filter(product__product_type=Product.ProductType.MERCHANDISE, production_status=ProductionStatus.IN_PRODUCTION).exclude(order__fulfillment_status=Order.FulfillmentStatus.CANCELLED)

    by_product: dict = {}
    for line in merch_in_production.filter(_NOT_PERSONALIZED).select_related("product", "variant"):
        entry = by_product.setdefault(line.product_id, {"product": line.product, "variants": {}, "personalized_count": 0})
        variant_entry = entry["variants"].setdefault(line.variant_id, {"variant": line.variant, "expected_quantity": 0})
        variant_entry["expected_quantity"] += line.quantity

    for line in merch_in_production.exclude(_NOT_PERSONALIZED).select_related("product"):
        entry = by_product.setdefault(line.product_id, {"product": line.product, "variants": {}, "personalized_count": 0})
        entry["personalized_count"] += 1

    summary = []
    for entry in by_product.values():
        entry["variants"] = sorted(entry["variants"].values(), key=lambda row: (row["variant"].ordering, row["variant"].name) if row["variant"] else (-1, ""))
        summary.append(entry)
    summary.sort(key=lambda entry: entry["product"].name)
    return summary


@dataclass
class ProductionReceipt:
    """One (product, variant) row's own result from ``receive_production``
    below -- what the confirmation screen shows staff after submitting
    physical counts, and everything it needs to explain a discrepancy."""

    product: Product
    variant: object  # ProductVariant | None
    expected_quantity: int
    received_quantity: int
    allocated_lines: list
    remaining_lines: list

    @property
    def shortfall(self) -> int:
        """How many fewer units arrived than were ever sent to production --
        0 when the physical count matched or exceeded it."""
        return max(0, self.expected_quantity - self.received_quantity)

    @property
    def surplus(self) -> int:
        """How many units arrived beyond anything on file accounts for -- 0
        when the physical count matched or fell short."""
        return max(0, self.received_quantity - self.expected_quantity)

    @property
    def has_discrepancy(self) -> bool:
        return bool(self.shortfall or self.surplus)


@transaction.atomic
def receive_production(receipts: dict) -> list[ProductionReceipt]:
    """``receipts`` maps ``(product, variant_or_None) -> received_quantity``
    (a physical count staff just entered). For each key, FIFO-allocates
    against every non-personalized OrderLine currently IN_PRODUCTION for
    that exact product+variant, oldest order first (``order__created``):
    walks them in that order, marking a line RECEIVED only once the running
    total-so-far plus that line's own quantity still fits within the
    physical count given -- a line an entered count can only *partially*
    cover is left entirely alone instead (there's no per-unit tracking to
    split it on), and every line at or after that point stays untouched
    too, so an order never gets skipped ahead of one placed earlier. Any
    count entered beyond the sum of every in-production line for that
    product+variant is an unexplained surplus -- reported on the returned
    ``ProductionReceipt`` but not allocated anywhere, since there's nothing
    left it could belong to.

    Personalized lines are never touched here at all (see this module's own
    ``_NOT_PERSONALIZED`` docstring) -- they're always received one at a
    time, by hand, via ``mark_line_received``/``OrderLineMarkReceivedView``,
    since only reading the name/number actually printed on a unit says
    which member it's for."""
    results = []
    touched_orders = set()

    for (product, variant), received_quantity in receipts.items():
        if received_quantity is None:
            continue

        lines = list(
            OrderLine.objects.filter(product=product, variant=variant, production_status=ProductionStatus.IN_PRODUCTION)
            .filter(_NOT_PERSONALIZED)
            .exclude(order__fulfillment_status=Order.FulfillmentStatus.CANCELLED)
            .select_related("order", "order__purchaser", "variant", "product")
            .order_by("order__created")
        )
        expected_quantity = sum(line.quantity for line in lines)

        allocated_lines, remaining_lines = [], []
        cumulative = 0
        still_covering = True
        for line in lines:
            if still_covering and cumulative + line.quantity <= received_quantity:
                allocated_lines.append(line)
                cumulative += line.quantity
            else:
                still_covering = False
                remaining_lines.append(line)

        if allocated_lines:
            OrderLine.objects.filter(pk__in=[line.pk for line in allocated_lines]).update(production_status=ProductionStatus.RECEIVED)
            touched_orders.update(line.order for line in allocated_lines)

        results.append(ProductionReceipt(product=product, variant=variant, expected_quantity=expected_quantity, received_quantity=received_quantity, allocated_lines=allocated_lines, remaining_lines=remaining_lines))

    for order in touched_orders:
        sync_production_status(order)

    return results
