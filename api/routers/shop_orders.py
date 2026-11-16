"""Shop orders (``/shop/orders``) and ``/shop/stats``. Orders need ``shop.manage_orders``; stats any shop permission. Rules live
in ``shop.services.orders``; errors map through the shared handlers (Invalid 422, Conflict 409, NotAllowed 403)."""

import json
import uuid
from decimal import Decimal

from django.db.models import Q
from ninja import Query, Router
from ninja.errors import HttpError

from api.auth import AUTHENTICATORS
from api.routers.shop_common import access, gate, member_ref, page
from api.schemas_shop_orders import (
    AvailableVoucherOut,
    BulkPaidIn,
    BulkReadyIn,
    CountOut,
    DiscountCodeIn,
    LineIn,
    LinePatch,
    OrderDetailOut,
    OrderIn,
    OrderListOut,
    OrderPatch,
    PreviewIn,
    PreviewOut,
    ReadyIn,
    StatsOut,
    VoucherApplyIn,
)  # fmt: skip
from members.models import Member
from members.services.errors import Invalid
from shop.models import Discount, Order, OrderLine, Payment, Product, ProductVariant
from shop.services import defaults, payments, pickup, pricing, stats, vouchers
from shop.services import orders as svc

router = Router(auth=AUTHENTICATORS)
stats_router = Router(auth=AUTHENTICATORS)


def _gate(request):
    gate(request, access.require_manage_shop_orders)


def ref(obj):
    return {"id": obj.pk, "name": str(obj)} if obj is not None else None


def num(obj):
    return {"id": obj.pk, "number": obj.number} if obj is not None else None


def order_item(o: Order) -> dict:
    paid = payments.amount_paid(o)
    invoice = getattr(o, "invoice", None) if hasattr(o, "invoice") else None
    return {
        "id": o.pk, "number": o.number, "purchaser": ref(o.purchaser), "payment_status": o.payment_status, "fulfillment_status": o.fulfillment_status,
        "production_status": o.production_status if o.has_production_lines else None, "member_status": svc.member_status(o), "total": o.total,
        "amount_paid": paid, "amount_due": max(Decimal("0"), o.total - paid), "line_count": o.lines.count(), "notes": o.notes,
        "pickup_instructions": o.pickup_instructions, "is_closed": o.is_closed, "ready_to_confirm": pickup.is_candidate(o), "invoice": num(invoice), "created": o.created,
    }  # fmt: skip


def line_out(line: OrderLine) -> dict:
    from shop.services import production

    return {
        "id": line.pk, "product": ref(line.product), "variant": {"id": line.variant.pk, "name": line.variant.name} if line.variant else None, "quantity": line.quantity,
        "unit_price": line.unit_price, "line_total": line.line_total, "beneficiary": ref(line.beneficiary), "team": ref(line.team),
        "personalization_number": line.personalization_number, "personalization_name": line.personalization_name,
        "production_status": line.production_status if production.is_production_line(line) else None, "received_quantity": line.received_quantity,
        "production_batch": num(line.production_batch),
    }  # fmt: skip


def payment_out(p: Payment) -> dict:
    return {
        "id": p.pk, "amount": p.amount, "method": p.method, "status": p.status, "reference": p.reference, "note": p.note, "paid_at": p.paid_at, "provider": p.provider,
        "payment_url": p.payment_url, "voucher": num(p.voucher), "recorded_by": (p.recorded_by.get_full_name() or p.recorded_by.email) if p.recorded_by else None, "created": p.created,
    }  # fmt: skip


