"""The delivery desk (``/shop/delivery``): orders ready for pickup that were not handed over yet, with payment on the spot.
Needs ``shop.manage_orders`` (the shop feature gate applies as everywhere in ``/shop``)."""

import uuid
from typing import Literal

from ninja import Query, Router

from api.auth import AUTHENTICATORS
from api.routers.shop_common import access, gate
from api.routers.shop_orders import get_order, num, order_detail
from api.schemas_shop_orders import DeliveryOut, HandOverIn, HandOverOut
from shop.services import delivery

router = Router(auth=AUTHENTICATORS)


def _gate(request):
    gate(request, access.require_manage_shop_orders)


def _item(line) -> dict:
    label = f"{line.product.name}{f' ({line.variant.name})' if line.variant_id else ''}"
    personalization = " ".join(part for part in (f"#{line.personalization_number}" if line.personalization_number else "", line.personalization_name) if part)
    return {"label": label, "quantity": line.quantity, "personalization": personalization}


def _order(o, due) -> dict:
    lines = list(o.lines.all())
    invoice = getattr(o, "invoice", None) if hasattr(o, "invoice") else None
    paid = o.total - due
    return {
        "id": o.pk,
        "number": o.number,
        "purchaser": {"id": o.purchaser_id, "name": str(o.purchaser)},
        "beneficiaries": sorted({str(ln.beneficiary) for ln in lines if ln.beneficiary_id}),
        "items": [_item(ln) for ln in lines],
        "total": o.total,
        "amount_paid": paid,
        "amount_due": due,
        "payment_status": o.payment_status,
        "pickup_instructions": o.pickup_instructions,
        "ready_since": o.modified,
        "invoice": num(invoice),
    }


@router.get("", response=DeliveryOut, summary="Orders waiting to be picked up", operation_id="get_shop_delivery")
def waiting(request, q: str = "", payment: Literal["all", "unpaid", "paid"] = Query("all")):
    """The orders marked ready for pickup and not handed over yet. ``q`` searches number, purchaser and beneficiary; ``payment`` keeps
    the unpaid or the paid ones. The counts follow ``q`` but not ``payment`` (they are the tabs)."""
    _gate(request)
    orders = list(delivery.waiting_queryset(q))
    split = delivery.split_by_payment(orders)
    shown = [o for o in orders if payment == "all" or (payment == "unpaid") == (split["due"][o.pk] > 0)]
    return {"orders": [_order(o, split["due"][o.pk]) for o in shown], "counts": {"all": split["all"], "unpaid": split["unpaid"], "paid": split["paid"]}}


@router.post("/{order_id}/hand-over", response=HandOverOut, summary="Hand an order over (optionally taking the payment)", operation_id="hand_over_shop_order")
def hand_over(request, order_id: uuid.UUID, payload: HandOverIn):
    """Records the payment first when a ``method`` is given and something is due, then marks the order delivered, in one transaction.
    Still something due and no ``allow_unpaid``: 409 ``unpaid``."""
    _gate(request)
    order = get_order(order_id)
    delivery.hand_over(order, method=payload.method, reference=payload.reference, allow_unpaid=payload.allow_unpaid, actor=request.user)
    return {"order": order_detail(order)}
