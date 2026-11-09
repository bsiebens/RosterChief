"""Staff-side orders: create for a member, edit lines, cancel, fulfilment. Stock, discounts, vouchers and totals go through the
foundation services (``inventory``, ``pricing``, ``vouchers``); payments through ``payments``; production through
``production``. Every mutation runs in one transaction."""

from dataclasses import dataclass
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum

from formbuilder.services.management import Conflict
from members.models import Member
from members.services.errors import Invalid
from shop.models import AppliedDiscount, Discount, InventoryMovement, Order, OrderLine, Payment, Product, ProductionBatch, ProductionStatus, ProductVariant, Voucher
from shop.services import defaults, inventory, notifications, payments, pricing, production, vouchers
from shop.settings_models import ShopSettings
from teams.models import Team

Fulfilment = Order.FulfillmentStatus
MAX_QUANTITY = 999


@dataclass
class LineSpec:
    """One wanted line; ``product``/``variant`` are model instances (used for the stock check as well)."""

    product: Product
    variant: ProductVariant | None
    quantity: int
    beneficiary: Member | None = None
    team: Team | None = None
    personalization_number: str = ""
    personalization_name: str = ""


def member_status(order) -> str:
    """The single member-facing status: not_ready / in_production / ready_for_pickup / completed / cancelled."""
    if order.fulfillment_status == Fulfilment.CANCELLED:
        return "cancelled"
    if order.fulfillment_status == Fulfilment.DELIVERED:
        return "completed"
    if order.fulfillment_status == Fulfilment.READY_FOR_PICKUP:
        return "ready_for_pickup"
    if order.has_production_lines and order.production_status == ProductionStatus.ORDERED:
        return "in_production"
    return "not_ready"


def lines_editable(order) -> bool:
    """Lines can change while the order is unpaid, not cancelled/delivered and nothing is at the supplier yet."""
    return order.payment_status == Order.PaymentStatus.PENDING and order.fulfillment_status == Fulfilment.NOT_READY and order.production_status == ProductionStatus.PENDING and not order.payments.filter(status=Payment.PaymentStatus.CONFIRMED).exists()


def _resolve_spec(spec: LineSpec, index: int) -> None:
    key = f"lines.{index}"
    p, v = spec.product, spec.variant
    if not p.is_active:
        raise Invalid(f"{p.name} is not available.", {key: ["This product is not active."]})
    if spec.quantity < 1 or spec.quantity > MAX_QUANTITY:
        raise Invalid("Invalid quantity.", {key: [f"Quantity must be between 1 and {MAX_QUANTITY}."]})
    if v is None and p.variants.filter(is_active=True).exists():
        raise Invalid(f"Choose a variant for {p.name}.", {key: ["A variant is required."]})
    if v is not None and (v.product_id != p.pk or not v.is_active):
        raise Invalid(f"Invalid variant for {p.name}.", {key: ["Not an active variant of this product."]})
    if _price(spec) < 0:
        raise Invalid(f"{p.name} can't be ordered.", {key: ["A negative price is only a credit on a registration."]})
    if (spec.personalization_number or spec.personalization_name) and not p.personalization_enabled:
        raise Invalid(f"{p.name} can't be personalized.", {key: ["Personalization is not enabled for this product."]})


def _price(spec: LineSpec) -> Decimal:
    return spec.variant.effective_price if spec.variant else spec.product.price


def _make_line(order, spec: LineSpec) -> OrderLine:
    unit = _price(spec)
    line = OrderLine.objects.create(
        order=order, product=spec.product, variant=spec.variant, quantity=spec.quantity, unit_price=unit, line_total=unit * spec.quantity,
        beneficiary=spec.beneficiary, team=spec.team, personalization_number=spec.personalization_number[:20], personalization_name=spec.personalization_name[:100],
    )  # fmt: skip
    if not production.is_production_line(line) and not spec.product.inventory_managed:
        # membership / event fee: nothing to make
        OrderLine.objects.filter(pk=line.pk).update(production_status=ProductionStatus.RECEIVED, received_quantity=line.quantity)
        line.production_status, line.received_quantity = ProductionStatus.RECEIVED, line.quantity
    return line


