"""Coach endpoints for events, series and the location / opponent pickers (API.md §7.3).

Reading is open to any staff of the team; every write needs a management position
(``403 manager_required``, ``can_edit_events`` in ``GET /context``). Validation is the web coach
screens' own (api.app.coach_events).
"""

import datetime
import uuid
from typing import Annotated

from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from ninja import Query, Router

from events.models import Event, EventSeries, Location, Opponent
from events.services import detach_occurrence, generate_occurrences, propagate_series
from events.services.coach import notify_event_cancelled, notify_event_changed
from events.services.notifications import dispatch_notify_new_event
from events.services.officials import officials_enabled_for
from events.services.recurrence import cancel_occurrence
from management.forms import LocationForm, OpponentForm

from .. import coach_events as ce
from ..coach import CoachScope, coach_scope
from ..coach_schemas import (
    CoachEventIn,
    CoachEventOut,
    CoachEventPatch,
    EventListOut,
    EventUpdateOut,
    LocationIn,
    LocationListOut,
    LocationRefOut,
    OpponentIn,
    OpponentListOut,
    OpponentRefOut,
    SeriesIn,
    SeriesOut,
    SeriesPatch,
)
from ..errors import ApiError
from ..idempotency import idempotent
from ..pagination import paginate
from ..presenters import event_out
from ..schemas import ErrorOut

router = Router(tags=["coach"])

ERRORS = {401: ErrorOut, 403: ErrorOut, 404: ErrorOut}
INVALID = {422: ErrorOut}


def _events():
    return Event.objects.select_related("location", "opponent", "series").prefetch_related("teams", "invited_members", "excluded_members")


def _team_event(coach: CoachScope, event_id) -> Event:
    return get_object_or_404(_events(), pk=event_id, club=coach.club, teams=coach.team)


def _team_series(coach: CoachScope, series_id) -> EventSeries:
    return get_object_or_404(EventSeries.objects.select_related("location", "opponent"), pk=series_id, club=coach.club, teams=coach.team)


def coach_event_out(event: Event, club) -> dict:
    return {
        **event_out(event),
        "series": ce.series_ref(event),
        "location_id": event.location_id,
        "opponent_id": event.opponent_id,
        "is_friendly": event.is_friendly,
        "external_game_id": event.external_game_id,
        "max_referees": event.max_referees,
        "max_officials": event.max_officials if officials_enabled_for(club) else None,
        "invited_member_ids": [member.pk for member in event.invited_members.all()],
        "excluded_member_ids": [member.pk for member in event.excluded_members.all()],
    }


# --- events ---------------------------------------------------------------------------------


@router.get("/teams/{team_id}/events", response={200: EventListOut, **ERRORS}, summary="The team's events")
def list_events(
    request,
    team_id: uuid.UUID,
    from_: Annotated[datetime.datetime | None, Query(alias="from", description="Earliest start (default: now). Pass a past time to look back.")] = None,
    to: Annotated[datetime.datetime | None, Query(description="Latest start, exclusive (default: no limit).")] = None,
    kind: Annotated[str | None, Query(description="Only this kind.")] = None,
    cursor: str | None = None,
    limit: int | None = None,
):
    """Events the team is invited to, soonest first, cancelled ones included (`cancelled: true`).
    Any staff of the team."""
    coach = coach_scope(request, team_id)
    queryset = _events().filter(club=coach.club, teams=coach.team, start__gte=from_ or timezone.now())
    if to:
        queryset = queryset.filter(start__lt=to)
    if kind:
        queryset = queryset.filter(kind=kind)
    page = paginate(queryset, sort_field="start", cursor=cursor, limit=limit)
    return {"items": [coach_event_out(event, coach.club) for event in page.items], "next_cursor": page.next_cursor}


@router.get("/teams/{team_id}/events/{event_id}", response={200: CoachEventOut, **ERRORS}, summary="One event, with what editing needs")
def get_event(request, team_id: uuid.UUID, event_id: uuid.UUID):
    coach = coach_scope(request, team_id)
    return coach_event_out(_team_event(coach, event_id), coach.club)


def _default_title(coach: CoachScope, kind: str) -> str:
    if kind == Event.EventKind.GAME:
        return coach.team.short_name or coach.team.name
    return str(Event.EventKind(kind).label)


