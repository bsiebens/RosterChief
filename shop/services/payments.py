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

from ..models import Order, Payment, Voucher


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


def lock_voucher(voucher) -> Voucher:
    """Re-reads ``voucher`` under a row lock (the caller must be inside a
    transaction) and copies the fresh balance back onto the caller's own
    instance. Every writer of Voucher.consumed_amount goes through this, so
    two concurrent requests (or a double submit) holding the same stale
    voucher can't both spend its balance -- the second one waits for the
    first to commit, then sees what's actually left."""
    locked = Voucher.objects.select_for_update().get(pk=voucher.pk)
    voucher.consumed_amount = locked.consumed_amount
    voucher.amount = locked.amount
    voucher.is_active = locked.is_active
    voucher.expiry_date = locked.expiry_date
    return locked


@transaction.atomic
def record_payment(order, *, amount: Decimal, method: str, reference: str = "", voucher=None) -> Payment:
    """Creates a Payment and settles it against the order -- and, when paying
    by voucher, deducts from that voucher's own balance in the same
    transaction, so a Payment can never exist without the voucher backing it
    actually being reserved. The voucher's balance is checked under a row
    lock (lock_voucher), not off the possibly stale instance passed in.
    """
    if amount <= 0:
        raise PaymentError(_("The amount must be greater than zero."))

    if method == Payment.PaymentMethod.VOUCHER:
        if voucher is None:
            raise PaymentError(_("A voucher is required for this payment method."))
        if voucher.club_id != order.club_id:
            raise PaymentError(_("That voucher doesn't belong to this club."))
        locked = lock_voucher(voucher)
        if not locked.is_usable:
            raise PaymentError(_("That voucher is expired, inactive, or fully used."))
        if amount > locked.available_amount:
            raise PaymentError(_("Only €%(amount)s is available on that voucher.") % {"amount": locked.available_amount})
    elif voucher is not None:
        raise PaymentError(_("A voucher can only be used with the voucher payment method."))

    payment = Payment.objects.create(order=order, amount=amount, method=method, status=Payment.PaymentStatus.CONFIRMED, reference=reference, paid_at=timezone.now(), voucher=voucher)

    if voucher is not None:
        locked.consumed_amount += amount
        locked.save(update_fields=["consumed_amount"])
        voucher.consumed_amount = locked.consumed_amount

    sync_payment_status(order)
    return payment


@transaction.atomic
def delete_payment(payment) -> None:
    """Deletes ``payment``; a confirmed voucher payment gives its amount back
    to the voucher, onto the voucher's *current* balance (read under a row
    lock), not whatever was loaded alongside the payment. Leaves the order's
    payment_status to the caller (see PaymentDeleteView).

    The voucher is locked first (the same order record_payment takes it in),
    and the payment is re-read under that lock: of two concurrent deletes of
    the same payment, only the one that still finds it credits the voucher
    back -- the other has nothing left to delete or restore. What's credited
    is the row as it is now, not the possibly stale instance passed in."""
    voucher = payment.voucher
    if voucher is not None:
        lock_voucher(voucher)

    current = Payment.objects.filter(pk=payment.pk).first()
    if current is None:  # a concurrent request already deleted it (and credited it back)
        return
    was_confirmed = current.status == Payment.PaymentStatus.CONFIRMED
    amount = current.amount
    current.delete()

    if voucher is not None and was_confirmed:
        locked = lock_voucher(voucher)
        locked.consumed_amount = max(Decimal("0"), locked.consumed_amount - amount)
        locked.save(update_fields=["consumed_amount"])
        voucher.consumed_amount = locked.consumed_amount