def find_discount(code: str, member) -> Discount | None:
    """None for an empty code; 422 ``discount_code`` for an unknown or unusable one. Row-locks the discount."""
    code = (code or "").strip()
    if not code:
        return None
    discount = Discount.objects.select_for_update().filter(code=code.upper()).first()
    reason = "This discount code isn't valid." if discount is None else pricing.validate_discount(discount, member)
    if reason:
        raise Invalid(reason, {"discount_code": [reason]})
    return discount


def refresh(order) -> None:
    """Recompute the total, payment status and production rollup after a change to lines/discounts."""
    order.total = pricing.order_total(order)
    order.save(update_fields=["total", "modified"])
    payments.sync_payment_status(order)
    production.sync_order(order)


def is_shop_open() -> bool:
    """Whether members may place orders (the shop's open/closed switch in the shop settings)."""
    return ShopSettings.current().shop_open


def ensure_shop_open() -> None:
    """For member-initiated ordering (the future storefront): 409 ``shop_closed`` while the shop is closed. Staff creating an order
    for a member do not need this: the shop being closed to members does not stop the club from registering an order."""
    settings = ShopSettings.current()
    if not settings.shop_open:
        raise Conflict(settings.closed_message or "The shop is closed.", code="shop_closed")


@transaction.atomic
def create_order(actor, *, purchaser: Member, specs: list[LineSpec], discount_code: str = "", notes: str = "", voucher_payments=()) -> Order:
    """Create an order for ``purchaser``: stock checked under lock and booked, prices snapshotted, discount applied,
    optional voucher payments (``[(Voucher, amount)]``) recorded in the same transaction."""
    if not specs:
        raise Invalid("Add at least one line.", {"lines": ["Add at least one line."]})
    for i, spec in enumerate(specs):
        _resolve_spec(spec, i)
    inventory.lock_products({s.product.pk for s in specs if s.product.inventory_managed})
    try:
        inventory.check_available(specs)
    except inventory.InventoryError as error:
        raise Conflict(str(error), code="insufficient_stock") from error
    discount = find_discount(discount_code, purchaser)
    user = actor if getattr(actor, "pk", None) else None
    order = Order.objects.create(purchaser=purchaser, total=Decimal("0"), notes=notes, created_by=user)
    lines = [_make_line(order, s) for s in specs]
    inventory.take_for_order_lines(lines)
    if discount is not None:
        AppliedDiscount.objects.create(
            order=order, discount=discount, discount_type=discount.discount_type, discount_amount=discount.discount_amount, description=discount.name, applied_by=None
        )  # fmt: skip
    refresh(order)
    for voucher, amount in voucher_payments:
        payments.record_payment(order, amount=amount, method=Payment.PaymentMethod.VOUCHER, voucher=voucher, actor=actor)
    transaction.on_commit(lambda: notifications.dispatch("placed", order.pk))
    return order


def discounts_editable(order) -> bool:
    """A discount code can be added or removed while nothing is paid yet (the total would otherwise move under a payment)."""
    return order.fulfillment_status not in (Fulfilment.CANCELLED, Fulfilment.DELIVERED) and order.payment_status == Order.PaymentStatus.PENDING and not order.payments.filter(status=Payment.PaymentStatus.CONFIRMED).exists()


def voucher_applicable(order) -> bool:
    """A voucher is a payment: possible while the order is open and something is still due."""
    return order.fulfillment_status not in (Fulfilment.CANCELLED, Fulfilment.DELIVERED) and payments.amount_due(order) > 0


def _invalidate_invoice(order) -> None:
    from shop.services import invoices

    invoices.invalidate_cache(order)


@transaction.atomic
def apply_discount_code(order, code: str, *, actor=None) -> AppliedDiscount:
    """Add a discount code to an unpaid order (one discount per order, like at creation); the total is recomputed."""
    order = _lock(order)
    if not discounts_editable(order):
        raise Conflict("A discount code can only be added to an order that is not paid yet.", code="not_editable")
    if not (code or "").strip():
        raise Invalid("Enter a discount code.", {"code": ["Enter a discount code."]})
    discount = find_discount(code, order.purchaser)
    if order.applied_discounts.exists():
        raise Conflict("This order already has a discount. Remove it first to use another code.", code="discount_exists")
    who = Member.objects.filter(user=actor).first() if getattr(actor, "pk", None) else None
    applied = AppliedDiscount.objects.create(order=order, discount=discount, discount_type=discount.discount_type, discount_amount=discount.discount_amount, description=discount.name, applied_by=who)
    refresh(order)
    _invalidate_invoice(order)
    return applied


