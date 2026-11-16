"""Shop discount codes (``/shop/discounts``) and vouchers (``/shop/vouchers``). Needs ``shop.manage_discounts``."""

import uuid

from django.db.models import Q
from ninja import Query, Router
from ninja.errors import HttpError

from api.auth import AUTHENTICATORS
from api.routers.shop_common import access, gate, member_ref, page
from api.schemas_shop_discounts import ConsumptionIn, DiscountIn, DiscountListOut, DiscountOut, DiscountPatch, HistoryRow, VoucherDetail, VoucherIn, VoucherListOut, VoucherOut, VoucherPatch
from members.models import Member
from members.services.errors import Invalid
from shop.models import Discount, Voucher, VoucherConsumption
from shop.services import catalog as svc
from shop.services import pricing
from shop.services import vouchers as vsvc

router = Router(auth=AUTHENTICATORS)
vouchers_router = Router(auth=AUTHENTICATORS)


def _gate(request):
    gate(request, access.require_manage_shop_discounts)


def _discount(d) -> dict:
    return {
        "id": d.pk, "name": d.name, "slug": d.slug, "description": d.description, "code": d.code, "discount_type": d.discount_type,
        "discount_amount": d.discount_amount, "is_active": d.is_active, "valid_from": d.valid_from, "valid_until": d.valid_until,
        "max_uses": d.max_uses, "once_per_member": d.once_per_member, "status": pricing.discount_status(d),
        "used_count": pricing.discount_uses(d), "created": d.created,
    }  # fmt: skip


def _get_discount(discount_id) -> Discount:
    d = Discount.objects.filter(pk=discount_id).first()
    if d is None:
        raise HttpError(404, "Not found.")
    return d


@router.get("", response=DiscountListOut, summary="List discount codes", operation_id="list_shop_discounts")
def list_discounts(request, q: str | None = None, status: str | None = None, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    _gate(request)
    qs = Discount.objects.all()
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(code__icontains=q))
    if status:
        rows = [d for d in qs if pricing.discount_status(d) == status]
        return {"count": len(rows), "results": [_discount(d) for d in rows[offset : offset + limit]]}
    return page(qs, limit, offset, _discount)


@router.post("", response={201: DiscountOut}, summary="Create a discount code", operation_id="create_shop_discount")
def create_discount(request, payload: DiscountIn):
    _gate(request)
    return 201, _discount(svc.create_discount(request.user, **payload.dict()))


@router.get("/{discount_id}", response=DiscountOut, summary="One discount code", operation_id="get_shop_discount")
def get_discount(request, discount_id: uuid.UUID):
    _gate(request)
    return _discount(_get_discount(discount_id))


@router.patch("/{discount_id}", response=DiscountOut, summary="Edit a discount code", operation_id="update_shop_discount")
def update_discount(request, discount_id: uuid.UUID, payload: DiscountPatch):
    _gate(request)
    data = {k: v for k, v in payload.dict(exclude_unset=True).items() if v is not None or k in ("valid_from", "valid_until", "max_uses")}
    return _discount(svc.update_discount(request.user, _get_discount(discount_id), **data))


@router.delete("/{discount_id}", response={204: None}, summary="Delete a discount code", operation_id="delete_shop_discount")
def delete_discount(request, discount_id: uuid.UUID):
    _gate(request)
    svc.delete_discount(request.user, _get_discount(discount_id))
    return 204, None


# --- vouchers -----------------------------------------------------------------------------------------------------


def _voucher(v) -> dict:
    return {
        "id": v.pk, "number": v.number, "issued_to": member_ref(v.issued_to), "amount": v.amount, "consumed_amount": v.consumed_amount,
        "available_amount": v.available_amount, "expiry_date": v.expiry_date, "is_active": v.is_active, "notes": v.notes,
        "status": vsvc.voucher_status(v), "created": v.created,
    }  # fmt: skip


def _voucher_detail(voucher_id) -> dict:
    v = Voucher.objects.select_related("issued_to").get(pk=voucher_id)
    return {**_voucher(v), "history": [HistoryRow(**r).dict() for r in vsvc.voucher_history(v)]}


def _get_voucher(voucher_id) -> Voucher:
    v = Voucher.objects.select_related("issued_to").filter(pk=voucher_id).first()
    if v is None:
        raise HttpError(404, "Not found.")
    return v


def _issued_to(member_id):
    if member_id is None:
        return None
    m = Member.objects.filter(pk=member_id).first()
    if m is None:
        raise Invalid("Unknown member.", {"issued_to_id": ["Unknown member."]})
    return m


