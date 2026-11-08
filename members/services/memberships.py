"""Season memberships and their fees.

Money rules (all ``Decimal``, 2 places):
* ``amount_paid`` is always the sum of the membership's ``FeePayment`` rows; it is recomputed, never hand-set.
* Early-payment discount: ``early_payment_discount`` comes off ``fee_amount`` when enough money arrived with
  ``paid_at.date() <= early_payment_deadline`` to cover the discounted fee; before the deadline has passed and
  nothing is paid yet, the discounted amount is what is quoted.
* ``fee_status``: ``waived`` stays waived; otherwise ``paid`` when nothing remains and ``fee_amount > 0``,
  ``partially_paid`` when something is paid but a balance remains, else ``unpaid``.
* Activation is explicit (``activate``): fee paid or waived and every active onboarding requirement resolved.
"""

import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from members.models import ClubMembership, FeePayment, Member

from . import audit, onboarding
from .access import require_admin
from .errors import Invalid

CENT = Decimal("0.01")
Status = ClubMembership.StatusChoices
FeeStatus = ClubMembership.FeeStatus
UPDATABLE_FIELDS = ("status", "fee_amount", "early_payment_deadline", "early_payment_discount", "signed_up_at")
MONEY_FIELDS = ("fee_amount", "early_payment_discount")
LOGGED_FIELDS = ("status", "fee_status", "fee_amount", "amount_paid", "early_payment_deadline", "early_payment_discount", "signed_up_at", "activated_at")


def money(value) -> Decimal:
    """``value`` as a non-negative 2-place Decimal, else ``Invalid``."""
    try:
        d = Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError) as e:
        raise Invalid("Not a valid amount.") from e
    if not d.is_finite() or d < 0:
        raise Invalid("Amounts cannot be negative.")
    return d


# --- fee math -----------------------------------------------------------------------------------------------------


def total_paid(membership: ClubMembership) -> Decimal:
    """Sum of all recorded payments."""
    return membership.payments.aggregate(t=Sum("amount"))["t"] or Decimal("0.00")


def discount_applies(membership: ClubMembership, *, today: datetime.date | None = None) -> bool:
    """Whether the early-payment discount is in force (see module docstring)."""
    deadline, discount = membership.early_payment_deadline, membership.early_payment_discount
    if deadline is None or not discount or discount <= 0:
        return False
    discounted = max(membership.fee_amount - discount, Decimal("0.00"))
    paid_in_time = sum((p.amount for p in membership.payments.all() if timezone.localtime(p.paid_at).date() <= deadline), Decimal("0.00"))
    if paid_in_time >= discounted:
        return True
    return (today or timezone.localdate()) <= deadline


def amount_due(membership: ClubMembership, *, today: datetime.date | None = None) -> Decimal:
    """What the membership costs: ``fee_amount``, less the early-payment discount when it applies."""
    if discount_applies(membership, today=today):
        return max(membership.fee_amount - membership.early_payment_discount, Decimal("0.00"))
    return membership.fee_amount


def remaining_balance(membership: ClubMembership, *, today: datetime.date | None = None) -> Decimal:
    """``amount_due`` minus payments, never below zero; zero when waived."""
    if membership.fee_status == FeeStatus.WAIVED:
        return Decimal("0.00")
    return max(amount_due(membership, today=today) - total_paid(membership), Decimal("0.00"))


def compute_fee_status(membership: ClubMembership, *, today: datetime.date | None = None) -> str:
    """Derive the fee status from the payments (see module docstring)."""
    if membership.fee_status == FeeStatus.WAIVED:
        return FeeStatus.WAIVED
    remaining = remaining_balance(membership, today=today)
    if remaining <= 0 and membership.fee_amount > 0:
        return FeeStatus.PAID
    if total_paid(membership) > 0:
        return FeeStatus.PARTIALLY_PAID
    return FeeStatus.UNPAID


def sync_fee_status(membership: ClubMembership, *, today: datetime.date | None = None) -> dict:
    """Recompute and save ``amount_paid`` and ``fee_status``. Returns the audit-style changes (empty if none).
    Writers call this and fold the result into their own audit entry."""
    new = {"amount_paid": total_paid(membership)}
    new["fee_status"] = compute_fee_status(membership, today=today)
    changes = audit.diff(membership, new)
    if changes:
        for k, v in new.items():
            setattr(membership, k, v)
        membership.save(update_fields=[*new, "modified"])
    return changes


# --- memberships --------------------------------------------------------------------------------------------------


