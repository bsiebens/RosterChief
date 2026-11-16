"""A club's credit ledger. Credit arises when a plan change leaves the club with money on a period that no longer needs it
(see ``plan_change``) and is carried forward: it pays the next invoices automatically. Nothing is ever paid out.

``credit_balance(club)`` is the sum of the append-only ``ClubCreditEntry`` rows. Concurrent writers serialise on the
club's ``ClubBilling`` row (``lock``), so the balance read while applying credit is the balance that is spent.
"""

from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from ..models import ClubBilling, ClubCreditEntry, CreditNote, Due, DuePayment

ZERO = Decimal("0.00")


def credit_balance(club) -> Decimal:
    return ClubCreditEntry.objects.filter(club=club).aggregate(total=Sum("amount"))["total"] or ZERO


def lock(club) -> None:
    """Serialise credit and plan-change work for one club (a no-op row lock; the row must exist)."""
    ClubBilling.objects.select_for_update().filter(club=club).first()


def add_credit(club, amount: Decimal, kind: str, *, due: Due | None = None, credit_note: CreditNote | None = None, note: str = "") -> ClubCreditEntry:
    return ClubCreditEntry.objects.create(club=club, amount=amount, kind=kind, due=due, credit_note=credit_note, note=note[:255])


@transaction.atomic
def apply_credit(due: Due, *, reference: str | None = None, credit_note: CreditNote | None = None) -> Decimal:
    """Pay as much of ``due`` as the club's credit allows: ``min(balance, open amount)``. Returns the amount applied.

    Creates a ``credit`` DuePayment (``reference`` = credit note number(s) or "club credit") and a matching negative
    ledger entry, then re-settles the period. Safe to call repeatedly: it only ever uses what is left.
    """
    from .dues import _resettle

    lock(due.club)
    due = Due.objects.select_for_update().get(pk=due.pk)
    if due.status not in Due.OWING or due.balance <= 0:
        return ZERO
    amount = min(credit_balance(due.club), due.balance)
    if amount <= 0:
        return ZERO
    reference = (reference or "club credit")[:255]
    DuePayment.objects.create(due=due, amount=amount, method=DuePayment.CREDIT, reference=reference, paid_at=timezone.now(), note="Paid from club credit")
    add_credit(due.club, -amount, ClubCreditEntry.APPLIED, due=due, credit_note=credit_note, note=f"Applied to period {due.period_start} to {due.period_end}")
    _resettle(due)
    return amount
