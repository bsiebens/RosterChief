"""Billing lifecycle: subscribe or trial -> period (Due) -> payments or waive -> renewal; unpaid past grace archives.

Prices and dates are frozen when a period opens. There is no proration: a plan change mid-period applies from the
next period. Grace counts from the period start.
"""

import datetime
from decimal import Decimal

from dateutil.relativedelta import relativedelta
from django.db import transaction
from django.utils import timezone

from club.services import archive_club, restore_club
from features import services as feature_services
from features.models import Plan, Subscription

from ..models import ClubBilling, Due, DuePayment, PlanPrice
from . import BillingError
from .invoices import issue_invoice


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


def _terms(plan: Plan):
    try:
        return plan.terms
    except Plan.terms.RelatedObjectDoesNotExist:  # pragma: no cover - created by signal
        raise BillingError(f"Plan '{plan.code}' has no billing terms.") from None


@transaction.atomic
def open_period(club, *, start: datetime.date | None = None, plan: Plan | None = None, is_trial: bool = False, actor=None) -> Due:
    """Open (and invoice) the billing period beginning ``start``. After a trial ends the club converts to its post-trial plan."""
    billing = ClubBilling.objects.filter(club=club).first()
    if billing is None:
        raise BillingError("This club is not being billed yet. Subscribe it to a plan first.")
    start = start or next_period_start(club)
    plan = plan or current_plan(club)
    if plan is None:
        raise BillingError("This club has no plan.")
    if billing.trial_ends_at and start > billing.trial_ends_at:
        plan = billing.post_trial_plan
        billing.trial_ends_at = billing.post_trial_plan = None
        billing.save(update_fields=["trial_ends_at", "post_trial_plan", "modified"])
        feature_services.subscribe(club, plan, actor=actor, reason="Trial ended")
        is_trial = False
    if Due.objects.filter(club=club, period_start=start).exists():
        raise BillingError(f"A period starting {start} already exists.")
    price = price_on(plan, start)
    if price is None:
        raise BillingError(f"Plan '{plan.code}' has no price on {start}.")
    due = Due.objects.create(club=club, plan=plan, amount=price.amount, period_start=start, is_trial=is_trial or _terms(plan).is_trial)
    if due.amount == 0:
        due.status, due.paid_at = Due.PAID, timezone.now()  # a free or trial period never trips archiving
        due.save(update_fields=["status", "paid_at", "modified"])
    issue_invoice(due)
    return due


@transaction.atomic
def subscribe(club, plan: Plan, *, start: datetime.date | None = None, auto_renew: bool = True, auto_archive: bool = True, notes: str = "", actor=None):
    """Bill ``club`` on ``plan``. Returns ``(billing, due)``; ``due`` is None when only the plan changed.

    First time: opens the first period. Already billed: just moves the club to the new plan (the current period keeps
    its amount; the new rate applies from the next period). A trial in progress is replaced.
    """
    if not plan.is_active:
        raise BillingError("That plan is not active.")
    if _terms(plan).is_trial:
        raise BillingError("Use start_trial for a trial plan.")
    billing, created = ClubBilling.objects.get_or_create(club=club)
    billing.auto_renew, billing.auto_archive, billing.notes = auto_renew, auto_archive, notes
    billing.trial_ends_at = billing.post_trial_plan = None
    billing.save()
    feature_services.subscribe(club, plan, actor=actor, reason="Billing subscription")
    if created or not Due.objects.filter(club=club).exists():
        return billing, open_period(club, start=start or today(), plan=plan, actor=actor)
    return billing, None


