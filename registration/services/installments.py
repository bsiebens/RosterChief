"""Payment schedules derived from confirmed invoices and the membership payment ledger."""

import datetime
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

from django.utils import timezone

from members.models import ClubMembership
from registration.models import RegistrationDetails, RegistrationSettings

ZERO = Decimal("0.00")
CENT = Decimal("0.01")


def dates(batch) -> list[datetime.date]:
    if not batch.confirmed_at or batch.cancelled_at or not batch.invoice_due_date or not batch.installment_offsets:
        return []
    return [batch.invoice_due_date + datetime.timedelta(days=offset) for offset in [0, *batch.installment_offsets]]


def split_amount(total: Decimal, count: int) -> list[Decimal]:
    cumulative = [(total * index / count).quantize(CENT, rounding=ROUND_HALF_UP) for index in range(count + 1)]
    return [cumulative[index] - cumulative[index - 1] for index in range(1, count + 1)]


def invoice_amounts(batch) -> list[tuple[datetime.date, Decimal]]:
    due_dates = dates(batch)
    if not due_dates:
        return []
    fees = batch.details.filter(excluded=False).values_list("membership__fee_amount", flat=True)
    portions = [split_amount(fee, len(due_dates)) for fee in fees]
    return [(due_date, amount) for index, due_date in enumerate(due_dates) if (amount := sum((part[index] for part in portions), ZERO)) > 0]


def member_schedule(membership, due_dates: list[datetime.date], *, today: datetime.date) -> list[dict]:
    """Divide this person's fee into equal instalments. Each payment settles the oldest open amount first."""
    if not due_dates or membership.fee_status == ClubMembership.FeeStatus.WAIVED:
        return []
    payments = [(timezone.localtime(payment.paid_at).date(), payment.amount) for payment in membership.payments.all()]
    paid = sum((amount for on, amount in payments if on <= today), ZERO)
    total = membership.fee_amount
    if membership.early_payment_deadline and membership.early_payment_discount:
        discounted_total = max(total - membership.early_payment_discount, ZERO)
        paid_by_deadline = sum((amount for on, amount in payments if on <= min(today, membership.early_payment_deadline)), ZERO)
        if paid_by_deadline >= discounted_total:
            total = discounted_total

    schedule = []
    cumulative = ZERO
    for due_date, amount in zip(due_dates, split_amount(total, len(due_dates)), strict=True):
        cumulative += amount
        remaining = min(amount, max(cumulative - paid, ZERO))
        schedule.append({"due_date": due_date, "amount": amount, "remaining": remaining, "late": remaining > 0 and due_date < today})
    return schedule


def batch_schedule(batch, *, today: datetime.date | None = None) -> tuple[list[dict], dict]:
    """Invoice instalments and the schedules of individual memberships, reusing prefetched payments."""
    due_dates = dates(batch)
    if not due_dates:
        return [], {}
    today = today or timezone.localdate()
    people = {detail.membership_id: member_schedule(detail.membership, due_dates, today=today) for detail in batch.details.all() if not detail.excluded}
    rows = []
    for index, due_date in enumerate(due_dates):
        amount = sum((schedule[index]["amount"] for schedule in people.values() if schedule), ZERO)
        remaining = sum((schedule[index]["remaining"] for schedule in people.values() if schedule), ZERO)
        if amount > 0:
            rows.append({"due_date": due_date, "amount": amount, "remaining": remaining, "late": remaining > 0 and due_date < today})
    return rows, people


def next_unpaid(schedule: list[dict]) -> dict | None:
    return next((row for row in schedule if row["remaining"] > 0), None)


def membership_schedule(membership, *, today: datetime.date | None = None) -> list[dict]:
    detail = getattr(membership, "registration_details", None)
    if detail is None or detail.excluded:
        return []
    return member_schedule(membership, dates(detail.batch), today=today or timezone.localdate())


def membership_next(membership, *, today: datetime.date | None = None) -> dict | None:
    return next_unpaid(membership_schedule(membership, today=today))


def can_activate(schedule: list[dict]) -> bool:
    """The first payable instalment must be settled, as must every instalment already overdue."""
    first = next((row for row in schedule if row["amount"] > 0), None)
    return (first is None or first["remaining"] == 0) and not any(row["late"] for row in schedule)


def allocate(batch, people, balances: dict, amount: Decimal, *, today: datetime.date) -> dict:
    """Fill the earliest open due date across all family members before moving to the next one."""
    due_dates = dates(batch)
    schedules = {m.pk: member_schedule(m, due_dates, today=today) for m in people}
    allocations = {}
    for index in range(len(due_dates)):
        capacity = {}
        for pk, schedule in schedules.items():
            if schedule:
                available = min(balances[pk] - allocations.get(pk, ZERO), schedule[index]["remaining"])
                if available > 0:
                    capacity[pk] = available
        total = sum(capacity.values(), ZERO)
        take = min(amount, total)
        if take <= 0:
            continue
        shares = {pk: (take * available / total).quantize(CENT, rounding=ROUND_DOWN) for pk, available in capacity.items()}
        pennies = int((take - sum(shares.values(), ZERO)) / CENT)
        for pk in list(shares)[:pennies]:
            shares[pk] += CENT
        for pk, share in shares.items():
            allocations[pk] = allocations.get(pk, ZERO) + share
        amount -= take
        if amount <= 0:
            break
    return {pk: share for pk, share in allocations.items() if share > 0}


def blocked_for_event(event, member, season, *, today: datetime.date | None = None) -> bool:
    if season is None:
        return False
    policy = RegistrationSettings.objects.first()
    if policy is None or event.kind not in policy.installment_block_kinds:
        return False
    detail = (
        RegistrationDetails.objects.filter(member=member, membership__season=season, excluded=False, batch__confirmed_at__isnull=False, batch__cancelled_at__isnull=True)
        .exclude(batch__installment_offsets=[])
        .exclude(membership__status=ClubMembership.StatusChoices.CANCELLED)
        .select_related("batch", "membership")
        .prefetch_related("membership__payments")
        .first()
    )
    if detail is None:
        return False
    today = today or timezone.localdate()
    oldest = next_unpaid(member_schedule(detail.membership, dates(detail.batch), today=today))
    return bool(oldest and oldest["due_date"] + datetime.timedelta(days=policy.installment_grace_days) < today)