@transaction.atomic
def create_membership(actor, member: Member, season, *, fee_amount=0, early_payment_deadline=None, early_payment_discount=0, status: str = Status.PENDING, signed_up_at: datetime.date | None = None) -> ClubMembership:
    """Sign ``member`` up for ``season`` (once per season). ``status`` may not be ACTIVE; use ``activate``."""
    require_admin(actor)
    if status not in (Status.PENDING, Status.LAPSED, Status.CANCELLED):
        raise Invalid("Use activate() to make a membership active.", {"status": ["Invalid choice."]})
    if ClubMembership.objects.filter(member=member, season=season).exists():
        raise Invalid("This person already has a membership for that season.")
    membership = ClubMembership.objects.create(
        member=member,
        season=season,
        status=status,
        fee_amount=money(fee_amount),
        early_payment_deadline=early_payment_deadline,
        early_payment_discount=money(early_payment_discount),
        signed_up_at=signed_up_at or timezone.localdate(),
    )
    sync_fee_status(membership)
    audit.record(actor, "membership.created", member=member, target=membership, changes=audit.snapshot(membership, LOGGED_FIELDS), target_label=f"{season} - {member}")
    return membership


@transaction.atomic
def update_membership(actor, membership: ClubMembership, **values) -> ClubMembership:
    """Change status (not to ACTIVE; see ``activate``) and fee settings, then re-derive the fee status.
    ``amount_paid`` and ``fee_status`` cannot be set here (payments drive them; see ``set_fee_waived``)."""
    require_admin(actor)
    unknown = set(values) - set(UPDATABLE_FIELDS)
    if unknown:
        raise Invalid(f"Unknown or derived fields: {sorted(unknown)}", {f: ["Not editable."] for f in unknown})
    if "status" in values and values["status"] not in Status.values:
        raise Invalid("Invalid status.", {"status": ["Invalid choice."]})
    if values.get("status") == Status.ACTIVE and membership.status != Status.ACTIVE:
        raise Invalid("Use activate() to make a membership active.", {"status": ["Invalid choice."]})
    for f in MONEY_FIELDS:
        if f in values:
            values[f] = money(values[f])
    changes = audit.diff(membership, values)
    for k, v in values.items():
        setattr(membership, k, v)
    if changes:
        membership.save(update_fields=[*values, "modified"])
    changes.update(sync_fee_status(membership))
    if changes:
        audit.record(actor, "membership.updated", member=membership.member, target=membership, changes=changes, target_label=f"{membership.season} - {membership.member}")
    return membership


@transaction.atomic
def set_fee_waived(actor, membership: ClubMembership, waived: bool = True) -> ClubMembership:
    """Waive the fee (stays waived whatever is paid) or lift the waiver (status is re-derived from payments)."""
    require_admin(actor)
    if waived == (membership.fee_status == FeeStatus.WAIVED):
        return membership
    before = membership.fee_status
    membership.fee_status = FeeStatus.WAIVED if waived else FeeStatus.UNPAID
    membership.save(update_fields=["fee_status", "modified"])
    sync_fee_status(membership)
    audit.record(
        actor,
        "membership.fee_waived" if waived else "membership.fee_unwaived",
        member=membership.member,
        target=membership,
        changes={"fee_status": {"from": before, "to": membership.fee_status}},
        target_label=f"{membership.season} - {membership.member}",
    )
    return membership


def _payment_changes(payment: FeePayment, fee_changes: dict, *, deleted: bool = False) -> dict:
    changes = audit.snapshot(payment, ["amount", "method", "reference", "paid_at"], deleted=deleted)
    return {**changes, **fee_changes}


@transaction.atomic
def record_payment(actor, membership: ClubMembership, amount, *, method: str = FeePayment.Method.BANK_TRANSFER, reference: str = "", paid_at: datetime.datetime | None = None, note: str = "") -> FeePayment:
    """Record money received (amount > 0) and re-derive ``amount_paid``/``fee_status``. Not for waived fees."""
    require_admin(actor)
    amount = money(amount)
    if amount <= 0:
        raise Invalid("The amount must be more than zero.", {"amount": ["Must be positive."]})
    if method not in FeePayment.Method.values:
        raise Invalid("Invalid payment method.", {"method": ["Invalid choice."]})
    if membership.fee_status == FeeStatus.WAIVED:
        raise Invalid("The fee is waived; there is nothing to pay.")
    payment = FeePayment.objects.create(membership=membership, amount=amount, method=method, reference=reference, paid_at=paid_at or timezone.now(), note=note, recorded_by=actor)
    fee_changes = sync_fee_status(membership)
    audit.record(actor, "payment.recorded", member=membership.member, target=payment, changes=_payment_changes(payment, fee_changes), target_label=f"{membership.season} - {membership.member}: {amount}")
    return payment


@transaction.atomic
def mark_paid(actor, membership: ClubMembership, *, method: str = FeePayment.Method.OTHER, reference: str = "", paid_at: datetime.datetime | None = None, note: str = "") -> FeePayment:
    """Record one payment for the whole remaining balance (as of ``paid_at``, so the early discount is honoured)."""
    require_admin(actor)
    when = paid_at or timezone.now()
    # Quote the balance as of the payment date: discount holds if that date is within the deadline.
    deadline = membership.early_payment_deadline
    discounted = bool(deadline and membership.early_payment_discount > 0 and timezone.localtime(when).date() <= deadline)
    due = max(membership.fee_amount - membership.early_payment_discount, Decimal("0.00")) if discounted else membership.fee_amount
    remaining = max(due - total_paid(membership), Decimal("0.00"))
    if membership.fee_status == FeeStatus.WAIVED or remaining <= 0:
        raise Invalid("There is no balance left to pay.")
    return record_payment(actor, membership, remaining, method=method, reference=reference, paid_at=when, note=note)


