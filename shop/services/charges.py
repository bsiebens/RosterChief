"""Charge invoices: an invoice staff write for an amount that is not a shop item (a fine, a late fee, lost equipment).

A charge is an ``Order`` of kind ``charge`` whose lines carry a free-text description instead of a product (they point at one hidden
system product), so the invoice number, the PDF, the payments and the member app's invoice list are the shop's own. The lifecycle:

* **draft**: an order without an invoice. Lines, member, due date and note can change, or it can be deleted. Nothing is sent.
* **sent**: ``send`` issues the invoice (number, due date, stored billing details) and tells the member, and for a child the guardians,
  by inbox, push and an email with the PDF. From here the lines are fixed.
* **paid / partially paid**: payments are recorded as for any order.
* **overdue**: past its due date and not paid. Reminders go out on the schedule in the shop settings (``send_due_reminders``, daily).
* **cancelled**: only while nothing is paid.

Charges carry no VAT. Everything here runs in one transaction per call."""

import datetime
import logging
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.utils import timezone

from formbuilder.services.management import Conflict
from members.models import Member
from members.services.errors import Invalid
from shop.models import Invoice, Order, OrderLine, Payment, Product, ProductionStatus
from shop.services import invoices, notifications, orders, payments
from shop.settings_models import ShopSettings

logger = logging.getLogger(__name__)

SYSTEM_SLUG = "system-charge"
MAX_LINES = 50
MAX_QUANTITY = 999
MAX_AMOUNT = Decimal("99999999.99")
CENT = Decimal("0.01")

Kind = Order.Kind
Fulfilment = Order.FulfillmentStatus


@dataclass
class ChargeLine:
    description: str
    quantity: int
    unit_price: Decimal


def system_product() -> Product:
    """The one hidden product every charge line points at (never listed in the shop, never in stock or production)."""
    product, _ = Product.objects.get_or_create(
        slug=SYSTEM_SLUG, defaults={"name": "Charge", "product_type": Product.ProductType.CHARGE, "price": Decimal("0"), "is_active": True, "is_public": False}
    )
    return product


def is_charge(order) -> bool:
    return order.kind == Kind.CHARGE


def is_draft(order) -> bool:
    """A charge that has not been sent: no invoice yet, and not cancelled."""
    return is_charge(order) and order.fulfillment_status != Fulfilment.CANCELLED and not Invoice.objects.filter(order=order).exists()


def status(order, *, today: datetime.date | None = None) -> str:
    """draft / sent / partially_paid / paid / overdue / cancelled."""
    return status_for(order, Invoice.objects.filter(order=order).first(), today or timezone.localdate())


def status_for(order, invoice, today: datetime.date) -> str:
    """``status`` for a caller that already has the invoice (a list does not look it up per row)."""
    if order.fulfillment_status == Fulfilment.CANCELLED:
        return "cancelled"
    if invoice is None:
        return "draft"
    if order.payment_status == Order.PaymentStatus.PAID:
        return "paid"
    if invoice.due_date is not None and invoice.due_date < today:
        return "overdue"
    return "partially_paid" if order.payment_status == Order.PaymentStatus.PARTIALLY_PAID else "sent"


def _lock(order) -> Order:
    locked = Order.objects.select_for_update().get(pk=order.pk)
    if not is_charge(locked):
        raise Conflict("This is not a charge invoice.", code="not_a_charge")
    return locked