@transaction.atomic
def remove_applied_discount(order, applied_id) -> None:
    order = _lock(order)
    if not discounts_editable(order):
        raise Conflict("A discount can only be removed from an order that is not paid yet.", code="not_editable")
    applied = order.applied_discounts.filter(pk=applied_id).first()
    if applied is None:
        raise Conflict("That discount is not on this order.", code="not_found")
    applied.delete()
    refresh(order)
    _invalidate_invoice(order)


@transaction.atomic
def apply_voucher(order, voucher: Voucher, *, amount: Decimal | None = None, actor=None) -> Payment:
    """Pay (part of) what is due with a voucher: the available balance, capped at the amount due, unless an amount is given."""
    order = _lock(order)
    if order.fulfillment_status in (Fulfilment.CANCELLED, Fulfilment.DELIVERED):
        raise Conflict("This order is closed.", code="not_editable")
    due = payments.amount_due(order)
    if due <= 0:
        raise Conflict("Nothing is due on this order.", code="nothing_due")
    voucher = vouchers.lock_voucher(voucher)
    if amount is None:
        amount = min(voucher.available_amount, due) if voucher.is_usable else Decimal("0")
    if amount <= 0:
        raise Invalid("This voucher can't be used (inactive, expired or empty).", {"voucher_number": ["This voucher can't be used (inactive, expired or empty)."]})
    if amount > due:
        raise Invalid(f"Only {due} is due on this order.", {"amount": [f"Only {due} is due on this order."]})
    return payments.record_payment(order, amount=amount, method=Payment.PaymentMethod.VOUCHER, voucher=voucher, actor=actor)


def _check_editable(order) -> None:
    if not lines_editable(order):
        raise Conflict("The lines can only change while the order is unpaid and not in production.", code="not_editable")


def _lock(order) -> Order:
    return Order.objects.select_for_update().get(pk=order.pk)


@transaction.atomic
def add_line(order, spec: LineSpec) -> OrderLine:
    order = _lock(order)
    _check_editable(order)
    _resolve_spec(spec, 0)
    inventory.lock_products([spec.product.pk] if spec.product.inventory_managed else [])
    try:
        inventory.check_available([spec])
    except inventory.InventoryError as error:
        raise Conflict(str(error), code="insufficient_stock") from error
    line = _make_line(order, spec)
    inventory.take_for_order_lines([line])
    refresh(order)
    return line


@transaction.atomic
def update_line(order, line: OrderLine, *, quantity=None, variant="keep", beneficiary="keep", team="keep", personalization_number=None, personalization_name=None) -> OrderLine:
    order = _lock(order)
    _check_editable(order)
    old_variant_id = line.variant_id
    if quantity is not None:
        if quantity < 1 or quantity > MAX_QUANTITY:
            raise Invalid("Invalid quantity.", {"quantity": [f"Quantity must be between 1 and {MAX_QUANTITY}."]})
        line.quantity = quantity
    if variant != "keep":
        if variant is not None and (variant.product_id != line.product_id or not variant.is_active):
            raise Invalid("Invalid variant.", {"variant_id": ["Not an active variant of this product."]})
        if variant is None and line.product.variants.filter(is_active=True).exists():
            raise Invalid("A variant is required.", {"variant_id": ["A variant is required."]})
        if getattr(variant, "pk", None) != old_variant_id:
            line.variant = variant
            line.unit_price = variant.effective_price if variant else line.product.price
            if line.unit_price < 0:
                raise Invalid("Invalid variant.", {"variant_id": ["A negative price is only a credit on a registration."]})
    if beneficiary != "keep":
        line.beneficiary = beneficiary
    if team != "keep":
        line.team = team
    if personalization_number is not None:
        line.personalization_number = personalization_number[:20]
    if personalization_name is not None:
        line.personalization_name = personalization_name[:100]
    if (line.personalization_number or line.personalization_name) and not line.product.personalization_enabled:
        raise Invalid("This product can't be personalized.", {"personalization_name": ["Personalization is not enabled for this product."]})
    line.line_total = line.unit_price * line.quantity
    line.save()
    try:
        inventory.sync_line_stock(line, old_variant_id=old_variant_id, user=None)
    except inventory.InventoryError as error:
        raise Conflict(str(error), code="insufficient_stock") from error
    if line.product.inventory_managed:
        OrderLine.objects.filter(pk=line.pk).update(received_quantity=line.quantity)
    refresh(order)
    return line