def order_detail(o: Order) -> dict:
    o = Order.objects.select_related("purchaser", "created_by").get(pk=o.pk)
    lines = list(o.lines.select_related("product", "variant", "beneficiary", "team", "production_batch"))
    discounts = list(o.applied_discounts.all())
    subtotal = sum((ln.line_total for ln in lines), Decimal("0"))
    provider, _ = payments.active_provider()
    return {
        **order_item(o), "lines": [line_out(ln) for ln in lines], "payments": [payment_out(p) for p in o.payments.select_related("voucher", "recorded_by")],
        "applied_discounts": [{"id": d.pk, "discount_id": d.discount_id, "description": d.description, "discount_type": d.discount_type, "discount_amount": d.discount_amount, "amount": d.amount} for d in discounts],
        "subtotal": subtotal, "discount_total": sum((d.amount for d in discounts), Decimal("0")),
        "created_by": (o.created_by.get_full_name() or o.created_by.email) if o.created_by else None,
        "lines_editable": svc.lines_editable(o), "online_payments_available": provider is not None,
        "payment_instructions": defaults.payment_instructions(), "default_pickup_instructions": defaults.pickup_instructions(),
        "can_cancel": o.fulfillment_status != Order.FulfillmentStatus.CANCELLED and not svc.cancel_blocked_reason(o),
        "voucher_refund_count": svc.voucher_payments_to_refund(o)[0], "voucher_refund_total": svc.voucher_payments_to_refund(o)[1], "can_edit_discounts": svc.discounts_editable(o), "can_apply_voucher": svc.voucher_applicable(o),
        "can_override_cancel": o.fulfillment_status not in (Order.FulfillmentStatus.CANCELLED, Order.FulfillmentStatus.DELIVERED) and bool(svc.cancel_blockers(o)),
        "cancel_blocked_reason": "" if o.fulfillment_status == Order.FulfillmentStatus.CANCELLED else svc.cancel_blocked_reason(o),
    }  # fmt: skip


def get_order(order_id) -> Order:
    order = Order.objects.select_related("purchaser").filter(pk=order_id, kind=Order.Kind.SHOP).first()
    if order is None:
        raise HttpError(404, "Not found.")
    return order


def _member(pk, field="purchaser_id") -> Member:
    m = Member.objects.filter(pk=pk).first()
    if m is None:
        raise Invalid("Unknown member.", {field: ["Unknown member."]})
    return m


def _spec(payload: LineIn, index: int) -> svc.LineSpec:
    key = f"lines.{index}"
    product = Product.objects.filter(pk=payload.product_id).first()
    if product is None:
        raise Invalid("Unknown product.", {key: ["Unknown product."]})
    variant = None
    if payload.variant_id is not None:
        variant = ProductVariant.objects.filter(pk=payload.variant_id, product=product).first()
        if variant is None:
            raise Invalid("Unknown variant.", {key: ["Unknown variant."]})
    beneficiary = _member(payload.beneficiary_id, key) if payload.beneficiary_id else None
    team = None
    if payload.team_id:
        from teams.models import Team

        team = Team.objects.filter(pk=payload.team_id).first()
        if team is None:
            raise Invalid("Unknown team.", {key: ["Unknown team."]})
    return svc.LineSpec(product, variant, payload.quantity, beneficiary, team, payload.personalization_number, payload.personalization_name)


@router.get("", response=OrderListOut, summary="List orders", operation_id="list_shop_orders")
def list_orders(
    request, q: str | None = None, payment_status: str | None = None, fulfillment_status: str | None = None, production_status: str | None = None,
    purchaser_id: uuid.UUID | None = None, closed: bool | None = None, ready_to_confirm: bool | None = None, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
):  # fmt: skip
    _gate(request)
    qs = Order.objects.filter(kind=Order.Kind.SHOP).select_related("purchaser").order_by("-created")
    if q:
        qs = qs.filter(Q(number__icontains=q) | Q(purchaser__first_name__icontains=q) | Q(purchaser__last_name__icontains=q))
    for field, value in (("payment_status", payment_status), ("fulfillment_status", fulfillment_status), ("production_status", production_status)):
        if value:
            qs = qs.filter(**{field: value})
    if purchaser_id:
        qs = qs.filter(purchaser_id=purchaser_id)
    if ready_to_confirm is not None:
        waiting = pickup.waiting_orders().values("pk")
        qs = qs.filter(pk__in=waiting) if ready_to_confirm else qs.exclude(pk__in=waiting)
    if closed is not None:
        closed_q = Q(fulfillment_status=Order.FulfillmentStatus.DELIVERED, payment_status=Order.PaymentStatus.PAID)
        qs = qs.filter(closed_q) if closed else qs.exclude(closed_q)
    return page(qs, limit, offset, order_item)


@router.post("", response={201: OrderDetailOut}, summary="Create an order for a member", operation_id="create_shop_order")
def create_order(request, payload: OrderIn):
    _gate(request)
    purchaser = _member(payload.purchaser_id)
    specs = [_spec(ln, i) for i, ln in enumerate(payload.lines)]
    voucher_payments = [(svc.find_voucher(v.number), v.amount) for v in payload.vouchers]
    order = svc.create_order(request.user, purchaser=purchaser, specs=specs, discount_code=payload.discount_code, notes=payload.notes, voucher_payments=voucher_payments)
    return 201, order_detail(order)


