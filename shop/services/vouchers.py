"""Vouchers: creation (single/bulk), the locked balance and manual consumptions."""

from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from formbuilder.services.management import Conflict
from members.services import access
from members.services.errors import Invalid
from shop.models import Payment, Voucher, VoucherConsumption


class VoucherError(Exception):
    """A voucher balance change that can't be made (user-facing message)."""


def voucher_status(v, today=None) -> str:
    if not v.is_active:
        return "inactive"
    if v.expiry_date < (today or timezone.localdate()):
        return "expired"
    return "exhausted" if v.available_amount <= 0 else "usable"


def lock_voucher(voucher) -> Voucher:
    return Voucher.objects.select_for_update().get(pk=voucher.pk)


def consume(voucher, amount: Decimal) -> Voucher:
    """Take ``amount`` from the voucher's balance (call inside a transaction; locks the row). Raises VoucherError."""
    if amount <= 0:
        raise VoucherError("The amount must be greater than zero.")
    locked = lock_voucher(voucher)
    if not locked.is_usable:
        raise VoucherError("This voucher can't be used (inactive, expired or empty).")
    if amount > locked.available_amount:
        raise VoucherError(f"Only {locked.available_amount} is available on that voucher.")
    locked.consumed_amount += amount
    locked.save(update_fields=["consumed_amount", "modified"])
    voucher.consumed_amount = locked.consumed_amount
    return locked


def restore(voucher, amount: Decimal) -> Voucher:
    """Give ``amount`` back (payment deleted/refunded, consumption removed). Locks the row."""
    locked = lock_voucher(voucher)
    locked.consumed_amount = max(locked.consumed_amount - amount, Decimal("0"))
    locked.save(update_fields=["consumed_amount", "modified"])
    voucher.consumed_amount = locked.consumed_amount
    return locked


@transaction.atomic
def create_vouchers(actor, *, amount, expiry_date, issued_to=None, notes="", count=1) -> list[Voucher]:
    access.require_manage_shop_discounts(actor)
    if amount is None or amount <= 0:
        raise Invalid("The amount must be greater than zero.", {"amount": ["Must be greater than zero."]})
    if not 1 <= count <= 200:
        raise Invalid("Create between 1 and 200 vouchers at a time.", {"count": ["Between 1 and 200."]})
    return [Voucher.objects.create(amount=amount, expiry_date=expiry_date, issued_to=issued_to, notes=notes) for _ in range(count)]


@transaction.atomic
def update_voucher(actor, voucher, **fields) -> Voucher:
    access.require_manage_shop_discounts(actor)
    voucher = lock_voucher(voucher)
    if "amount" in fields:
        if fields["amount"] <= 0 or fields["amount"] < voucher.consumed_amount:
            raise Invalid("The amount can't be below what was already used.", {"amount": ["Can't be below the consumed amount."]})
    for k, v in fields.items():
        setattr(voucher, k, v)
    voucher.save()
    return voucher


@transaction.atomic
def delete_voucher(actor, voucher) -> None:
    access.require_manage_shop_discounts(actor)
    voucher = lock_voucher(voucher)
    if voucher.consumed_amount > 0 or voucher.payments.exists() or voucher.consumptions.exists():
        raise Conflict("This voucher has been used; deactivate it instead.", "has_usage")
    voucher.delete()


@transaction.atomic
def record_consumption(actor, voucher, *, amount: Decimal, note="") -> VoucherConsumption:
    access.require_manage_shop_discounts(actor)
    try:
        consume(voucher, amount)
    except VoucherError as e:
        raise Invalid(str(e), {"amount": [str(e)]}) from e
    return VoucherConsumption.objects.create(voucher=voucher, amount=amount, note=note, recorded_by=actor)


@transaction.atomic
def delete_consumption(actor, consumption: VoucherConsumption) -> None:
    access.require_manage_shop_discounts(actor)
    current = VoucherConsumption.objects.select_for_update().filter(pk=consumption.pk).first()
    if current is None:
        return
    restore(current.voucher, current.amount)
    current.delete()


def voucher_history(voucher) -> list[dict]:
    """Manual consumptions and confirmed order payments, newest first."""
    rows = [
        {"kind": "manual", "amount": c.amount, "when": c.recorded_at, "detail": c.note, "order_id": None, "consumption_id": c.pk, "recorded_by": str(c.recorded_by) if c.recorded_by else None}
        for c in voucher.consumptions.select_related("recorded_by")
    ]
    rows += [
        {"kind": "order", "amount": p.amount, "when": p.paid_at or p.created, "detail": p.order.number, "order_id": p.order_id, "consumption_id": None, "recorded_by": None}
        for p in voucher.payments.filter(status=Payment.PaymentStatus.CONFIRMED).select_related("order")
    ]
    rows.sort(key=lambda r: r["when"], reverse=True)
    return rows


def available_for(member) -> list[dict]:
    """The vouchers a member's order can be paid with: usable ones (active, not expired, balance left) issued to the member
    themselves, to anyone in the same family (any parent, guardian or child) and the general ones issued to nobody. Own first, then
    family, then general; soonest expiry first within each."""
    from django.db.models import F, Q

    from members.models import FamilyMembership

    today = timezone.localdate()
    families = FamilyMembership.objects.filter(member=member).values_list("family_id", flat=True)
    family_ids = set(FamilyMembership.objects.filter(family_id__in=families).values_list("member_id", flat=True)) - {member.pk}
    qs = (
        Voucher.objects.filter(is_active=True, expiry_date__gte=today, amount__gt=F("consumed_amount"))
        .filter(Q(issued_to=member) | Q(issued_to_id__in=family_ids) | Q(issued_to__isnull=True))
        .select_related("issued_to")
    )
    rank = {"own": 0, "family": 1, "general": 2}
    rows = []
    for v in qs:
        scope = "general" if v.issued_to_id is None else "own" if v.issued_to_id == member.pk else "family"
        rows.append({"id": v.pk, "number": v.number, "scope": scope, "issued_to": {"id": v.issued_to_id, "name": str(v.issued_to)} if v.issued_to_id else None, "available_amount": v.available_amount, "expiry_date": v.expiry_date, "notes": v.notes})
    return sorted(rows, key=lambda r: (rank[r["scope"]], r["expiry_date"], r["number"]))