@transaction.atomic
def start_trial(club, trial_plan: Plan, post_trial_plan: Plan, *, start: datetime.date | None = None, auto_renew: bool = True, auto_archive: bool = True, actor=None):
    """Start a free trial; the trial's length comes from its plan, and the club converts to ``post_trial_plan`` after it."""
    if ClubBilling.objects.filter(club=club).exists():
        raise BillingError("This club is already being billed.")
    if not _terms(trial_plan).is_trial or not trial_plan.is_active:
        raise BillingError("The trial plan must be an active trial plan.")
    if _terms(post_trial_plan).is_trial or not post_trial_plan.is_active:
        raise BillingError("The plan after the trial must be an active, non-trial plan.")
    start = start or today()
    trial_ends_at = start + relativedelta(months=trial_plan.terms.duration_months) - datetime.timedelta(days=1)
    billing = ClubBilling.objects.create(club=club, auto_renew=auto_renew, auto_archive=auto_archive, trial_ends_at=trial_ends_at, post_trial_plan=post_trial_plan)
    entitlements_end = timezone.make_aware(datetime.datetime.combine(trial_ends_at + datetime.timedelta(days=1), datetime.time.min))
    feature_services.subscribe(club, trial_plan, status=Subscription.TRIAL, trial_ends_at=entitlements_end, actor=actor, reason="Trial started")
    due = open_period(club, start=start, plan=trial_plan, is_trial=True, actor=actor)
    return billing, due


def _resettle(due: Due) -> Due:
    """Re-derive amount_paid and status from the payments (never incrementally)."""
    paid = sum((p.amount for p in due.payments.all()), Decimal("0"))
    due.amount_paid = paid
    if paid >= due.amount:
        due.status = Due.PAID
        due.paid_at = due.paid_at or timezone.now()
    elif paid > 0:
        due.status, due.paid_at = Due.PARTIAL, None
    else:
        due.status, due.paid_at = Due.UNPAID, None
    due.save(update_fields=["amount_paid", "status", "paid_at", "modified"])
    return due


@transaction.atomic
def record_payment(due: Due, *, amount: Decimal, method: str = DuePayment.BANK_TRANSFER, reference: str = "", paid_at=None, note: str = "", user=None) -> DuePayment:
    due = Due.objects.select_for_update().get(pk=due.pk)
    if due.status in (Due.WAIVED, Due.CANCELLED):
        raise BillingError(f"A {due.status} period cannot take payments.")
    if amount <= 0:
        raise BillingError("The payment must be more than zero.")
    payment = DuePayment.objects.create(due=due, amount=amount, method=method, reference=reference, paid_at=paid_at or timezone.now(), note=note, recorded_by=user)
    _resettle(due)
    return payment


@transaction.atomic
def remove_payment(payment: DuePayment) -> Due:
    due = Due.objects.select_for_update().get(pk=payment.due_id)
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
    return due


@transaction.atomic
def reactivate(club, *, start: datetime.date | None = None, actor=None) -> Due:
    """Restore an archived club and open the next period (default: continuing from the last, so the lapse is still owed)."""
    restore_club(club)
    return open_period(club, start=start, actor=actor)


# --- queries used by reminders, renewal and archiving ----------------------------------------------------------------


def owing_dues():
    return Due.objects.filter(status__in=Due.OWING).select_related("club", "plan")


def dues_in_grace(day: datetime.date | None = None):
    day = day or today()
    return owing_dues().filter(period_start__lte=day, grace_until__gte=day)


def dues_overdue(day: datetime.date | None = None):
    return owing_dues().filter(grace_until__lt=day or today())


def archivable_clubs(day: datetime.date | None = None):
    """Active clubs with an overdue period whose billing allows auto-archiving."""
    from club.models import Club

    ids = dues_overdue(day).values("club_id")
    return Club.objects.filter(pk__in=ids, archived_at__isnull=True, billing__auto_archive=True)


def subscriptions_due_for_renewal(day: datetime.date | None = None) -> list:
    """Billing records whose next period should be opened now (renewal lead reached). Idempotent."""
    day = day or today()
    due = []
    for billing in ClubBilling.objects.filter(auto_renew=True, club__archived_at__isnull=True).select_related("club"):
        plan = current_plan(billing.club)
        if plan is None:
            continue
        last = Due.objects.filter(club=billing.club).exclude(status=Due.CANCELLED).order_by("-period_end").first()
        if last is None or last.period_end <= day + datetime.timedelta(days=_terms(plan).renewal_lead_days):
            due.append(billing)
    return due


def renew(billing: ClubBilling, *, actor=None) -> Due:
    return open_period(billing.club, actor=actor)


def archive_overdue(club, *, actor=None):
    return archive_club(club)
