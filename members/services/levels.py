"""Referee and official levels (``teams.RefereeLevel`` / ``teams.OfficialLevel``): the club-defined qualification tiers.
Admin only. Whether the club may use them (feature ``referee_officials``) is the API layer's concern.

Audit: ``AuditEntry.member`` is nullable, so level changes are recorded club-level (no member) with the level as target."""

from django.core.exceptions import ValidationError
from django.db import transaction

from teams.models import OfficialLevel, OfficialProfile, RefereeLevel, RefereeProfile, Team

from . import audit
from .access import require_settings
from .errors import Invalid

KINDS = {"referee": (RefereeLevel, RefereeProfile), "official": (OfficialLevel, OfficialProfile)}


def _model(kind):
    return KINDS[kind][0]


def level_out(level, kind: str) -> dict:
    return {
        "id": level.pk,
        "name": level.name,
        "team_ids": sorted(t.pk for t in level.teams.all()),
        "inherits_from_id": level.inherits_from_id,
        "member_count": KINDS[kind][1].objects.filter(level=level).count(),
        "inherited_by_count": level.inherited_by.count(),
    }


def list_levels(actor, kind: str) -> list[dict]:
    require_settings(actor)
    return [level_out(level, kind) for level in _model(kind).objects.prefetch_related("teams").order_by("name")]


def get_level(kind: str, level_id):
    return _model(kind).objects.filter(pk=level_id).first()


def _validate(kind, level, name, team_ids, inherits_from_id):
    model = _model(kind)
    errors = {}
    name = (name or "").strip()
    if not name:
        errors["name"] = ["This field is required."]
    elif model.objects.filter(name__iexact=name).exclude(pk=level.pk).exists():
        errors["name"] = ["A level with this name already exists."]
    wanted = set(team_ids)
    teams = list(Team.objects.filter(pk__in=wanted))
    if len(teams) != len(wanted):
        errors["team_ids"] = ["Unknown team."]
    parent = None
    if inherits_from_id is not None:
        parent = model.objects.filter(pk=inherits_from_id).first()
        if parent is None:
            errors["inherits_from_id"] = ["Unknown level."]
    if errors:
        raise Invalid("Please correct the highlighted fields.", errors)
    level.name, level.inherits_from = name, parent
    try:
        level.clean()  # loop detection
    except ValidationError as e:
        raise Invalid("Please correct the highlighted fields.", {"inherits_from_id": [str(m) for m in e.messages]}) from e
    return teams


def _state(level) -> dict:
    return {"name": level.name, "teams": sorted(t.name for t in level.teams.all()), "inherits_from": level.inherits_from.name if level.inherits_from_id else None}


@transaction.atomic
def create_level(actor, kind: str, *, name, team_ids, inherits_from_id):
    require_settings(actor)
    level = _model(kind)()
    teams = _validate(kind, level, name, team_ids, inherits_from_id)
    level.save()
    level.teams.set(teams)
    audit.record(actor, f"club.{kind}_level_created", target=level, changes={k: {"from": None, "to": v} for k, v in _state(level).items()})
    return level


@transaction.atomic
def update_level(actor, kind: str, level, *, name, team_ids, inherits_from_id):
    require_settings(actor)
    before = _state(level)
    teams = _validate(kind, level, name, team_ids, inherits_from_id)
    level.save()
    level.teams.set(teams)
    after = _state(level)
    changes = {k: {"from": before[k], "to": after[k]} for k in after if before[k] != after[k]}
    if changes:
        audit.record(actor, f"club.{kind}_level_updated", target=level, changes=changes)
    return level


@transaction.atomic
def delete_level(actor, kind: str, level) -> None:
    require_settings(actor)
    held = KINDS[kind][1].objects.filter(level=level).count()
    children = list(level.inherited_by.order_by("name").values_list("name", flat=True))
    problems = []
    if held:
        problems.append(f"{held} member{'s' if held != 1 else ''} hold{'' if held != 1 else 's'} it")
    if children:
        problems.append("the level" + ("s " if len(children) > 1 else " ") + ", ".join(f"'{c}'" for c in children) + " inherit" + ("" if len(children) > 1 else "s") + " from it")
    if problems:
        msg = f"This level can't be deleted: {' and '.join(problems)}."
        raise Invalid(msg, {"__all__": [msg]})
    snapshot, pk = _state(level), level.pk
    label = level.name
    level.delete()
    entry = audit.record(actor, f"club.{kind}_level_deleted", changes={k: {"from": v, "to": None} for k, v in snapshot.items()})
    entry.target_type, entry.target_id, entry.target_label = f"{kind}level", str(pk), label[:255]
    entry.save(update_fields=["target_type", "target_id", "target_label", "modified"])