@router.post("/teams/{team_id}/events", response={201: CoachEventOut, **ERRORS, **INVALID}, summary="Create an event for the team")
def create_event(request, team_id: uuid.UUID, payload: CoachEventIn):
    """The team is the audience (plus `invited_member_ids`, minus `excluded_member_ids`). Players
    start as `no_response`. With `notify` (the default) everybody invited is told. Send an
    `Idempotency-Key`. Managers only."""
    coach = coach_scope(request, team_id, manager=True)

    def work():
        values = payload.model_dump(exclude={"notify"})
        values["title"] = values["title"].strip() or _default_title(coach, payload.kind)
        values["max_referees"] = values["max_referees"] or 2
        values["max_officials"] = values["max_officials"] or 1
        instance = Event(club=coach.club, created_by=coach.club_scope.me, kind=payload.kind)
        with transaction.atomic():
            event = ce.save_event(ce.build_event_form(coach, values, instance=instance, editing=False, team_pks=[coach.team.pk]))
        if payload.notify:
            dispatch_notify_new_event(str(event.pk))
        return 201, coach_event_out(_team_event(coach, event.pk), coach.club)

    return idempotent(request, work)


@router.patch("/teams/{team_id}/events/{event_id}", response={200: EventUpdateOut, **ERRORS, **INVALID}, summary="Change an event")
def update_event(request, team_id: uuid.UUID, event_id: uuid.UUID, payload: CoachEventPatch):
    """Only the fields sent change. For an occurrence of a series, `scope` says what happens to the
    rest of the series (see the field). Managers only."""
    coach = coach_scope(request, team_id, manager=True)
    event = _team_event(coach, event_id)
    data = payload.model_dump(exclude_unset=True)
    scope = data.pop("scope", "this")
    notify = data.pop("notify", False)
    if scope != "this" and event.series_id is None:
        raise ApiError(422, "not_in_series", _("This event is not part of a series."), fields={"scope": [_("This event is not part of a series.")]})

    before = [team.pk for team in event.teams.all()]
    managed = ce.managed_team_pks(coach)
    form_teams = [pk for pk in before if pk in managed] or [coach.team.pk]
    values = {**ce.event_baseline(event), **data}
    template_touched = ce.TEMPLATE_FIELDS & data.keys()
    updated = 0
    with transaction.atomic():
        event = ce.save_event(ce.build_event_form(coach, values, instance=event, editing=True, team_pks=form_teams))
        ce.keep_other_teams(event, before, managed)
        if scope == "this":
            if event.series_id and not event.detached and template_touched:
                detach_occurrence(event)
        elif template_touched:
            series = ce.split_series(event) if scope == "this_and_following" else event.series
            event.series = series
            updated = ce.apply_to_series(series, event, template_touched)
    if notify:
        notify_event_changed(event)
    return {"event": coach_event_out(_team_event(coach, event.pk), coach.club), "occurrences_updated": updated}


@router.delete("/teams/{team_id}/events/{event_id}", response={204: None, **ERRORS}, summary="Delete or cancel an event")
def delete_event(
    request,
    team_id: uuid.UUID,
    event_id: uuid.UUID,
    keep_record: Annotated[bool, Query(description="For an occurrence of a series: keep it, marked `cancelled`, instead of removing it. A one-off event is always removed.")] = False,
    notify: Annotated[bool, Query(description="Tell everybody invited that it is cancelled. A removed event leaves a notification that opens the calendar.")] = False,
):
    """A series occurrence is cancelled (the series will not regenerate it); a one-off event is
    deleted with its attendance. Managers only."""
    coach = coach_scope(request, team_id, manager=True)
    event = _team_event(coach, event_id)
    removed = not (event.series_id and keep_record)
    with transaction.atomic():
        if notify:
            notify_event_cancelled(event, keep_link=not removed)
        if event.series_id:
            cancel_occurrence(event, hard_delete=removed)
        else:
            event.delete()
    return 204, None


@router.post("/teams/{team_id}/events/{event_id}/detach", response={200: CoachEventOut, **ERRORS, **INVALID}, summary="Stop an occurrence following its series")
def detach_event(request, team_id: uuid.UUID, event_id: uuid.UUID):
    """Later series-wide changes skip it. Managers only."""
    coach = coach_scope(request, team_id, manager=True)
    event = _team_event(coach, event_id)
    if event.series_id is None:
        raise ApiError(422, "not_in_series", _("This event is not part of a series."))
    detach_occurrence(event)
    return coach_event_out(_team_event(coach, event.pk), coach.club)


# --- series ---------------------------------------------------------------------------------


@router.get("/teams/{team_id}/series/{series_id}", response={200: SeriesOut, **ERRORS}, summary="A recurring series")
def get_series(request, team_id: uuid.UUID, series_id: uuid.UUID):
    coach = coach_scope(request, team_id)
    return ce.series_out(_team_series(coach, series_id))


