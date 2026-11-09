"""Stock for inventory-managed products: the sum of InventoryMovement rows per product/variant (no stored counter).
Everything that moves stock goes through here; take ``lock_products`` before any decision that depends on stock.
No backorders: an order can't take more than is on hand."""

from collections import defaultdict
from dataclasses import dataclass

from django.db import transaction
from django.db.models import F, Sum

from members.services import access
from members.services.errors import Invalid
from shop.models import InventoryMovement, Order, OrderLine, Product, ProductionStatus

Reason = InventoryMovement.Reason


class InventoryError(Exception):
    """A stock change that can't be made (user-facing message)."""


def stock_levels(products) -> dict:
    """``{(product_id, variant_id | None): on_hand}`` from one query."""
    rows = InventoryMovement.objects.filter(product__in=products).values("product_id", "variant_id").annotate(total=Sum("quantity"))
    return {(r["product_id"], r["variant_id"]): r["total"] for r in rows}


def stock_for(product, variant=None) -> int:
    return InventoryMovement.objects.filter(product=product, variant=variant).aggregate(total=Sum("quantity"))["total"] or 0


def is_in_stock(product, variant=None, quantity=1) -> bool:
    return not product.inventory_managed or stock_for(product, variant) >= quantity


def lock_products(product_ids):
    """Row-locks the products (inside the caller's transaction)."""
    return list(Product.objects.select_for_update().filter(pk__in=product_ids).order_by("pk"))


def record_movement(product, variant, quantity, reason, *, note="", order=None, user=None):
    if quantity == 0:
        return None
    return InventoryMovement.objects.create(product=product, variant=variant, quantity=quantity, reason=reason, note=note, order=order, created_by=user)


def _label(product, variant):
    return f"{product.name} ({variant.name})" if variant is not None else product.name


def check_available(items):
    """Raise InventoryError naming each inventory-managed item wanted beyond stock. ``items``: objects with
    product, variant, quantity (lines of the same product/variant are summed). Call after ``lock_products``."""
    wanted, labels = defaultdict(int), {}
    items = list(items)
    for item in items:
        if item.product.inventory_managed:
            key = (item.product.pk, item.variant.pk if item.variant else None)
            wanted[key] += item.quantity
            labels[key] = _label(item.product, item.variant)
    if not wanted:
        return
    levels = stock_levels([i.product for i in items if i.product.inventory_managed])
    short = [f"{labels[k]}: {levels.get(k, 0)}" if levels.get(k, 0) > 0 else str(labels[k]) for k, q in wanted.items() if levels.get(k, 0) < q]
    if short:
        raise InventoryError("Not enough stock for: " + ", ".join(short))


def take_for_order_lines(lines):
    """For a just-created order: book inventory-managed lines out of stock and mark them RECEIVED (they skip production).
    Resyncing Order.production_status is the caller's job."""
    taken = []
    for line in lines:
        if line.product.inventory_managed:
            record_movement(line.product, line.variant, -line.quantity, Reason.ORDER, order=line.order)
            taken.append(line.pk)
    OrderLine.objects.filter(pk__in=taken).update(production_status=ProductionStatus.RECEIVED, received_quantity=F("quantity"))
    for line in lines:
        if line.pk in taken:
            line.production_status = ProductionStatus.RECEIVED
            line.received_quantity = line.quantity


@transaction.atomic
def return_order_to_stock(order, *, user=None):
    """Put back what a cancelled order took. Safe to call twice (only returns what the ledger still shows held)."""
    lock_products({line.product_id for line in order.lines.all()})
    held, keys = defaultdict(int), {}
    for m in InventoryMovement.objects.filter(order=order, reason__in=[Reason.ORDER, Reason.ORDER_RETURNED]).select_related("product", "variant"):
        key = (m.product_id, m.variant_id)
        held[key] -= m.quantity
        keys[key] = (m.product, m.variant)
    for key, quantity in held.items():
        if quantity > 0:
            product, variant = keys[key]
            record_movement(product, variant, quantity, Reason.ORDER_RETURNED, order=order, user=user)


