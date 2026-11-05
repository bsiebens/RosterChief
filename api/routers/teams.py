"""Teams API (``/api/v1/teams``). Everyone reads; creating, deleting and ordering teams needs the settings permission,
editing a team, its roster and its staff a management position on that team (403), 404 for unknown ids, 422 ``{detail, code, errors}`` for rule
violations. Writes go through ``teams.services.teams``. Static routes are declared before ``/{team_id}``."""

import uuid
from typing import Literal

from ninja import File, Form, Router, UploadedFile
from ninja.errors import HttpError

from api.routers.analytics import analytics_available
from api.schemas_events import TeamAttendanceOut
from api.schemas_teams import (
    EligibleRefereeOut,
    PeopleIn,
    PeopleOut,
    PoolOverviewOut,
    RosterEntryOut,
    RosterIn,
    RosterPatch,
    StaffEntryOut,
    StaffIn,
    StaffPatch,
    TeamBase,
    TeamDetailOut,
    TeamIn,
    TeamListOut,
    TeamNumbersOut,
    TeamOrderIn,
    TeamPatch,
)
from events.services import attendance_report
from members.services import access
from members.services.errors import Invalid
from teams.services import teams as svc

router = Router()


def _settings(request):
    access.require_settings(request.user)


def _season(season_id):
    """The requested season (422 if unknown) or the current one (``None`` when the club has none)."""
    season = svc.current_season(season_id)
    if season_id is not None and season is None:
        raise Invalid("Unknown season.", {"season": ["Unknown season."]})
    return season


def _detail(request, team, season) -> dict:
    return {**svc.team_detail(request.user, team, season), "analytics_available": analytics_available(request)}


def _team(team_id):
    team = svc.get_team(team_id)
    if team is None:
        raise HttpError(404, "Not found.")
    return team


@router.get("", response=list[TeamListOut], summary="List teams with player/staff counts for a season", operation_id="list_teams")
def list_teams(request, season: uuid.UUID | None = None, q: str = "", coach: Literal["any", "none"] = "any"):
    """``coach=none``: only teams without a coach, i.e. nobody on the staff holds a management position in the season."""
    return svc.list_teams(request.user, _season(season), q, coach=coach)


@router.post("", response={201: TeamBase}, summary="Create a team (settings permission)", operation_id="create_team")
def create_team(request, payload: TeamIn):
    _settings(request)
    return 201, svc.team_out(svc.create_team(request.user, payload.dict()))


@router.post("/reorder", response={204: None}, summary="Set the order of the teams (settings permission)", operation_id="reorder_teams")
def reorder_teams(request, payload: TeamOrderIn):
    """``ids`` in the wanted order (ordering becomes 1..n). Teams not listed follow after them."""
    _settings(request)
    svc.reorder_teams(request.user, payload.ids)
    return 204, None


@router.get("/numbers/overview", response=list[PoolOverviewOut], summary="Jersey numbers per pool for a season", operation_id="teams_numbers_overview")
def numbers_overview(request, season: uuid.UUID | None = None, pool: uuid.UUID | None = None):
    return svc.numbers_overview(request.user, _season(season), pool)


@router.get("/{team_id}", response=TeamDetailOut, summary="A team with its roster and staff for a season", operation_id="get_team")
def get_team(request, team_id: uuid.UUID, season: uuid.UUID | None = None):
    team = _team(team_id)
    return _detail(request, team, _season(season))


@router.patch("/{team_id}", response=TeamBase, summary="Edit a team (team manager)", operation_id="update_team")
def update_team(request, team_id: uuid.UUID, payload: TeamPatch):
    team = _team(team_id)
    return svc.team_out(svc.update_team(request.user, team, payload.dict(exclude_unset=True)))


@router.delete("/{team_id}", response={204: None}, summary="Delete a team (settings permission)", operation_id="delete_team")
def delete_team(request, team_id: uuid.UUID):
    _settings(request)
    svc.delete_team(request.user, _team(team_id))
    return 204, None


@router.post("/{team_id}/photo", response=TeamDetailOut, summary="Upload the team photo of a season (team manager)", operation_id="set_team_photo")
def set_team_photo(request, team_id: uuid.UUID, file: File[UploadedFile], season_id: uuid.UUID | None = Form(None)):
    """Multipart: ``file`` (JPEG, PNG or WebP up to 5 MB) and an optional ``season_id`` (default: the current season).
    One photo per team and season; it replaces the previous one."""
    team = _team(team_id)
    season = _season(season_id)
    if season is None:
        raise Invalid("There is no current season.", {"season_id": ["There is no current season."]})
    svc.set_photo(request.user, team, season, file)
    return _detail(request, team, season)