@transaction.atomic
def delete_payment(actor, payment: FeePayment) -> ClubMembership:
    """Remove a mistaken payment and re-derive the fee status."""
    require_admin(actor)
    membership = payment.membership
    snapshot = _payment_changes(payment, {}, deleted=True)
    pk, label = payment.pk, f"{membership.season} - {membership.member}: {payment.amount}"
    payment.delete()
    fee_changes = sync_fee_status(membership)
    entry = audit.record(actor, "payment.deleted", member=membership.member, changes={**snapshot, **fee_changes})
    entry.target_type, entry.target_id, entry.target_label = "feepayment", str(pk), label[:255]
    entry.save(update_fields=["target_type", "target_id", "target_label", "modified"])
    return membership


@transaction.atomic
def activate(actor, membership: ClubMembership) -> ClubMembership:
    """Make the membership ACTIVE. Requires fee paid or waived and every active requirement resolved."""
    require_admin(actor)
    if membership.status == Status.ACTIVE:
        raise Invalid("The membership is already active.")
    if membership.status == Status.CANCELLED:
        raise Invalid("A cancelled membership cannot be activated.")
    sync_fee_status(membership)
    if membership.fee_status not in (FeeStatus.PAID, FeeStatus.WAIVED):
        raise Invalid("The fee must be paid or waived first.", {"fee_status": ["Not settled."]})
    open_items = onboarding.unresolved(membership)
    if open_items:
        raise Invalid("Onboarding requirements are still open: " + ", ".join(r.name for r in open_items), {"requirements": [r.name for r in open_items]})
    changes = {"status": {"from": membership.status, "to": Status.ACTIVE}}
    membership.status = Status.ACTIVE
    fields = ["status", "modified"]
    if membership.activated_at is None:
        today = timezone.localdate()
        changes["activated_at"] = {"from": None, "to": str(today)}
        membership.activated_at = today
        fields.append("activated_at")
    membership.save(update_fields=fields)
    audit.record(actor, "membership.activated", member=membership.member, target=membership, changes=changes, target_label=f"{membership.season} - {membership.member}")
    return membership


def can_delete_membership(membership: ClubMembership) -> bool:
    """Only a membership that never got anywhere: still pending, nothing paid and not waived."""
    return membership.status == Status.PENDING and membership.fee_status == FeeStatus.UNPAID and total_paid(membership) == 0


@transaction.atomic
def delete_membership(actor, membership: ClubMembership) -> None:
    """Remove a season membership that is pending and unpaid (a sign-up made by mistake). Anything further along
    is cancelled instead (``cancel``), so the money trail stays intact."""
    require_admin(actor)
    if not can_delete_membership(membership):
        raise Invalid("Only a pending, unpaid membership can be deleted. Cancel it instead.", {"__all__": ["Only a pending, unpaid membership can be deleted."]})
    from teams.models import TeamMembership

    member, label, pk = membership.member, f"{membership.season} - {membership.member}", membership.pk
    snapshot = audit.snapshot(membership, LOGGED_FIELDS)
    # A person who isn't signed up for the season can't be on a roster in it: the roster spots go too (staff roles stay).
    for spot in TeamMembership.objects.filter(member=member, season=membership.season).select_related("team", "position", "season"):
        spot_changes = {
            "team": {"from": spot.team.name, "to": None},
            "season": {"from": spot.season.name, "to": None},
            "position": {"from": spot.position.name if spot.position_id else None, "to": None},
            "jersey_number": {"from": spot.jersey_number, "to": None},
        }
        entry = audit.record(actor, "team.roster_removed", member=member, changes=spot_changes)
        entry.target_type, entry.target_id, entry.target_label = "teammembership", str(spot.pk), f"{member} - {spot.team.name}"[:255]
        entry.save(update_fields=["target_type", "target_id", "target_label", "modified"])
        spot.delete()
    membership.delete()
    entry = audit.record(actor, "membership.deleted", member=member, changes={k: {"from": v, "to": None} for k, v in snapshot.items()})
    entry.target_type, entry.target_id, entry.target_label = "clubmembership", str(pk), label[:255]
    entry.save(update_fields=["target_type", "target_id", "target_label", "modified"])


@transaction.atomic
def cancel(actor, membership: ClubMembership) -> ClubMembership:
    """Cancel the membership. Payments are kept."""
    require_admin(actor)
    if membership.status == Status.CANCELLED:
        raise Invalid("The membership is already cancelled.")
    before = membership.status
    membership.status = Status.CANCELLED
    membership.save(update_fields=["status", "modified"])
    audit.record(actor, "membership.cancelled", member=membership.member, target=membership, changes={"status": {"from": before, "to": Status.CANCELLED}}, target_label=f"{membership.season} - {membership.member}")
    return membership
