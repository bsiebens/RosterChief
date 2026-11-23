"""``/tournaments``: the match attributes of a tournament (extra per-match numbers such as shots or penalty minutes, usable as standings
tie-breakers) and the game timeline of a match (goals, penalties ...). Reading needs either tournament permission, changing anything needs
``tournaments.operate_tournaments``. Goal events fill in the score and timeline attributes follow the events (``tournaments.services.timeline``)."""

import uuid

from django.db import connection, transaction
from django.db.models import Max
from django.utils.text import slugify
from ninja import Router

from api.auth import AUTHENTICATORS
from api.routers import tournaments_common as c
from api.routers.tournaments_competition import _match
from api.schemas_tournaments import AttributeIn, AttributePatch, EventIn, EventPatch
from formbuilder.services.management import Conflict
from members.services.errors import Invalid
from tournaments.models import MatchAttribute, MatchEvent, MatchEventType, TournamentRosterEntry
from tournaments.services import alerts, timeline
from tournaments.services.scheduling import ATTR_PREFIX, stage_tiebreakers
from tournaments.sports import ICE_HOCKEY_PENALTIES, PENALTY_LENGTHS, is_ice_hockey, normalise_position, penalty_minutes

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


def chronological(match) -> list[MatchEvent]:
    """The events of a match in the order they happened. The clock either counts up (earliest first) or down (time left: the most time
    left came first), so the sort follows the tournament's clock direction; events without a time keep their entry order after the timed ones."""
    rows = list(match.events.select_related("player_entry").all())
    down = match.stage.tournament.clock_direction == "down"
    return sorted(rows, key=lambda e: (e.minute is None, 0 if e.minute is None else (-1 if down else 1) * ((e.minute or 0) * 60 + (e.second or 0)), e.created))


def event_out(event: MatchEvent) -> dict:
    d = event.details or {}
    return {
        "id": event.pk, "type": event.type, "side": event.side or None, "minute": event.minute, "second": event.second, "player": event.player, "player_id": event.player_entry_id,
        "player_number": d.get("number"), "assists": d.get("assists", []), "penalty_code": d.get("penalty_code", ""), "penalty_name": d.get("penalty_name", ""),
        "penalty_length": d.get("penalty_length", ""), "goalie_in": d.get("goalie_in"), "goalie_out": d.get("goalie_out"), "note": event.note, "value": event.value,
    }  # fmt: skip


def _person(entry: TournamentRosterEntry) -> dict:
    return {"id": str(entry.pk), "name": entry.display_name, "number": entry.jersey_number}


def _roster(team) -> list[dict]:
    """The players of a tournament team for the game screen (goalies first is up to the user: the position label is shown)."""
    if team is None:
        return []
    entries = team.roster_entries.select_related("member").filter(role="player").order_by("jersey_number", "pk")
    hockey = is_ice_hockey(getattr(connection.tenant, "sport", None))
    return [{**_person(e), "position": normalise_position(e.position_label) if hockey else e.position_label, "is_goalie": hockey and normalise_position(e.position_label) == "Goalie"} for e in entries]


def _entry(match, side, entry_id, key):
    """A roster player of the team playing on ``side``; anything else is a 422."""
    team = match.home_team if side == "home" else match.away_team
    entry = TournamentRosterEntry.objects.select_related("member").filter(pk=entry_id, tournament_team=team).first() if team is not None else None
    if entry is None:
        message = "Choose a player of the team of this event."
        raise Invalid(message, {key: [message]})
    return entry


