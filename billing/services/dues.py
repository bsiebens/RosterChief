"""Billing lifecycle: subscribe (optionally with a free trial) -> period (Due) -> payments or waive -> renewal.

Unpaid past grace: the club is first DOWNGRADED to the default plan (``ClubBilling.downgraded_at``), and archived
``BillingSettings.archive_after_days`` later if it still owes. Paying (or waiving) the last overdue period restores it.

Prices and dates are frozen when a period opens. Changing the plan of a billed club mid-period is a *plan change*
(``services/plan_change.py``): the running period is prorated, a credit note is issued and a new cycle starts on the new
plan; credit that results from it is carried forward on the club's credit ledger and applied to the next invoices
(``apply_credit`` below, called whenever a period opens). Grace is ``max(period_start, day the period was opened) + grace_days`` (see ``models.grace_until_for``).

Trials are a property of the club, not of a plan: ``subscribe(..., trial_days=N)`` gives the plan's features at once and
a free period of N days. The first paid period follows the day after, opened and invoiced in advance by the renewal job.
"""

import datetime
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from dateutil.relativedelta import relativedelta
from django.db import transaction
from django.db.models import Max, Q
from django.utils import timezone

from club.services import archive_club, restore_club
from features import services as feature_services
from features.models import Plan, Subscription

from ..models import EU_COUNTRIES, BillingSettings, ClubBilling, Due, DuePayment, PlanPrice, grace_until_for
from . import BillingError
from .invoices import clean_number, issue_invoice


def today() -> datetime.date:
    return timezone.localdate()


def price_on(plan: Plan, day: datetime.date) -> PlanPrice | None:
    """The price in force on ``day``; ``None`` means the plan cannot be billed (never "free")."""
    return PlanPrice.objects.filter(plan=plan, active_from__lte=day).order_by("-active_from").first()


def current_plan(club) -> Plan | None:
    sub = Subscription.objects.filter(club=club, ends_at__isnull=True).select_related("plan").first()
    return sub.plan if sub else None


def next_period_start(club) -> datetime.date:
    """The day after the last period ends (so lapsed time stays owed), or today for a club with no period yet."""
    last = Due.objects.filter(club=club).exclude(status=Due.CANCELLED).order_by("-period_end").first()
    return last.period_end + datetime.timedelta(days=1) if last else today()


def compute_vat(club, net: Decimal, settings: BillingSettings | None = None) -> tuple[Decimal, Decimal, bool]:
    """``(rate, vat_amount, reverse_charge)`` for ``net``.

    Reverse charge (rate 0) applies when the club has a VAT id and its legal country is an EU state other than the
    issuer's (the issuer's country must be set to tell). Otherwise the standard rate applies. A zero amount follows the
    same rules, so a free or trial period shows the rate it would carry (e.g. 21%) with a VAT amount of 0.00.
    """
    settings = settings or BillingSettings.current()
    zero = Decimal("0")
    issuer_country = str(settings.country or "")
    club_country = str(getattr(club, "legal_country", "") or "")
    if club.vat_id.strip() and issuer_country and club_country in EU_COUNTRIES and club_country != issuer_country:
        return zero, zero, True
    rate = Decimal(settings.vat_rate)
    return rate, (net * rate / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), False


def _terms(plan: Plan):
    try:
        return plan.terms
    except Plan.terms.RelatedObjectDoesNotExist:  # pragma: no cover - created by signal
        raise BillingError(f"Plan '{plan.code}' has no billing terms.") from None


@dataclass(frozen=True)
class PeriodQuote:
    """What a period for ``plan`` starting ``start`` would be: dates, frozen price and VAT. Shared by ``open_period`` and
    the plan-change preview, so the numbers shown before a change are the numbers used by it."""

    plan: Plan
    start: datetime.date
    end: datetime.date
    net: Decimal
    vat_rate: Decimal
    vat_amount: Decimal
    reverse_charge: bool
    grace_until: datetime.date

    @property
    def total(self) -> Decimal:
        return self.net + self.vat_amount


