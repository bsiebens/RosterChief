"""Automatic document numbers from a free pattern with placeholders.

A pattern is literal text plus placeholders:

* ``{YYYY}`` 4-digit year, ``{YY}`` 2-digit year, ``{MM}`` 2-digit month (of the issue date)
* exactly one sequence placeholder made of N's: ``{N}`` is the number without padding, ``{NNNNN}`` zero-pads to that many
  digits (the width is the count of N's; a longer number is never cut)

Literal text may only contain letters, digits, space and ``/ - _ . #``.

The counter is independent of the pattern text. It is keyed by a *scope* derived from the finest date placeholder:
``{MM}`` -> one counter per month (``"2026-11"``), ``{YYYY}``/``{YY}`` only -> per year (``"2026"``), no date placeholder
-> one continuous counter (``"all"``). Changing the pattern therefore keeps counting within the same scope, so numbers
stay gapless; the counter only starts over when the scope changes (a new year or month for a dated pattern).

Allocation (``allocate``) must run inside the transaction that creates the document: the scope row is locked with
``select_for_update`` and the increment rolls back together with a failed creation, so a rolled-back document burns no
number. Uniqueness is guaranteed on top of that: if the rendered number already exists (a pattern change or an older
custom number collides), the counter moves on to the next value. Such skipped values are the only permitted gap.
"""

import datetime
import re
from collections.abc import Callable

from django.db import transaction

from . import BillingError

DEFAULT_INVOICE_PATTERN = "INV-{YYYY}-{NNNNN}"
DEFAULT_CREDIT_NOTE_PATTERN = "CN-{YYYY}-{NNNNN}"
PATTERN_MAX_LENGTH = 40
NUMBER_MAX_LENGTH = 32
LITERAL_CHARS = "letters, digits, space and / - _ . #"
MAX_COLLISION_SKIPS = 10000
SAMPLE_SEQUENCE = 999_999

PLACEHOLDERS = [
    {"token": "{YYYY}", "meaning": "Year with 4 digits, e.g. 2026"},
    {"token": "{YY}", "meaning": "Year with 2 digits, e.g. 26"},
    {"token": "{MM}", "meaning": "Month with 2 digits, e.g. 07. The counter restarts every month"},
    {"token": "{N}", "meaning": "Sequence number without padding, e.g. 7. Use exactly one sequence placeholder"},
    {"token": "{NNNNN}", "meaning": "Sequence number zero-padded to the number of N's, e.g. 00007"},
]

_TOKEN = re.compile(r"\{([^{}]*)\}")
_LITERAL_OK = re.compile(r"^[A-Za-z0-9 /\-_.#]*$")
_SEQUENCE = re.compile(r"^N+$")
_DATE_TOKENS = ("YYYY", "YY", "MM")


def _parts(pattern: str) -> list[tuple[str, str]]:
    """Split into ``("lit", text)`` / ``("tok", name)`` parts. ``ValueError`` for anything that is not valid."""
    if not isinstance(pattern, str) or not pattern.strip():
        raise ValueError("Enter a pattern, for example INV-{YYYY}-{NNNNN}.")
    if len(pattern) > PATTERN_MAX_LENGTH:
        raise ValueError(f"The pattern can be at most {PATTERN_MAX_LENGTH} characters.")
    parts: list[tuple[str, str]] = []
    pos = 0
    sequences = 0
    for m in _TOKEN.finditer(pattern):
        _add_literal(parts, pattern[pos : m.start()])
        name = m.group(1)
        if name in _DATE_TOKENS:
            pass
        elif _SEQUENCE.match(name):
            sequences += 1
        else:
            raise ValueError(f"Unknown placeholder {{{name}}}. Use {{YYYY}}, {{YY}}, {{MM}} and {{N}} / {{NNNNN}}.")
        parts.append(("tok", name))
        pos = m.end()
    _add_literal(parts, pattern[pos:])
    if sequences == 0:
        raise ValueError("The pattern needs a sequence placeholder such as {NNNNN}.")
    if sequences > 1:
        raise ValueError("Use only one sequence placeholder such as {NNNNN}.")
    return parts


def _add_literal(parts: list, text: str) -> None:
    if not text:
        return
    if "{" in text or "}" in text:
        raise ValueError("Braces are only allowed around a placeholder such as {YYYY} or {NNNNN}.")
    if not _LITERAL_OK.match(text):
        bad = sorted({c for c in text if not _LITERAL_OK.match(c)})
        raise ValueError(f"The character {' '.join(repr(c) for c in bad)} is not allowed. Use {LITERAL_CHARS}.")
    parts.append(("lit", text))