@router.post("/preview", response=PreviewOut, summary="Price an order without saving it", operation_id="preview_shop_order")
def preview(request, payload: PreviewIn):
    _gate(request)
    purchaser = _member(payload.purchaser_id)
    specs = [_spec(ln, i) for i, ln in enumerate(payload.lines)]
    for i, s in enumerate(specs):
        svc._resolve_spec(s, i)
    lines = [{"unit_price": svc._price(s), "line_total": svc._price(s) * s.quantity} for s in specs]
    subtotal = sum((ln["line_total"] for ln in lines), Decimal("0"))
    discount = None
    code = payload.discount_code.strip()
    if code:
        d = Discount.objects.filter(code=code.upper()).first()
        reason = "This discount code isn't valid." if d is None else pricing.validate_discount(d, purchaser)
        if reason:
            raise Invalid(reason, {"discount_code": [reason]})
        discount = {"description": d.name, "amount": pricing.discount_amount_for(subtotal, d)}
    return {"lines": lines, "subtotal": subtotal, "discount": discount, "total": subtotal - (discount["amount"] if discount else Decimal("0"))}


@router.post("/bulk/mark-paid", response=CountOut, summary="Mark several orders paid", operation_id="bulk_mark_shop_orders_paid")
def bulk_paid(request, payload: BulkPaidIn):
    _gate(request)
    return {"count": svc.bulk_mark_paid(request.user, Order.objects.filter(pk__in=payload.order_ids, kind=Order.Kind.SHOP), payload.method)}


@router.post("/bulk/ready", response=CountOut, summary="Mark several orders ready for pickup", operation_id="bulk_mark_shop_orders_ready")
def bulk_ready(request, payload: BulkReadyIn):
    _gate(request)
    return {"count": svc.bulk_mark_ready(Order.objects.filter(pk__in=payload.order_ids, kind=Order.Kind.SHOP), payload.pickup_instructions)}


@router.get("/{order_id}", response=OrderDetailOut, summary="One order", operation_id="get_shop_order")
def get_one(request, order_id: uuid.UUID):
    _gate(request)
    return order_detail(get_order(order_id))


@router.patch("/{order_id}", response=OrderDetailOut, summary="Edit notes / pickup instructions", operation_id="update_shop_order")
def patch_order(request, order_id: uuid.UUID, payload: OrderPatch):
    _gate(request)
    order = get_order(order_id)
    svc.update_order(order, **payload.dict(exclude_unset=True))
    return order_detail(order)


def _line(order, line_id) -> OrderLine:
    line = order.lines.select_related("product", "variant").filter(pk=line_id).first()
    if line is None:
        raise HttpError(404, "Not found.")
    return line


@router.post("/{order_id}/lines", response={201: OrderDetailOut}, summary="Add a line", operation_id="add_shop_order_line")
def add_line(request, order_id: uuid.UUID, payload: LineIn):
    _gate(request)
    order = get_order(order_id)
    svc.add_line(order, _spec(payload, 0))
    return 201, order_detail(order)


@router.patch("/{order_id}/lines/{line_id}", response=OrderDetailOut, summary="Edit a line", operation_id="update_shop_order_line")
def patch_line(request, order_id: uuid.UUID, line_id: uuid.UUID, payload: LinePatch):
    _gate(request)
    order = get_order(order_id)
    line = _line(order, line_id)
    data = payload.dict(exclude_unset=True)
    kwargs = {k: data[k] for k in ("quantity", "personalization_number", "personalization_name") if k in data}
    if "variant_id" in data:
        variant = None
        if data["variant_id"] is not None:
            variant = ProductVariant.objects.filter(pk=data["variant_id"], product=line.product).first()
            if variant is None:
                raise Invalid("Unknown variant.", {"variant_id": ["Unknown variant."]})
        kwargs["variant"] = variant
    if "beneficiary_id" in data:
        kwargs["beneficiary"] = _member(data["beneficiary_id"], "beneficiary_id") if data["beneficiary_id"] else None
    if "team_id" in data:
        from teams.models import Team

        team = Team.objects.filter(pk=data["team_id"]).first() if data["team_id"] else None
        if data["team_id"] and team is None:
            raise Invalid("Unknown team.", {"team_id": ["Unknown team."]})
        kwargs["team"] = team
    svc.update_line(order, line, **kwargs)
    return order_detail(order)