def _apply_details(match, event: MatchEvent, data: dict) -> None:
    """Resolves the players and the hockey specifics of an event into ``player``/``player_entry``/``details``. ``data`` holds the extra
    request values; a missing key keeps what the event already has (an edit that only changes the minute)."""
    hockey = is_ice_hockey(getattr(connection.tenant, "sport", None))
    details = dict(event.details or {})
    side = event.side

    def has(key):
        return key in data

    if event.type in (MatchEvent.Type.GOAL, MatchEvent.Type.PENALTY, MatchEvent.Type.OTHER) and has("player_id"):
        if data["player_id"] is None:
            event.player_entry = None
            details.pop("number", None)
        else:
            entry = _entry(match, side, data["player_id"], "player_id")
            event.player_entry, event.player = entry, entry.display_name
            details["number"] = entry.jersey_number
    if event.type == MatchEvent.Type.GOAL and has("assist_ids"):
        ids = list(dict.fromkeys(data["assist_ids"] or []))
        if ids and not hockey:
            raise Invalid("Assists are only kept for ice hockey.", {"assist_ids": ["Assists are only kept for ice hockey."]})
        if event.player_entry_id and event.player_entry_id in ids:
            raise Invalid("The scorer cannot assist their own goal.", {"assist_ids": ["The scorer cannot assist their own goal."]})
        details["assists"] = [_person(_entry(match, side, i, "assist_ids")) for i in ids]
    if event.type == MatchEvent.Type.PENALTY and (has("penalty_code") or has("penalty_length")):
        code = data.get("penalty_code", details.get("penalty_code", ""))
        length = data.get("penalty_length", details.get("penalty_length", ""))
        if hockey:
            names = dict(ICE_HOCKEY_PENALTIES)
            if code not in names:
                raise Invalid("Choose a penalty code.", {"penalty_code": ["Choose a penalty code."]})
            if length not in PENALTY_LENGTHS:
                raise Invalid("Choose how long the penalty is.", {"penalty_length": ["Choose how long the penalty is."]})
            details.update(penalty_code=code, penalty_name=names[code], penalty_length=length)
            event.value = penalty_minutes(length)  # feeds the penalty minutes attribute of the timeline
        elif code or length:
            details.update(penalty_code=code, penalty_name=code, penalty_length=length)
    if event.type == MatchEvent.Type.GOALIE_CHANGE and (has("goalie_in_id") or has("goalie_out_id")):
        for key, field in (("goalie_in", "goalie_in_id"), ("goalie_out", "goalie_out_id")):
            if has(field):
                details[key] = None if data[field] is None else _person(_entry(match, side, data[field], field))
        if details.get("goalie_in") and details.get("goalie_in") == details.get("goalie_out"):
            raise Invalid("The goalie going in and out must differ.", {"goalie_in_id": ["The goalie going in and out must differ."]})
    if event.type == MatchEvent.Type.TIMEOUT:
        event.player, event.player_entry = "", None
        details = {}
    event.details = details


EXTRA = ("player_id", "assist_ids", "penalty_code", "penalty_length", "goalie_in_id", "goalie_out_id")


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
    sport = getattr(connection.tenant, "sport", None)
    hockey = is_ice_hockey(sport)
    tie = [match] if match.leg_number is None else sorted([m for m in [match.leg_of, *match.leg_of.first_legs.all()] if m is not None] if match.leg_of_id else [match, *match.first_legs.all()], key=lambda m: m.leg_number or 0)
    legs = [{"id": m.pk, "leg": m.leg_number, "status": m.status, "score_home": m.score_home, "score_away": m.score_away} for m in tie] if match.leg_number is not None else []
    return {
        "legs": legs, "match": c.match_out(match), "events": [event_out(e) for e in chronological(match)],
        "sport": {"slug": sport.slug, "name": sport.name} if sport else None, "ice_hockey": hockey,
        "penalty_codes": [{"code": code, "name": name} for code, name in ICE_HOCKEY_PENALTIES] if hockey else [], "penalty_lengths": PENALTY_LENGTHS if hockey else [],
        "rosters": {"home": _roster(match.home_team), "away": _roster(match.away_team)},
    }


@router.post("/matches/{match_id}/events", summary="Add an event to the timeline of a match", operation_id="tournaments_match_event_create", response={201: dict})
def create_event(request, match_id: uuid.UUID, payload: EventIn):
    c.gate_operate(request)
    match = _match(match_id)
    data = payload.dict()
    extra = {k: data.pop(k) for k in EXTRA}
    event = MatchEvent(match=match, **data)
    _check_side(event)
    _apply_details(match, event, extra)
    c.validate(event)
    _changing(match, event.save)
    if event.type in (MatchEvent.Type.GOAL, MatchEvent.Type.PENALTY):
        alerts.after_commit(alerts.event_recorded, event.pk)
    return 201, {**event_out(event), "match": c.match_out(_match(match_id))}


@router.patch("/matches/events/{event_id}", summary="Change an event of a timeline", operation_id="tournaments_match_event_update")
def update_event(request, event_id: uuid.UUID, payload: EventPatch):
    c.gate_operate(request)
    event = _event(event_id)
    match = _match(event.match_id)
    data = {k: v for k, v in payload.dict(exclude_unset=True).items() if not (v is None and k in ("type", "player", "note"))}
    extra = {k: data.pop(k) for k in EXTRA if k in data}
    c.apply(event, data)
    _check_side(event)
    _apply_details(match, event, extra)
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