def _clean_lines(lines: list[ChargeLine]) -> list[ChargeLine]:
    if not lines:
        raise Invalid("Add at least one line.", {"lines": ["Add at least one line."]})
    if len(lines) > MAX_LINES:
        raise Invalid(f"At most {MAX_LINES} lines.", {"lines": [f"At most {MAX_LINES} lines."]})
    clean = []
    for i, line in enumerate(lines):
        key = f"lines.{i}"
        description = (line.description or "").strip()
        if not description:
            raise Invalid("Describe every line.", {key: ["A description is required."]})
        if len(description) > 255:
            raise Invalid("A description is too long.", {key: ["At most 255 characters."]})
        if line.quantity is None or line.quantity < 1 or line.quantity > MAX_QUANTITY:
            raise Invalid("Invalid quantity.", {key: [f"The quantity must be between 1 and {MAX_QUANTITY}."]})
        try:
            price = Decimal(line.unit_price).quantize(CENT, rounding=ROUND_HALF_UP)
        except Exception as error:
            raise Invalid("Invalid amount.", {key: ["Enter an amount."]}) from error
        if price <= 0 or price > MAX_AMOUNT or price * line.quantity > MAX_AMOUNT:
            raise Invalid("Invalid amount.", {key: ["The amount must be more than zero."]})
        clean.append(ChargeLine(description, int(line.quantity), price))
    return clean


def _check_due(due_date: datetime.date | None) -> None:
    if due_date is not None and due_date < timezone.localdate():
        raise Invalid("The due date is in the past.", {"due_date": ["Choose today or a later date."]})


def _write_lines(order, lines: list[ChargeLine]) -> None:
    order.lines.all().delete()
    product = system_product()
    for line in lines:
        OrderLine.objects.create(
            order=order, product=product, description=line.description, quantity=line.quantity, unit_price=line.unit_price,
            line_total=line.unit_price * line.quantity, vat_rate=Decimal("0"),
            production_status=ProductionStatus.RECEIVED, received_quantity=line.quantity,
        )  # fmt: skip


@transaction.atomic
def create_draft(actor, *, member: Member, lines: list[ChargeLine], due_date: datetime.date | None = None, notes: str = "", invoice_text: str = "", payment_instructions: str = "") -> Order:
    clean = _clean_lines(lines)
    _check_due(due_date)
    user = actor if getattr(actor, "pk", None) else None
    order = Order.objects.create(
        kind=Kind.CHARGE, purchaser=member, total=Decimal("0"), notes=(notes or "").strip(), invoice_text=(invoice_text or "").strip()[:2000],
        payment_instructions=(payment_instructions or "").strip()[:2000], due_date=due_date, created_by=user,
    )  # fmt: skip
    _write_lines(order, clean)
    orders.refresh(order)
    return order


@transaction.atomic
def update_draft(
    actor, order, *, member: Member | None = None, lines: list[ChargeLine] | None = None, due_date="keep", notes: str | None = None, invoice_text: str | None = None,
    payment_instructions: str | None = None,
) -> Order:
    order = _lock(order)
    if not is_draft(order):
        raise Conflict("Only a draft can be changed. A sent invoice is fixed: cancel it and write a new one.", code="not_a_draft")
    if member is not None:
        order.purchaser = member
    if due_date != "keep":
        _check_due(due_date)
        order.due_date = due_date
    if notes is not None:
        order.notes = notes.strip()
    if invoice_text is not None:
        order.invoice_text = invoice_text.strip()[:2000]
    if payment_instructions is not None:
        order.payment_instructions = payment_instructions.strip()[:2000]
    order.save()
    if lines is not None:
        _write_lines(order, _clean_lines(lines))
        orders.refresh(order)
    return order


@transaction.atomic
def delete_draft(order) -> None:
    order = _lock(order)
    if not is_draft(order):
        raise Conflict("Only a draft can be deleted. Cancel a sent invoice instead.", code="not_a_draft")
    order.delete()


@transaction.atomic
def send(actor, order, *, due_date: datetime.date | None = None) -> Invoice:
    """Issue the invoice of a draft and tell the member (inbox, push and an email with the PDF)."""
    order = _lock(order)
    if not is_draft(order):
        raise Conflict("This invoice was already sent or is cancelled.", code="not_a_draft")
    if not order.lines.exists() or order.total <= 0:
        raise Invalid("Add at least one line with an amount.", {"lines": ["Add at least one line with an amount."]})
    due = due_date or order.due_date or timezone.localdate() + datetime.timedelta(days=ShopSettings.current().invoice_due_days)
    _check_due(due)
    invoice = invoices.create_invoice(order, due_date=due)
    transaction.on_commit(lambda: notifications.dispatch("charge", order.pk))
    return invoice


