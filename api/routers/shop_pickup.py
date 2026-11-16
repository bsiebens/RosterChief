"""The ready-for-pickup workflow (``/shop/pickup``): the orders whose items are all fulfilled, confirmed in bulk with a notification
to the members. Needs ``shop.manage_orders`` (the shop feature gate applies as everywhere in ``/shop``)."""

from decimal import Decimal

from ninja import Router

from api.auth import AUTHENTICATORS
from api.routers.shop_common import access, gate
from api.schemas_shop_orders import PickupConfirmIn, PickupConfirmOut, PickupOut
from shop.services import defaults, payments, pickup

router = Router(auth=AUTHENTICATORS)


def _gate(request):
    gate(request, access.require_manage_shop_orders)


def _order(o) -> dict:
    lines = list(o.lines.all())
    beneficiaries = sorted({str(ln.beneficiary) for ln in lines if ln.beneficiary_id})
    paid = payments.amount_paid(o)
    return {
        "id": o.pk,
        "number": o.number,
        "purchaser": {"id": o.purchaser_id, "name": str(o.purchaser)},
        "beneficiaries": beneficiaries,
        "line_count": len(lines),
        "quantity": sum(ln.quantity for ln in lines),
        "total": o.total,
        "amount_due": max(Decimal("0"), o.total - paid),
        "payment_status": o.payment_status,
        "created": o.created,
    }


@router.get("", response=PickupOut, summary="Orders waiting to be confirmed ready for pickup", operation_id="get_shop_pickup")
def waiting(request):
    """Fulfilled orders (all items in stock or received) that are not marked ready yet, plus the default texts: the shop's pickup
    instructions and the club-level payment instructions."""
    _gate(request)
    return {"orders": [_order(o) for o in pickup.waiting_orders()], "pickup_instructions": defaults.pickup_instructions(), "payment_instructions": defaults.payment_instructions()}


@router.post("/confirm", response=PickupConfirmOut, summary="Mark orders ready for pickup and notify the members", operation_id="confirm_shop_pickup")
def confirm(request, payload: PickupConfirmIn):
    """Marks the selected orders ready, stores the pickup instructions on them and notifies each purchaser. The payment instructions
    are included for orders that are not fully paid, for these notifications only. ``save_as_default`` also saves the pickup text as
    the shop's default. Selected orders that are no longer waiting are skipped (and counted)."""
    _gate(request)
    return pickup.confirm(payload.order_ids, pickup_instructions=payload.pickup_instructions, payment_instructions=payload.payment_instructions, save_as_default=payload.save_as_default)
