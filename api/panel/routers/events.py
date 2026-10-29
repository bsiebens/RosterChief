"""Management events (``/manage/events``): the API behind the /manage event screens.

Same rules as management.views: anyone with management access may read what
``scoped_to_managed_teams`` shows them (an event outside it is a 404); creating needs a team to
manage, a group to belong to, or the admin role; changing, cancelling, detaching, scoring and
setting attendance need to be an admin, a manager of one of the event's teams or a member of one
of its groups (``403 forbidden``). Everything is scoped to the club on the host; another club's
event is a 404. Validation is ``management.forms.EventForm`` / ``EventSeriesForm``'s own: a JSON
body is turned into the form's POST data and a failed form answers ``422`` with ``fields``.

Not here: the game timeline and moments, referees / officials, tasks, attachments, fees, the
competition fetch and the fixture imports (the web screens keep them), and check-ins.
"""

import datetime
import uuid
from typing import Annotated, Literal

from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from ninja import Query, Router

from api.core.errors import ApiError
from api.core.idempotency import idempotent
from api.core.pagination import paginate
from api.core.schemas import ErrorOut
from api.panel.events_access import NOT_FOUND, _attendance_counts, _can_manage, _event_payload, _events, _fresh_event_payload, _get_event, _get_manageable_event, _manage_sets, _require_manage, _row
from api.panel.guards import panel_club, require
from api.panel.schemas.events import (
    GeneratedOut,
    PanelAttendanceOut,
    PanelAttendanceRowOut,
    PanelEventDetailOut,
    PanelEventIn,
    PanelEventListOut,
    PanelEventOut,
    PanelEventPatch,
    PanelEventUpdateOut,
    PanelLocationListOut,
    PanelOpponentListOut,
    PanelResultIn,
    PanelSeriesIn,
    PanelSeriesListOut,
    PanelSeriesOut,
    PanelSeriesPatch,
)
from api.protected import coach_events as ce
from api.protected.coach_schemas import AttendanceStatusIn
from api.protected.presenters import event_out
from club.models import Season
from club.services.access import groups_manageable_by, is_club_admin, scoped_to_managed_teams, teams_managed_by
from events.models import Attendance, Event, EventSeries, Location, Opponent
from events.services import detach_occurrence, generate_occurrences, propagate_series
from events.services.attendance import CoachAnswerError, set_answer_as_coach
from events.services.coach import notify_event_cancelled, notify_event_changed
from events.services.notifications import dispatch_notify_new_event
from events.services.recurrence import cancel_occurrence
from management.forms import EventForm, EventSeriesForm
from members.models import Member
from teams.models import Team

router = Router(tags=["manage-events"])

ERRORS = {401: ErrorOut, 403: ErrorOut, 404: ErrorOut}
INVALID = {422: ErrorOut}

#: Request keys that are not form fields (they become the form's audience or steer the route).
_AUDIENCE_KEYS = {"team_ids", "group_ids", "club_wide"}


# --- who may do what ------------------------------------------------------------------------


def _can_create(user, club) -> bool:
    return is_club_admin(user, club) or teams_managed_by(user, club).exists() or groups_manageable_by(user, club).exists()


# --- querysets and shapes -------------------------------------------------------------------


def _series_qs(club):
    return EventSeries.objects.filter(club=club).select_related("location", "opponent").prefetch_related("teams", "groups", "invited_members", "excluded_members")


def _get_series(request, club, series_id) -> EventSeries:
    series = scoped_to_managed_teams(_series_qs(club), request.user, club).filter(pk=series_id).first()
    if series is None:
        raise ApiError(404, "not_found", NOT_FOUND)
    return series


def _get_manageable_series(request, club, series_id) -> EventSeries:
    series = _series_qs(club).filter(pk=series_id).first()
    if series is None:
        raise ApiError(404, "not_found", NOT_FOUND)
    _require_manage(request, club, series)
    return series