@transaction.atomic
def cancel(actor, order) -> Order:
    """Cancel a sent invoice that has no payments (refund them first). A draft is deleted instead."""
    order = _lock(order)
    if order.fulfillment_status == Fulfilment.CANCELLED:
        return order
    if is_draft(order):
        raise Conflict("A draft is deleted, not cancelled.", code="is_a_draft")
    if order.payments.filter(status=Payment.PaymentStatus.CONFIRMED).exists():
        raise Conflict("This invoice has payments. Refund them first.", code="has_payments")
    order.fulfillment_status = Fulfilment.CANCELLED
    order.save(update_fields=["fulfillment_status", "modified"])
    order.payments.filter(status=Payment.PaymentStatus.PENDING).update(status=Payment.PaymentStatus.FAILED)
    invoices.invalidate_cache(order)
    transaction.on_commit(lambda: notifications.dispatch("charge_cancelled", order.pk))
    return order


def record_payment(order, **kwargs) -> Payment:
    """Record a payment on a sent invoice (a draft has nothing to pay yet)."""
    order = Order.objects.get(pk=order.pk)
    if is_charge(order) and is_draft(order):
        raise Conflict("Send the invoice first.", code="is_a_draft")
    return payments.record_payment(order, **kwargs)


# ------------------------------------------------------------------------------------------------- reminders


def days_overdue(invoice, today: datetime.date) -> int:
    return (today - invoice.due_date).days if invoice.due_date else 0


def reminder_due(invoice, settings: ShopSettings, today: datetime.date) -> bool:
    """Is a reminder owed now? The first one once the invoice is ``charge_reminder_after_days`` past due, then one every
    ``charge_reminder_repeat_days``, at most ``charge_reminder_max`` in all."""
    if invoice.reminders_sent >= settings.charge_reminder_max:
        return False
    if days_overdue(invoice, today) < max(settings.charge_reminder_after_days, 1):
        return False
    if invoice.last_reminder_at is None:
        return True
    return (today - timezone.localtime(invoice.last_reminder_at).date()).days >= max(settings.charge_reminder_repeat_days, 1)


def send_reminder(invoice) -> Invoice:
    """Remind the member about an overdue (or any open) charge now: counts as a reminder sent. Staff use it from the invoice page."""
    with transaction.atomic():
        invoice = Invoice.objects.select_for_update().select_related("order").get(pk=invoice.pk)
        order = invoice.order
        if not is_charge(order):
            raise Conflict("This is not a charge invoice.", code="not_a_charge")
        if order.fulfillment_status == Fulfilment.CANCELLED or order.payment_status == Order.PaymentStatus.PAID:
            raise Conflict("Nothing is owed on this invoice.", code="nothing_due")
        invoice.reminders_sent += 1
        invoice.last_reminder_at = timezone.now()
        invoice.save(update_fields=["reminders_sent", "last_reminder_at"])
        number = invoice.reminders_sent
        transaction.on_commit(lambda: notifications.dispatch("charge_reminder", order.pk, {"reminder": number}))
    return invoice


def send_due_reminders(today: datetime.date | None = None) -> dict:
    """The daily sweep for the current club: remind every charge invoice that is owed a reminder. Returns ``{"reminders": n}``."""
    today = today or timezone.localdate()
    settings = ShopSettings.current()
    if not settings.charge_reminders_enabled:
        return {"reminders": 0}
    candidates = (
        Invoice.objects.filter(order__kind=Kind.CHARGE, due_date__lt=today, order__payment_status__in=[Order.PaymentStatus.PENDING, Order.PaymentStatus.PARTIALLY_PAID])
        .exclude(order__fulfillment_status=Fulfilment.CANCELLED)
        .select_related("order")
    )
    sent = 0
    for invoice in candidates:
        if not reminder_due(invoice, settings, today):
            continue
        try:
            send_reminder(invoice)
            sent += 1
        except Exception:
            logger.exception("shop.charge_reminder_failed invoice=%s", invoice.pk)
    return {"reminders": sent}
