"""Append-only audit trail for members and everything attached to them. Every service write calls ``record``."""

import datetime
import decimal
import enum
import uuid
from collections.abc import Iterable, Mapping
from typing import Any

from django.db.models import Model, QuerySet

from members.models import AuditEntry, Member


def _json(value: Any):
    """JSON-safe form of a field value: dates, decimals, uuids and other objects become strings."""
    if isinstance(value, enum.Enum):
        return _json(value.value)
    if isinstance(value, str):
        return str(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, (datetime.date, datetime.datetime, decimal.Decimal, uuid.UUID)):
        return str(value)
    if isinstance(value, Mapping):
        return {str(k): _json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json(v) for v in value]
    if hasattr(value, "name") and hasattr(value, "storage"):  # a FieldFile
        return value.name or ""
    return str(value)


def _current(instance: Model, name: str):
    field = instance._meta.get_field(name)
    return getattr(instance, field.attname)


def diff(instance: Model, new_values: Mapping[str, Any]) -> dict:
    """``{field: {"from": old, "to": new}}`` for the fields in ``new_values`` whose value differs from ``instance``.

    Values are compared and stored in JSON-safe form (dates and decimals as ``str``); a Decimal that only differs
    in scale (``10`` vs ``10.00``) is not a change. Foreign keys can be given as ``<name>_id`` or ``<name>``.
    """
    changes = {}
    for name, new in new_values.items():
        old = _current(instance, name)
        if old == new:
            continue
        if isinstance(old, decimal.Decimal) and isinstance(new, (decimal.Decimal, int, str)):
            try:
                if old == decimal.Decimal(new):
                    continue
            except decimal.InvalidOperation:
                pass
        old_j, new_j = _json(old), _json(new)
        if old_j != new_j:
            changes[name] = {"from": old_j, "to": new_j}
    return changes


def snapshot(instance: Model, fields: Iterable[str], *, deleted: bool = False) -> dict:
    """Changes dict for a created (``from`` is None) or deleted (``to`` is None) row, over ``fields``."""
    out = {}
    for name in fields:
        value = _json(_current(instance, name))
        out[name] = {"from": value, "to": None} if deleted else {"from": None, "to": value}
    return out


def record(actor, action: str, *, member: Member | None = None, target: Model | None = None, changes: dict | None = None, target_label: str = "") -> AuditEntry:
    """Append one audit entry. ``actor`` is a user (or None for the system), ``member`` the person it is about,
    ``target`` the exact row touched. Never edits or deletes existing entries."""
    entry = AuditEntry(
        actor=actor if actor is not None and getattr(actor, "pk", None) else None,
        member=member,
        action=action,
        changes=_json(changes or {}),
    )
    if target is not None:
        entry.target_type = target._meta.model_name
        entry.target_id = str(target.pk)
        entry.target_label = (target_label or str(target))[:255]
    entry.save()
    return entry


def history(member: Member) -> QuerySet[AuditEntry]:
    """Every audit entry about ``member``, newest first."""
    return AuditEntry.objects.filter(member=member).select_related("actor").order_by("-created")
