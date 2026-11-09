"""Payments against an order: manual records, refunds, online payment links and their confirmation.

The one place that creates/changes ``Payment`` rows, moves voucher balances with them (same transaction) and derives
``Order.payment_status`` from the confirmed payments, so every entry point agrees on what paid / partially paid means."""

from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from formbuilder.services.management import Conflict
from members.services.errors import Invalid
from shop.models import Order, Payment
from shop.services import payment_providers, vouchers
from shop.settings_models import ShopSettings

Method = Payment.PaymentMethod
Status = Payment.PaymentStatus
MANUAL_METHODS = (Method.CASH, Method.BANK_TRANSFER, Method.CREDIT_CARD, Method.VOUCHER)


def amount_paid(order) -> Decimal:
    return order.payments.filter(status=Status.CONFIRMED).aggregate(total=Sum("amount"))["total"] or Decimal("0")


def amount_due(order) -> Decimal:
    return max(Decimal("0"), order.total - amount_paid(order))


def derive_status(order) -> str:
    paid = amount_paid(order)
    if paid <= 0:
        if order.payments.filter(status=Status.REFUNDED).exists() and not order.payments.filter(status=Status.PENDING).exists():
            return Order.PaymentStatus.REFUNDED
        return Order.PaymentStatus.PENDING
    return Order.PaymentStatus.PAID if paid >= order.total else Order.PaymentStatus.PARTIALLY_PAID


def sync_payment_status(order) -> None:
    new = derive_status(order)
    if order.payment_status != new:
        order.payment_status = new
        order.save(update_fields=["payment_status", "modified"])
    from shop.services import invoices

    invoices.invalidate_cache(order)


def _open_order(order) -> None:
    if order.fulfillment_status == Order.FulfillmentStatus.CANCELLED:
        raise Conflict("This order is cancelled.", code="order_cancelled")


@transaction.atomic
def record_payment(order, *, amount: Decimal, method: str, reference: str = "", note: str = "", voucher=None, paid_at=None, actor=None) -> Payment:
    """Record a confirmed manual payment (cash, bank transfer, card taken elsewhere, voucher)."""
    order = Order.objects.select_for_update().get(pk=order.pk)
    _open_order(order)
    if method not in MANUAL_METHODS:
        raise Invalid("Choose cash, bank transfer, credit card or voucher.", {"method": ["Not a manual payment method."]})
    if amount is None or amount <= 0:
        raise Invalid("The amount must be greater than zero.", {"amount": ["Must be greater than zero."]})
    if method == Method.VOUCHER:
        if voucher is None:
            raise Invalid("A voucher is required.", {"voucher_number": ["Required for a voucher payment."]})
        try:
            vouchers.consume(voucher, amount)
        except vouchers.VoucherError as error:
            raise Invalid(str(error), {"amount": [str(error)]}) from error
    elif voucher is not None:
        raise Invalid("A voucher can only be used with the voucher method.")
    payment = Payment.objects.create(
        order=order, amount=amount, method=method, status=Status.CONFIRMED, reference=reference[:255], note=note[:255], paid_at=paid_at or timezone.now(), voucher=voucher, recorded_by=actor if getattr(actor, "pk", None) else None
    )
    sync_payment_status(order)
    return payment


@transaction.atomic
def update_payment(payment: Payment, *, amount=None, method=None, reference=None, note=None, paid_at=None) -> Payment:
    """Edit a confirmed manual payment. A voucher payment keeps its voucher (amount changes move the balance); online
    and non-confirmed payments only take a note."""
    payment = Payment.objects.select_for_update(of=("self",)).select_related("order").get(pk=payment.pk)
    if payment.status != Status.CONFIRMED or payment.method == Method.ONLINE:
        if reference is not None or amount is not None or method is not None or paid_at is not None:
            raise Conflict("Only the note of this payment can be changed.", code="payment_locked")
    if note is not None:
        payment.note = note[:255]
    if reference is not None:
        payment.reference = reference[:255]
    if paid_at is not None:
        payment.paid_at = paid_at
    if method is not None and method != payment.method:
        if payment.method == Method.VOUCHER or method not in (Method.CASH, Method.BANK_TRANSFER, Method.CREDIT_CARD):
            raise Invalid("The method of this payment can't be changed.", {"method": ["Not allowed."]})
        payment.method = method
    if amount is not None and amount != payment.amount:
        if amount <= 0:
            raise Invalid("The amount must be greater than zero.", {"amount": ["Must be greater than zero."]})
        if payment.voucher_id:
            delta = amount - payment.amount
            try:
                vouchers.consume(payment.voucher, delta) if delta > 0 else vouchers.restore(payment.voucher, -delta)
            except vouchers.VoucherError as error:
                raise Invalid(str(error), {"amount": [str(error)]}) from error
        payment.amount = amount
    payment.save()
    sync_payment_status(payment.order)
    return payment


