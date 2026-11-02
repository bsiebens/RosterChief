"""Void and reissue an invoice.

A superuser voids an invoice (with a reason) and a replacement invoice for the SAME period is issued. The voided invoice
keeps its number forever; the replacement takes the next automatic number (or a custom one) inside the same transaction,
so the sequence stays gapless and a failed attempt burns nothing. When the voided invoice had an amount, a ``void``
credit note for its full amount is issued against it: a DOCUMENT ONLY. The period (amounts, payments, status, grace) and
the club's credit are never touched, because the replacement covers the same period and amounts.

No platform-admin email is sent for the replacement: a superuser just did this on purpose, and the "new invoice" mail
announces a period that was billed, which a reissue is not. The replacement does need sending to the club again.
"""

import datetime
from dataclasses import dataclass
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone

from ..models import CreditNote, Due, Invoice
from . import BillingError
from .credit_notes import issue_credit_note
from .invoices import build_snapshot, clean_number, next_automatic_number

REASON_MAX_LENGTH = 300


@dataclass
class VoidResult:
    voided: Invoice
    invoice: Invoice
    credit_note: CreditNote | None


def clean_reason(raw: str) -> str:
    """``ValueError`` (422) when blank or longer than 300 characters."""
    reason = (raw or "").strip()
    if not reason:
        raise ValueError("A reason is required.")
    if len(reason) > REASON_MAX_LENGTH:
        raise ValueError(f"The reason can be at most {REASON_MAX_LENGTH} characters.")
    return reason


@transaction.atomic
def void_and_reissue(invoice: Invoice, *, reason: str, number: str | None = None, actor=None) -> VoidResult:
    """Raises ``ValueError`` for an invalid reason / number format and ``BillingError`` for a refused precondition; nothing
    is changed in either case."""
    reason = clean_reason(reason)
    due = Due.objects.select_for_update().select_related("club", "plan").get(pk=invoice.due_id)
    old = Invoice.objects.select_for_update().get(pk=invoice.pk)
    if old.voided_at is not None:
        successor = old.replaced_by.number if old.replaced_by_id else None
        raise BillingError(f"Invoice {old.number} was already voided" + (f" (replaced by {successor})." if successor else "."))
    if due.status == Due.CANCELLED:
        raise BillingError("This period was cancelled; its invoice cannot be voided.")
    if old.credit_notes.exists():
        raise BillingError("This invoice has credit notes; it cannot be voided.")
    custom = clean_number(number) if number is not None else None  # ValueError = bad format, BillingError = in use

    today = timezone.localdate()
    old.voided_at, old.voided_by, old.void_reason = timezone.now(), actor, reason
    old.save(update_fields=["voided_at", "voided_by", "void_reason", "modified"])  # frees the period's "one active invoice" slot
    try:
        with transaction.atomic():
            new = Invoice.objects.create(due=due, number=custom or next_automatic_number(today), custom_number=custom is not None, issued_at=today, snapshot=build_snapshot(due))
    except IntegrityError:
        raise BillingError(f"Invoice number {custom} is already in use.") from None
    old.replaced_by = new
    old.save(update_fields=["replaced_by", "modified"])

    note = None
    snap = old.snapshot or {}
    net = Decimal(snap.get("net", due.amount))
    vat = Decimal(snap.get("vat", due.vat_amount))
    if net + vat > 0:
        note = issue_credit_note(
            old,
            net=net,
            vat=vat,
            reason=f"Invoice voided and reissued: {reason}",
            credited_from=_snap_date(snap, "period_start", due.period_start),
            credited_to=_snap_date(snap, "period_end", due.period_end),
            kind=CreditNote.VOID,
            replacement_invoice=new,
            vat_rate=Decimal(snap.get("vat_rate", due.vat_rate)),
            reverse_charge=snap.get("reverse_charge", due.reverse_charge),
        )
    return VoidResult(voided=old, invoice=new, credit_note=note)


def _snap_date(snap: dict, key: str, fallback):
    return datetime.date.fromisoformat(snap[key]) if snap.get(key) else fallback