def quote_period(club, plan: Plan, start: datetime.date, *, is_trial: bool = False, period_end: datetime.date | None = None) -> PeriodQuote:
    price = price_on(plan, start)
    if price is None:
        raise BillingError(f"Plan '{plan.code}' has no price on {start}.")
    terms = _terms(plan)
    if period_end is None:  # a trial passes its own end; otherwise the plan's period length applies
        period_end = start + relativedelta(months=terms.duration_months) - datetime.timedelta(days=1)
    net = Decimal("0") if is_trial else price.amount
    rate, vat_amount, reverse_charge = compute_vat(club, net)
    return PeriodQuote(plan, start, period_end, net, rate, vat_amount, reverse_charge, grace_until_for(start, terms.grace_days))


@transaction.atomic
def open_period(
    club, *, start: datetime.date | None = None, plan: Plan | None = None, is_trial: bool = False, period_end: datetime.date | None = None, actor=None, credit_reference: str | None = None, credit_note=None,
    invoice_number: str | None = None, invoice: bool = True
) -> Due:
    """Open (and invoice) the billing period beginning ``start``.

    ``is_trial`` makes it a free trial period; its ``period_end`` is then passed explicitly (otherwise the plan's
    ``duration_months`` applies). Opening the first paid period after a trial converts the club's TRIAL subscription to
    ACTIVE on the same plan. We convert when the period is *opened* (in advance), not when it starts: the plan is the
    same, so entitlements do not change, and the subscription never has a trial end date that could lapse and drop the
    club to the default plan before the paid period exists. ``ClubBilling.trial_ends_at`` is cleared at the same time.

    Once invoiced, any credit the club holds is applied to the period (``credit_reference`` / ``credit_note`` only label
    that application).

    ``invoice_number`` gives the period's invoice a custom number instead of the next automatic one (only the manual
    actions pass it; the unattended renewal never does). It is validated first, so a bad number changes nothing:
    ``ValueError`` = invalid format (422), ``BillingError`` = already in use.

    ``invoice=False`` opens the period only: no invoice is issued (so no number is taken and no credit is applied yet). The
    invoice is created afterwards with ``issue_period_invoice`` (the manual flow while automatic invoicing is off).
    """
    custom = clean_number(invoice_number) if invoice_number is not None else None
    if not invoice and custom is not None:
        raise ValueError("A period opened without an invoice cannot take an invoice number yet: create the invoice afterwards.")
    billing = ClubBilling.objects.filter(club=club).first()
    if billing is None:
        raise BillingError("This club is not being billed yet. Subscribe it to a plan first.")
    start = start or next_period_start(club)
    plan = plan or current_plan(club)
    if plan is None:
        raise BillingError("This club has no plan.")
    if Due.objects.filter(club=club, period_start=start).exists():
        raise BillingError(f"A period starting {start} already exists.")
    quote = quote_period(club, plan, start, is_trial=is_trial, period_end=period_end)
    if billing.trial_ends_at and start > billing.trial_ends_at and not is_trial:
        feature_services.convert_trial(club, actor=actor, reason="Trial ended: first paid period opened")
        billing.trial_ends_at = None
        billing.save(update_fields=["trial_ends_at", "modified"])
    due = Due.objects.create(
        club=club,
        plan=plan,
        amount=quote.net,
        vat_rate=quote.vat_rate,
        vat_amount=quote.vat_amount,
        reverse_charge=quote.reverse_charge,
        period_start=start,
        period_end=quote.end,
        grace_until=quote.grace_until,
        is_trial=is_trial,
    )
    if due.total == 0:
        due.status, due.paid_at = Due.PAID, timezone.now()  # a free or trial period never trips downgrading or archiving
        due.save(update_fields=["status", "paid_at", "modified"])
    if invoice:
        issue_invoice(due, number=custom)
        if due.total > 0:
            from .credit import apply_credit

            if apply_credit(due, reference=credit_reference, credit_note=credit_note) > 0:
                due.refresh_from_db()
    return due


