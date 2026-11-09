"""Production of made-to-order merchandise: line ``pending`` (to order) -> batch created (draft, CSV for the supplier) ->
batch marked ordered (lines ``ordered``) -> received (whole batch, or per line / partial quantity).

Inventory-managed and non-merchandise lines skip production (they are created ``received``). An order rolls its production
lines up into ``Order.production_status``. A fully received order does NOT become ready by itself: staff confirm it in bulk in the
ready-for-pickup workflow (``shop.services.pickup``)."""

import csv
import io

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from formbuilder.services.management import Conflict
from members.services.errors import Invalid
from shop.models import Order, OrderLine, Product, ProductionBatch, ProductionStatus

Batch = ProductionBatch.Status


def is_production_line(line) -> bool:
    p = line.product
    return p.product_type == Product.ProductType.MERCHANDISE and not p.inventory_managed


def production_lines(order):
    return order.lines.filter(product__product_type=Product.ProductType.MERCHANDISE, product__inventory_managed=False)


def sync_order(order, *, actor=None) -> None:
    """Recompute ``order.production_status`` from its production lines (received orders then show up in the pickup workflow)."""
    statuses = set(production_lines(order).values_list("production_status", flat=True))
    if not statuses:
        return
    new = ProductionStatus.RECEIVED if statuses == {ProductionStatus.RECEIVED} else ProductionStatus.PENDING if statuses == {ProductionStatus.PENDING} else ProductionStatus.ORDERED
    if order.production_status != new:
        order.production_status = new
        order.save(update_fields=["production_status", "modified"])


def lines_to_order(product_id=None):
    """Lines nobody has ordered from the supplier yet (not in a batch), on orders that are not cancelled."""
    qs = (
        OrderLine.objects.filter(product__product_type=Product.ProductType.MERCHANDISE, product__inventory_managed=False, production_status=ProductionStatus.PENDING, production_batch__isnull=True)
        .exclude(order__fulfillment_status=Order.FulfillmentStatus.CANCELLED)
        .select_related("order", "order__purchaser", "product", "variant", "beneficiary", "team")
        .order_by("product__name", "order__created")
    )
    return qs.filter(product_id=product_id) if product_id else qs


@transaction.atomic
def create_batch(actor, line_ids, *, reference="", supplier="", note="") -> ProductionBatch:
    """Group lines into a draft batch. Every line must still be in 'to order' and not in another batch."""
    ids = list(dict.fromkeys(line_ids))
    if not ids:
        raise Invalid("Select at least one line.", {"line_ids": ["Required."]})
    lines = list(lines_to_order().select_for_update(of=("self",)).filter(pk__in=ids))
    if len(lines) != len(ids):
        raise Conflict("Some lines are not available to order any more.", code="lines_unavailable")
    batch = ProductionBatch.objects.create(reference=reference.strip()[:255], supplier=supplier.strip()[:255], note=note, status=Batch.DRAFT, created_by=actor if getattr(actor, "pk", None) else None)
    OrderLine.objects.filter(pk__in=ids).update(production_batch=batch)
    return batch


@transaction.atomic
def update_batch(batch, **fields) -> ProductionBatch:
    for key in ("reference", "supplier", "note"):
        if fields.get(key) is not None:
            setattr(batch, key, fields[key])
    batch.save()
    return batch


@transaction.atomic
def delete_batch(batch) -> None:
    batch = ProductionBatch.objects.select_for_update().get(pk=batch.pk)
    if batch.status != Batch.DRAFT:
        raise Conflict("Only a draft batch can be deleted.", code="not_draft")
    batch.lines.update(production_batch=None)
    batch.delete()


@transaction.atomic
def mark_ordered(batch) -> ProductionBatch:
    """The supplier has the order: lines become 'ordered'."""
    batch = ProductionBatch.objects.select_for_update().get(pk=batch.pk)
    if batch.status != Batch.DRAFT:
        raise Conflict("This batch was already ordered.", code="already_ordered")
    now = timezone.now()
    by_order: dict = {}
    for line in batch.lines.select_related("order", "product", "variant"):
        by_order.setdefault(line.order, []).append(line)
    batch.lines.update(production_status=ProductionStatus.ORDERED, ordered_at=now)
    batch.status, batch.ordered_at = Batch.ORDERED, now
    batch.save(update_fields=["status", "ordered_at", "modified"])
    for order, lines in by_order.items():
        sync_order(order)
        _tell_in_production(order, lines)
    return batch


