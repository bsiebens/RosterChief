"""Credit notes: gapless numbering from a configurable pattern (default ``CN-YYYY-00001``, separate from invoices), issuing, PDF.

A credit note is immutable and refers to an invoice, which is never edited. It is always created together with the
change it documents (a plan change), inside the same transaction, so a rolled-back change does not burn a number.
"""

import datetime
from decimal import Decimal

from django.db import transaction
from django.template.loader import render_to_string
from django.utils import timezone

from ..models import BillingSettings, CreditNote, CreditNoteSequence, Invoice
from . import BillingError, numbering
from .invoices import bill_to_snapshot, issuer_snapshot, split_description


def next_credit_note_number(on_date: datetime.date | None = None) -> str:
    """Allocate the next automatic credit note number from the configured pattern. Must run inside the transaction that
    creates the credit note (see ``numbering.allocate``)."""
    pattern = BillingSettings.current().credit_note_number_pattern
    return numbering.allocate(CreditNoteSequence, pattern, on_date or timezone.localdate(), lambda n: CreditNote.objects.filter(number=n).exists())


def peek_next_credit_note_number(pattern: str | None = None, on_date: datetime.date | None = None) -> str:
    """The number the next credit note would get right now. Writes nothing."""
    pattern = pattern or BillingSettings.current().credit_note_number_pattern
    return numbering.peek(CreditNoteSequence, pattern, on_date or timezone.localdate(), lambda n: CreditNote.objects.filter(number=n).exists())[0]


def issue_credit_note(
    invoice: Invoice,
    *,
    net: Decimal,
    vat: Decimal,
    reason: str,
    credited_from: datetime.date,
    credited_to: datetime.date,
    kind: str = CreditNote.PLAN_CHANGE,
    replacement_invoice: Invoice | None = None,
    vat_rate: Decimal | None = None,
    reverse_charge: bool | None = None,
) -> CreditNote:
    """Credit ``net`` + ``vat`` against ``invoice``. VAT rate and reverse charge are those of the invoice's period unless
    given (a ``void`` note uses the voided invoice's own). A ``void`` note is a document only: this function never touches
    the period, payments or club credit, for any kind."""
    due = invoice.due
    vat_rate = due.vat_rate if vat_rate is None else vat_rate
    reverse_charge = due.reverse_charge if reverse_charge is None else reverse_charge
    total = net + vat
    if total <= 0:
        raise BillingError("A credit note needs an amount above zero.")
    today = timezone.localdate()
    snap = invoice.snapshot or {}
    snapshot = {
        "issuer": issuer_snapshot(),
        "bill_to": bill_to_snapshot(due.club),
        "invoice_number": invoice.number,
        "invoice_issued_at": invoice.issued_at.isoformat(),
        "description": f"{due.plan.name} - platform subscription",
        "credited_from": credited_from.isoformat(),
        "credited_to": credited_to.isoformat(),
        "reason": reason,
        "net": f"{net:.2f}",
        "vat": f"{vat:.2f}",
        "total": f"{total:.2f}",
        "vat_rate": f"{vat_rate:.2f}",
        "reverse_charge": reverse_charge,
        "kind": kind,
        "replacement_invoice_number": replacement_invoice.number if replacement_invoice else None,
        "invoice_total": snap.get("total", f"{due.original_total or due.total:.2f}"),
    }
    with transaction.atomic():
        return CreditNote.objects.create(
            club=due.club,
            invoice=invoice,
            due=due,
            number=next_credit_note_number(today),
            issued_at=today,
            net=net,
            vat=vat,
            total=total,
            vat_rate=vat_rate,
            reverse_charge=reverse_charge,
            reason=reason[:255],
            snapshot=snapshot,
            kind=kind,
            replacement_invoice=replacement_invoice,
        )


def credit_note_context(note: CreditNote) -> dict:
    snap = note.snapshot
    return {
        "note": note,
        "snap": snap,
        "line_name": split_description(snap["description"])[0],
        "line_detail": split_description(snap["description"])[1],
        "issuer": snap["issuer"],
        "bill_to": snap["bill_to"],
        "credited_from": datetime.date.fromisoformat(snap["credited_from"]),
        "credited_to": datetime.date.fromisoformat(snap["credited_to"]),
        "invoice_number": snap["invoice_number"],
        "is_void": note.kind == CreditNote.VOID,
        "replacement_invoice_number": snap.get("replacement_invoice_number"),
        "net": Decimal(snap["net"]),
        "vat": Decimal(snap["vat"]),
        "total": Decimal(snap["total"]),
        "vat_rate": Decimal(snap["vat_rate"]),
    }


def credit_note_html(note: CreditNote) -> str:
    return render_to_string("billing/credit_note.html", credit_note_context(note))


def credit_note_pdf(note: CreditNote) -> bytes:
    try:
        from weasyprint import HTML
    except (ImportError, OSError) as e:  # system libraries (pango) missing
        raise BillingError("PDF rendering is not available on this server.") from e
    return HTML(string=credit_note_html(note)).write_pdf()