@transaction.atomic
def issue_period_invoice(due: Due, *, number: str | None = None, credit_reference: str | None = None):
    """Create the invoice of a period that was opened without one, then apply any credit the club holds, like ``open_period`` does.

    Idempotent: a period that already has an invoice returns it. ``number`` is a custom number (validated first; ``ValueError`` = format,
    ``BillingError`` = already in use) and never touches the automatic counter.
    """
    existing = due.invoice
    if existing:
        return existing
    invoice = issue_invoice(due, number=number)
    if due.total > 0:
        from .credit import apply_credit

        if apply_credit(due, reference=credit_reference) > 0:
            due.refresh_from_db()
    return invoice


@transaction.atomic
def subscribe(club, plan: Plan, *, start: datetime.date | None = None, auto_renew: bool = True, auto_archive: bool = True, notes: str = "", trial_days: int | None = None, actor=None, invoice_number: str | None = None):
    """Bill ``club`` on ``plan``. Returns ``(billing, due)``; ``due`` is None when only the plan changed.

    First time: opens the first period. Already billed: just moves the club to the new plan without touching the periods; the platform API
    refuses this for billed clubs and points to ``plan_change.execute_plan_change`` (proration, credit notes, new cycle).

    ``trial_days`` (only for a club that is not billed yet): the first period is a free trial of that many days on
    ``plan``; the plan's features apply at once and the club simply continues on the same plan afterwards.

    ``invoice_number``: custom number for the first period's invoice (the trial's zero invoice included); validated up
    front. Ignored when no period opens.
    """
    if invoice_number is not None:
        clean_number(invoice_number)
    if not plan.is_active:
        raise BillingError("That plan is not active.")
    if trial_days is not None:
        if not 1 <= trial_days <= 365:
            raise BillingError("A trial lasts 1 to 365 days.")
        if ClubBilling.objects.filter(club=club).exists():
            raise BillingError("A trial can only be started for a club that is not being billed yet.")
        return _start_trial(club, plan, trial_days, start=start or today(), auto_renew=auto_renew, auto_archive=auto_archive, notes=notes, actor=actor, invoice_number=invoice_number)
    billing, created = ClubBilling.objects.get_or_create(club=club)
    billing.auto_renew, billing.auto_archive, billing.notes = auto_renew, auto_archive, notes
    billing.trial_ends_at = None
    billing.save()
    feature_services.subscribe(club, plan, actor=actor, reason="Billing subscription")
    if created or not Due.objects.filter(club=club).exists():
        return billing, open_period(club, start=start or today(), plan=plan, actor=actor, invoice_number=invoice_number)
    return billing, None


def _start_trial(club, plan: Plan, trial_days: int, *, start, auto_renew, auto_archive, notes, actor, invoice_number=None):
    trial_ends_at = start + datetime.timedelta(days=trial_days - 1)
    billing = ClubBilling.objects.create(club=club, auto_renew=auto_renew, auto_archive=auto_archive, notes=notes, trial_ends_at=trial_ends_at)
    # Subscription.trial_ends_at stays empty on purpose: a dated TRIAL subscription stops granting access on that date,
    # which would drop the club to the default plan before its first paid period starts. The billing trial end rules.
    feature_services.subscribe(club, plan, status=Subscription.TRIAL, actor=actor, reason=f"Trial started ({trial_days} days)")
    due = open_period(club, start=start, plan=plan, is_trial=True, period_end=trial_ends_at, actor=actor, invoice_number=invoice_number)
    return billing, due


def _resettle(due: Due) -> Due:
    """Re-derive amount_paid and status from the payments (never incrementally).

    ``amount_paid`` is what the period keeps: the payments minus the part moved to the club's credit by a plan change
    (``credit_transferred``). The payments themselves are never changed, so the history stays complete."""
    paid = sum((p.amount for p in due.payments.all()), Decimal("0")) - due.credit_transferred
    due.amount_paid = paid
    if paid >= due.total:
        due.status = Due.PAID
        due.paid_at = due.paid_at or timezone.now()
    elif paid > 0:
        due.status, due.paid_at = Due.PARTIAL, None
    else:
        due.status, due.paid_at = Due.UNPAID, None
    due.save(update_fields=["amount_paid", "status", "paid_at", "modified"])
    restore_if_settled(due.club)
    return due


