"""Switching a billed club's plan in the middle of a billing cycle: prorate the old period, start a new cycle.

``build_plan_change`` does ALL the arithmetic and validation and writes nothing; the API preview returns it as is.
``execute_plan_change`` runs the very same function inside a transaction (club billing row and dues locked) and then
applies exactly what it computed, so the numbers shown before a change cannot drift from the ones used by it.

What a change does, for the switch date D (default today; back-dating is allowed within the current period):

1. The period containing D is shortened to end on D-1 and prorated by days: ``net * used_days / total_days`` rounded
   to cents (half up), where ``used_days = D - period_start`` and ``total_days`` is the original length. VAT is
   recomputed at the period's own frozen rate (reverse charge stays reverse charge). The original figures stay on the
   Due (``original_*``); ``amount``/``vat_amount``/``period_end`` become the prorated values, so every balance and
   status rule keeps working on them. A trial period is only shortened (it costs nothing); a waived period is only
   shortened (nothing was owed).
2. If that period had an invoice with a total above zero, a credit note is issued for the reduction. The invoice is
   never edited.
3. Money. Payments are never changed or refunded. If the period is now cheaper than what was paid on it, the surplus
   is moved to the club's credit: ``Due.credit_transferred`` goes up by that amount (``amount_paid`` is always
   ``sum(payments) - credit_transferred``, so the period settles as paid with balance 0) and a positive ledger entry is
   written. An unpaid period just gets cheaper; a part-paid one has its open balance lowered first and only a real
   surplus becomes credit.
4. Periods that start after D and were already opened (invoiced in advance) are cancelled. Each with an invoice total
   above zero gets a credit note for the full invoice; what was paid on it becomes club credit the same way.
5. The club's subscription moves to the new plan, ``trial_ends_at`` is cleared, and a new period opens on D for the
   new plan (full length, price in force on D, VAT, normal invoice, normal grace rule). The credit the club now holds is
   applied to that invoice; any rest stays on the ledger for the next invoices.

If no period covers D (the club has lapsed: its last period ended before D), nothing is prorated and the new period
starts the day after the last period ended, exactly where renewal would have put it, so the lapsed time is still
billed (on the new plan). With no period at all, the new one starts on D (which must then be today).
"""

import datetime
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.utils import timezone

from features import services as feature_services
from features.models import Plan

from ..models import ClubBilling, ClubCreditEntry, CreditNote, Due, Invoice
from . import BillingError
from . import credit as credit_svc
from .credit_notes import issue_credit_note
from .dues import PeriodQuote, _resettle, current_plan, open_period, quote_period, today

CENT = Decimal("0.01")
ZERO = Decimal("0.00")
ONE_DAY = datetime.timedelta(days=1)


class PlanChangeDateError(BillingError):
    """The switch date is outside the allowed range (the API answers 422 on the ``effective_date`` field)."""


@dataclass(frozen=True)
class Money:
    net: Decimal
    vat: Decimal

    @property
    def total(self) -> Decimal:
        return self.net + self.vat


@dataclass
class OldPeriod:
    due: Due
    invoice: Invoice | None
    period_start: datetime.date
    period_end: datetime.date  # as it was before the change (``due`` itself is mutated when the change is executed)
    total_days: int
    used_days: int
    new_period_end: datetime.date
    original: Money
    prorated: Money
    prorates_money: bool  # False for a trial or waived period: shortened only, figures unchanged
    paid: Decimal
    credit_to_issue: Money | None
    overpayment: Decimal
    status_before: str
    status_after: str

    @property
    def unused_days(self) -> int:
        return self.total_days - self.used_days


@dataclass
class CancelledPeriod:
    due: Due
    invoice: Invoice | None
    total: Decimal
    paid: Decimal
    credit_to_issue: Money | None


@dataclass
class PlanChange:
    club: object
    plan_from: Plan | None
    plan_to: Plan
    effective_date: datetime.date
    old: OldPeriod | None
    cancelled: list[CancelledPeriod]
    quote: PeriodQuote
    credit_balance_before: Decimal
    credit_available: Decimal
    credit_applied: Decimal
    amount_payable: Decimal
    credit_balance_after: Decimal
    warnings: list[str] = field(default_factory=list)
    trial_replaced: bool = False

    @property
    def credit_notes_to_issue(self) -> list[tuple[Invoice, Money, Due]]:
        out = []
        if self.old and self.old.credit_to_issue:
            out.append((self.old.invoice, self.old.credit_to_issue, self.old.due))
        out += [(c.invoice, c.credit_to_issue, c.due) for c in self.cancelled if c.credit_to_issue]
        return out


@dataclass
class PlanChangeResult:
    change: PlanChange
    new_due: Due
    old_due: Due | None
    credit_notes: list[CreditNote]
    cancelled_dues: list[Due]
    billing: ClubBilling


