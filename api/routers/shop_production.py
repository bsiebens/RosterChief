"""Shop production (``/shop/production``): lines to order, batches (draft -> ordered -> received), CSV export. Needs ``shop.manage_orders``."""

import uuid

from django.db.models import Sum
from django.http import HttpResponse
from ninja import Query, Router
from ninja.errors import HttpError

from api.auth import AUTHENTICATORS
from api.routers.shop_common import access, gate, page
from api.routers.shop_orders import num, ref
from api.schemas_shop_orders import BatchDetailOut, BatchIn, BatchListOut, BatchPatch, ReceiveIn, ToOrderListOut
from shop.models import ProductionBatch
from shop.services import production as svc

router = Router(auth=AUTHENTICATORS)


def _gate(request):
    gate(request, access.require_manage_shop_orders)


def _variant(v):
    return {"id": v.pk, "name": v.name} if v else None


def _to_order(ln) -> dict:
    return {
        "id": ln.pk, "order": num(ln.order), "purchaser": ref(ln.order.purchaser), "product": ref(ln.product), "variant": _variant(ln.variant), "quantity": ln.quantity,
        "beneficiary": ref(ln.beneficiary), "team": ref(ln.team), "personalization_number": ln.personalization_number, "personalization_name": ln.personalization_name, "order_created": ln.order.created,
    }  # fmt: skip


def _item(b: ProductionBatch) -> dict:
    agg = b.lines.aggregate(q=Sum("quantity"), r=Sum("received_quantity"))
    return {
        "id": b.pk, "number": b.number, "reference": b.reference, "supplier": b.supplier, "note": b.note, "status": b.status, "line_count": b.lines.count(),
        "quantity": agg["q"] or 0, "received_quantity": agg["r"] or 0, "ordered_at": None if b.status == b.Status.DRAFT else b.ordered_at, "received_at": b.received_at, "created": b.created,
    }  # fmt: skip


def _detail(b: ProductionBatch) -> dict:
    b = ProductionBatch.objects.get(pk=b.pk)
    lines = b.lines.select_related("order__purchaser", "product", "variant", "beneficiary", "team").order_by("product__name", "order__number")
    return {
        **_item(b),
        "lines": [
            {
                "id": ln.pk, "order": num(ln.order), "purchaser": ref(ln.order.purchaser), "product": ref(ln.product), "variant": _variant(ln.variant), "quantity": ln.quantity, "received_quantity": ln.received_quantity,
                "production_status": ln.production_status, "beneficiary": ref(ln.beneficiary), "team": ref(ln.team), "personalization_number": ln.personalization_number, "personalization_name": ln.personalization_name,
            }
            for ln in lines
        ],
    }  # fmt: skip


def _batch(batch_id) -> ProductionBatch:
    b = ProductionBatch.objects.filter(pk=batch_id).first()
    if b is None:
        raise HttpError(404, "Not found.")
    return b


@router.get("/lines", response=ToOrderListOut, summary="Merchandise lines still to order from the supplier", operation_id="shop_production_lines")
def lines(request, product_id: uuid.UUID | None = None, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    _gate(request)
    return page(svc.lines_to_order(product_id), limit, offset, _to_order)


@router.post("/batches", response={201: BatchDetailOut}, summary="Group lines into a draft batch", operation_id="create_shop_batch")
def create_batch(request, payload: BatchIn):
    _gate(request)
    return 201, _detail(svc.create_batch(request.user, payload.line_ids, reference=payload.reference, supplier=payload.supplier, note=payload.note))


@router.get("/batches", response=BatchListOut, summary="List batches", operation_id="list_shop_batches")
def list_batches(request, status: str | None = None, q: str | None = None, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    _gate(request)
    qs = svc.search(svc.batches(), q)
    if status:
        qs = qs.filter(status=status)
    return page(qs.order_by("-created"), limit, offset, _item)


@router.get("/batches/{batch_id}", response=BatchDetailOut, summary="One batch with its lines", operation_id="get_shop_batch")
def get_batch(request, batch_id: uuid.UUID):
    _gate(request)
    return _detail(_batch(batch_id))


@router.patch("/batches/{batch_id}", response=BatchDetailOut, summary="Edit reference / supplier / note", operation_id="update_shop_batch")
def patch_batch(request, batch_id: uuid.UUID, payload: BatchPatch):
    _gate(request)
    b = _batch(batch_id)
    svc.update_batch(b, **payload.dict(exclude_unset=True))
    return _detail(b)


@router.delete("/batches/{batch_id}", response={204: None}, summary="Delete a draft batch", operation_id="delete_shop_batch")
def delete_batch(request, batch_id: uuid.UUID):
    _gate(request)
    svc.delete_batch(_batch(batch_id))
    return 204, None


@router.get("/batches/{batch_id}/export", summary="CSV for the supplier", operation_id="export_shop_batch")
def export(request, batch_id: uuid.UUID):
    _gate(request)
    b = _batch(batch_id)
    response = HttpResponse(svc.batch_csv(b), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{b.number}.csv"'
    response["X-Content-Type-Options"] = "nosniff"
    return response


@router.post("/batches/{batch_id}/mark-ordered", response=BatchDetailOut, summary="The supplier has the order", operation_id="mark_shop_batch_ordered")
def mark_ordered(request, batch_id: uuid.UUID):
    _gate(request)
    b = _batch(batch_id)
    svc.mark_ordered(b)
    return _detail(b)


@router.post("/batches/{batch_id}/receive", response=BatchDetailOut, summary="Receive the whole batch or some lines", operation_id="receive_shop_batch")
def receive(request, batch_id: uuid.UUID, payload: ReceiveIn):
    _gate(request)
    b = _batch(batch_id)
    items = None if payload.items is None else [(i.line_id, i.quantity) for i in payload.items]
    svc.receive(b, items)
    return _detail(b)