@transaction.atomic
def record_payment(due: Due, *, amount: Decimal, method: str = DuePayment.BANK_TRANSFER, reference: str = "", paid_at=None, note: str = "", user=None) -> DuePayment:
    due = Due.objects.select_for_update().get(pk=due.pk)
    if due.status in (Due.WAIVED, Due.CANCELLED):
        raise BillingError(f"A {due.status} period cannot take payments.")
    if method == DuePayment.CREDIT:
        raise BillingError("Club credit is applied automatically; it cannot be recorded by hand.")
    if amount <= 0:
        raise BillingError("The payment must be more than zero.")
    if amount > due.balance:
        raise BillingError(f"The payment ({amount}) is more than the open balance ({due.balance}).")
    payment = DuePayment.objects.create(due=due, amount=amount, method=method, reference=reference, paid_at=paid_at or timezone.now(), note=note, recorded_by=user)
    _resettle(due)
    return payment


@transaction.atomic
def remove_payment(payment: DuePayment) -> Due:
    due = Due.objects.select_for_update().get(pk=payment.due_id)
    if due.status == Due.CANCELLED:
        raise BillingError("A cancelled period cannot be changed.")
    if payment.method == DuePayment.CREDIT:
        raise BillingError("Credit applied from the club's credit cannot be removed.")
    if due.credit_transferred > 0:
        raise BillingError("Part of this period's payments was moved to the club's credit by a plan change, so its payments can no longer be removed.")
    payment.delete()
    return _resettle(due)


@transaction.atomic
def waive(due: Due) -> Due:
    due = Due.objects.select_for_update().get(pk=due.pk)
    if due.payments.exists():
        raise BillingError("A period with payments cannot be waived.")
    if due.status != Due.UNPAID:
        raise BillingError("Only an unpaid period can be waived.")
    due.status = Due.WAIVED
    due.save(update_fields=["status", "modified"])
    restore_if_settled(due.club)
    return due


@transaction.atomic
def reactivate(club, *, start: datetime.date | None = None, actor=None, invoice_number: str | None = None) -> Due:
    """Restore an archived club and open the next period (default: continuing from the last, so the lapse is still owed)."""
    if invoice_number is not None:
        clean_number(invoice_number)  # before the club is restored
    restore_club(club)
    return open_period(club, start=start, actor=actor, invoice_number=invoice_number)


# --- queries used by reminders, renewal and archiving ----------------------------------------------------------------


def owing_dues():
    return Due.objects.filter(status__in=Due.OWING).select_related("club", "plan")


def dues_in_grace(day: datetime.date | None = None):
    day = day or today()
    return owing_dues().filter(period_start__lte=day, grace_until__gte=day)


def dues_overdue(day: datetime.date | None = None):
    return owing_dues().filter(grace_until__lt=day or today())


def archivable_clubs(day: datetime.date | None = None, now: datetime.datetime | None = None):
    """Active clubs with an overdue period, auto-archive on, downgraded for at least ``archive_after_days`` days."""
    from club.models import Club

    cutoff = (now or timezone.now()) - datetime.timedelta(days=BillingSettings.current().archive_after_days)
    ids = dues_overdue(day).values("club_id")
    return Club.objects.filter(pk__in=ids, archived_at__isnull=True, billing__auto_archive=True, billing__downgraded_at__lte=cutoff)


def clubs_to_downgrade(day: datetime.date | None = None):
    """Active billed clubs with an overdue period that are not downgraded yet (independent of ``auto_archive``)."""
    from club.models import Club

    return Club.objects.filter(pk__in=dues_overdue(day).values("club_id"), archived_at__isnull=True, billing__isnull=False, billing__downgraded_at__isnull=True)