def _series_payload(request, club, series, *, created=0, updated=0, refetch=True) -> dict:
    if refetch:
        series = _series_qs(club).get(pk=series.pk)
    return {
        **ce.series_out(series, created=created, updated=updated),
        "team_ids": [team.pk for team in series.teams.all()],
        "group_ids": [group.pk for group in series.groups.all()],
        "club_wide": series.club_wide,
        "can_manage": _can_manage(request, club, series),
    }


# --- form plumbing --------------------------------------------------------------------------


def _audience_data(data: dict, team_ids, group_ids, club_wide) -> dict:
    data["groups"] = [str(pk) for pk in group_ids]
    if club_wide:
        data["club_wide"] = "on"
    return data


def _event_form(request, club, values: dict, *, instance, editing: bool, team_ids, group_ids, club_wide: bool):
    data = _audience_data(ce.post_data(values, team_ids), team_ids, group_ids, club_wide)
    return EventForm(data, club=club, user=request.user, editing=editing, auto_titles=True, instance=instance)


def _series_form(request, club, values: dict, *, instance, team_ids, group_ids, club_wide: bool):
    data = ce.post_data({key: value for key, value in values.items() if key not in ("duration_minutes", "weekdays")}, team_ids)
    _audience_data(data, team_ids, group_ids, club_wide)
    data["weekdays"] = list(values.get("weekdays") or [])
    if values.get("duration_minutes") is not None:
        data["duration_hours"], data["duration_minutes"] = divmod(values["duration_minutes"], 60)
    return EventSeriesForm(data, club=club, user=request.user, instance=instance)


def _audience_baseline(obj) -> dict:
    return {"team_ids": [team.pk for team in obj.teams.all()], "group_ids": [group.pk for group in obj.groups.all()], "club_wide": obj.club_wide}


def _my_member(request, club):
    return Member.objects.filter(user=request.user, member_of__club=club).first()


# --- events ---------------------------------------------------------------------------------


@router.get("/manage/events", response={200: PanelEventListOut, **ERRORS}, summary="The club's events")
def list_events(
    request,
    from_: Annotated[datetime.datetime | None, Query(alias="from", description="Earliest start.")] = None,
    to: Annotated[datetime.datetime | None, Query(description="Latest start, exclusive.")] = None,
    team: Annotated[uuid.UUID | None, Query(description="Only events this team is invited to.")] = None,
    season: Annotated[uuid.UUID | None, Query(description="Only this season's events (its own, else those starting inside it).")] = None,
    kind: Annotated[str | None, Query(description="Only this kind.")] = None,
    series: Annotated[uuid.UUID | None, Query(description="Only occurrences of this series.")] = None,
    cancelled: Annotated[bool | None, Query(description="Only cancelled (true) or only live (false) events.")] = None,
    order: Annotated[Literal["asc", "desc"], Query(description="By start: `asc` (default) or `desc` (most recent first).")] = "asc",
    cursor: str | None = None,
    limit: int | None = None,
):
    """What the calendar and the table show, cancelled ones included (`cancelled: true`), limited to
    what the caller may see. Any staff."""
    club = panel_club(request)
    queryset = scoped_to_managed_teams(_events(club), request.user, club)
    if from_:
        queryset = queryset.filter(start__gte=from_)
    if to:
        queryset = queryset.filter(start__lt=to)
    if kind:
        queryset = queryset.filter(kind=kind)
    if series:
        queryset = queryset.filter(series_id=series)
    if cancelled is not None:
        queryset = queryset.filter(cancelled=cancelled)
    if team:
        get_object_or_404(Team.objects.filter(club=club), pk=team)
        queryset = queryset.filter(teams__id=team)
    if season:
        chosen = get_object_or_404(Season.objects.filter(club=club), pk=season)
        queryset = queryset.filter(Q(season=chosen) | Q(season__isnull=True, start__date__gte=chosen.start_date, start__date__lte=chosen.end_date))
    page = paginate(queryset.distinct(), sort_field="start" if order == "asc" else "-start", cursor=cursor, limit=limit)
    return {"items": [_event_payload(request, club, event) for event in page.items], "next_cursor": page.next_cursor}