@router.post("/teams/{team_id}/series", response={201: SeriesOut, **ERRORS, **INVALID}, summary="Create a recurring series")
def create_series(request, team_id: uuid.UUID, payload: SeriesIn):
    """Generates the occurrences for the next 90 days now (nobody is notified per occurrence; the
    daily reminders pick them up) and extends nightly. Send an `Idempotency-Key`. Managers only."""
    coach = coach_scope(request, team_id, manager=True)

    def work():
        instance = EventSeries(club=coach.club, kind=payload.kind)
        with transaction.atomic():
            series = ce.save_series(ce.build_series_form(coach, payload.model_dump(), instance=instance, team_pks=[coach.team.pk]))
            created = generate_occurrences(series)
        return 201, ce.series_out(series, created=len(created))

    return idempotent(request, work)


@router.patch("/teams/{team_id}/series/{series_id}", response={200: SeriesOut, **ERRORS, **INVALID}, summary="Change a recurring series")
def update_series(request, team_id: uuid.UUID, series_id: uuid.UUID, payload: SeriesPatch):
    """Only the fields sent change. The template (title, kind, place, opponent, durations, invited
    and excluded people) reaches every upcoming occurrence that still follows the series. A new
    `dtstart` or pattern only decides *future* dates: existing occurrences keep theirs, and ones that
    no longer fit stay until deleted. Managers only."""
    coach = coach_scope(request, team_id, manager=True)
    series = _team_series(coach, series_id)
    values = {**ce.series_baseline(series), **payload.model_dump(exclude_unset=True)}
    with transaction.atomic():
        series = ce.save_series(ce.build_series_form(coach, values, instance=series, team_pks=[team.pk for team in series.teams.all()] or [coach.team.pk]))
        updated = propagate_series(series)
        created = generate_occurrences(series)
    return ce.series_out(series, created=len(created), updated=len(updated))


@router.delete("/teams/{team_id}/series/{series_id}", response={204: None, **ERRORS}, summary="Delete a series and all its occurrences")
def delete_series(request, team_id: uuid.UUID, series_id: uuid.UUID):
    """Removes every occurrence, past ones with their attendance too. To keep history, stop it
    instead: `PATCH` its `until`. Managers only."""
    coach = coach_scope(request, team_id, manager=True)
    _team_series(coach, series_id).delete()
    return 204, None


# --- pickers --------------------------------------------------------------------------------


def _location_out(location) -> dict:
    return {"id": location.pk, "name": location.name, "address": location.address, "city": location.city, "zip_code": location.zip_code, "country": location.country.code, "is_home": location.is_home}


@router.get("/teams/{team_id}/locations", response={200: LocationListOut, **ERRORS}, summary="The club's locations")
def list_locations(request, team_id: uuid.UUID, q: str | None = None):
    """For a picker: every location of the club by name, optionally narrowed by `q` (name or city).
    `{items: [...]}`, no pagination."""
    coach = coach_scope(request, team_id)
    queryset = Location.objects.filter(club=coach.club)
    if q:
        queryset = queryset.filter(name__icontains=q) | queryset.filter(city__icontains=q)
    return {"items": [_location_out(location) for location in queryset.order_by("name")]}


@router.post("/teams/{team_id}/locations", response={201: LocationRefOut, **ERRORS, **INVALID}, summary="Quick-create a location")
def create_location(request, team_id: uuid.UUID, payload: LocationIn):
    """Managers only. Send an `Idempotency-Key`: a repeated tap otherwise makes a second one."""
    coach = coach_scope(request, team_id, manager=True)

    def work():
        form = LocationForm(payload.model_dump())
        if not form.is_valid():
            raise ce.form_error(form)
        location = form.save(commit=False)
        location.club = coach.club
        location.save()
        return 201, _location_out(location)

    return idempotent(request, work)


@router.get("/teams/{team_id}/opponents", response={200: OpponentListOut, **ERRORS}, summary="The club's opponents")
def list_opponents(request, team_id: uuid.UUID, q: str | None = None):
    """For a picker, by name; `q` narrows by name. `{items: [...]}`, no pagination."""
    coach = coach_scope(request, team_id)
    queryset = Opponent.objects.filter(club=coach.club)
    if q:
        queryset = queryset.filter(name__icontains=q)
    return {"items": [{"id": opponent.pk, "name": opponent.name} for opponent in queryset.order_by("name")]}


@router.post("/teams/{team_id}/opponents", response={201: OpponentRefOut, **ERRORS, **INVALID}, summary="Quick-create an opponent")
def create_opponent(request, team_id: uuid.UUID, payload: OpponentIn):
    """Managers only. Send an `Idempotency-Key`."""
    coach = coach_scope(request, team_id, manager=True)

    def work():
        form = OpponentForm(payload.model_dump())
        if not form.is_valid():
            raise ce.form_error(form)
        opponent = form.save(commit=False)
        opponent.club = coach.club
        opponent.save()
        return 201, {"id": opponent.pk, "name": opponent.name}

    return idempotent(request, work)
