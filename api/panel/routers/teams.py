"""Management API: teams, seasons, rosters and staff (API.md §17).

The web screens are management.views (TeamListView ... TeamStaffRemoveView); the rules are the
same predicates and forms:

* reading a team: club/member admins see every team, other staff only the teams they staff this
  season (``can_manage_members`` / ``teams_staffed_by``); anything else is a 404;
* creating, editing and deleting a team: ``can_manage_members``;
* roster and staff changes: a club admin or a manager of *this* team (``teams_managed_by``);
* the roster and staff forms are management.forms.TeamMembershipForm / StaffAssignmentForm, so
  the eligibility, position and jersey-number rules (and their messages) are the web's.
"""

import uuid
from typing import Annotated

from django.db import IntegrityError, transaction
from django.db.models import Case, Count, IntegerField, Q, Value, When
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404
from django.utils.translation import gettext_lazy as _
from ninja import Query, Router

from api.core.errors import ApiError
from api.core.forms import form_error
from api.core.idempotency import idempotent
from api.core.pagination import paginate_keyset
from api.core.schemas import ErrorOut
from api.panel.guards import panel_club, require
from api.panel.schemas.teams import (
    CurrentSeasonOut,
    RosterAddIn,
    RosterEntryOut,
    RosterOut,
    RosterPatch,
    SeasonListOut,
    StaffAddIn,
    StaffEntryOut,
    StaffListOut,
    TeamDetailOut,
    TeamIn,
    TeamListOut,
    TeamPatch,
)
from club.models import ClubMembership, Season
from club.services.access import can_manage_members, current_season, is_club_admin, teams_managed_by, teams_staffed_by
from management.forms import StaffAssignmentForm, TeamForm, TeamMembershipForm
from teams.models import StaffAssignment, Team, TeamMembership

router = Router(tags=["panel: teams"])

ERRORS = {401: ErrorOut, 403: ErrorOut, 404: ErrorOut}
INVALID = {422: ErrorOut}


def _data(values: dict) -> dict:
    """API values -> what a Django form reads from a POST (``*_id`` keys are the form field names)."""
    out = {}
    for key, value in values.items():
        key = key.removesuffix("_id")
        out[key] = "" if value is None else str(value) if isinstance(value, uuid.UUID) else value
    return out


# --- scoping and authority -------------------------------------------------------------------


def _visible_teams(request, club):
    """The web TeamListView/TeamDetailView visibility rule."""
    if can_manage_members(request.user, club):
        return Team.objects.filter(club=club)
    return teams_staffed_by(request.user, club)


def _can_manage_team(user, club, team) -> bool:
    return is_club_admin(user, club) or teams_managed_by(user, club).filter(pk=team.pk).exists()


def _visible_team(request, club, team_id) -> Team:
    return get_object_or_404(_visible_teams(request, club), pk=team_id)


def _managed_team(request, club, team_id) -> Team:
    """A team of this club (404 otherwise) the caller may change the roster/staff of (403 otherwise)."""
    team = get_object_or_404(Team.objects.filter(club=club), pk=team_id)
    require(request, club, lambda user, c: _can_manage_team(user, c, team))
    return team


def _season(club, season_id) -> Season:
    """The named season of this club, else the current one (409 when there is none)."""
    if season_id is not None:
        return get_object_or_404(Season.objects.filter(club=club), pk=season_id)
    season = current_season(club)
    if season is None:
        raise ApiError(409, "no_current_season", _("The club has no current season."))
    return season


# --- presenters ---------------------------------------------------------------------------------


def _season_out(season, current) -> dict:
    return {"id": season.pk, "name": season.name, "start_date": season.start_date, "end_date": season.end_date, "is_current": current is not None and season.pk == current.pk}


def _team_out(team, **extra) -> dict:
    return {
        "id": team.pk,
        "name": team.name,
        "short_name": team.short_name,
        "referee_management": team.referee_management,
        "pool_id": team.pool_id,
        "age_min": team.age_min,
        "age_max": team.age_max,
        "feeds_into_id": team.feeds_into_id,
        "tournament_only": team.tournament_only,
        "ordering": team.ordering,
        "player_count": getattr(team, "player_count", 0),
        "staff_count": getattr(team, "staff_count", 0),
        **extra,
    }