@router.get("/manage/events/locations", response={200: PanelLocationListOut, **ERRORS}, summary="The club's locations (picker)")
def list_locations(request, q: str | None = None):
    """For a picker: every location of the club by name, `q` narrows by name or city. No pagination."""
    club = panel_club(request)
    queryset = Location.objects.filter(club=club)
    if q:
        queryset = queryset.filter(Q(name__icontains=q) | Q(city__icontains=q))
    return {"items": [{"id": loc.pk, "name": loc.name, "address": loc.address, "city": loc.city, "zip_code": loc.zip_code, "country": loc.country.code, "is_home": loc.is_home} for loc in queryset.order_by("name")]}


@router.get("/manage/events/opponents", response={200: PanelOpponentListOut, **ERRORS}, summary="The club's opponents (picker)")
def list_opponents(request, q: str | None = None):
    club = panel_club(request)
    queryset = Opponent.objects.filter(club=club)
    if q:
        queryset = queryset.filter(name__icontains=q)
    return {"items": [{"id": opponent.pk, "name": opponent.name} for opponent in queryset.order_by("name")]}


@router.post("/manage/events", response={201: PanelEventOut, **ERRORS, **INVALID}, summary="Create an event")
def create_event(request, payload: PanelEventIn):
    """One event; for a repeating one create a series (`POST …/series`). A game's title is its
    match-up and a new training / tournament is simply called that. With `notify` (the default) everybody
    invited is told. Send an `Idempotency-Key`. Needs a team to manage, a group to belong to or the admin role."""
    club = panel_club(request)
    require(request, club, _can_create)

    def work():
        values = payload.model_dump(exclude={"notify", *_AUDIENCE_KEYS})
        values["max_referees"] = values["max_referees"] or 2
        values["max_officials"] = values["max_officials"] or 1
        instance = Event(club=club, created_by=_my_member(request, club))
        with transaction.atomic():
            form = _event_form(request, club, values, instance=instance, editing=False, team_ids=payload.team_ids, group_ids=payload.group_ids, club_wide=payload.club_wide)
            event = ce.save_event(form)
        if payload.notify:
            dispatch_notify_new_event(str(event.pk))
        return 201, _fresh_event_payload(request, club, event.pk)

    return idempotent(request, work)


@router.get("/manage/events/{uuid:event_id}", response={200: PanelEventDetailOut, **ERRORS}, summary="One event, with its attendance counts")
def get_event(request, event_id: uuid.UUID):
    club = panel_club(request)
    event = _get_event(request, club, event_id)
    counts = _attendance_counts(event)
    return {**_event_payload(request, club, event), "attendance_counts": counts}