def clubs_to_restore(day: datetime.date | None = None):
    """Downgraded clubs that no longer have an overdue period."""
    from club.models import Club

    return Club.objects.filter(billing__downgraded_at__isnull=False).exclude(pk__in=dues_overdue(day).values("club_id"))


def downgrade_club(club) -> bool:
    return bool(ClubBilling.objects.filter(club=club, downgraded_at__isnull=True).update(downgraded_at=timezone.now()))


def restore_if_settled(club, day: datetime.date | None = None) -> bool:
    """Lift the downgrade when the club has no overdue period left. Returns whether anything was cleared."""
    from controlpanel import audit

    if dues_overdue(day).filter(club=club).exists():
        return False
    cleared = ClubBilling.objects.filter(club=club, downgraded_at__isnull=False).update(downgraded_at=None)
    if cleared:
        audit.record(None, "billing.club_restored_after_payment", club=club, target=club)
    return bool(cleared)


def lapsed_billings(day: datetime.date | None = None):
    """Billed, active clubs whose last (non-cancelled) period ended before ``day`` with nothing after it."""
    day = day or today()
    last_end = Max("club__dues__period_end", filter=~Q(club__dues__status=Due.CANCELLED))
    return ClubBilling.objects.filter(club__archived_at__isnull=True).annotate(last_end=last_end).filter(last_end__lt=day)


def subscriptions_due_for_renewal(day: datetime.date | None = None) -> list:
    """Billing records whose next period should be opened now. Idempotent.

    auto_renew clubs: once the plan's renewal lead time is reached. Clubs without auto_renew are only picked up once
    LAPSED (last period ended, nothing after it), so the normal overdue -> downgrade -> archive flow applies to them.
    """
    day = day or today()
    due = []
    for billing in ClubBilling.objects.filter(club__archived_at__isnull=True).select_related("club"):
        plan = current_plan(billing.club)
        if plan is None:
            continue
        last = Due.objects.filter(club=billing.club).exclude(status=Due.CANCELLED).order_by("-period_end").first()
        if billing.auto_renew:
            if last is None or last.period_end <= day + datetime.timedelta(days=_terms(plan).renewal_lead_days):
                due.append(billing)
        elif last is not None and last.period_end < day:
            due.append(billing)
    return due


def upcoming_renewals(day: datetime.date | None = None) -> list[dict]:
    """Billed, active clubs: when the next period starts and when that invoice is due to open.

    Includes every auto-renew club, and clubs without auto-renew only once they have ``lapsed``.
    """
    day = day or today()
    rows = []
    for billing in ClubBilling.objects.filter(club__archived_at__isnull=True).select_related("club"):
        club = billing.club
        plan = current_plan(club)
        if plan is None:
            continue
        periods = Due.objects.filter(club=club).exclude(status=Due.CANCELLED)
        last = periods.order_by("-period_end").first()
        lapsed = last is not None and last.period_end < day
        if not billing.auto_renew and not lapsed:
            continue
        current = periods.filter(period_start__lte=day).order_by("-period_start").first()
        if current:
            next_start = current.period_end + datetime.timedelta(days=1)
        else:  # only future periods (or none): the first of those is the "next" one
            first = periods.order_by("period_start").first()
            next_start = first.period_start if first else day
        lead = _terms(plan).renewal_lead_days
        rows.append(
            {
                "club": club,
                "plan": plan,
                "current_period_end": current.period_end if current else None,
                "next_period_start": next_start,
                "invoice_opens_on": next_start - datetime.timedelta(days=lead),
                "renewal_lead_days": lead,
                "opened": periods.filter(period_start=next_start).exists(),
                "trial_ends_at": billing.trial_ends_at,
                "lapsed": lapsed,
                "downgraded_at": billing.downgraded_at,
            }
        )
    return sorted(rows, key=lambda r: (r["invoice_opens_on"], r["club"].name))


def renew(billing: ClubBilling, *, actor=None) -> Due:
    return open_period(billing.club, actor=actor)


def archive_overdue(club, *, actor=None):
    return archive_club(club)