@transaction.atomic
def sync_line_stock(line, *, old_variant_id, user=None):
    """After staff change an inventory-managed line (other variant / quantity): move the stock to match. Raises
    InventoryError, changing nothing, when the new variant lacks stock."""
    product = line.product
    if not product.inventory_managed or line.order.fulfillment_status == Order.FulfillmentStatus.CANCELLED:
        return
    lock_products([product.pk])
    movements = InventoryMovement.objects.filter(order=line.order, product=product, variant_id=old_variant_id, reason__in=[Reason.ORDER, Reason.ORDER_RETURNED])
    held = -(movements.aggregate(total=Sum("quantity"))["total"] or 0)
    if held == line.quantity and old_variant_id == line.variant_id:
        return
    available = stock_for(product, line.variant) + (held if old_variant_id == line.variant_id else 0)
    if available < line.quantity:
        raise InventoryError(f"Only {max(available, 0)} of {_label(product, line.variant)} in stock.")
    old_variant = product.variants.filter(pk=old_variant_id).first() if old_variant_id else None
    record_movement(product, old_variant, held, Reason.ORDER_RETURNED, order=line.order, user=user)
    record_movement(product, line.variant, -line.quantity, Reason.ORDER, order=line.order, user=user)


@dataclass
class StockRow:
    product: Product
    variant: object
    on_hand: int
    committed: int


def inventory_overview() -> list[StockRow]:
    """One row per stock-keeping combination of the inventory-managed products (each active variant, or the product itself),
    with what is on hand and what open orders hold. A switched-off variant that still has stock stays listed."""
    products = list(Product.objects.filter(inventory_managed=True).prefetch_related("variants").order_by("name"))
    levels = stock_levels(products)
    rows = []
    for product in products:
        variants = [v for v in product.variants.all() if v.is_active or levels.get((product.pk, v.pk), 0)]
        if variants:
            rows.extend(StockRow(product, v, levels.get((product.pk, v.pk), 0), 0) for v in variants)
        else:
            rows.append(StockRow(product, None, levels.get((product.pk, None), 0), 0))
    open_lines = OrderLine.objects.filter(product__inventory_managed=True).exclude(order__fulfillment_status__in=[Order.FulfillmentStatus.DELIVERED, Order.FulfillmentStatus.CANCELLED])
    committed = {(r["product_id"], r["variant_id"]): r["total"] for r in open_lines.values("product_id", "variant_id").annotate(total=Sum("quantity"))}
    for row in rows:
        row.committed = committed.get((row.product.pk, row.variant.pk if row.variant else None), 0)
    return rows


# --- staff actions (permission checked) -----------------------------------------------------------------------------


def _target(product, variant):
    if not product.inventory_managed:
        raise Invalid("This product isn't inventory managed.", {"product_id": ["Not inventory managed."]})
    if variant is not None and variant.product_id != product.pk:
        raise Invalid("The variant belongs to another product.", {"variant_id": ["Wrong product."]})
    if (variant is None) == product.variants.filter(is_active=True).exists():
        raise Invalid("A product with variants is stocked per variant, one without per product.", {"variant_id": ["Pick a variant." if variant is None else "This product has no variants."]})


@transaction.atomic
def receive_stock(actor, items, *, note=""):
    """``items``: [(product, variant|None, quantity>=1)]. Returns the movements."""
    access.require_manage_shop_inventory(actor)
    movements = []
    for product, variant, quantity in items:
        if quantity < 1:
            raise Invalid("Quantity must be at least 1.", {"quantity": ["Must be at least 1."]})
        _target(product, variant)
        movements.append(record_movement(product, variant, quantity, Reason.RECEIVED, note=note, user=actor))
    return movements


@transaction.atomic
def set_stock(actor, product, variant, counted: int, *, note=""):
    """Correct the on-hand count to ``counted``; returns the adjustment movement or None when it already matched."""
    access.require_manage_shop_inventory(actor)
    if counted < 0:
        raise Invalid("A count can't be negative.", {"counted": ["Can't be negative."]})
    _target(product, variant)
    lock_products([product.pk])
    return record_movement(product, variant, counted - stock_for(product, variant), Reason.ADJUSTMENT, note=note, user=actor)