def _render_parts(parts: list[tuple[str, str]], seq: int, on_date: datetime.date) -> str:
    out = []
    for kind, value in parts:
        if kind == "lit":
            out.append(value)
        elif value == "YYYY":
            out.append(f"{on_date.year:04d}")
        elif value == "YY":
            out.append(f"{on_date.year % 100:02d}")
        elif value == "MM":
            out.append(f"{on_date.month:02d}")
        else:
            out.append(f"{seq:0{len(value)}d}")
    return "".join(out)


def validate_pattern(pattern: str) -> str:
    """Return the pattern when it is valid, else raise ``ValueError`` with a clear message. Also refuses patterns whose
    numbers could outgrow the 32-character number column (checked with a six-digit sequence and today's date)."""
    parts = _parts(pattern)
    sample = _render_parts(parts, SAMPLE_SEQUENCE, datetime.date.today())
    if len(sample) > NUMBER_MAX_LENGTH:
        raise ValueError(f"Numbers would be too long ({sample!r} has {len(sample)} characters; the limit is {NUMBER_MAX_LENGTH}). Shorten the pattern.")
    return pattern


def render(pattern: str, seq: int, on_date: datetime.date) -> str:
    return _render_parts(_parts(pattern), seq, on_date)


def resets(pattern: str) -> str:
    """``"monthly"``, ``"yearly"`` or ``"never"``: how often the counter starts again."""
    names = {v for k, v in _parts(pattern) if k == "tok"}
    if "MM" in names:
        return "monthly"
    if names & {"YYYY", "YY"}:
        return "yearly"
    return "never"


def scope_key(pattern: str, on_date: datetime.date) -> str:
    kind = resets(pattern)
    if kind == "monthly":
        return f"{on_date.year:04d}-{on_date.month:02d}"
    if kind == "yearly":
        return f"{on_date.year:04d}"
    return "all"


def scope_label(pattern: str, on_date: datetime.date) -> str:
    kind = resets(pattern)
    if kind == "monthly":
        return on_date.strftime("%B %Y")
    if kind == "yearly":
        return str(on_date.year)
    return "All time"


def matches_pattern(pattern: str, number: str) -> bool:
    """Does ``number`` look like something this pattern generates? (Used to keep custom numbers out of the automatic range.)"""
    try:
        parts = _parts(pattern)
    except ValueError:
        return False
    rx = []
    for kind, value in parts:
        if kind == "lit":
            rx.append(re.escape(value))
        elif value == "YYYY":
            rx.append(r"\d{4}")
        elif value in ("YY", "MM"):
            rx.append(r"\d{2}")
        else:
            rx.append(rf"\d{{{len(value)},}}")
    return re.fullmatch("".join(rx), number) is not None


def current_value(model, scope: str) -> int:
    row = model.objects.filter(scope=scope).first()
    return row.last_number if row else 0


def peek(model, pattern: str, on_date: datetime.date, exists: Callable[[str], bool]) -> tuple[str, int]:
    """The number ``allocate`` would hand out right now, without writing anything: ``(number, sequence value)``."""
    seq = current_value(model, scope_key(pattern, on_date))
    for _ in range(MAX_COLLISION_SKIPS):
        seq += 1
        number = render(pattern, seq, on_date)
        if not exists(number):
            return number, seq
    raise BillingError("No free number was found for this pattern; change the pattern or the next number.")


def allocate(model, pattern: str, on_date: datetime.date, exists: Callable[[str], bool]) -> str:
    """Take the next free number. Must run inside the transaction that creates the document."""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("Numbers must be allocated inside the transaction that creates the document.")
    scope = scope_key(pattern, on_date)
    model.objects.get_or_create(scope=scope)
    row = model.objects.select_for_update().get(scope=scope)
    for _ in range(MAX_COLLISION_SKIPS):
        row.last_number += 1
        number = render(pattern, row.last_number, on_date)
        if not exists(number):
            row.save(update_fields=["last_number"])
            return number
    raise BillingError("No free number was found for this pattern; change the pattern or the next number.")


def set_next(model, pattern: str, on_date: datetime.date, next_value: int) -> int:
    """Make ``next_value`` the sequence value of the next number in today's scope. Only forward: ``ValueError`` (naming the
    current value) when it would not be above the counter. Returns the previous counter value. Run inside a transaction."""
    scope = scope_key(pattern, on_date)
    model.objects.get_or_create(scope=scope)
    row = model.objects.select_for_update().get(scope=scope)
    if next_value <= row.last_number:
        raise ValueError(f"Cannot go back: the sequence is already at {row.last_number}.")
    previous = row.last_number
    row.last_number = next_value - 1
    row.save(update_fields=["last_number"])
    return previous