def _with_counts(queryset, season):
    return queryset.annotate(
        player_count=Count("roster", filter=Q(roster__season=season), distinct=True),
        staff_count=Count("staff_assignments", filter=Q(staff_assignments__season=season), distinct=True),
    )


def _roster_entry(membership, pending) -> dict:
    member = membership.member
    return {
        "id": membership.pk,
        "member_id": member.pk,
        "first_name": member.first_name,
        "last_name": member.last_name,
        "position_id": membership.position_id,
        "position": membership.position.name if membership.position_id else "",
        "jersey_number": membership.jersey_number,
        "is_captain": membership.is_captain,
        "is_alternate_captain": membership.is_alternate_captain,
        "sign_up_pending": member.pk in pending,
    }


def _staff_entry(assignment) -> dict:
    member = assignment.member
    return {
        "id": assignment.pk,
        "member_id": member.pk,
        "first_name": member.first_name,
        "last_name": member.last_name,
        "position_id": assignment.position_id,
        "position": assignment.position.name,
        "is_manager": assignment.position.management_position,
    }


def _staff_rows(team, season):
    return StaffAssignment.objects.filter(team=team, season=season).select_related("member", "position").order_by("position__ordering", "member__last_name", "member__first_name")


# --- seasons ------------------------------------------------------------------------------------


@router.get("/manage/seasons", response={200: SeasonListOut, **ERRORS}, summary="The club's seasons")
def list_seasons(request):
    """Every season of the club, newest first. Any staff."""
    club = panel_club(request)
    current = current_season(club)
    return {"items": [_season_out(season, current) for season in Season.objects.filter(club=club).order_by("-start_date")]}


@router.get("/manage/seasons/current", response={200: CurrentSeasonOut, **ERRORS}, summary="The current season")
def get_current_season(request):
    """The season covering today (`season` is `null` when there is none). Any staff."""
    club = panel_club(request)
    current = current_season(club)
    return {"season": _season_out(current, current) if current else None}


# --- teams --------------------------------------------------------------------------------------


@router.get("/manage/teams", response={200: TeamListOut, **ERRORS}, summary="Teams")
def list_teams(request, q: Annotated[str | None, Query(description="Name contains.")] = None, cursor: str | None = None, limit: int | None = None):
    """Admins see every team, other staff the teams they staff this season. Ordered like the web
    list: teams with an `ordering` first (lowest first), then by name. Player and staff counts are
    for the current season."""
    club = panel_club(request)
    teams = _visible_teams(request, club)
    if q and q.strip():
        teams = teams.filter(name__icontains=q.strip())
    teams = _with_counts(teams, current_season(club)).annotate(
        order_flag=Case(When(ordering__isnull=True, then=Value(1)), default=Value(0), output_field=IntegerField()),
        order_value=Coalesce("ordering", 0),
    )
    page = paginate_keyset(teams, fields=["order_flag", "order_value", "name", "pk"], cursor=cursor, limit=limit)
    return {"items": [_team_out(team) for team in page.items], "next_cursor": page.next_cursor}


def _detail(request, club, team_id) -> dict:
    team = get_object_or_404(_with_counts(_visible_teams(request, club), current_season(club)), pk=team_id)
    return _team_out(team, can_manage=_can_manage_team(request.user, club, team), can_edit=can_manage_members(request.user, club))


@router.get("/manage/teams/{team_id}", response={200: TeamDetailOut, **ERRORS}, summary="One team")
def get_team(request, team_id: uuid.UUID):
    club = panel_club(request)
    return _detail(request, club, team_id)


def _save_team(form) -> Team:
    if not form.is_valid():
        raise form_error(form)
    return form.save()