@router.delete("/{order_id}/lines/{line_id}", response=OrderDetailOut, summary="Remove a line", operation_id="delete_shop_order_line")
def delete_line(request, order_id: uuid.UUID, line_id: uuid.UUID):
    _gate(request)
    order = get_order(order_id)
    svc.remove_line(order, _line(order, line_id), actor=request.user)
    return order_detail(order)


def _cancel_options(request) -> dict:
    """The optional JSON body of a cancel: ``{"override": bool, "refund_vouchers": bool}``. Read by hand so that a plain POST without a
    body keeps working; anything else means False."""
    try:
        data = json.loads(request.body or b"null")
    except ValueError:
        data = None
    data = data if isinstance(data, dict) else {}
    return {"override": data.get("override") is True, "refund_vouchers": data.get("refund_vouchers") is True}


@router.post("/{order_id}/discount", response=OrderDetailOut, summary="Apply a discount code to an unpaid order", operation_id="apply_shop_order_discount")
def apply_discount(request, order_id: uuid.UUID, payload: DiscountCodeIn):
    """422 ``discount_code`` for an unknown or unusable code; 409 ``not_editable`` once something is paid, ``discount_exists`` when the
    order already has one (remove it first)."""
    _gate(request)
    order = get_order(order_id)
    svc.apply_discount_code(order, payload.code, actor=request.user)
    return order_detail(order)


@router.delete("/{order_id}/discounts/{applied_id}", response=OrderDetailOut, summary="Remove an applied discount from an unpaid order", operation_id="remove_shop_order_discount")
def remove_discount(request, order_id: uuid.UUID, applied_id: uuid.UUID):
    _gate(request)
    order = get_order(order_id)
    svc.remove_applied_discount(order, applied_id)
    return order_detail(order)


@router.get("/{order_id}/vouchers", response=list[AvailableVoucherOut], summary="Vouchers this order could be paid with", operation_id="list_shop_order_vouchers")
def available_vouchers(request, order_id: uuid.UUID):
    """The usable vouchers: the purchaser's own, those of the people the order is for, of anyone in their families, and the general ones."""
    _gate(request)
    return vouchers.available_for_order(get_order(order_id))


@router.post("/{order_id}/voucher", response=OrderDetailOut, summary="Pay (part of) what is due with a voucher", operation_id="apply_shop_order_voucher")
def apply_voucher(request, order_id: uuid.UUID, payload: VoucherApplyIn):
    """Takes the voucher's available balance, capped at the amount due (or the given ``amount``), as a voucher payment."""
    _gate(request)
    order = get_order(order_id)
    svc.apply_voucher(order, svc.find_voucher(payload.number), amount=payload.amount, actor=request.user)
    return order_detail(order)


@router.post("/{order_id}/cancel", response=OrderDetailOut, summary="Cancel an order", operation_id="cancel_shop_order")
def cancel(request, order_id: uuid.UUID):
    """Refused (409 ``customized_in_production``) while customised items are in production. An optional JSON body
    ``{"override": true}`` cancels anyway; ``{"refund_vouchers": true}`` also refunds the confirmed voucher payments (their amounts go
    back on the vouchers). The body is read by hand so that a plain POST without a body keeps working."""
    _gate(request)
    order = get_order(order_id)
    svc.cancel(request.user, order, **_cancel_options(request))
    return order_detail(order)


@router.post("/{order_id}/ready", response=OrderDetailOut, summary="Mark ready for pickup", operation_id="ready_shop_order")
def ready(request, order_id: uuid.UUID, payload: ReadyIn):
    _gate(request)
    order = get_order(order_id)
    svc.mark_ready(order, pickup_instructions=payload.pickup_instructions)
    return order_detail(order)


@router.post("/{order_id}/deliver", response=OrderDetailOut, summary="Mark delivered", operation_id="deliver_shop_order")
def deliver(request, order_id: uuid.UUID):
    _gate(request)
    order = get_order(order_id)
    svc.mark_delivered(order)
    return order_detail(order)


@router.post("/{order_id}/reopen", response=OrderDetailOut, summary="One step back: picked up to ready for pickup, ready for pickup to not ready", operation_id="reopen_shop_order")
def reopen(request, order_id: uuid.UUID):
    _gate(request)
    order = get_order(order_id)
    svc.reopen(order)
    return order_detail(order)


@stats_router.get("/stats", response=StatsOut, summary="Shop numbers for the overview", operation_id="shop_stats")
def shop_stats(request):
    gate(request, access.require_manage_shop_any)
    return stats.shop_stats()


__all__ = ["member_ref"]
