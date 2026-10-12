"""Stock for inventory-managed products (Product.inventory_managed): bought in
bulk and counted, instead of made to order. An order draws the items down from
stock and its lines skip production (they're RECEIVED from the start); a
product or variant with nothing left can't be ordered until new stock is
taken in.

Stock is the sum of a product's InventoryMovement rows, per variant (or per
product, for one without variants) -- there's no stored counter. Everything
that moves stock goes through this module so the ledger and the order lines
can't disagree; the only locking needed is on the Product row (see
``lock_products``), taken before reading stock whenever a decision depends on
it.
"""

from collections import defaultdict
from dataclasses import dataclass

from django.db import transaction
from django.db.models import Sum, prefetch_related_objects
from django.utils.translation import gettext_lazy as _

from ..models import InventoryMovement, Order, OrderLine, Product, ProductionStatus

Reason = InventoryMovement.Reason


class InventoryError(Exception):
    """A stock change that can't be made -- shown to staff as-is (already a
    translated, user-facing message)."""


def stock_levels(products) -> dict:
    """``{(product_id, variant_id | None): on_hand}`` for these products, from
    one query. A combination with no movements yet is simply absent (stock 0)."""
    rows = InventoryMovement.objects.filter(product__in=products).values("product_id", "variant_id").annotate(total=Sum("quantity"))
    return {(row["product_id"], row["variant_id"]): row["total"] for row in rows}


def stock_for(product, variant=None) -> int:
    """On-hand stock of one product/variant (variant None for a product without
    variants). Meaningless for a product that isn't inventory managed."""
    total = InventoryMovement.objects.filter(product=product, variant=variant).aggregate(total=Sum("quantity"))["total"]
    return total or 0


def is_in_stock(product, variant=None, quantity=1) -> bool:
    """Whether ``quantity`` of this product/variant can be ordered right now:
    always for a product that isn't inventory managed, otherwise only while
    enough is on hand."""
    return not product.inventory_managed or stock_for(product, variant) >= quantity


def annotate_availability(products):
    """Sets ``in_stock`` on each product and on every variant in its prefetched
    ``variants`` (active ones, the way the shop lists them), from one stock
    query: a managed product without variants is in stock while it has any;
    one with variants is in stock while some active variant is. Products that
    aren't inventory managed are always in stock. Returns ``products`` as a list."""
    products = list(products)
    prefetch_related_objects(products, "variants")  # a no-op when already prefetched; the flags below live on those cached objects
    levels = stock_levels([product for product in products if product.inventory_managed])
    for product in products:
        variants = [variant for variant in product.variants.all() if variant.is_active]
        if not product.inventory_managed:
            product.in_stock = True
            for variant in variants:
                variant.in_stock = True
            continue
        for variant in variants:
            variant.in_stock = levels.get((product.pk, variant.pk), 0) > 0
        product.in_stock = any(variant.in_stock for variant in variants) if variants else levels.get((product.pk, None), 0) > 0
    return products


def annotate_cart_variants(items):
    """Sets ``in_stock`` on the option of each cart item (per option, like
    ``annotate_availability``) from one stock query -- the same flag the
    catalogue shows for that option."""
    items = [item for item in items if item.variant_id]
    levels = stock_levels({item.product for item in items if item.product.inventory_managed})
    for item in items:
        item.variant.in_stock = not item.product.inventory_managed or levels.get((item.product_id, item.variant_id), 0) > 0


def lock_products(product_ids):
    """Row-locks these products (inside the caller's transaction) so two orders
    or stock changes racing for the same stock are handled one at a time --
    stock is only trustworthy to read after this."""
    return list(Product.objects.select_for_update().filter(pk__in=product_ids).order_by("pk"))


def _label(product, variant):
    return f"{product.name} ({variant.name})" if variant is not None else product.name


def record_movement(product, variant, quantity, reason, *, note="", order=None, user=None):
    if quantity == 0:
        return None
    return InventoryMovement.objects.create(product=product, variant=variant, quantity=quantity, reason=reason, note=note, order=order, created_by=user)


@transaction.atomic
def receive_stock(club, receipts: dict, *, note="", user=None) -> list:
    """``receipts`` maps ``(product, variant_or_None) -> quantity`` taken in
    (positive). Anything for another club's product, a product that isn't
    inventory managed, or a quantity of 0 or less is skipped. Returns the
    movements created."""
    movements = []
    for (product, variant), quantity in receipts.items():
        if not quantity or quantity < 1 or product.club_id != club.pk or not product.inventory_managed:
            continue
        if (variant is None) == product.variants.filter(is_active=True).exists():
            # A product with variants is stocked per variant, one without per product.
            continue
        movements.append(record_movement(product, variant, quantity, Reason.RECEIVED, note=note, user=user))
    return movements


@transaction.atomic
def set_stock(club, product, variant, counted: int, *, note="", user=None):
    """Corrects the on-hand count to ``counted`` (a recount, breakage, a
    miscounted delivery) by recording the difference as an ADJUSTMENT. Returns
    the movement, or None when the count already matched."""
    if product.club_id != club.pk or not product.inventory_managed:
        raise InventoryError(_("This product isn't inventory managed."))
    if counted < 0:
        raise InventoryError(_("A count can't be negative."))
    lock_products([product.pk])
    return record_movement(product, variant, counted - stock_for(product, variant), Reason.ADJUSTMENT, note=note, user=user)