def _q(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _invoice_of(due: Due) -> Invoice | None:
    return getattr(due, "invoice", None)


def prorate(due: Due, used_days: int, total_days: int) -> Money:
    """The prorated figures of ``due``: net by days (cents, half up), VAT at the period's own frozen rate."""
    net = _q(due.amount * used_days / total_days)
    vat = ZERO if (due.reverse_charge or due.vat_rate == 0 or net == 0) else _q(net * due.vat_rate / 100)
    return Money(net, vat)


def _date_bounds(dues: list[Due], day: datetime.date) -> tuple[datetime.date, datetime.date, str]:
    """``(earliest, latest, explanation)`` for the switch date."""
    started = [d for d in dues if d.period_start <= day]
    anchor = max(started, key=lambda d: d.period_start) if started else None
    if anchor is None:
        return day, day, "The club has no period yet, so the plan can only change today."
    if anchor.period_end >= day:  # a period is running: at least one day of it must have been used
        return anchor.period_start + ONE_DAY, day, f"The switch date must be after the current period's start ({anchor.period_start}) and not in the future."
    return anchor.period_end + ONE_DAY, day, f"The club's last period ended {anchor.period_end}; the switch date must be after that and not in the future."


def build_plan_change(club, plan: Plan, effective_date: datetime.date | None = None) -> PlanChange:
    """Validate and compute a plan change. No writes."""
    day = today()
    billing = ClubBilling.objects.filter(club=club).first()
    if billing is None:
        raise BillingError("This club is not billed. Use the features page to change its plan; billing only prorates billed clubs.")
    if club.is_archived:
        raise BillingError("This club is archived. Restore it before changing its plan.")
    if not plan.is_active:
        raise BillingError("That plan is not active.")
    plan_from = current_plan(club)
    if plan_from is not None and plan_from.pk == plan.pk:
        raise BillingError("The club is already on that plan.")

    switch = effective_date or day
    dues = list(Due.objects.filter(club=club).exclude(status=Due.CANCELLED).select_related("plan", "club").order_by("period_start"))
    low, high, why = _date_bounds(dues, day)
    if switch > day:
        raise PlanChangeDateError(f"The switch date cannot be in the future. {why}")
    if not low <= switch <= high:
        if low > high:
            raise PlanChangeDateError(f"The current period started today, so there is nothing to prorate yet. Change the plan from tomorrow. {why}")
        raise PlanChangeDateError(f"The switch date must be between {low} and {high}. {why}")

    covering = [d for d in dues if d.period_start <= switch <= d.period_end]
    futures = [d for d in dues if d.period_start > switch]
    warnings: list[str] = []
    old = None
    if covering:
        old = _plan_old_period(covering[-1], switch)
        new_start = switch
    else:
        last = max((d for d in dues if d.period_start <= switch), key=lambda d: d.period_start, default=None)
        new_start = last.period_end + ONE_DAY if last else switch
        if last is not None:
            warnings.append(f"No period covers {switch} (the last one ended {last.period_end}): nothing is prorated and the new period starts {new_start}, so the time in between is billed on the new plan.")
    if Due.objects.filter(club=club, period_start=new_start).exists():
        raise BillingError(f"A period starting {new_start} already exists (possibly a cancelled one).")
    quote = quote_period(club, plan, new_start)  # BillingError when the plan has no price on that day

    cancelled = [_plan_cancelled(d) for d in futures]
    trial_replaced = bool(old and old.due.is_trial)
    if trial_replaced:
        warnings.append(f"The free trial ends on {old.new_period_end}; the club is billed on {plan.name} from {new_start}.")
    elif billing.trial_ends_at:
        warnings.append("The club's trial end date is cleared.")
    if old and old.due.status == Due.WAIVED:
        warnings.append("The current period was waived: it is shortened but nothing is credited.")
    if cancelled:
        listing = ", ".join(f"{c.due.period_start} to {c.due.period_end}" for c in cancelled)
        warnings.append(f"{len(cancelled)} period(s) already opened in advance will be cancelled ({listing}); their invoices are credited and anything paid on them becomes club credit.")
    if billing.downgraded_at is not None:
        warnings.append("The club is currently downgraded because of an overdue period. The new plan's features apply once nothing is overdue.")
    if switch < day:
        warnings.append(f"The switch date is back-dated: billing starts {new_start}, but the club's features change today.")

    balance_before = credit_svc.credit_balance(club)
    new_credit = (old.overpayment if old else ZERO) + sum((c.paid for c in cancelled), ZERO)
    available = balance_before + new_credit
    applied = min(available, quote.total) if quote.total > 0 else ZERO
    return PlanChange(
        club=club,
        plan_from=plan_from,
        plan_to=plan,
        effective_date=switch,
        old=old,
        cancelled=cancelled,
        quote=quote,
        credit_balance_before=balance_before,
        credit_available=available,
        credit_applied=applied,
        amount_payable=quote.total - applied,
        credit_balance_after=available - applied,
        warnings=warnings,
        trial_replaced=trial_replaced,
    )


def _plan_old_period(due: Due, switch: datetime.date) -> OldPeriod:
    if due.is_prorated:
        raise BillingError("That period was already prorated by an earlier plan change.")
    total_days = (due.period_end - due.period_start).days + 1
    used_days = (switch - due.period_start).days
    original = Money(due.amount, due.vat_amount)
    money = not due.is_trial and due.status != Due.WAIVED
    prorated = prorate(due, used_days, total_days) if money else original
    invoice = _invoice_of(due)
    paid = due.amount_paid
    credit_to_issue = None
    if money and invoice is not None and original.total > 0 and original.total - prorated.total > 0:
        credit_to_issue = Money(original.net - prorated.net, original.vat - prorated.vat)
    overpayment = max(ZERO, paid - prorated.total) if money else ZERO
    if not money:
        status_after = due.status
    elif paid - overpayment >= prorated.total:
        status_after = Due.PAID
    else:
        status_after = Due.PARTIAL if paid > 0 else Due.UNPAID
    return OldPeriod(
        due=due,
        invoice=invoice,
        period_start=due.period_start,
        period_end=due.period_end,
        total_days=total_days,
        used_days=used_days,
        new_period_end=switch - ONE_DAY,
        original=original,
        prorated=prorated,
        prorates_money=money,
        paid=paid,
        credit_to_issue=credit_to_issue,
        overpayment=overpayment,
        status_before=due.status,
        status_after=status_after,
    )


def _plan_cancelled(due: Due) -> CancelledPeriod:
    invoice = _invoice_of(due)
    credit = Money(due.amount, due.vat_amount) if invoice is not None and due.status != Due.WAIVED and due.total > 0 else None
    return CancelledPeriod(due=due, invoice=invoice, total=due.total, paid=due.amount_paid, credit_to_issue=credit)


@transaction.atomic
def execute_plan_change(club, plan: Plan, *, effective_date: datetime.date | None = None, actor=None) -> PlanChangeResult:
    """Apply a plan change atomically. Raises ``BillingError`` (or ``PlanChangeDateError``) before touching anything."""
    credit_svc.lock(club)
    list(Due.objects.select_for_update().filter(club=club))  # lock the periods
    change = build_plan_change(club, plan, effective_date)
    now = timezone.now()
    notes: list[CreditNote] = []
    old_due = None

    old = change.old
    if old is not None:
        due = old_due = old.due
        due.original_amount, due.original_vat_amount, due.original_period_end, due.prorated_at = due.amount, due.vat_amount, due.period_end, now
        due.amount, due.vat_amount, due.period_end = old.prorated.net, old.prorated.vat, old.new_period_end
        due.credit_transferred += old.overpayment
        due.save(update_fields=["original_amount", "original_vat_amount", "original_period_end", "prorated_at", "amount", "vat_amount", "period_end", "credit_transferred", "modified"])
        note = None
        if old.credit_to_issue:
            note = issue_credit_note(
                old.invoice,
                net=old.credit_to_issue.net,
                vat=old.credit_to_issue.vat,
                reason=f"Plan change: unused time {change.effective_date} to {old.due.original_period_end} on {due.plan.name}",
                credited_from=change.effective_date,
                credited_to=due.original_period_end,
            )
            notes.append(note)
        if old.overpayment > 0:
            credit_svc.add_credit(club, old.overpayment, ClubCreditEntry.OVERPAYMENT, due=due, credit_note=note, note=f"Paid more than the prorated period ({old.prorated.total}) costs")
        if old.prorates_money:
            _resettle(due)  # amount_paid = payments - credit_transferred; status follows

    cancelled_dues = []
    for item in change.cancelled:
        due = item.due
        note = None
        if item.credit_to_issue:
            note = issue_credit_note(
                item.invoice,
                net=item.credit_to_issue.net,
                vat=item.credit_to_issue.vat,
                reason=f"Plan change: period {due.period_start} to {due.period_end} on {due.plan.name} cancelled",
                credited_from=due.period_start,
                credited_to=due.period_end,
            )
            notes.append(note)
        due.status, due.credit_transferred, due.amount_paid = Due.CANCELLED, due.credit_transferred + item.paid, ZERO
        due.save(update_fields=["status", "credit_transferred", "amount_paid", "modified"])
        if item.paid > 0:
            credit_svc.add_credit(club, item.paid, ClubCreditEntry.CANCELLED, due=due, credit_note=note, note=f"Payment on cancelled period {due.period_start} to {due.period_end}")
        cancelled_dues.append(due)

    billing = ClubBilling.objects.get(club=club)
    billing.trial_ends_at = None
    billing.save(update_fields=["trial_ends_at", "modified"])
    feature_services.subscribe(club, plan, actor=actor, reason="Plan change")

    reference = ", ".join(n.number for n in notes) if notes and change.credit_balance_before == 0 else None
    new_due = open_period(club, start=change.quote.start, plan=plan, actor=actor, credit_reference=reference, credit_note=notes[-1] if notes else None)
    new_due.refresh_from_db()
    if new_due.amount_paid != change.credit_applied or new_due.total != change.quote.total:  # the preview and the change must agree
        raise RuntimeError(f"Plan change drifted from its preview: credit {new_due.amount_paid} != {change.credit_applied}")
    return PlanChangeResult(change=change, new_due=new_due, old_due=old_due, credit_notes=notes, cancelled_dues=cancelled_dues, billing=billing)
