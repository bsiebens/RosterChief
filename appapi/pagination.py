"""Cursor pagination for unbounded lists (API.md §6).

``?limit=`` (default 25, max 100) and an opaque ``?cursor=`` in; ``{"items": [...],
"next_cursor": "…" | null}`` out. The cursor encodes the last row's ``(sort value, id)``,
so pages stay stable while rows are added, and the id breaks ties between equal sort
values. Clients must treat it as opaque.

Usage::

    page = paginate(queryset, sort_field="-created", cursor=cursor, limit=limit)
    return {"items": [serialize(row) for row in page.items], "next_cursor": page.next_cursor}
"""

import base64
import datetime
import json
from dataclasses import dataclass

from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from .errors import ApiError

DEFAULT_LIMIT = 25
MAX_LIMIT = 100


@dataclass
class Page:
    items: list
    next_cursor: str | None


def _encode(value, pk) -> str:
    if isinstance(value, datetime.datetime | datetime.date):
        value = {"t": value.isoformat(), "dt": isinstance(value, datetime.datetime)}
    raw = json.dumps([value, str(pk)], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode(cursor: str):
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        value, pk = json.loads(raw)
    except ValueError, TypeError:
        raise ApiError(400, "invalid_cursor", _("This page link is no longer valid. Start from the first page.")) from None
    if isinstance(value, dict) and "t" in value:
        value = datetime.datetime.fromisoformat(value["t"]) if value.get("dt") else datetime.date.fromisoformat(value["t"])
    return value, pk


def clamp_limit(limit: int | None) -> int:
    if not limit or limit < 1:
        return DEFAULT_LIMIT
    return min(limit, MAX_LIMIT)


def paginate(queryset, *, sort_field: str, cursor: str | None = None, limit: int | None = None) -> Page:
    """One page of ``queryset`` ordered by ``sort_field`` (prefix ``-`` for descending),
    with ``pk`` as the tie-breaker."""
    descending = sort_field.startswith("-")
    field = sort_field.lstrip("-")
    limit = clamp_limit(limit)

    queryset = queryset.order_by(sort_field, "-pk" if descending else "pk")
    if cursor:
        value, pk = _decode(cursor)
        beyond, same = ("lt", "lt") if descending else ("gt", "gt")
        queryset = queryset.filter(Q(**{f"{field}__{beyond}": value}) | Q(**{field: value, f"pk__{same}": pk}))

    rows = list(queryset[: limit + 1])
    items = rows[:limit]
    next_cursor = _encode(getattr(items[-1], field), items[-1].pk) if len(rows) > limit else None
    return Page(items=items, next_cursor=next_cursor)


def paginate_keyset(queryset, *, fields: list[str], cursor: str | None = None, limit: int | None = None) -> Page:
    """Like ``paginate`` but over several ascending sort fields (the last should be unique, usually
    ``pk``): rows come back ordered by ``fields`` and the cursor carries every field's value of the
    last row, so the order stays stable however many rows share the leading values. Fields may be
    annotations. Values must be JSON-serialisable (str/int)."""
    limit = clamp_limit(limit)
    queryset = queryset.order_by(*fields)
    if cursor:
        values = _decode_values(cursor, len(fields))
        after = Q()
        for index, field in enumerate(fields):
            step = Q(**{f"{field}__gt": values[index]})
            for earlier in range(index):
                step &= Q(**{fields[earlier]: values[earlier]})
            after |= step
        queryset = queryset.filter(after)

    rows = list(queryset[: limit + 1])
    items = rows[:limit]
    next_cursor = None
    if len(rows) > limit:
        last = items[-1]
        raw = json.dumps([str(getattr(last, field)) if field == "pk" else getattr(last, field) for field in fields], separators=(",", ":")).encode()
        next_cursor = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return Page(items=items, next_cursor=next_cursor)


def _decode_values(cursor: str, count: int) -> list:
    try:
        values = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
    except ValueError, TypeError:
        values = None
    if not isinstance(values, list) or len(values) != count:
        raise ApiError(400, "invalid_cursor", _("This page link is no longer valid. Start from the first page.")) from None
    return values