@vouchers_router.get("", response=VoucherListOut, summary="List vouchers", operation_id="list_shop_vouchers")
def list_vouchers(
    request, q: str | None = None, status: str | None = None, issued_to_id: uuid.UUID | None = None,
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
):  # fmt: skip
    _gate(request)
    qs = Voucher.objects.select_related("issued_to")
    if q:
        qs = qs.filter(Q(number__icontains=q) | Q(issued_to__first_name__icontains=q) | Q(issued_to__last_name__icontains=q))
    if issued_to_id:
        qs = qs.filter(issued_to_id=issued_to_id)
    if status:
        rows = [v for v in qs if vsvc.voucher_status(v) == status]
        return {"count": len(rows), "results": [_voucher(v) for v in rows[offset : offset + limit]]}
    return page(qs, limit, offset, _voucher)


@vouchers_router.post("", response={201: VoucherListOut}, summary="Create one or more vouchers", operation_id="create_shop_vouchers")
def create_vouchers(request, payload: VoucherIn):
    _gate(request)
    data = payload.dict()
    issued_to = _issued_to(data.pop("issued_to_id"))
    created = vsvc.create_vouchers(request.user, issued_to=issued_to, **data)
    return 201, {"count": len(created), "results": [_voucher(v) for v in created]}


@vouchers_router.get("/lookup", response=VoucherOut, summary="Find a voucher by number", operation_id="lookup_shop_voucher")
def lookup_voucher(request, number: str):
    _gate(request)
    v = Voucher.objects.select_related("issued_to").filter(number__iexact=number.strip()).first()
    if v is None:
        raise HttpError(404, "Not found.")
    return _voucher(v)


@vouchers_router.get("/{voucher_id}", response=VoucherDetail, summary="One voucher with its history", operation_id="get_shop_voucher")
def get_voucher(request, voucher_id: uuid.UUID):
    _gate(request)
    return _voucher_detail(_get_voucher(voucher_id).pk)


@vouchers_router.patch("/{voucher_id}", response=VoucherDetail, summary="Edit a voucher", operation_id="update_shop_voucher")
def update_voucher(request, voucher_id: uuid.UUID, payload: VoucherPatch):
    _gate(request)
    data = payload.dict(exclude_unset=True)
    if "issued_to_id" in data:
        data["issued_to"] = _issued_to(data.pop("issued_to_id"))
    data = {k: v for k, v in data.items() if v is not None or k == "issued_to"}
    vsvc.update_voucher(request.user, _get_voucher(voucher_id), **data)
    return _voucher_detail(voucher_id)


@vouchers_router.post("/{voucher_id}/deactivate", response=VoucherDetail, summary="Deactivate a voucher", operation_id="deactivate_shop_voucher")
def deactivate_voucher(request, voucher_id: uuid.UUID):
    _gate(request)
    vsvc.update_voucher(request.user, _get_voucher(voucher_id), is_active=False)
    return _voucher_detail(voucher_id)


@vouchers_router.post("/{voucher_id}/activate", response=VoucherDetail, summary="Reactivate a voucher", operation_id="activate_shop_voucher")
def activate_voucher(request, voucher_id: uuid.UUID):
    _gate(request)
    vsvc.update_voucher(request.user, _get_voucher(voucher_id), is_active=True)
    return _voucher_detail(voucher_id)


@vouchers_router.delete("/{voucher_id}", response={204: None}, summary="Delete an unused voucher", operation_id="delete_shop_voucher")
def delete_voucher(request, voucher_id: uuid.UUID):
    _gate(request)
    vsvc.delete_voucher(request.user, _get_voucher(voucher_id))
    return 204, None


@vouchers_router.post("/{voucher_id}/consumptions", response={201: HistoryRow}, summary="Record a manual use", operation_id="create_shop_voucher_consumption")
def create_consumption(request, voucher_id: uuid.UUID, payload: ConsumptionIn):
    _gate(request)
    c = vsvc.record_consumption(request.user, _get_voucher(voucher_id), amount=payload.amount, note=payload.note)
    return 201, {"kind": "manual", "amount": c.amount, "when": c.recorded_at, "detail": c.note, "order_id": None, "consumption_id": c.pk, "recorded_by": str(c.recorded_by) if c.recorded_by else None}


@vouchers_router.delete("/{voucher_id}/consumptions/{consumption_id}", response={204: None}, summary="Remove a manual use", operation_id="delete_shop_voucher_consumption")
def delete_consumption(request, voucher_id: uuid.UUID, consumption_id: uuid.UUID):
    _gate(request)
    c = VoucherConsumption.objects.filter(pk=consumption_id, voucher_id=voucher_id).first()
    if c is None:
        raise HttpError(404, "Not found.")
    vsvc.delete_consumption(request.user, c)
    return 204, None
