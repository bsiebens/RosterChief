"""Event subtypes ("Fitness training", "Friendly game", ...): the club's own flavours of an event kind. Everyone reads
(the event form offers them); changing them needs the settings permission. Audit is club-level (``event_subtype.*``).

``title_template`` is the suggested title. Placeholders: ``{teams}`` and ``{kind}`` (the kind's label). ``{teams}`` is
the audience's team and group names, comma separated; for a game with an opponent it is the match-up, home side first
("U12 vs Rivals", or "Rivals vs U12" away). A game *always* follows its template (default ``{teams}``); a training
without a title follows it too (default ``{kind} {teams}``); for the other kinds it is only a suggestion in the title box."""

import re

from django.db import transaction
from django.db.models import Max

from events.models import Event, EventSubtype
from members.services import audit
from members.services.access import require_settings
from members.services.errors import Invalid

FIELDS = ("kind", "name", "title_template", "ordering")
DEFAULT_TEMPLATES = {Event.EventKind.TRAINING: "{kind} {teams}", Event.EventKind.GAME: "{teams}"}
PLACEHOLDER = re.compile(r"\{(teams|kind)\}")


def render_title(template: str, kind: str, *, teams: str = "") -> str:
    """The template with its placeholders filled in and blanks tidied up ("Training " -> "Training")."""
    values = {"teams": teams, "kind": str(Event.EventKind(kind).label) if kind in Event.EventKind.values else kind}
    return " ".join(PLACEHOLDER.sub(lambda m: values[m.group(1)], template or "").split())


def template_for(subtype, kind: str) -> str:
    return (subtype.title_template if subtype is not None and subtype.title_template else DEFAULT_TEMPLATES.get(kind, ""))


def subtype_out(subtype) -> dict:
    return {
        "id": subtype.pk, "kind": subtype.kind, "kind_label": str(Event.EventKind(subtype.kind).label), "name": subtype.name,
        "title_template": subtype.title_template, "ordering": subtype.ordering,
        "in_use_count": subtype.events.count() + subtype.event_series.count(),
    }  # fmt: skip


def list_subtypes() -> list[dict]:
    return [subtype_out(s) for s in EventSubtype.objects.all()]


def get_subtype(subtype_id):
    return EventSubtype.objects.filter(pk=subtype_id).first()


def _state(subtype) -> dict:
    return {f: getattr(subtype, f) for f in FIELDS}


def _apply(subtype, values: dict) -> None:
    for k, v in values.items():
        if v is not None and k in FIELDS:
            setattr(subtype, k, v)
    errors = {}
    subtype.name = (subtype.name or "").strip()
    subtype.title_template = (subtype.title_template or "").strip()
    if not subtype.name:
        errors["name"] = ["This field is required."]
    if subtype.kind not in Event.EventKind.values:
        errors["kind"] = ["Choose a valid kind."]
    elif subtype.name and EventSubtype.objects.filter(kind=subtype.kind, name__iexact=subtype.name).exclude(pk=subtype.pk).exists():
        errors["name"] = ["This kind already has a subtype with that name."]
    if errors:
        raise Invalid("Please correct the highlighted fields.", errors)


@transaction.atomic
def create_subtype(actor, **values) -> EventSubtype:
    require_settings(actor)
    subtype = EventSubtype(kind="", name="", ordering=0)
    _apply(subtype, values)
    if not subtype.ordering:  # new ones go to the end of their kind
        subtype.ordering = (EventSubtype.objects.filter(kind=subtype.kind).aggregate(m=Max("ordering"))["m"] or 0) + 1
    subtype.save()
    audit.record(actor, "event_subtype.created", target=subtype, changes=audit.snapshot(subtype, FIELDS))
    return subtype


@transaction.atomic
def update_subtype(actor, subtype, **values) -> EventSubtype:
    require_settings(actor)
    before = _state(subtype)
    if "kind" in values and values["kind"] != subtype.kind and (subtype.events.exists() or subtype.event_series.exists()):
        raise Invalid("Please correct the highlighted fields.", {"kind": ["This subtype is in use, so it can't move to another kind."]})
    _apply(subtype, values)
    subtype.save()
    after = _state(subtype)
    changes = {k: {"from": before[k], "to": after[k]} for k in after if before[k] != after[k]}
    if changes:
        audit.record(actor, "event_subtype.updated", target=subtype, changes=changes)
    return subtype


@transaction.atomic
def delete_subtype(actor, subtype) -> None:
    """Events keep their title; they just lose the subtype."""
    require_settings(actor)
    pk, label, state = subtype.pk, str(subtype), _state(subtype)
    subtype.delete()
    entry = audit.record(actor, "event_subtype.deleted", changes={k: {"from": v, "to": None} for k, v in state.items()})
    entry.target_type, entry.target_id, entry.target_label = "eventsubtype", str(pk), label[:255]
    entry.save(update_fields=["target_type", "target_id", "target_label", "modified"])


@transaction.atomic
def reorder_subtypes(actor, ids: list) -> list[dict]:
    """The listed subtypes get ordering 1..n per kind, in the order given; ones not listed follow after their kind's
    listed ones (keeping their order). Unknown or repeated ids are a 422."""
    require_settings(actor)
    if len(set(ids)) != len(ids):
        raise Invalid("Please correct the highlighted fields.", {"ids": ["Each event type can only be listed once."]})
    by_id = {s.pk: s for s in EventSubtype.objects.all()}
    if any(i not in by_id for i in ids):
        raise Invalid("Please correct the highlighted fields.", {"ids": ["Unknown event type."]})
    listed = [by_id[i] for i in ids]
    rest = [s for s in EventSubtype.objects.all() if s.pk not in set(ids)]
    counters, changed = {}, {}
    for subtype in listed + rest:
        counters[subtype.kind] = counters.get(subtype.kind, 0) + 1
        if subtype.ordering != counters[subtype.kind]:
            changed[str(subtype.pk)] = {"from": subtype.ordering, "to": counters[subtype.kind]}
            subtype.ordering = counters[subtype.kind]
            subtype.save(update_fields=["ordering", "modified"])
    if changed:
        audit.record(actor, "event_subtype.reordered", changes=changed)
    return list_subtypes()
