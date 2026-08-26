"""Recording a Payment against an Order -- the one place that touches both
Payment and (when paying by voucher) Voucher.consumed_amount together, so
the two can never drift out of sync with each other. Also the one place
Order.payment_status gets derived from actual Payment rows, so every entry
point (recording a payment, deleting one) agrees on what "paid"/"partially
paid"/"pending" means.
"""

from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from ..models import Order, Payment


class PaymentError(Exception):
    """Anything that stops a payment from being recorded -- shown to staff
    as-is (already a translated, user-facing message), not logged as a bug."""


def amount_paid(order) -> Decimal:
    """Sum of every confirmed Payment against this order."""
    return order.payments.filter(status=Payment.PaymentStatus.CONFIRMED).aggregate(total=Sum("amount"))["total"] or Decimal("0")


def amount_due(order) -> Decimal:
    """What's left to collect -- never negative (an overpaid order has
    nothing further "due", even though it happened)."""
    return max(Decimal("0"), order.total - amount_paid(order))


def _status_for_amount(order, paid: Decimal) -> str:
    if paid <= 0:
        return Order.PaymentStatus.PENDING
    if paid >= order.total:
        return Order.PaymentStatus.PAID
    return Order.PaymentStatus.PARTIALLY_PAID


def sync_payment_status(order):
    """Recompute Order.payment_status from its own confirmed Payments. Safe
    to call any time the set of confirmed Payments on an order changes."""
    new_status = _status_for_amount(order, amount_paid(order))
    if order.payment_status != new_status:
        order.payment_status = new_status
        order.save(update_fields=["payment_status"])


@transaction.atomic
def record_payment(order, *, amount: Decimal, method: str, reference: str = "", voucher=None) -> Payment:
    """Creates a Payment and settles it against the order -- and, when paying
    by voucher, deducts from that voucher's own balance in the same
    transaction, so a Payment can never exist without the voucher backing it
    actually being reserved.
    """
    if amount <= 0:
        raise PaymentError(_("The amount must be greater than zero."))

    if method == Payment.PaymentMethod.VOUCHER:
        if voucher is None:
            raise PaymentError(_("A voucher is required for this payment method."))
        if voucher.club_id != order.club_id:
            raise PaymentError(_("That voucher doesn't belong to this club."))
        if not voucher.is_usable:
            raise PaymentError(_("That voucher is expired, inactive, or fully used."))
        if amount > voucher.available_amount:
            raise PaymentError(_("Only €%(amount)s is available on that voucher.") % {"amount": voucher.available_amount})
    elif voucher is not None:
        raise PaymentError(_("A voucher can only be used with the voucher payment method."))

    payment = Payment.objects.create(order=order, amount=amount, method=method, status=Payment.PaymentStatus.CONFIRMED, reference=reference, paid_at=timezone.now(), voucher=voucher)

    if voucher is not None:
        voucher.consumed_amount += amount
        voucher.save(update_fields=["consumed_amount"])

    sync_payment_status(order)
    return payment
