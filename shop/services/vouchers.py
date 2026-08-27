"""Manual voucher consumption -- an offline/cash use of a voucher outside the
shop's own checkout flow (see shop.services.payments.record_payment for the
order-based one). record_manual_consumption keeps Voucher.consumed_amount in
lock-step the same way that does, as a second, independent writer -- never
re-derived from either (see Voucher's own docstring).
"""

from decimal import Decimal

from django.db import transaction
from django.utils.translation import gettext as _

from ..models import Payment, VoucherConsumption
from .payments import PaymentError


@transaction.atomic
def record_manual_consumption(voucher, *, amount: Decimal, note: str, recorded_by=None) -> VoucherConsumption:
    if amount <= 0:
        raise PaymentError(_("The amount must be greater than zero."))
    if amount > voucher.available_amount:
        raise PaymentError(_("Only €%(amount)s is available on that voucher.") % {"amount": voucher.available_amount})

    consumption = VoucherConsumption.objects.create(voucher=voucher, amount=amount, note=note, recorded_by=recorded_by)
    voucher.consumed_amount += amount
    voucher.save(update_fields=["consumed_amount"])
    return consumption


@transaction.atomic
def delete_manual_consumption(consumption: VoucherConsumption) -> None:
    voucher = consumption.voucher
    voucher.consumed_amount = max(voucher.consumed_amount - consumption.amount, Decimal("0"))
    voucher.save(update_fields=["consumed_amount"])
    consumption.delete()


def voucher_history(voucher):
    """Every way this voucher's balance has moved -- manual consumptions and
    real order Payments together, newest first, so staff have one place to
    see where it went instead of two disconnected lists. Each row:
    ``{"kind": "manual"|"order", "amount", "when", "detail", "recorded_by"}``
    -- ``detail`` is the note for a manual row, the Order for an order row;
    ``recorded_by`` is always None for an order row (Payment carries no such
    field -- nothing to show there, not a gap in this history)."""
    rows = [
        {"kind": "manual", "amount": consumption.amount, "when": consumption.recorded_at, "detail": consumption.note, "recorded_by": consumption.recorded_by, "consumption": consumption}
        for consumption in voucher.consumptions.select_related("recorded_by")
    ]
    rows += [
        {"kind": "order", "amount": payment.amount, "when": payment.paid_at or payment.created, "detail": payment.order, "recorded_by": None, "consumption": None}
        for payment in voucher.payments.filter(status=Payment.PaymentStatus.CONFIRMED).select_related("order")
    ]
    rows.sort(key=lambda row: row["when"], reverse=True)
    return rows