@router.post("/manage/teams", response={201: TeamDetailOut, **ERRORS, **INVALID}, summary="Create a team")
def create_team(request, payload: TeamIn):
    """Club admins and member admins."""
    club = panel_club(request)
    require(request, club, can_manage_members)

    def work():
        with transaction.atomic():
            team = _save_team(TeamForm(_data(payload.dict()), club=club, instance=Team(club=club)))
        return 201, _detail(request, club, team.pk)

    return idempotent(request, work)


@router.patch("/manage/teams/{team_id}", response={200: TeamDetailOut, **ERRORS, **INVALID}, summary="Edit a team")
def update_team(request, team_id: uuid.UUID, payload: TeamPatch):
    """Send only the fields to change. Club admins and member admins."""
    club = panel_club(request)
    require(request, club, can_manage_members)
    team = get_object_or_404(Team.objects.filter(club=club), pk=team_id)
    current = {
        "name": team.name,
        "short_name": team.short_name,
        "referee_management": team.referee_management,
        "pool": team.pool_id,
        "age_min": team.age_min,
        "age_max": team.age_max,
        "feeds_into": team.feeds_into_id,
        "tournament_only": team.tournament_only,
        "ordering": team.ordering,
    }
    current.update(_data(payload.dict(exclude_unset=True)))
    with transaction.atomic():
        _save_team(TeamForm(_data(current), club=club, instance=team))
    return _detail(request, club, team_id)


@router.delete("/manage/teams/{team_id}", response={204: None, **ERRORS}, summary="Delete a team")
def delete_team(request, team_id: uuid.UUID):
    """Also removes the team's rosters and staff assignments. Club admins and member admins."""
    club = panel_club(request)
    require(request, club, can_manage_members)
    get_object_or_404(Team.objects.filter(club=club), pk=team_id).delete()
    return 204, None


# --- roster -------------------------------------------------------------------------------------


@router.get("/manage/teams/{team_id}/roster", response={200: RosterOut, **ERRORS}, summary="A team's players and staff in a season")
def get_roster(request, team_id: uuid.UUID, season_id: uuid.UUID | None = None):
    """`season_id` defaults to the current season (both lists are empty when the club has none)."""
    club = panel_club(request)
    team = _visible_team(request, club, team_id)
    season = get_object_or_404(Season.objects.filter(club=club), pk=season_id) if season_id else current_season(club)
    if season is None:
        return {"season_id": None, "players": [], "staff": []}
    memberships = list(TeamMembership.objects.filter(team=team, season=season).select_related("member", "position").order_by("position__ordering", "member__last_name", "member__first_name"))
    pending = set(ClubMembership.objects.filter(club=club, season=season, member_id__in=[m.member_id for m in memberships], status=ClubMembership.StatusChoices.PENDING).values_list("member_id", flat=True))
    return {"season_id": season.pk, "players": [_roster_entry(m, pending) for m in memberships], "staff": [_staff_entry(a) for a in _staff_rows(team, season)]}


def _roster_form(request, club, team, season, instance, values) -> TeamMembershipForm:
    return TeamMembershipForm(_data(values), instance=instance, club=club, team=team, season=season, can_override=can_manage_members(request.user, club))


def _save_roster(form) -> TeamMembership:
    if not form.is_valid():
        raise form_error(form)
    try:
        with transaction.atomic():
            return form.save()
    except IntegrityError:
        raise ApiError(422, "validation_error", fields={"jersey_number": [str(_("That player could not be saved -- check the jersey number and try again."))]}) from None


def _roster_out(membership) -> dict:
    membership = TeamMembership.objects.select_related("member", "position").get(pk=membership.pk)
    pending = ClubMembership.objects.filter(club=membership.team.club, season=membership.season, member=membership.member, status=ClubMembership.StatusChoices.PENDING).exists()
    return _roster_entry(membership, {membership.member_id} if pending else set())


@router.post("/manage/teams/{team_id}/roster", response={201: RosterEntryOut, **ERRORS, **INVALID}, summary="Add a player to the roster")
def add_player(request, team_id: uuid.UUID, payload: RosterAddIn):
    """A club admin or a manager of this team. The member must be eligible (active this or next
    season); the jersey number must be free in the team (and in its number pool, unless a member
    admin sets `override_conflict`)."""
    club = panel_club(request)
    team = _managed_team(request, club, team_id)
    season = _season(club, payload.season_id)
    values = payload.dict(exclude={"season_id"})

    def work():
        form = _roster_form(request, club, team, season, TeamMembership(team=team, season=season), values)
        return 201, _roster_out(_save_roster(form))

    return idempotent(request, work)


