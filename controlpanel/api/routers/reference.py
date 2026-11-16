"""Shared reference data: sports, referee types and competitions."""

import re
from uuid import UUID

from django.db import transaction
from django.db.models import Count, Max
from django.shortcuts import get_object_or_404
from ninja import Router
from ninja.pagination import paginate

from club.lineup_template import TemplateError, clean_template
from club.models import Competition, RefereeType, Sport
from controlpanel import audit
from features.catalog import CATALOG
from features.models import Feature

from ..errors import ApiError
from ..schemas import (
    CompetitionIn,
    CompetitionOut,
    CompetitionUpdateIn,
    RefereeTypeCreateIn,
    RefereeTypeOut,
    RefereeTypeReorderIn,
    RefereeTypeUpdateIn,
    SportIn,
    SportLineupSlotsIn,
    SportOut,
)

router = Router(tags=["reference data"])

MODULE_RE = re.compile(r"^[A-Za-z_][\w]*(\.[A-Za-z_][\w]*)*$")


def _sport_out(sport) -> SportOut:
    return SportOut(
        id=sport.pk,
        name=sport.name,
        slug=sport.slug,
        club_count=sport.clubs.count(),
        competition_count=sport.competitions.count(),
        referee_type_count=sport.referee_types.count(),
        lineup_slots_enabled=sport.lineup_slots_enabled,
        lineup_template=sport.lineup_template or [],
    )


def _invalid(field: str, message: str) -> ApiError:
    return ApiError(422, "validation_error", message, {field: [message]})


# --- sports -------------------------------------------------------------------------------------------------------


@router.get("/sports", response=list[SportOut], summary="Sports, with how many clubs and competitions use them")
@paginate
def list_sports(request):
    return [_sport_out(s) for s in Sport.objects.annotate(n=Count("clubs")).order_by("name")]


@router.post("/sports", response={201: SportOut})
def create_sport(request, payload: SportIn):
    name = payload.name.strip()
    if not name:
        raise _invalid("name", "Name is required.")
    if Sport.objects.filter(name__iexact=name).exists():
        raise _invalid("name", "A sport with that name already exists.")
    sport = Sport.objects.create(name=name)
    audit.record(request.user, "sport.created", target=sport, data={"name": name})
    return 201, _sport_out(sport)


@router.patch("/sports/{sport_id}", response=SportOut, summary="Rename a sport (its slug never changes)")
def rename_sport(request, sport_id: UUID, payload: SportIn):
    sport = get_object_or_404(Sport, pk=sport_id)
    name = payload.name.strip()
    if not name:
        raise _invalid("name", "Name is required.")
    if Sport.objects.filter(name__iexact=name).exclude(pk=sport.pk).exists():
        raise _invalid("name", "A sport with that name already exists.")
    sport.name = name
    sport.save(update_fields=["name", "modified"])
    audit.record(request.user, "sport.renamed", target=sport, data={"name": name})
    return _sport_out(sport)


@router.put("/sports/{sport_id}/lineup-slots", response=SportOut, summary="Switch line-up lines on or off for a sport and set its template (informational only, never used for statistics)")
def set_sport_lineup_slots(request, sport_id: UUID, payload: SportLineupSlotsIn):
    sport = get_object_or_404(Sport, pk=sport_id)
    try:
        template = clean_template(payload.template)
    except TemplateError as exc:
        raise _invalid("template", str(exc))
    if payload.enabled and not template:
        raise _invalid("template", "Add at least one group with a slot before switching line-up lines on.")
    before = {"enabled": sport.lineup_slots_enabled, "template": sport.lineup_template}
    sport.lineup_slots_enabled, sport.lineup_template = payload.enabled, template
    sport.save(update_fields=["lineup_slots_enabled", "lineup_template", "modified"])
    audit.record(request.user, "sport.lineup_slots_changed", target=sport, data={"before": before, "after": {"enabled": payload.enabled, "template": template}})
    return _sport_out(sport)


@router.delete("/sports/{sport_id}", response={204: None}, summary="Delete a sport nobody uses (its referee types go with it)")
def delete_sport(request, sport_id: UUID):
    sport = get_object_or_404(Sport, pk=sport_id)
    clubs, competitions = sport.clubs.count(), sport.competitions.count()
    if clubs or competitions:
        raise ApiError(409, "sport_in_use", f"In use by {clubs} club(s) and {competitions} competition(s).")
    audit.record(request.user, "sport.deleted", target_type="sport", target_id=sport.pk, data={"name": sport.name})
    sport.delete()
    return 204, None


# --- referee types ------------------------------------------------------------------------------------------------


def _check_kind(kind: str) -> None:
    if kind not in dict(RefereeType.KIND_CHOICES):
        raise _invalid("kind", "Use referee or official.")


def _unique_name(sport, kind, name, exclude=None) -> None:
    qs = RefereeType.objects.filter(sport=sport, kind=kind, name__iexact=name)
    if exclude is not None:
        qs = qs.exclude(pk=exclude.pk)
    if qs.exists():
        raise _invalid("name", "That name is already used for this sport and kind.")


@router.get("/sports/{sport_id}/referee-types", response=list[RefereeTypeOut], summary="Referee/official types of a sport, in display order")
def list_referee_types(request, sport_id: UUID, kind: str | None = None):
    sport = get_object_or_404(Sport, pk=sport_id)
    qs = RefereeType.objects.filter(sport=sport)
    if kind:
        _check_kind(kind)
        qs = qs.filter(kind=kind)
    return [RefereeTypeOut(id=t.pk, sport_id=t.sport_id, kind=t.kind, name=t.name, ordering=t.ordering) for t in qs]