@router.delete("/{team_id}/photo", response={204: None}, summary="Remove the team photo of a season (team manager)", operation_id="remove_team_photo")
def remove_team_photo(request, team_id: uuid.UUID, season: uuid.UUID | None = None):
    team = _team(team_id)
    chosen = _season(season)
    if chosen is not None:
        svc.remove_photo(request.user, team, chosen)
    return 204, None


@router.get("/{team_id}/referees", response=list[EligibleRefereeOut], summary="Referees eligible for this team (referee_officials feature)", operation_id="team_eligible_referees")
def eligible_referees(request, team_id: uuid.UUID):
    if not request.features.has("referee_officials"):
        raise HttpError(404, "Not found.")
    return svc.eligible_referees(_team(team_id))


@router.get("/{team_id}/officials", response=list[EligibleRefereeOut], summary="Officials eligible for this team (referee_officials feature)", operation_id="team_eligible_officials")
def eligible_officials(request, team_id: uuid.UUID):
    if not request.features.has("referee_officials"):
        raise HttpError(404, "Not found.")
    return svc.eligible_officials(_team(team_id))


@router.get("/{team_id}/numbers", response=TeamNumbersOut, summary="A team's numbers, free numbers and numbers taken by others", operation_id="get_team_numbers")
def team_numbers(request, team_id: uuid.UUID, season: uuid.UUID | None = None):
    team = _team(team_id)
    return svc.team_numbers(request.user, team, _season(season))


@router.get("/{team_id}/attendance", response=TeamAttendanceOut, summary="A team's attendance statistics for a season", operation_id="team_attendance")
def team_attendance(request, team_id: uuid.UUID, season: uuid.UUID | None = None):
    """Turnout (present / (present + absent) over past events), sessions, the top 3 attenders and the 3 needing attention
    (at least 3 answers), who missed the last 2 practices, and no-shows (said yes, checked in as not there)."""
    return attendance_report.team_report(_team(team_id), _season(season))


# -- roster --


def _membership(team, membership_id):
    m = svc.get_membership(team, membership_id)
    if m is None:
        raise HttpError(404, "Not found.")
    return m


@router.post("/{team_id}/roster", response={201: RosterEntryOut}, summary="Add a player to a team's roster (team manager)", operation_id="add_team_player")
def add_player(request, team_id: uuid.UUID, payload: RosterIn):
    team = _team(team_id)
    m = svc.add_player(request.user, team, **payload.dict())
    return 201, svc.roster_out(m)


@router.post("/{team_id}/people", response=PeopleOut, summary="Add several players and staff at once (team manager)", operation_id="add_team_people")
def add_people(request, team_id: uuid.UUID, payload: PeopleIn):
    """One row per person and position: a staff position adds them to the staff, any other position to the roster
    (give someone two rows to add them as both). Only members with an active membership in the season; a row that
    fails is reported with the reason and the others are added."""
    data = payload.dict()
    return svc.add_people(request.user, _team(team_id), season_id=data["season_id"], rows=data["rows"])


@router.patch("/{team_id}/roster/{membership_id}", response=RosterEntryOut, summary="Edit a roster entry (team manager)", operation_id="update_team_player")
def update_player(request, team_id: uuid.UUID, membership_id: uuid.UUID, payload: RosterPatch):
    m = _membership(_team(team_id), membership_id)
    return svc.roster_out(svc.update_player(request.user, m, payload.dict(exclude_unset=True)))


@router.delete("/{team_id}/roster/{membership_id}", response={204: None}, summary="Remove a player from the roster (team manager)", operation_id="remove_team_player")
def remove_player(request, team_id: uuid.UUID, membership_id: uuid.UUID):
    svc.remove_player(request.user, _membership(_team(team_id), membership_id))
    return 204, None


# -- staff --


def _assignment(team, assignment_id):
    a = svc.get_assignment(team, assignment_id)
    if a is None:
        raise HttpError(404, "Not found.")
    return a


@router.post("/{team_id}/staff", response={201: StaffEntryOut}, summary="Add a staff member to a team (team manager)", operation_id="add_team_staff")
def add_staff(request, team_id: uuid.UUID, payload: StaffIn):
    team = _team(team_id)
    return 201, svc.staff_out(svc.add_staff(request.user, team, **payload.dict()))


@router.patch("/{team_id}/staff/{assignment_id}", response=StaffEntryOut, summary="Change a staff member's position (team manager)", operation_id="update_team_staff")
def update_staff(request, team_id: uuid.UUID, assignment_id: uuid.UUID, payload: StaffPatch):
    a = _assignment(_team(team_id), assignment_id)
    return svc.staff_out(svc.update_staff(request.user, a, position_id=payload.position_id))


@router.delete("/{team_id}/staff/{assignment_id}", response={204: None}, summary="Remove a staff member from a team (team manager)", operation_id="remove_team_staff")
def remove_staff(request, team_id: uuid.UUID, assignment_id: uuid.UUID):
    svc.remove_staff(request.user, _assignment(_team(team_id), assignment_id))
    return 204, None
