"""Recording money received against a membership's fee.

Mirrors billing.services.dues.record_payment for a different kind of money: a
member's own club fee, not the club's platform subscription. amount_paid is kept in
step here, never recomputed by re-aggregating FeePayment on every read.
"""

from decimal import Decimal

from django.db.models import F

from club.models import ClubMembership, FeePayment


def remaining_balance(membership):
    return max(membership.fee_amount - membership.amount_paid, Decimal("0.00"))


def open_dues_rows(club, people, season):
    """Every season-dues row still owed by ``people`` in ``season`` -- shared by
    mobile's Home dues card and its Payments & dues screen so the two never
    drift out of sync on what counts as "still open". WAIVED memberships and
    fully-paid balances are excluded."""
    if season is None or not people:
        return []

    memberships = ClubMembership.objects.filter(club=club, member__in=people, season=season).exclude(fee_status=ClubMembership.FeeStatus.WAIVED).select_related("dues_invoice", "member")
    rows = []
    for membership in memberships:
        balance = remaining_balance(membership)
        if balance > 0:
            rows.append({"membership": membership, "balance": balance, "invoice": getattr(membership, "dues_invoice", None)})
    return rows


def record_payment(membership, *, amount, method=FeePayment.Method.BANK_TRANSFER, reference="", note="", recorded_by=None):
    """Record money received against one membership's fee. Several payments may
    land on one membership -- a family paying in two installments must not read as
    unpaid. Updates amount_paid and re-syncs fee_status to match; membership.status
    is untouched -- see _sync_fee_status."""
    payment = FeePayment.objects.create(membership=membership, amount=amount, method=method, reference=reference, note=note, recorded_by=recorded_by)

    membership.amount_paid = F("amount_paid") + amount
    membership.save(update_fields=["amount_paid"])
    membership.refresh_from_db(fields=["amount_paid"])
    _sync_fee_status(membership)

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
        _sync_fee_status(membership, force_paid=True)


def _sync_fee_status(membership, *, force_paid=False):
    if membership.fee_status == ClubMembership.FeeStatus.WAIVED:
        return  # manual, independent of payments -- this never overrides it

    if force_paid or (membership.fee_amount > 0 and membership.amount_paid >= membership.fee_amount):
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
