"""``/tournaments``: the match attributes of a tournament (extra per-match numbers such as shots or penalty minutes, usable as standings
tie-breakers) and the game timeline of a match (goals, penalties ...). Reading needs either tournament permission, changing anything needs
``tournaments.operate_tournaments``. Goal events fill in the score and timeline attributes follow the events (``tournaments.services.timeline``)."""

import uuid

from django.db import transaction
from django.db.models import Max
from django.utils.text import slugify
from ninja import Router

from api.auth import AUTHENTICATORS
from api.routers import tournaments_common as c
from api.routers.tournaments_competition import _match
from api.schemas_tournaments import AttributeIn, AttributePatch, EventIn, EventPatch
from formbuilder.services.management import Conflict
from members.services.errors import Invalid
from tournaments.models import MatchAttribute, MatchEvent, MatchEventType
from tournaments.services import timeline
from tournaments.services.scheduling import ATTR_PREFIX, stage_tiebreakers

router = Router(auth=AUTHENTICATORS)


def attribute_out(attribute: MatchAttribute) -> dict:
    timeline_source = attribute.source == MatchAttribute.Source.TIMELINE
    return {
        "id": attribute.pk, "name": attribute.name, "key": attribute.key, "source": attribute.source,
        "event_type": attribute.event_type if timeline_source else None, "aggregate": attribute.aggregate if timeline_source else None, "position": attribute.position,
    }  # fmt: skip


def _attribute(tournament, attribute_id) -> MatchAttribute:
    return c.get_or_404(MatchAttribute, pk=attribute_id, tournament=tournament)


def _need(field, message="Required for a timeline attribute."):
    raise Invalid(message, {field: [message]})


@router.get("/{tournament_id}/attributes", summary="The match attributes of a tournament and the event types a timeline can have", operation_id="tournaments_attributes")
def attributes(request, tournament_id: uuid.UUID):
    c.gate_view(request)
    t = c.get_tournament(tournament_id)
    return {"event_types": [value for value, _label in MatchEventType.choices], "results": [attribute_out(a) for a in t.match_attributes.all()]}


@router.post("/{tournament_id}/attributes", summary="Add a match attribute", operation_id="tournaments_attribute_create", response={201: dict})
def create_attribute(request, tournament_id: uuid.UUID, payload: AttributeIn):
    c.gate_operate(request)
    t = c.get_tournament(tournament_id)
    name = payload.name.strip()
    key = slugify(name).replace("-", "_")[:50]
    if not name or not key:
        raise Invalid("Give the attribute a name with letters or digits.", {"name": ["Give the attribute a name with letters or digits."]})
    if t.match_attributes.filter(key=key).exists():
        raise Invalid("An attribute with this name already exists.", {"name": ["An attribute with this name already exists."]})
    if payload.source == "timeline":
        if not payload.event_type:
            _need("event_type")
        if not payload.aggregate:
            _need("aggregate")
    elif payload.event_type or payload.aggregate:
        _need("event_type" if payload.event_type else "aggregate", "Only for a timeline attribute.")
    last = t.match_attributes.aggregate(n=Max("position"))["n"]
    attribute = MatchAttribute(
        tournament=t, name=name, key=key, source=payload.source, event_type=payload.event_type or "", aggregate=payload.aggregate or "", position=0 if last is None else last + 1
    )  # fmt: skip
    c.validate(attribute)
    with transaction.atomic():
        attribute.save()
        if attribute.source == MatchAttribute.Source.TIMELINE:
            timeline.recompute_attribute(attribute)
    return 201, attribute_out(attribute)


@router.patch("/{tournament_id}/attributes/{attribute_id}", summary="Rename, reorder or (timeline) re-aim a match attribute", operation_id="tournaments_attribute_update")
def update_attribute(request, tournament_id: uuid.UUID, attribute_id: uuid.UUID, payload: AttributePatch):
    c.gate_operate(request)
    attribute = _attribute(c.get_tournament(tournament_id), attribute_id)
    data = payload.dict(exclude_unset=True)
    if attribute.source == MatchAttribute.Source.MANUAL and (data.get("event_type") or data.get("aggregate")):
        _need("event_type" if data.get("event_type") else "aggregate", "Only for a timeline attribute.")
    if data.get("name") is not None:
        attribute.name = data["name"].strip()
    if attribute.source == MatchAttribute.Source.TIMELINE:
        for field in ("event_type", "aggregate"):
            if data.get(field):
                setattr(attribute, field, data[field])
    if data.get("position") is not None:
        attribute.position = data["position"]
    c.validate(attribute)
    with transaction.atomic():
        attribute.save()
        if attribute.source == MatchAttribute.Source.TIMELINE and ({"event_type", "aggregate"} & data.keys()):
            timeline.recompute_attribute(attribute)
    return attribute_out(attribute)