@router.post("/sports/{sport_id}/referee-types", response={201: RefereeTypeOut})
def create_referee_type(request, sport_id: UUID, payload: RefereeTypeCreateIn):
    sport = get_object_or_404(Sport, pk=sport_id)
    _check_kind(payload.kind)
    name = payload.name.strip()
    if not name:
        raise _invalid("name", "Name is required.")
    _unique_name(sport, payload.kind, name)
    last = RefereeType.objects.filter(sport=sport, kind=payload.kind).aggregate(m=Max("ordering"))["m"] or 0
    t = RefereeType.objects.create(sport=sport, kind=payload.kind, name=name, ordering=last + 1)
    audit.record(request.user, "referee_type.created", target=t, data={"sport": sport.slug, "kind": t.kind, "name": name})
    return 201, RefereeTypeOut(id=t.pk, sport_id=sport.pk, kind=t.kind, name=t.name, ordering=t.ordering)


@router.patch("/referee-types/{type_id}", response=RefereeTypeOut, summary="Rename a referee type")
def rename_referee_type(request, type_id: UUID, payload: RefereeTypeUpdateIn):
    t = get_object_or_404(RefereeType, pk=type_id)
    name = payload.name.strip()
    if not name:
        raise _invalid("name", "Name is required.")
    _unique_name(t.sport, t.kind, name, exclude=t)
    t.name = name
    t.save(update_fields=["name", "modified"])
    audit.record(request.user, "referee_type.renamed", target=t, data={"name": name})
    return RefereeTypeOut(id=t.pk, sport_id=t.sport_id, kind=t.kind, name=t.name, ordering=t.ordering)


@router.delete("/referee-types/{type_id}", response={204: None})
def delete_referee_type(request, type_id: UUID):
    t = get_object_or_404(RefereeType, pk=type_id)
    audit.record(request.user, "referee_type.deleted", target_type="refereetype", target_id=t.pk, data={"name": t.name})
    t.delete()
    return 204, None


@router.post("/sports/{sport_id}/referee-types/reorder", response=list[RefereeTypeOut], summary="Set the display order: send every id of that kind, once")
@transaction.atomic
def reorder_referee_types(request, sport_id: UUID, payload: RefereeTypeReorderIn):
    sport = get_object_or_404(Sport, pk=sport_id)
    _check_kind(payload.kind)
    existing = {t.pk: t for t in RefereeType.objects.filter(sport=sport, kind=payload.kind)}
    if len(payload.ids) != len(set(payload.ids)) or set(payload.ids) != set(existing):
        raise _invalid("ids", "Send exactly the ids of all types of this sport and kind, without duplicates.")
    for position, type_id in enumerate(payload.ids, start=1):
        existing[type_id].ordering = position
    RefereeType.objects.bulk_update(existing.values(), ["ordering"])
    audit.record(request.user, "referee_type.reordered", target=sport, data={"kind": payload.kind})
    return [RefereeTypeOut(id=t.pk, sport_id=sport.pk, kind=t.kind, name=t.name, ordering=t.ordering) for t in sorted(existing.values(), key=lambda t: t.ordering)]


# --- competitions -------------------------------------------------------------------------------------------------


def _feature(code: str | None):
    if code is None:
        return None
    if code not in CATALOG:
        raise _invalid("feature_code", f"Unknown feature '{code}'.")
    return Feature.objects.get(code=code)


def _check_module(module: str) -> None:
    if not MODULE_RE.match(module):
        raise _invalid("module", "Use a dotted Python path, e.g. events.competitions.rbihf.")


@router.get("/competitions", response=list[CompetitionOut], summary="Competitions clubs can import fixtures from")
@paginate
def list_competitions(request, sport_id: UUID | None = None):
    qs = Competition.objects.select_related("sport", "feature")
    if sport_id:
        qs = qs.filter(sport_id=sport_id)
    return [CompetitionOut.of(c) for c in qs]


@router.post("/competitions", response={201: CompetitionOut})
def create_competition(request, payload: CompetitionIn):
    _check_module(payload.module)
    sport = get_object_or_404(Sport, pk=payload.sport_id) if payload.sport_id else None
    c = Competition.objects.create(name=payload.name.strip(), module=payload.module, sport=sport, feature=_feature(payload.feature_code))
    audit.record(request.user, "competition.created", target=c, data={"name": c.name})
    return 201, CompetitionOut.of(c)


@router.patch("/competitions/{competition_id}", response=CompetitionOut)
def update_competition(request, competition_id: UUID, payload: CompetitionUpdateIn):
    c = get_object_or_404(Competition.objects.select_related("sport", "feature"), pk=competition_id)
    changes = payload.dict(exclude_unset=True)
    if changes.get("name"):
        c.name = changes["name"].strip()
    if changes.get("module"):
        _check_module(changes["module"])
        c.module = changes["module"]
    if "sport_id" in changes:
        c.sport = get_object_or_404(Sport, pk=changes["sport_id"]) if changes["sport_id"] else None
    if "feature_code" in changes:
        c.feature = _feature(changes["feature_code"])
    c.save()
    audit.record(request.user, "competition.updated", target=c, data={"fields": sorted(changes)})
    return CompetitionOut.of(c)


@router.delete("/competitions/{competition_id}", response={204: None})
def delete_competition(request, competition_id: UUID):
    c = get_object_or_404(Competition, pk=competition_id)
    audit.record(request.user, "competition.deleted", target_type="competition", target_id=c.pk, data={"name": c.name})
    c.delete()
    return 204, None