def check_available(cart_items):
    """Raises InventoryError naming every inventory-managed item that the cart
    wants more of than is in stock -- several lines for the same
    product/variant (one per beneficiary, say) are counted together. Call after
    ``lock_products`` so the answer holds until the transaction ends."""
    wanted = defaultdict(int)
    labels = {}
    for item in cart_items:
        if item.product.inventory_managed:
            key = (item.product_id, item.variant_id)
            wanted[key] += item.quantity
            labels[key] = _label(item.product, item.variant)
    if not wanted:
        return

    levels = stock_levels([item.product for item in cart_items if item.product.inventory_managed])
    short = [f"{labels[key]}: {levels.get(key, 0)}" if levels.get(key, 0) > 0 else str(labels[key]) for key, quantity in wanted.items() if levels.get(key, 0) < quantity]
    if short:
        raise InventoryError(_("Not enough stock for: %(items)s. Lower the quantity or remove the item from your cart.") % {"items": ", ".join(short)})


def take_for_order_lines(lines):
    """For an order just created: books every inventory-managed line out of
    stock and marks it RECEIVED -- it's already on the shelf, so it skips
    production. Resyncing the order's own production_status is left to the
    caller (shop.services.production.sync_production_status)."""
    taken = []
    for line in lines:
        if not line.product.inventory_managed:
            continue
        record_movement(line.product, line.variant, -line.quantity, Reason.ORDER, order=line.order)
        taken.append(line.pk)
    OrderLine.objects.filter(pk__in=taken).update(production_status=ProductionStatus.RECEIVED)
    for line in lines:
        if line.pk in taken:
            line.production_status = ProductionStatus.RECEIVED


def _drawn_from_stock(order, product, variant_id) -> int:
    """Net units this order currently holds out of stock for one product/variant, per the ledger."""
    movements = InventoryMovement.objects.filter(order=order, product=product, variant_id=variant_id, reason__in=[Reason.ORDER, Reason.ORDER_RETURNED])
    return -(movements.aggregate(total=Sum("quantity"))["total"] or 0)


@transaction.atomic
def return_order_to_stock(order, *, user=None):
    """Puts back what a cancelled order took from stock. Safe to call twice:
    it only returns what the ledger still shows this order holding."""
    products = {line.product_id: line.product for line in order.order_items.select_related("product", "variant") if line.product.inventory_managed}
    lock_products(products)

    held = defaultdict(int)
    keys = {}
    for movement in InventoryMovement.objects.filter(order=order, reason__in=[Reason.ORDER, Reason.ORDER_RETURNED]).select_related("product", "variant"):
        key = (movement.product_id, movement.variant_id)
        held[key] -= movement.quantity
        keys[key] = (movement.product, movement.variant)
    for key, quantity in held.items():
        if quantity > 0:
            product, variant = keys[key]
            record_movement(product, variant, quantity, Reason.ORDER_RETURNED, order=order, user=user)


@transaction.atomic
def sync_line_stock(line, *, old_variant_id, user=None):
    """After staff edit an inventory-managed order line (another size, another
    quantity): moves the stock to match -- puts back what the line held, takes
    what it now needs. Raises InventoryError, changing nothing, when the new
    size doesn't have enough on hand."""
    product = line.product
    if not product.inventory_managed or line.order.fulfillment_status == Order.FulfillmentStatus.CANCELLED:
        return

    lock_products([product.pk])
    new_variant = line.variant
    old_variant = product.variants.filter(pk=old_variant_id).first() if old_variant_id else None
    held = _drawn_from_stock(line.order, product, old_variant_id)
    if held == line.quantity and old_variant_id == line.variant_id:
        return

    available = stock_for(product, new_variant) + (held if old_variant_id == line.variant_id else 0)
    if available < line.quantity:
        raise InventoryError(_("Only %(count)s of “%(item)s” in stock.") % {"count": max(available, 0), "item": _label(product, new_variant)})

    record_movement(product, old_variant, held, Reason.ORDER_RETURNED, order=line.order, user=user)  # no-op when nothing was held
    record_movement(product, new_variant, -line.quantity, Reason.ORDER, order=line.order, user=user)


@dataclass
class StockRow:
    product: Product
    variant: object  # ProductVariant | None
    on_hand: int
    committed: int


def inventory_overview(club) -> list[StockRow]:
    """One row per stock-keeping combination of the club's inventory-managed
    products -- each active variant, or the product itself when it has none --
    with what's on hand and how much of it is already promised to open orders.
    Inactive products stay listed (they may still hold stock); a variant that
    was switched off but still has stock stays too."""
    products = list(Product.objects.filter(club=club, inventory_managed=True).prefetch_related("variants").order_by("name"))
    levels = stock_levels(products)
    rows = []
    for product in products:
        variants = [variant for variant in product.variants.all() if variant.is_active or levels.get((product.pk, variant.pk), 0)]
        if variants:
            rows.extend(StockRow(product, variant, levels.get((product.pk, variant.pk), 0), 0) for variant in variants)
        else:
            rows.append(StockRow(product, None, levels.get((product.pk, None), 0), 0))

    open_lines = OrderLine.objects.filter(order__club=club, product__inventory_managed=True).exclude(order__fulfillment_status__in=[Order.FulfillmentStatus.DELIVERED, Order.FulfillmentStatus.CANCELLED])
    committed = {(row["product_id"], row["variant_id"]): row["total"] for row in open_lines.values("product_id", "variant_id").annotate(total=Sum("quantity"))}
    for row in rows:
        row.committed = committed.get((row.product.pk, row.variant.pk if row.variant else None), 0)
    return rows