def actor_user(actor):
    return actor if getattr(actor, "pk", None) else None


@transaction.atomic
def remove_line(order, line: OrderLine, *, actor=None) -> None:
    order = _lock(order)
    _check_editable(order)
    if order.lines.count() <= 1:
        raise Invalid("An order needs at least one line. Cancel the order instead.", {"lines": ["Can't remove the last line."]})
    if line.product.inventory_managed:
        inventory.lock_products([line.product_id])
        held = -(InventoryMovement.objects.filter(order=order, product=line.product, variant=line.variant).aggregate(t=Sum("quantity"))["t"] or 0)
        if held > 0:
            inventory.record_movement(line.product, line.variant, held, InventoryMovement.Reason.ORDER_RETURNED, order=order, user=actor_user(actor))
    line.delete()
    refresh(order)


@transaction.atomic
def update_order(order, *, notes=None, pickup_instructions=None) -> Order:
    if notes is not None:
        order.notes = notes
    if pickup_instructions is not None:
        order.pickup_instructions = pickup_instructions
    order.save()
    return order


def _describe_line(line: OrderLine) -> str:
    name = f"{line.product.name}{f' ({line.variant.name})' if line.variant_id else ''}"
    custom = " ".join(part for part in (f"#{line.personalization_number}" if line.personalization_number else "", line.personalization_name) if part)
    return f"{name} {custom}".strip()


def cancel_blockers(order) -> list[OrderLine]:
    """The customised lines (a number or a name was printed on them) that already went into production: they are made to measure
    for the member, so once they were ordered from the supplier the order can no longer be cancelled."""
    return [
        line
        for line in order.lines.select_related("product", "variant")
        if (line.personalization_number or line.personalization_name) and line.production_status != ProductionStatus.PENDING
    ]


def cancel_blocked_reason(order) -> str:
    """Why the order can't be cancelled, in plain words; empty when it can."""
    if order.fulfillment_status == Fulfilment.DELIVERED:
        return "A delivered order can't be cancelled."
    blockers = cancel_blockers(order)
    if not blockers:
        return ""
    items = "; ".join(_describe_line(line) for line in blockers)
    return f"This order can't be cancelled because it contains customised items that are already in production ({items}). Customised items can only be cancelled until they are ordered from the supplier."


@transaction.atomic
def cancel(actor, order, *, override: bool = False, refund_vouchers: bool = False) -> Order:
    """Cancel: stock goes back, pending online payments are voided, the discount use is given back. Confirmed payments stay
    (refund them separately). Customised items already in production block it (409), unless the caller explicitly overrides:
    those items stay with the supplier and a note on the order records who cancelled it anyway. ``refund_vouchers`` also refunds the
    confirmed voucher payments, which puts their amounts back on the vouchers (other payment methods are returned by hand)."""
    order = _lock(order)
    if order.fulfillment_status == Fulfilment.CANCELLED:
        return order
    if order.fulfillment_status == Fulfilment.DELIVERED:
        raise Conflict("A delivered order can't be cancelled.", code="delivered")
    blockers = cancel_blockers(order)
    if blockers and not override:
        raise Conflict(cancel_blocked_reason(order), code="customized_in_production")
    if blockers:
        who = (getattr(actor, "get_full_name", lambda: "")() or getattr(actor, "email", "") or "staff").strip()
        items = "; ".join(_describe_line(line) for line in blockers)
        note = f"Cancelled by {who} although customised items were already in production ({items})."
        order.notes = f"{order.notes}\n{note}".strip()
        order.save(update_fields=["notes"])
    order.fulfillment_status = Fulfilment.CANCELLED
    order.save(update_fields=["fulfillment_status", "modified"])
    # lines not ordered yet must not stay in a draft batch for the supplier
    order.lines.filter(production_batch__status=ProductionBatch.Status.DRAFT).update(production_batch=None)
    inventory.return_order_to_stock(order, user=actor_user(actor))
    order.payments.filter(status=Payment.PaymentStatus.PENDING).update(status=Payment.PaymentStatus.FAILED)
    from shop.services import invoices

    invoices.invalidate_cache(order)
    if refund_vouchers:
        for payment in order.payments.filter(status=Payment.PaymentStatus.CONFIRMED, method=Payment.PaymentMethod.VOUCHER):
            payments.refund_payment(payment, actor=actor)
    transaction.on_commit(lambda: notifications.dispatch("cancelled", order.pk))
    return order