@router.patch("/manage/events/{uuid:event_id}", response={200: PanelEventUpdateOut, **ERRORS, **INVALID}, summary="Change an event")
def update_event(request, event_id: uuid.UUID, payload: PanelEventPatch):
    """Only the fields sent change. For an occurrence of a series, `scope` says what happens to the
    rest of the series (see the field)."""
    club = panel_club(request)
    event = _get_manageable_event(request, club, event_id)
    data = payload.model_dump(exclude_unset=True)
    scope = data.pop("scope", "this")
    notify = data.pop("notify", False)
    if scope != "this" and event.series_id is None:
        raise ApiError(422, "not_in_series", _("This event is not part of a series."), fields={"scope": [_("This event is not part of a series.")]})

    _admin, managed_teams, managed_groups = _manage_sets(request, club)
    before_teams = [team.pk for team in event.teams.all()]
    before_groups = [group.pk for group in event.groups.all()]
    team_ids = data.pop("team_ids", None)
    group_ids = data.pop("group_ids", None)
    club_wide = data.pop("club_wide", None)
    # The form only offers what the caller manages; the rest of an existing audience is kept below.
    team_ids = [pk for pk in before_teams if pk in managed_teams] if team_ids is None else team_ids
    group_ids = [pk for pk in before_groups if pk in managed_groups] if group_ids is None else group_ids
    club_wide = event.club_wide if club_wide is None else club_wide

    values = {**ce.event_baseline(event), **{key: value for key, value in data.items() if key not in _AUDIENCE_KEYS}}
    template_touched = ce.TEMPLATE_FIELDS & data.keys()
    updated = 0
    with transaction.atomic():
        form = _event_form(request, club, values, instance=event, editing=True, team_ids=team_ids, group_ids=group_ids, club_wide=club_wide)
        event = ce.save_event(form)
        ce.keep_other_teams(event, before_teams, managed_teams)
        other_groups = [pk for pk in before_groups if pk not in managed_groups]
        if other_groups:
            event.groups.add(*other_groups)
        if scope == "this":
            if event.series_id and not event.detached and template_touched:
                detach_occurrence(event)
        elif template_touched:
            series = ce.split_series(event) if scope == "this_and_following" else event.series
            event.series = series
            updated = ce.apply_to_series(series, event, template_touched)
    if notify:
        notify_event_changed(event)
    return {"event": _fresh_event_payload(request, club, event.pk), "occurrences_updated": updated}


@router.delete("/manage/events/{uuid:event_id}", response={204: None, **ERRORS}, summary="Delete or cancel an event")
def delete_event(
    request,
    event_id: uuid.UUID,
    keep_record: Annotated[bool, Query(description="For an occurrence of a series: keep it, marked `cancelled`, instead of removing it. A one-off event is always removed.")] = False,
    notify: Annotated[bool, Query(description="Tell everybody invited that it is cancelled.")] = False,
):
    """A series occurrence is cancelled (the series will not regenerate it); a one-off event is
    deleted with its attendance."""
    club = panel_club(request)
    event = _get_manageable_event(request, club, event_id)
    removed = not (event.series_id and keep_record)
    with transaction.atomic():
        if notify:
            notify_event_cancelled(event, keep_link=not removed)
        if event.series_id:
            cancel_occurrence(event, hard_delete=removed)
        else:
            event.delete()
    return 204, None


@router.post("/manage/events/{uuid:event_id}/detach", response={200: PanelEventOut, **ERRORS, **INVALID}, summary="Stop an occurrence following its series")
def detach_event(request, event_id: uuid.UUID):
    """Later series-wide changes skip it."""
    club = panel_club(request)
    event = _get_manageable_event(request, club, event_id)
    if event.series_id is None:
        raise ApiError(422, "not_in_series", _("This event is not part of a series."))
    detach_occurrence(event)
    return _fresh_event_payload(request, club, event.pk)


@router.put("/manage/events/{uuid:event_id}/result", response={200: PanelEventOut, **ERRORS, **INVALID}, summary="Enter a game's result")
def set_result(request, event_id: uuid.UUID, payload: PanelResultIn):
    """The final (or running, with `is_live`) score of a game. Both scores or neither: `null` clears it."""
    club = panel_club(request)
    event = _get_manageable_event(request, club, event_id)
    if event.kind != Event.EventKind.GAME:
        raise ApiError(422, "not_a_game", _("Only a game has a result."))
    if (payload.score_for is None) != (payload.score_against is None):
        message = _("Enter both scores, or neither.")
        raise ApiError(422, "validation_error", message, fields={"score_for": [str(message)], "score_against": [str(message)]})

    audience = _audience_baseline(event)
    values = {**ce.event_baseline(event), **payload.model_dump()}
    with transaction.atomic():
        form = _event_form(request, club, values, instance=event, editing=True, team_ids=audience["team_ids"], group_ids=audience["group_ids"], club_wide=audience["club_wide"])
        event = ce.save_event(form)
    return _fresh_event_payload(request, club, event.pk)


