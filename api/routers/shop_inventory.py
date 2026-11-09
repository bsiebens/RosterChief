"""Shop stock (``/shop/inventory``): overview, movement history, taking stock in and recounts. Needs ``shop.manage_inventory``."""

import uuid

from django.db.models import Q
from ninja import Query, Router
from ninja.errors import HttpError

from api.auth import AUTHENTICATORS
from api.routers.shop_common import access, gate
from api.schemas_shop_inventory import AdjustIn, AdjustOut, MovementListOut, ReceiveIn, ReceiveOut, StockListOut
from members.services.errors import Invalid
from shop.models import InventoryMovement, Product, ProductVariant
from shop.services import inventory as svc

router = Router(auth=AUTHENTICATORS)


def _gate(request):
    gate(request, access.require_manage_shop_inventory)


def _movement(m) -> dict:
    return {
        "id": m.pk, "product_id": m.product_id, "product_name": m.product.name, "variant_id": m.variant_id,
        "variant_name": m.variant.name if m.variant else None, "quantity": m.quantity, "reason": m.reason, "note": m.note,
        "order_id": m.order_id, "order_number": m.order.number if m.order else None,
        "created_by": (m.created_by.get_full_name() or m.created_by.email) if m.created_by else None, "created": m.created,
    }  # fmt: skip


def _target(product_id, variant_id):
    product = Product.objects.filter(pk=product_id).first()
    if product is None:
        raise Invalid("Unknown product.", {"product_id": ["Unknown product."]})
    variant = None
    if variant_id is not None:
        variant = ProductVariant.objects.filter(pk=variant_id, product=product).first()
        if variant is None:
            raise Invalid("Unknown variant.", {"variant_id": ["Unknown variant."]})
    return product, variant


@router.get("", response=StockListOut, summary="Stock per product and variant", operation_id="list_shop_stock")
def list_stock(request, q: str | None = None, low: bool = False, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    _gate(request)
    rows = svc.inventory_overview()
    if q:
        rows = [r for r in rows if q.lower() in r.product.name.lower() or (r.variant and q.lower() in r.variant.name.lower())]
    if low:
        rows = [r for r in rows if r.on_hand <= 0]
    results = [
        {
            "product_id": r.product.pk, "product_name": r.product.name, "variant_id": r.variant.pk if r.variant else None,
            "variant_name": r.variant.name if r.variant else None, "on_hand": r.on_hand, "committed": r.committed, "available": r.on_hand,
        }
        for r in rows[offset : offset + limit]
    ]  # fmt: skip
    return {"count": len(rows), "results": results}


@router.get("/movements", response=MovementListOut, summary="Stock movement history", operation_id="list_shop_stock_movements")
def list_movements(
    request, product_id: uuid.UUID | None = None, variant_id: uuid.UUID | None = None, reason: str | None = None,
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
):  # fmt: skip
    _gate(request)
    qs = InventoryMovement.objects.select_related("product", "variant", "order", "created_by").order_by("-created", "-id")
    if product_id:
        qs = qs.filter(product_id=product_id)
    if variant_id:
        qs = qs.filter(variant_id=variant_id)
    if reason:
        qs = qs.filter(Q(reason=reason))
    return {"count": qs.count(), "results": [_movement(m) for m in qs[offset : offset + limit]]}


@router.post("/receive", response={201: ReceiveOut}, summary="Take stock in", operation_id="receive_shop_stock")
def receive(request, payload: ReceiveIn):
    _gate(request)
    items = [(*_target(i.product_id, i.variant_id), i.quantity) for i in payload.items]
    movements = svc.receive_stock(request.user, items, note=payload.note)
    return 201, {"movements": [_movement(InventoryMovement.objects.select_related("product", "variant", "order", "created_by").get(pk=m.pk)) for m in movements]}


@router.post("/adjust", response=AdjustOut, summary="Correct the counted stock", operation_id="adjust_shop_stock")
def adjust(request, payload: AdjustIn):
    _gate(request)
    product, variant = _target(payload.product_id, payload.variant_id)
    m = svc.set_stock(request.user, product, variant, payload.counted, note=payload.note)
    if m is None:
        return {"movement": None}
    return {"movement": _movement(InventoryMovement.objects.select_related("product", "variant", "order", "created_by").get(pk=m.pk))}


__all__ = ["HttpError", "router"]
