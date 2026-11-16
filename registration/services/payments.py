"""Payments against a confirmed registration (its invoice), for the finance page.

The money itself lives on each person's membership as ``FeePayment`` rows (``members.services.memberships``), so the member pages, the fee status
and the early-payment discount keep working. This module adds the invoice level on top: one amount against the invoice is spread over the people's
memberships (in the order of the registration, each up to what it still owes), or the finance person says exactly how much goes to whom.
Everything needs ``registration.manage_finance`` and a confirmed, not cancelled registration.
"""

import datetime
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from members.models import ClubMembership, FeePayment
from members.services import access
from members.services import memberships as fees
from members.services.errors import Invalid
from registration.models import RegistrationBatch
from registration.services import installments

ZERO = Decimal("0.00")
FeeStatus = ClubMembership.FeeStatus


def memberships_of(batch: RegistrationBatch) -> list[ClubMembership]:
    """The memberships on the invoice, in the order of the registration."""
    return [d.membership for d in sorted(batch.details.all(), key=lambda d: d.created)]


def _payable(batch: RegistrationBatch) -> None:
    if batch.cancelled_at:
        raise Invalid("This registration was cancelled.", {"__all__": ["This registration was cancelled."]}, code="cancelled")
    if not batch.confirmed_at:
        raise Invalid("The invoice is not confirmed yet.", {"__all__": ["The invoice is not confirmed yet."]}, code="not_confirmed")


def summary(batch: RegistrationBatch, *, today: datetime.date | None = None) -> dict:
    """What the invoice amounts to and how much of it is paid: ``due`` (the fees, less early-payment discounts that apply), ``paid`` and ``balance``."""
    due = paid = balance = ZERO
    for m in memberships_of(batch):
        if m.fee_status == FeeStatus.WAIVED:
            continue
        m_due, m_paid = fees.amount_due(m, today=today), sum((p.amount for p in m.payments.all()), ZERO)
        due, paid, balance = due + m_due, paid + m_paid, balance + max(m_due - m_paid, ZERO)
    return {"due": due, "paid": paid, "balance": balance}


def early_bird(batch: RegistrationBatch, *, today: datetime.date | None = None) -> dict | None:
    """The early-bird discount while it still applies, else ``None`` (so it disappears by itself once the deadline has passed or everything is paid).
    ``deadline`` is the earliest deadline among the people it applies to, ``discount`` what it saves on the invoice, ``amount_due`` what the invoice
    comes to with it (the discount is already out of it) and ``balance`` what is still open."""
    if batch.cancelled_at or not batch.confirmed_at:
        return None
    deadlines, saved = [], ZERO
    for m in memberships_of(batch):
        if m.fee_status == FeeStatus.WAIVED or not m.early_payment_deadline or not m.early_payment_discount or m.early_payment_discount <= 0:
            continue
        if fees.discount_applies(m, today=today) and fees.remaining_balance(m, today=today) > 0:
            deadlines.append(m.early_payment_deadline)
            saved += min(m.early_payment_discount, m.fee_amount)
    if not deadlines:
        return None
    totals = summary(batch, today=today)
    return {"deadline": min(deadlines), "discount": saved, "amount_due": totals["due"], "balance": totals["balance"]}


def person(m: ClubMembership, *, today: datetime.date | None = None) -> dict:
    """One person's share: ``fee_status``, ``amount_due``, ``amount_paid`` and ``balance``."""
    waived = m.fee_status == FeeStatus.WAIVED
    due = ZERO if waived else fees.amount_due(m, today=today)
    paid = sum((p.amount for p in m.payments.all()), ZERO)
    return {"fee_status": m.fee_status, "amount_due": due, "amount_paid": paid, "balance": ZERO if waived else max(due - paid, ZERO)}


def status(batch: RegistrationBatch, totals: dict, *, today: datetime.date | None = None, next_installment: dict | None = None) -> str | None:
    """unpaid | partial | paid | overdue (open past the due date); ``None`` until the invoice is confirmed and when cancelled."""
    if batch.cancelled_at or not batch.confirmed_at:
        return None
    if batch.installment_offsets:
        if next_installment and next_installment["late"]:
            return "overdue"
        return ("partial" if totals["paid"] > 0 else "unpaid") if next_installment else "paid"
    if totals["balance"] <= 0:
        return "paid"
    if batch.invoice_due_date and batch.invoice_due_date < (today or timezone.localdate()):
        return "overdue"
    return "partial" if totals["paid"] > 0 else "unpaid"