# --- attendance -----------------------------------------------------------------------------


@router.get("/manage/events/{uuid:event_id}/attendance", response={200: PanelAttendanceOut, **ERRORS}, summary="Who is invited and what they answered")
def attendance_list(request, event_id: uuid.UUID, status: Annotated[str | None, Query(description="Only this status (`present`, `absent`, `excused`, `selected`, `not_selected`, `maybe`, `no_response`).")] = None):
    """One row per invited person, by last name. `counts` cover everybody whatever the filter.
    `coach_note` is only filled for someone who may manage the event. Any staff who can see the event."""
    club = panel_club(request)
    event = _get_event(request, club, event_id)
    rows = Attendance.objects.filter(event=event).select_related("member").order_by("member__last_name", "member__first_name")
    counts = _attendance_counts(event)
    if status:
        rows = rows.filter(status=status)
    private = _can_manage(request, club, event)
    return {"event": event_out(event), "counts": {**counts, "total": sum(counts.values())}, "items": [_row(row, with_private=private) for row in rows]}


@router.post("/manage/events/{uuid:event_id}/attendance/{attendance_id}/status", response={200: PanelAttendanceRowOut, **ERRORS, 409: ErrorOut, 422: ErrorOut}, summary="Set a person's answer")
def set_attendance_status(request, event_id: uuid.UUID, attendance_id: uuid.UUID, payload: AttendanceStatusIn):
    """Sets the answer (`present`, `absent`, `maybe`, or `no_response` to reset it) and/or the
    private coach note on the person's behalf. Works after the reply deadline and after the
    start; a cancelled event is `409 event_cancelled`, a person in the published line-up can only
    be marked `absent` (else `422 invalid_status`). Accepts an `Idempotency-Key`."""
    club = panel_club(request)
    event = _get_manageable_event(request, club, event_id)
    actor = _my_member(request, club)

    def work():
        with transaction.atomic():
            attendance = get_object_or_404(Attendance.objects.select_for_update().select_related("event", "member"), pk=attendance_id, event=event)
            try:
                set_answer_as_coach(attendance, payload.status, payload.coach_note, coach=actor, notify=payload.notify)
            except CoachAnswerError as exc:
                raise ApiError(422 if exc.code == "invalid_status" else 409, exc.code, exc.message) from exc
        return 200, _row(attendance, with_private=True)

    return idempotent(request, work)[1]


# --- series ---------------------------------------------------------------------------------


@router.get("/manage/events/series", response={200: PanelSeriesListOut, **ERRORS}, summary="The club's recurring series")
def list_series(request, cursor: str | None = None, limit: int | None = None):
    """Newest first, limited to what the caller may see. Any staff."""
    club = panel_club(request)
    queryset = scoped_to_managed_teams(_series_qs(club), request.user, club)
    page = paginate(queryset, sort_field="-dtstart", cursor=cursor, limit=limit)
    return {"items": [_series_payload(request, club, series, refetch=False) for series in page.items], "next_cursor": page.next_cursor}


@router.post("/manage/events/series", response={201: PanelSeriesOut, **ERRORS, **INVALID}, summary="Create a recurring series")
def create_series(request, payload: PanelSeriesIn):
    """Weekly (on `weekdays`) or monthly every `interval`, or a raw `advanced_rrule`. Generates the
    occurrences for the next 90 days now (`occurrences_created`; nobody is notified per
    occurrence) and extends nightly. Send an `Idempotency-Key`. Needs a team to manage, a group to
    belong to or the admin role."""
    club = panel_club(request)
    require(request, club, _can_create)

    def work():
        values = payload.model_dump(exclude=_AUDIENCE_KEYS)
        with transaction.atomic():
            form = _series_form(request, club, values, instance=EventSeries(club=club), team_ids=payload.team_ids, group_ids=payload.group_ids, club_wide=payload.club_wide)
            series = ce.save_series(form)
            created = generate_occurrences(series)
        return 201, _series_payload(request, club, series, created=len(created))

    return idempotent(request, work)