@transaction.atomic
def delete_payment(payment: Payment) -> None:
    payment = Payment.objects.select_for_update(of=("self",)).select_related("order", "voucher").filter(pk=payment.pk).first()
    if payment is None:
        return
    order, voucher, amount, confirmed = payment.order, payment.voucher, payment.amount, payment.status == Status.CONFIRMED
    payment.delete()
    if voucher is not None and confirmed:
        vouchers.restore(voucher, amount)
    sync_payment_status(order)


@transaction.atomic
def refund_payment(payment: Payment, *, actor=None) -> Payment:
    """Mark a confirmed payment refunded (returns a voucher payment's amount to the voucher; asks the provider for an
    online one when it supports refunds, else the money must be returned by hand)."""
    payment = Payment.objects.select_for_update(of=("self",)).select_related("order", "voucher").get(pk=payment.pk)
    if payment.status != Status.CONFIRMED:
        raise Conflict("Only a confirmed payment can be refunded.", code="not_confirmed")
    if payment.provider:
        provider = payment_providers.get_provider(payment.provider)
        if provider is not None and provider.supports_refunds:
            try:
                provider.refund(payment, ShopSettings.current().credentials)
            except payment_providers.ProviderError as error:
                raise Conflict(str(error), code="refund_failed") from error
    payment.status = Status.REFUNDED
    payment.save(update_fields=["status", "modified"])
    if payment.voucher_id:
        vouchers.restore(payment.voucher, payment.amount)
    sync_payment_status(payment.order)
    return payment


# --- online payments ---------------------------------------------------------------------------------------------------


def active_provider():
    """``(provider, credentials)`` of the club's configured provider, else ``(None, {})``."""
    settings = ShopSettings.current()
    provider = payment_providers.get_provider(settings.payment_provider) if settings.payment_provider else None
    return provider, settings.credentials if provider else {}


@transaction.atomic
def create_payment_link(order, *, return_url: str, amount: Decimal | None = None, actor=None) -> Payment:
    """A pending online ``Payment`` with the provider's link (``payment_url``) for ``amount`` (default: what is still due)."""
    order = Order.objects.select_for_update().get(pk=order.pk)
    _open_order(order)
    provider, credentials = active_provider()
    if provider is None:
        raise Conflict("No online payment provider is configured.", code="no_provider")
    due = amount_due(order)
    amount = due if amount is None else amount
    if amount <= 0 or amount > due:
        raise Invalid("Nothing to pay, or more than is due.", {"amount": [f"Must be between 0.01 and {due}."]})
    try:
        link = provider.create_payment_link(order, amount, return_url, credentials)
    except payment_providers.ProviderError as error:
        raise Conflict(str(error), code="provider_error") from error
    return Payment.objects.create(
        order=order, amount=amount, method=Method.ONLINE, status=Status.PENDING, provider=provider.key, provider_payment_id=link.external_id, payment_url=link.url, provider_data=link.data or None,
        recorded_by=actor if getattr(actor, "pk", None) else None,
    )  # fmt: skip


@transaction.atomic
def apply_provider_event(provider_key: str, event) -> str:
    """Apply a verified webhook event. Idempotent: repeating an event changes nothing. Returns what happened:
    ``confirmed``, ``failed``, ``refunded``, ``unchanged`` or ``unknown`` (no such payment)."""
    payment = Payment.objects.select_for_update(of=("self",)).select_related("order").filter(provider=provider_key, provider_payment_id=event.external_id).first()
    if payment is None:
        return "unknown"
    order = payment.order
    if event.status == "paid":
        if payment.status == Status.CONFIRMED:
            return "unchanged"
        if payment.status == Status.REFUNDED:
            return "unchanged"
        payment.status, payment.paid_at = Status.CONFIRMED, timezone.now()
        result = "confirmed"
    elif event.status == "failed":
        if payment.status != Status.PENDING:
            return "unchanged"
        payment.status, result = Status.FAILED, "failed"
    else:  # refunded
        if payment.status != Status.CONFIRMED:
            return "unchanged"
        payment.status, result = Status.REFUNDED, "refunded"
    payment.save(update_fields=["status", "paid_at", "modified"])
    sync_payment_status(order)
    if result == "confirmed":
        from shop.services import notifications

        transaction.on_commit(lambda: notifications.dispatch("payment_received", order.pk))
    return result


@transaction.atomic
def mark_paid(order, *, method: str, reference: str = "", actor=None) -> Payment | None:
    """Settle what is still due with one manual payment (None when nothing is due)."""
    order = Order.objects.select_for_update().get(pk=order.pk)
    due = amount_due(order)
    if due <= 0:
        return None
    return record_payment(order, amount=due, method=method, reference=reference, actor=actor)