def _membership(club, team_id, membership_id) -> TeamMembership:
    return get_object_or_404(TeamMembership.objects.filter(team__club=club, team__pk=team_id).select_related("team", "season", "member", "position"), pk=membership_id)


@router.patch("/manage/teams/{team_id}/roster/{membership_id}", response={200: RosterEntryOut, **ERRORS, **INVALID}, summary="Edit a roster entry")
def update_player(request, team_id: uuid.UUID, membership_id: uuid.UUID, payload: RosterPatch):
    """Position, jersey number, captaincy. Send only what changes."""
    club = panel_club(request)
    membership = _membership(club, team_id, membership_id)
    require(request, club, lambda user, c: _can_manage_team(user, c, membership.team))
    values = {"member": membership.member_id, "position": membership.position_id, "jersey_number": membership.jersey_number, "is_captain": membership.is_captain, "is_alternate_captain": membership.is_alternate_captain}
    values.update(_data(payload.dict(exclude_unset=True)))
    form = _roster_form(request, club, membership.team, membership.season, membership, values)
    return _roster_out(_save_roster(form))


@router.delete("/manage/teams/{team_id}/roster/{membership_id}", response={204: None, **ERRORS}, summary="Remove a player from the roster")
def remove_player(request, team_id: uuid.UUID, membership_id: uuid.UUID):
    club = panel_club(request)
    membership = _membership(club, team_id, membership_id)
    require(request, club, lambda user, c: _can_manage_team(user, c, membership.team))
    membership.delete()
    return 204, None


# --- staff --------------------------------------------------------------------------------------


@router.get("/manage/teams/{team_id}/staff", response={200: StaffListOut, **ERRORS}, summary="A team's staff assignments in a season")
def list_staff(request, team_id: uuid.UUID, season_id: uuid.UUID | None = None):
    club = panel_club(request)
    team = _visible_team(request, club, team_id)
    season = get_object_or_404(Season.objects.filter(club=club), pk=season_id) if season_id else current_season(club)
    if season is None:
        return {"season_id": None, "items": []}
    return {"season_id": season.pk, "items": [_staff_entry(a) for a in _staff_rows(team, season)]}


@router.post("/manage/teams/{team_id}/staff", response={201: StaffEntryOut, **ERRORS, **INVALID}, summary="Assign staff to a team")
def add_staff(request, team_id: uuid.UUID, payload: StaffAddIn):
    """A club admin or a manager of this team. `position_id` must be a staff position."""
    club = panel_club(request)
    team = _managed_team(request, club, team_id)
    season = _season(club, payload.season_id)
    values = payload.dict(exclude={"season_id"})

    def work():
        form = StaffAssignmentForm(_data(values), instance=StaffAssignment(team=team, season=season), club=club, team=team, season=season)
        if not form.is_valid():
            raise form_error(form)
        try:
            with transaction.atomic():
                assignment = form.save()
        except IntegrityError:
            raise ApiError(422, "validation_error", fields={"member_id": [str(_("That person is already assigned to this team this season."))]}) from None
        return 201, _staff_entry(StaffAssignment.objects.select_related("member", "position").get(pk=assignment.pk))

    return idempotent(request, work)


@router.delete("/manage/teams/{team_id}/staff/{assignment_id}", response={204: None, **ERRORS}, summary="Remove a staff assignment")
def remove_staff(request, team_id: uuid.UUID, assignment_id: uuid.UUID):
    club = panel_club(request)
    assignment = get_object_or_404(StaffAssignment.objects.filter(team__club=club, team__pk=team_id).select_related("team"), pk=assignment_id)
    require(request, club, lambda user, c: _can_manage_team(user, c, assignment.team))
    assignment.delete()
    return 204, None