def _split(batch: RegistrationBatch, amount: Decimal, allocations, on: datetime.date) -> list[tuple[ClubMembership, Decimal]]:
    """Who gets how much. ``allocations`` (``[{"membership_id", "amount"}]``) is taken as given, else the amount fills the people in order."""
    people = {m.pk: m for m in memberships_of(batch)}
    if allocations:
        parts: dict = {}
        for row in allocations:
            m = people.get(row["membership_id"])
            if m is None:
                raise Invalid("That person is not on this registration.", {"allocations": ["Not on this registration."]})
            parts[m.pk] = parts.get(m.pk, ZERO) + fees.money(row["amount"])
        parts = {pk: a for pk, a in parts.items() if a > 0}
        if sum(parts.values(), ZERO) != amount:
            raise Invalid("The amounts per person must add up to the payment.", {"allocations": ["Must add up to the amount."]})
        for pk, a in parts.items():
            if a > fees.remaining_balance(people[pk], today=on):
                raise Invalid(f"{people[pk].member} only owes {fees.remaining_balance(people[pk], today=on)}.", {"allocations": ["More than that person owes."]})
        return [(people[pk], a) for pk, a in parts.items()]
    if installments.dates(batch):
        balances = {m.pk: fees.remaining_balance(m, today=on) for m in people.values()}
        parts = installments.allocate(batch, people.values(), balances, amount, today=on)
        if sum(parts.values(), ZERO) != amount:
            raise Invalid("That is more than the invoice still has open.", {"amount": ["More than the open balance."]})
        return [(people[pk], share) for pk, share in parts.items()]
    out, left = [], amount
    for m in people.values():
        take = min(fees.remaining_balance(m, today=on), left)
        if take > 0:
            out.append((m, take))
            left -= take
    if left > 0:
        raise Invalid(f"That is {left} more than the invoice still has open.", {"amount": ["More than the open balance."]})
    return out


@transaction.atomic
def record(actor, batch: RegistrationBatch, amount, *, method: str, reference: str = "", paid_at: datetime.datetime | None = None, note: str = "", voucher=None, allocations=None) -> list[FeePayment]:
    """One payment against the invoice, as one ``FeePayment`` per person it lands on (a voucher payment takes the same total from the voucher)."""
    access.require_finance(actor)
    batch = RegistrationBatch.objects.select_for_update().get(pk=batch.pk)
    _payable(batch)
    amount = fees.money(amount)
    if amount <= 0:
        raise Invalid("The amount must be more than zero.", {"amount": ["Must be positive."]})
    when = paid_at or timezone.now()
    parts = _split(batch, amount, allocations, timezone.localtime(when).date())
    if not parts:
        raise Invalid("There is nothing left to pay on this invoice.", {"__all__": ["Nothing left to pay."]}, code="nothing_due")
    return [fees.record_payment(actor, m, a, method=method, reference=reference, paid_at=when, note=note, voucher=voucher) for m, a in parts]


@transaction.atomic
def mark_paid(actor, batch: RegistrationBatch, *, method: str, reference: str = "", paid_at: datetime.datetime | None = None, note: str = "", voucher=None) -> list[FeePayment]:
    """Settle everything the invoice still has open with one payment."""
    access.require_finance(actor)
    batch = RegistrationBatch.objects.select_for_update().get(pk=batch.pk)
    _payable(batch)
    when = paid_at or timezone.now()
    open_amount = summary(batch, today=timezone.localtime(when).date())["balance"]
    if open_amount <= 0:
        raise Invalid("There is nothing left to pay on this invoice.", {"__all__": ["Nothing left to pay."]}, code="nothing_due")
    return record(actor, batch, open_amount, method=method, reference=reference, paid_at=when, note=note, voucher=voucher)


@transaction.atomic
def delete_payment(actor, batch: RegistrationBatch, payment_id) -> None:
    """Take a mistaken payment off the invoice (a voucher payment gives the amount back to its voucher)."""
    access.require_finance(actor)
    batch = RegistrationBatch.objects.select_for_update().get(pk=batch.pk)
    payment = FeePayment.objects.select_related("membership", "membership__member", "voucher").filter(pk=payment_id, membership__registration_details__batch=batch).first()
    if payment is None:
        raise Invalid("That payment is not on this registration.", code="not_found")
    fees.delete_payment(actor, payment)