@router.get("/manage/events/series/{uuid:series_id}", response={200: PanelSeriesOut, **ERRORS}, summary="A recurring series")
def get_series(request, series_id: uuid.UUID):
    """Its occurrences: `GET /manage/events?series=<id>`."""
    club = panel_club(request)
    return _series_payload(request, club, _get_series(request, club, series_id))


@router.patch("/manage/events/series/{uuid:series_id}", response={200: PanelSeriesOut, **ERRORS, **INVALID}, summary="Change a recurring series")
def update_series(request, series_id: uuid.UUID, payload: PanelSeriesPatch):
    """Only what is sent changes. The template reaches every upcoming occurrence that still follows
    the series; a new `dtstart` or pattern only decides *future* dates, occurrences that no longer fit
    stay until cancelled."""
    club = panel_club(request)
    series = _get_manageable_series(request, club, series_id)
    data = payload.model_dump(exclude_unset=True)
    _admin, managed_teams, managed_groups = _manage_sets(request, club)
    before_teams = [team.pk for team in series.teams.all()]
    before_groups = [group.pk for group in series.groups.all()]
    team_ids = data.pop("team_ids", None)
    group_ids = data.pop("group_ids", None)
    club_wide = data.pop("club_wide", None)
    team_ids = [pk for pk in before_teams if pk in managed_teams] if team_ids is None else team_ids
    group_ids = [pk for pk in before_groups if pk in managed_groups] if group_ids is None else group_ids
    club_wide = series.club_wide if club_wide is None else club_wide

    values = ce.series_baseline(series)
    if values["frequency"] is None:
        # A hand-written / imported rule: keep it verbatim unless the pattern itself is changed.
        values["frequency"] = "weekly"
        if not ({"frequency", "interval", "weekdays"} & data.keys()):
            values["advanced_rrule"] = series.rrule
    values.update(data)
    with transaction.atomic():
        form = _series_form(request, club, values, instance=series, team_ids=team_ids, group_ids=group_ids, club_wide=club_wide)
        series = ce.save_series(form)
        other_teams = [pk for pk in before_teams if pk not in managed_teams]
        if other_teams:
            series.teams.add(*other_teams)
        other_groups = [pk for pk in before_groups if pk not in managed_groups]
        if other_groups:
            series.groups.add(*other_groups)
        updated = propagate_series(series)
        created = generate_occurrences(series)
    return _series_payload(request, club, series, created=len(created), updated=len(updated))


@router.delete("/manage/events/series/{uuid:series_id}", response={204: None, **ERRORS}, summary="Delete a series and all its occurrences")
def delete_series(request, series_id: uuid.UUID):
    """Removes every occurrence, past ones with their attendance too. To keep history, stop it instead."""
    club = panel_club(request)
    _get_manageable_series(request, club, series_id).delete()
    return 204, None


@router.post("/manage/events/series/{uuid:series_id}/stop", response={200: PanelSeriesOut, **ERRORS}, summary="Stop a series generating new occurrences")
def stop_series(request, series_id: uuid.UUID):
    """Sets `until` to now; existing occurrences and their attendance are untouched."""
    club = panel_club(request)
    series = _get_manageable_series(request, club, series_id)
    series.until = timezone.now()
    series.save(update_fields=["until"])
    return _series_payload(request, club, series)


@router.post("/manage/events/series/{uuid:series_id}/generate", response={200: GeneratedOut, **ERRORS}, summary="Generate the series' upcoming occurrences")
def generate_series(request, series_id: uuid.UUID):
    club = panel_club(request)
    series = _get_manageable_series(request, club, series_id)
    return {"occurrences_created": len(generate_occurrences(series))}
