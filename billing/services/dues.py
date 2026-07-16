"""The billing lifecycle. Views and the archive command go through here, never through the
models directly — a Due whose amount_paid disagrees with its payments is a wrong invoice.
"""

from datetime import date, timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import DateField, OuterRef, Subquery, Sum
from django.utils import timezone

from billing.models import RENEWAL_LEAD_DAYS, ZERO, Due, DuePayment, Subscription, Tier
from billing.services import BillingError
from billing.services.invoices import issue_invoice


def subscribe(club, tier: Tier, *, start: date | None = None, auto_archive: bool = True, auto_renew: bool = True) -> Subscription:
    """Put a club on a tier and open its first period."""
    subscription, _created = Subscription.objects.update_or_create(club=club, defaults={"tier": tier, "auto_archive": auto_archive, "auto_renew": auto_renew})
    open_period(club, start=start)

    return subscription


def next_period_start(club, today: date | None = None) -> date:
    """Where the club's next period begins.

    The day after the last one ended — not today. A club that pays two months late has still
    used those two months, and restarting the clock at the payment date would quietly gift
    them away. Callers can override; that is what the start field on the renew form is for.
    """
    today = today or timezone.localdate()
    last = club.dues.exclude(status=Due.Status.CANCELLED).order_by("-period_end").first()

    return last.period_end + timedelta(days=1) if last else today


@transaction.atomic
def open_period(club, *, start: date | None = None, tier: Tier | None = None) -> Due:
    """Issue the next due for a club, snapshotting the tier and the price of the day."""
    subscription = getattr(club, "subscription", None)
    tier = tier or (subscription.tier if subscription else None)
    if tier is None:
        raise BillingError(f"{club} has no tier: put it on a subscription before billing it.")

    start = start or next_period_start(club)

    amount = tier.price_on(start)
    if amount is None:
        raise BillingError(f"{tier} has no price in force on {start:%d %b %Y}. Add one before opening the period.")

    if club.dues.filter(period_start=start).exists():
        raise BillingError(f"{club} is already billed for a period starting {start:%d %b %Y}.")

    due = Due.objects.create(club=club, tier=tier, amount=amount, period_start=start)
    issue_invoice(due)  # every period is billable the moment it opens

    return due


@transaction.atomic
def record_payment(due: Due, amount: Decimal, *, method=DuePayment.Method.BANK_TRANSFER, reference: str = "", paid_at=None, note: str = "", user=None) -> DuePayment:
    """Log money against a due and re-derive its status from the payments."""
    if due.status in (Due.Status.WAIVED, Due.Status.CANCELLED):
        raise BillingError(f"This period is {due.get_status_display()}; it cannot take a payment.")
    if amount <= ZERO:
        raise BillingError("A payment must be for a positive amount.")

    payment = DuePayment.objects.create(due=due, amount=amount, method=method, reference=reference, paid_at=paid_at or timezone.now(), note=note, recorded_by=user)
    _resettle(due)

    return payment


@transaction.atomic
def remove_payment(payment: DuePayment) -> None:
    """Undo a mis-keyed payment, then re-derive the due from what is left."""
    due = payment.due
    payment.delete()
    _resettle(due)


def _resettle(due: Due) -> None:
    """Recompute amount_paid and status from the payments on record.

    Summed from the payments rather than incremented: an increment drifts the moment a
    payment is edited or deleted, and the drift is invisible — the number still looks like
    money.
    """
    paid = due.payments.aggregate(total=Sum("amount"))["total"] or ZERO

    due.amount_paid = paid
    if paid >= due.amount:
        due.status = Due.Status.PAID
        due.paid_at = due.payments.order_by("-paid_at").first().paid_at
    elif paid > ZERO:
        due.status = Due.Status.PARTIAL
        due.paid_at = None
    else:
        due.status = Due.Status.UNPAID
        due.paid_at = None
    due.save(update_fields=["amount_paid", "status", "paid_at", "modified"])


@transaction.atomic
def waive(due: Due, *, note: str = "") -> Due:
    """Write a period off. It stops owing, and stops counting towards archiving."""
    if due.payments.exists():
        raise BillingError("This period has payments against it; remove them before waiving it.")

    due.status = Due.Status.WAIVED
    due.save(update_fields=["status", "modified"])

    return due


def owing_dues():
    return Due.objects.filter(status__in=Due.OWING)


def dues_in_grace(today: date | None = None):
    """Period over, unpaid, not yet archivable."""
    today = today or timezone.localdate()

    return owing_dues().filter(period_end__lt=today, grace_until__gte=today)


def dues_overdue(today: date | None = None):
    """Past grace: these are the clubs the archive command would take down."""
    today = today or timezone.localdate()

    return owing_dues().filter(grace_until__lt=today)


def archivable_clubs(today: date | None = None):
    """Clubs the archive command would act on: overdue, still live, and opted in.

    A club with auto_archive off is deliberately spared — that flag is how you keep a club
    you are negotiating with from being switched off overnight.
    """
    return dues_overdue(today).filter(club__archived_at__isnull=True, club__subscription__auto_archive=True).select_related("club", "tier").order_by("club__name")


@transaction.atomic
def reactivate(club, *, start: date | None = None) -> Due:
    """Bring an archived club back and bill it again.

    The new period defaults to continuing from the last one, so a lapsed year is still owed.
    Pass ``start`` to forgive the gap and begin today instead.
    """
    club.restore()

    return open_period(club, start=start)


def subscriptions_due_for_renewal(today: date | None = None, lead_days: int = RENEWAL_LEAD_DAYS):
    """Clubs whose next period should be issued now.

    Idempotent by construction: a club that has just been renewed has a latest period ending a
    year out, which is past the horizon, so it cannot be picked up twice. Running the job twice
    a day is harmless.

    A subscription with no period at all (its only due was cancelled) counts too — a club on a
    plan and billed for nothing is the leak this whole job exists to close.
    """
    today = today or timezone.localdate()
    horizon = today + timedelta(days=lead_days)

    latest_period_end = Subquery(
        Due.objects.filter(club=OuterRef("club")).exclude(status=Due.Status.CANCELLED).order_by("-period_end").values("period_end")[:1],
        output_field=DateField(),
    )

    subscriptions = Subscription.objects.filter(auto_renew=True, club__archived_at__isnull=True).select_related("club", "tier").annotate(latest_period_end=latest_period_end).order_by("club__name")

    return [subscription for subscription in subscriptions if subscription.latest_period_end is None or subscription.latest_period_end <= horizon]


def renew(subscription: Subscription) -> Due:
    """Open the club's next period, continuing from the last one."""
    return open_period(subscription.club)