def _item_label(line) -> str:
    name = f"{line.product.name}{f' ({line.variant.name})' if line.variant_id else ''}"
    custom = " ".join(part for part in (f"#{line.personalization_number}" if line.personalization_number else "", line.personalization_name) if part)
    return f"{name} {custom}".strip() + (f" x{line.quantity}" if line.quantity > 1 else "")


def _tell_in_production(order, lines) -> None:
    """Tell the purchaser these lines went into production: the whole order when nothing made-to-order is left waiting, else part
    of it (the other lines follow in another batch). Cancelled orders are not told."""
    if order.fulfillment_status == Order.FulfillmentStatus.CANCELLED:
        return
    from shop.services import notifications

    waiting = production_lines(order).filter(production_status=ProductionStatus.PENDING).exists()
    shown = [_item_label(line) for line in lines]
    items = ", ".join(shown[:6]) + (" ..." if len(shown) > 6 else "")
    transaction.on_commit(lambda: notifications.dispatch("production_partial" if waiting else "production", order.pk, {"items": items}))


def _refresh_batch(batch) -> None:
    remaining = batch.lines.exclude(production_status=ProductionStatus.RECEIVED).exists()
    received_any = batch.lines.filter(received_quantity__gt=0).exists()
    status = Batch.PARTIALLY_RECEIVED if remaining and received_any else Batch.ORDERED if remaining else Batch.RECEIVED
    batch.status = status
    batch.received_at = timezone.now() if status == Batch.RECEIVED else None
    batch.save(update_fields=["status", "received_at", "modified"])


@transaction.atomic
def receive(batch, items=None) -> ProductionBatch:
    """Receive ``items`` (``[(line_id, quantity)]``, partial quantities allowed) or, with ``items`` None, everything still
    outstanding in the batch."""
    batch = ProductionBatch.objects.select_for_update().get(pk=batch.pk)
    if batch.status == Batch.DRAFT:
        raise Conflict("Mark the batch as ordered first.", code="not_ordered")
    lines = {line.pk: line for line in batch.lines.select_for_update(of=("self",)).select_related("order")}
    if items is None:
        items = [(pk, line.quantity - line.received_quantity) for pk, line in lines.items() if line.production_status != ProductionStatus.RECEIVED]
    now = timezone.now()
    touched = {}
    for line_id, quantity in items:
        line = lines.get(line_id)
        if line is None:
            raise Invalid("A line does not belong to this batch.", {"items": ["Unknown line."]})
        if quantity < 1:
            raise Invalid("Quantity must be at least 1.", {"items": ["Quantity must be at least 1."]})
        if line.received_quantity + quantity > line.quantity:
            raise Invalid("More received than ordered.", {"items": [f"Only {line.quantity - line.received_quantity} still expected."]})
        line.received_quantity += quantity
        if line.received_quantity >= line.quantity:
            line.production_status, line.received_at = ProductionStatus.RECEIVED, now
        line.save(update_fields=["received_quantity", "production_status", "received_at", "modified"])
        touched[line.order_id] = line.order
    _refresh_batch(batch)
    for order in touched.values():
        sync_order(order)
    return batch


def _safe(value) -> str:
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def batch_csv(batch) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["batch", "product", "variant", "quantity", "personalization_number", "personalization_name", "order", "purchaser", "beneficiary", "team"])
    lines = batch.lines.select_related("order__purchaser", "product", "variant", "beneficiary", "team").order_by("product__name", "variant__name", "order__number")
    for line in lines:
        writer.writerow(
            [
                _safe(x)
                for x in (
                    batch.number,
                    line.product.name,
                    line.variant.name if line.variant else "",
                    line.quantity,
                    line.personalization_number,
                    line.personalization_name,
                    line.order.number,
                    line.order.purchaser,
                    line.beneficiary or "",
                    line.team.name if line.team else "",
                )
            ]
        )
    return out.getvalue()


def batches():
    return ProductionBatch.objects.all()


def search(qs, q):
    return qs.filter(Q(number__icontains=q) | Q(reference__icontains=q) | Q(supplier__icontains=q)) if q else qs