@router.delete("/{tournament_id}/attributes/{attribute_id}", summary="Delete a match attribute (not while a stage tie-breaker uses it)", operation_id="tournaments_attribute_delete", response={204: None})
def delete_attribute(request, tournament_id: uuid.UUID, attribute_id: uuid.UUID):
    c.gate_operate(request)
    t = c.get_tournament(tournament_id)
    attribute = _attribute(t, attribute_id)
    users = [s.name for s in t.stages.order_by("sequence") if any(tb["criterion"] == f"{ATTR_PREFIX}{attribute.key}" for tb in stage_tiebreakers(s))]
    if users:
        raise Conflict(f"This attribute is a tie-breaker of: {', '.join(users)}. Remove it there first.", "in_use")
    attribute.delete()
    return 204, None


# --- the timeline of a match -----------------------------------------------------------------------------------------------

#: Every event type but "other" belongs to a team, so it needs a side; "other" may be neutral (a period end) or belong to a side.
NEUTRAL_TYPES = (MatchEvent.Type.OTHER,)


def event_out(event: MatchEvent) -> dict:
    return {"id": event.pk, "type": event.type, "side": event.side or None, "minute": event.minute, "player": event.player, "note": event.note, "value": event.value}


def _event(event_id) -> MatchEvent:
    return c.get_or_404(MatchEvent.objects.select_related("match"), pk=event_id)


def _check_side(event: MatchEvent):
    event.side = event.side or ""
    if not event.side and event.type not in NEUTRAL_TYPES:
        raise Invalid("Choose home or away for this event.", {"side": ["Choose home or away for this event."]})


def _changing(match, change):
    """Runs ``change`` (which adds, edits or deletes events) and re-syncs the match in one transaction; a change that cannot be pushed
    through to the bracket is rolled back as 409 ``match_complete``."""
    try:
        with transaction.atomic():
            result = change()
            timeline.sync_match(match)
    except timeline.MatchLocked as error:
        raise Conflict(f"{error} Change the result of the match instead.", "match_complete") from error
    return result


@router.get("/matches/{match_id}/events", summary="The timeline of a match", operation_id="tournaments_match_events")
def events(request, match_id: uuid.UUID):
    c.gate_view(request)
    match = _match(match_id)
    return {"match": c.match_out(match), "events": [event_out(e) for e in match.events.all()]}


@router.post("/matches/{match_id}/events", summary="Add an event to the timeline of a match", operation_id="tournaments_match_event_create", response={201: dict})
def create_event(request, match_id: uuid.UUID, payload: EventIn):
    c.gate_operate(request)
    match = _match(match_id)
    event = MatchEvent(match=match, **payload.dict())
    _check_side(event)
    c.validate(event)
    _changing(match, event.save)
    return 201, {**event_out(event), "match": c.match_out(_match(match_id))}


@router.patch("/matches/events/{event_id}", summary="Change an event of a timeline", operation_id="tournaments_match_event_update")
def update_event(request, event_id: uuid.UUID, payload: EventPatch):
    c.gate_operate(request)
    event = _event(event_id)
    match = _match(event.match_id)
    c.apply(event, {k: v for k, v in payload.dict(exclude_unset=True).items() if not (v is None and k in ("type", "player", "note"))})
    _check_side(event)
    c.validate(event)
    _changing(match, event.save)
    return {**event_out(event), "match": c.match_out(_match(match.pk))}


@router.delete("/matches/events/{event_id}", summary="Remove an event from a timeline; returns the updated match", operation_id="tournaments_match_event_delete")
def delete_event(request, event_id: uuid.UUID):
    c.gate_operate(request)
    event = _event(event_id)
    match = _match(event.match_id)
    _changing(match, event.delete)
    return {"match": c.match_out(_match(match.pk))}
