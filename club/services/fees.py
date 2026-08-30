"""Recording money received against a membership's fee.

Mirrors billing.services.dues.record_payment for a different kind of money: a
member's own club fee, not the club's platform subscription. amount_paid is kept in
step here, never recomputed by re-aggregating FeePayment on every read.
"""

from decimal import Decimal

from django.db.models import F
from django.utils import timezone

from club.models import ClubMembership, FeePayment


def effective_fee_amount(membership, when=None):
    """What's actually owed, accounting for an early-payment discount
    (membership.early_payment_deadline/early_payment_discount, set by
    registration.services.pricing when a matched Product.early_bird_discount_*
    condition can't be confirmed until the fee is paid). ``when`` defaults to
    today; record_payment/sync_fee_status pass the triggering payment's own
    paid_at date instead, so a payment made before the deadline is judged
    against it even if recorded/backdated later."""
    when = when or timezone.localdate()
    if membership.early_payment_deadline is not None and when <= membership.early_payment_deadline:
        return max(membership.fee_amount - membership.early_payment_discount, Decimal("0.00"))
    return membership.fee_amount


def remaining_balance(membership, when=None):
    return max(effective_fee_amount(membership, when) - membership.amount_paid, Decimal("0.00"))


def early_payment_offer(membership, when=None):
    """Whether an early-payment discount is still live for ``membership`` --
    ``None`` once its deadline has passed (or none was ever set), otherwise a
    dict with ``deadline``, ``discount`` and ``discounted_total`` (fee_amount
    minus the discount, floored at 0, same arithmetic as effective_fee_amount
    -- this is the single place both read that condition from, so the mobile
    Payments & dues page and the registration status page can never disagree
    about whether the offer is still on)."""
    when = when or timezone.localdate()
    if membership.early_payment_deadline is None or when > membership.early_payment_deadline:
        return None
    return {
        "deadline": membership.early_payment_deadline,
        "discount": membership.early_payment_discount,
        "discounted_total": max(membership.fee_amount - membership.early_payment_discount, Decimal("0.00")),
    }


def open_dues_rows(club, people, season):
    """Every season-dues row still owed by ``people`` in ``season`` -- shared by
    mobile's Home dues card and its Payments & dues screen so the two never
    drift out of sync on what counts as "still open". WAIVED and CANCELLED
    memberships and fully-paid balances are excluded -- a cancelled
    membership has no active claim on the family any more, whatever balance
    it was left carrying."""
    if season is None or not people:
        return []

    memberships = (
        ClubMembership.objects.filter(club=club, member__in=people, season=season)
        .exclude(fee_status=ClubMembership.FeeStatus.WAIVED)
        .exclude(status=ClubMembership.StatusChoices.CANCELLED)
        .select_related("dues_invoice", "member")
    )
    rows = []
    for membership in memberships:
        balance = remaining_balance(membership)
        if balance > 0:
            rows.append({"membership": membership, "balance": balance, "invoice": getattr(membership, "dues_invoice", None), "early_payment": early_payment_offer(membership)})
    return rows


def record_payment(membership, *, amount, method=FeePayment.Method.BANK_TRANSFER, reference="", note="", recorded_by=None):
    """Record money received against one membership's fee. Several payments may
    land on one membership -- a family paying in two installments must not read as
    unpaid. Updates amount_paid and re-syncs fee_status to match; membership.status
    is untouched -- see sync_fee_status."""
    payment = FeePayment.objects.create(membership=membership, amount=amount, method=method, reference=reference, note=note, recorded_by=recorded_by)

    membership.amount_paid = F("amount_paid") + amount
    membership.save(update_fields=["amount_paid"])
    membership.refresh_from_db(fields=["amount_paid"])
    sync_fee_status(membership, when=payment.paid_at.date())

    return payment


def mark_as_paid(membership, *, recorded_by=None):
    """The "settle this one" action behind both the per-row and bulk buttons. If
    there's a real remaining balance, records it as a payment (auditable, shows up
    in history); if fee_amount was never priced (remaining is 0), just flips the
    flags directly -- there's no real transaction to log."""
    remaining = remaining_balance(membership)
    if remaining > 0:
        record_payment(membership, amount=remaining, method=FeePayment.Method.OTHER, note="Marked as paid", recorded_by=recorded_by)
    else:
        sync_fee_status(membership, force_paid=True)


def sync_fee_status(membership, *, force_paid=False, when=None):
    if membership.fee_status == ClubMembership.FeeStatus.WAIVED:
        return  # manual, independent of payments -- this never overrides it

    effective_amount = effective_fee_amount(membership, when)
    if force_paid or (effective_amount > 0 and membership.amount_paid >= effective_amount):
        new_status = ClubMembership.FeeStatus.PAID
    elif membership.amount_paid > 0:
        new_status = ClubMembership.FeeStatus.PARTIALLY_PAID
    else:
        new_status = ClubMembership.FeeStatus.UNPAID

    # fee_status only -- membership.status is never touched here. Paying in full
    # used to also flip status straight to ACTIVE on its own; now that's exclusively
    # club.services.onboarding.approve_one/approve_all_clean's call, so a paid-up
    # membership still waits on that deliberate admin step. See OnboardingRequirement's
    # docstring (club/models.py) for why.
    membership.fee_status = new_status
    membership.save(update_fields=["fee_status"])