def voucher_payments_to_refund(order) -> tuple[int, Decimal]:
    """How many confirmed voucher payments the order has and their total: what cancelling can put back on the vouchers."""
    confirmed = order.payments.filter(status=Payment.PaymentStatus.CONFIRMED, method=Payment.PaymentMethod.VOUCHER)
    return confirmed.count(), confirmed.aggregate(total=Sum("amount"))["total"] or Decimal("0")


@transaction.atomic
def mark_ready(order, *, pickup_instructions: str | None = None, payment_instructions: str | None = None) -> Order:
    order = _lock(order)
    if order.fulfillment_status in (Fulfilment.CANCELLED, Fulfilment.DELIVERED):
        raise Conflict("This order is already closed.", code="closed")
    newly = order.fulfillment_status != Fulfilment.READY_FOR_PICKUP
    # instructions given now win; otherwise keep the order's own; otherwise the shop's default
    if (pickup_instructions or "").strip():
        order.pickup_instructions = pickup_instructions.strip()
    elif not order.pickup_instructions:
        order.pickup_instructions = defaults.pickup_instructions()
    order.fulfillment_status = Fulfilment.READY_FOR_PICKUP
    order.save(update_fields=["fulfillment_status", "pickup_instructions", "modified"])
    from shop.services import invoices

    invoices.ensure_invoice(order)  # ready for pickup: the member can be handed an invoice, so make sure it exists
    if newly:
        transaction.on_commit(lambda: notifications.dispatch("ready", order.pk, {"payment_instructions": payment_instructions} if payment_instructions is not None else None))
    return order


@transaction.atomic
def mark_delivered(order) -> Order:
    order = _lock(order)
    if order.fulfillment_status == Fulfilment.CANCELLED:
        raise Conflict("This order is cancelled.", code="order_cancelled")
    order.fulfillment_status = Fulfilment.DELIVERED
    order.save(update_fields=["fulfillment_status", "modified"])
    return order


@transaction.atomic
def reopen(order) -> Order:
    """One step back (a mistake): picked up -> ready for pickup, ready for pickup -> not ready."""
    order = _lock(order)
    if order.fulfillment_status in (Fulfilment.CANCELLED, Fulfilment.NOT_READY):
        raise Conflict("Nothing to undo.", code="nothing_to_undo")
    order.fulfillment_status = Fulfilment.READY_FOR_PICKUP if order.fulfillment_status == Fulfilment.DELIVERED else Fulfilment.NOT_READY
    order.save(update_fields=["fulfillment_status", "modified"])
    return order


def bulk_mark_paid(actor, orders, method: str) -> int:
    count = 0
    with transaction.atomic():
        for order in orders.exclude(fulfillment_status=Fulfilment.CANCELLED):
            if payments.mark_paid(order, method=method, actor=actor) is not None:
                count += 1
    return count


def bulk_mark_ready(orders, pickup_instructions: str) -> int:
    count = 0
    with transaction.atomic():
        for order in orders.exclude(fulfillment_status__in=[Fulfilment.CANCELLED, Fulfilment.DELIVERED]):
            mark_ready(order, pickup_instructions=pickup_instructions)
            count += 1
    return count


def find_voucher(number: str) -> Voucher:
    voucher = Voucher.objects.filter(number__iexact=(number or "").strip()).first()
    if voucher is None:
        raise Invalid("Unknown voucher.", {"voucher_number": ["Unknown voucher."]})
    return voucher
