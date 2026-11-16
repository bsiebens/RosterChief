"""Automatic numbers for the shop's documents (orders, invoices, vouchers, production batches) from a free pattern, per club.

Same pattern language and counters as the platform's invoice numbering (``billing.services.numbering``): literal text plus
``{YYYY}`` ``{YY}`` ``{MM}`` and exactly one sequence placeholder such as ``{NNNNN}``; the counter restarts per year or month when
the pattern has a date placeholder, and never otherwise. One counter per document type and scope (``ShopNumberSequence``), allocated
under a row lock inside the transaction that creates the document, so a rolled-back document burns no number.

A counter that starts (first document in a scope, or a changed pattern) begins after the highest number already issued that fits
the pattern in that scope, so switching to the new scheme never reuses or collides with existing numbers. If a rendered number
exists anyway (a manual edit, an older pattern) the counter moves on to the next free one."""

import datetime
import re

from django.db import IntegrityError, transaction
from django.utils import timezone

from billing.services import numbering as core
from members.services.errors import Invalid
from shop.settings_models import ShopNumberSequence, ShopSettings

#: kind -> (ShopSettings field, default pattern)
KINDS = {
    "order": ("order_number_pattern", "ORD-{YYYY}-{NNNNN}"),
    "invoice": ("invoice_number_pattern", "INV-{YYYY}-{NNNNN}"),
    "voucher": ("voucher_number_pattern", "VCH-{YYYY}-{NNNNN}"),
    "batch": ("batch_number_pattern", "PB-{YYYY}-{NNNNN}"),
}
PLACEHOLDERS = core.PLACEHOLDERS


def _model(kind: str):
    from shop.models import Invoice, Order, ProductionBatch, Voucher

    return {"order": Order, "invoice": Invoice, "voucher": Voucher, "batch": ProductionBatch}[kind]


def _kind(kind: str) -> str:
    if kind not in KINDS:
        raise Invalid("Unknown document type.", {"kind": ["Unknown document type."]})
    return kind


def pattern_for(kind: str) -> str:
    return getattr(ShopSettings.current(), KINDS[_kind(kind)][0]) or KINDS[kind][1]


def check_pattern(pattern: str, field: str = "pattern") -> str:
    """The pattern when valid, else a 422 whose message says what to fix."""
    try:
        return core.validate_pattern(pattern)
    except ValueError as error:
        raise Invalid(str(error), {field: [str(error)]}) from error


def _capture(pattern: str):
    """A regex matching numbers of this pattern, capturing the sequence and the date parts."""
    rx = []
    for kind, value in core._parts(pattern):
        if kind == "lit":
            rx.append(re.escape(value))
        elif value == "YYYY":
            rx.append(r"(?P<year>\d{4})")
        elif value == "YY":
            rx.append(r"(?P<yy>\d{2})")
        elif value == "MM":
            rx.append(r"(?P<month>\d{2})")
        else:
            rx.append(rf"(?P<seq>\d{{{len(value)},}})")
    return re.compile("".join(rx))


def highest_issued(kind: str, pattern: str, on_date: datetime.date) -> int:
    """The highest sequence value among the numbers already issued that fit ``pattern`` in the scope of ``on_date``."""
    rx = _capture(pattern)
    best = 0
    for number in _model(kind).objects.values_list("number", flat=True):
        m = rx.fullmatch(number or "")
        if m is None:
            continue
        groups = m.groupdict()
        year = int(groups["year"]) if groups.get("year") else (2000 + int(groups["yy"]) if groups.get("yy") else None)
        month = int(groups["month"]) if groups.get("month") else None
        if year is not None and year != on_date.year:
            continue
        if month is not None and month != on_date.month:
            continue
        best = max(best, int(groups["seq"]))
    return best


def _row(kind: str, pattern: str, on_date: datetime.date, *, lock: bool):
    scope = core.scope_key(pattern, on_date)
    try:
        row, created = ShopNumberSequence.objects.get_or_create(kind=kind, scope=scope)
    except IntegrityError:  # another request created it at the same moment
        row, created = ShopNumberSequence.objects.get(kind=kind, scope=scope), False
    if lock:
        row = ShopNumberSequence.objects.select_for_update().get(pk=row.pk)
    if created:
        row.last_number = highest_issued(kind, pattern, on_date)
        row.save(update_fields=["last_number"])
    return row


def allocate(kind: str, on_date: datetime.date | None = None) -> str:
    """Take the next free number for ``kind``; must run inside the transaction that creates the document."""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("Numbers must be allocated inside the transaction that creates the document.")
    on_date = on_date or timezone.localdate()
    pattern = pattern_for(kind)
    row = _row(_kind(kind), pattern, on_date, lock=True)
    model = _model(kind)
    for _ in range(core.MAX_COLLISION_SKIPS):
        row.last_number += 1
        number = core.render(pattern, row.last_number, on_date)
        if not model.objects.filter(number=number).exists():
            row.save(update_fields=["last_number"])
            return number
    raise RuntimeError("No free number was found for this pattern; change the pattern or the next number.")


def peek(kind: str, pattern: str | None = None, on_date: datetime.date | None = None) -> tuple[str, int]:
    """The number the next document would get right now (``(number, sequence value)``), without writing anything. ``pattern`` previews
    a pattern that is not saved yet."""
    on_date = on_date or timezone.localdate()
    pattern = pattern or pattern_for(kind)
    scope = core.scope_key(pattern, on_date)
    row = ShopNumberSequence.objects.filter(kind=_kind(kind), scope=scope).first()
    seq = row.last_number if row else highest_issued(kind, pattern, on_date)
    model = _model(kind)
    for _ in range(core.MAX_COLLISION_SKIPS):
        seq += 1
        number = core.render(pattern, seq, on_date)
        if not model.objects.filter(number=number).exists():
            return number, seq
    raise Invalid("No free number was found for this pattern.", {"pattern": ["No free number was found for this pattern."]})


def status(kind: str, pattern: str | None = None, on_date: datetime.date | None = None) -> dict:
    """What the settings page shows for one document type."""
    on_date = on_date or timezone.localdate()
    pattern = pattern or pattern_for(kind)
    number, seq = peek(kind, pattern, on_date)
    return {"kind": kind, "pattern": pattern, "default_pattern": KINDS[kind][1], "next_number": number, "next_sequence": seq, "resets": core.resets(pattern), "scope_label": core.scope_label(pattern, on_date)}


@transaction.atomic
def set_next(kind: str, next_value: int, on_date: datetime.date | None = None) -> None:
    """Make ``next_value`` the sequence value of the next number in today's scope. Only forward: it must be above the current counter."""
    on_date = on_date or timezone.localdate()
    pattern = pattern_for(kind)
    row = _row(_kind(kind), pattern, on_date, lock=True)
    if next_value <= row.last_number:
        message = f"Cannot go back: the sequence is already at {row.last_number}."
        raise Invalid(message, {"next": [message]})
    row.last_number = next_value - 1
    row.save(update_fields=["last_number"])
