"""Credit notes: gapless ``CN-YYYY-00001`` numbering (separate from invoices), issuing, PDF.

A credit note is immutable and refers to an invoice, which is never edited. It is always created together with the
change it documents (a plan change), inside the same transaction, so a rolled-back change does not burn a number.
"""

import datetime
from decimal import Decimal

from django.db import transaction
from django.template.loader import render_to_string
from django.utils import timezone

from ..models import CreditNote, CreditNoteSequence, Invoice
from . import BillingError
from .invoices import bill_to_snapshot, issuer_snapshot


def next_credit_note_number(year: int) -> str:
    """Allocate the next ``CN-YYYY-00001`` number. Must run inside the transaction that creates the credit note."""
    CreditNoteSequence.objects.get_or_create(year=year)
    seq = CreditNoteSequence.objects.select_for_update().get(year=year)
    seq.last_number += 1
    seq.save(update_fields=["last_number"])
    return f"CN-{year}-{seq.last_number:05d}"


def issue_credit_note(invoice: Invoice, *, net: Decimal, vat: Decimal, reason: str, credited_from: datetime.date, credited_to: datetime.date) -> CreditNote:
    """Credit ``net`` + ``vat`` against ``invoice``. VAT rate and reverse charge are those of the invoice's period."""
    due = invoice.due
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
        "vat_rate": f"{due.vat_rate:.2f}",
        "reverse_charge": due.reverse_charge,
        "invoice_total": snap.get("total", f"{due.original_total or due.total:.2f}"),
    }
    with transaction.atomic():
        return CreditNote.objects.create(
            club=due.club,
            invoice=invoice,
            due=due,
            number=next_credit_note_number(today.year),
            issued_at=today,
            net=net,
            vat=vat,
            total=total,
            vat_rate=due.vat_rate,
            reverse_charge=due.reverse_charge,
            reason=reason[:255],
            snapshot=snapshot,
        )


def credit_note_context(note: CreditNote) -> dict:
    snap = note.snapshot
    return {
        "note": note,
        "snap": snap,
        "issuer": snap["issuer"],
        "bill_to": snap["bill_to"],
        "credited_from": datetime.date.fromisoformat(snap["credited_from"]),
        "credited_to": datetime.date.fromisoformat(snap["credited_to"]),
        "invoice_number": snap["invoice_number"],
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
