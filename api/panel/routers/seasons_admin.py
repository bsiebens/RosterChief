"""Management API: creating, editing and deleting seasons (API.md §17).

Extends the read-only ``GET /manage/seasons`` of routers/teams.py under the same prefix. A path
can only answer several methods when they sit on one Router (two routers with the same path
resolve to the first, 405 for the other's methods), so ``POST /manage/seasons`` is registered on
the teams router; the ``{season_id}`` routes have their own. The web
has no season screen (seasons are generated from the club's season settings,
club.services.seasons.generate_seasons), so this is the only caller of the hand-made-season
services next to it. Club admins only (``is_club_admin``, the rule of the club settings).
"""

import uuid

from django.shortcuts import get_object_or_404
from ninja import Router

from api.core.errors import ApiError
from api.core.idempotency import idempotent
from api.core.schemas import ErrorOut
from api.panel.guards import panel_club, require
from api.panel.routers.teams import router as teams_router
from api.panel.schemas.seasons_admin import SeasonIn, SeasonPatch
from api.panel.schemas.teams import SeasonOut
from club.models import Season
from club.services.access import current_season, is_club_admin
from club.services.seasons import SeasonError, create_season, delete_season, update_season

router = Router(tags=["panel: seasons"])

PREFIX = "/manage/seasons"
ERRORS = {401: ErrorOut, 403: ErrorOut, 404: ErrorOut}
INVALID = {422: ErrorOut}
CONFLICT = {409: ErrorOut}


def _club(request):
    club = panel_club(request)
    require(request, club, is_club_admin)
    return club


def _out(season) -> dict:
    current = current_season(season.club)
    return {"id": season.pk, "name": season.name, "start_date": season.start_date, "end_date": season.end_date, "is_current": current is not None and season.pk == current.pk}


def _invalid(error: SeasonError) -> ApiError:
    return ApiError(422, "validation_error", fields={error.field or "non_field_errors": [str(error)]})


@teams_router.post(PREFIX, response={201: SeasonOut, **ERRORS, **INVALID}, summary="Create a season")
def add_season(request, payload: SeasonIn):
    """422 on `end_date` unless it is after `start_date`, 422 on `start_date` when the dates overlap another season. Club admins."""
    club = _club(request)

    def work():
        try:
            season = create_season(club, payload.start_date, payload.end_date)
        except SeasonError as error:
            raise _invalid(error) from error
        return 201, _out(season)

    return idempotent(request, work)


@router.patch(PREFIX + "/{uuid:season_id}", response={200: SeasonOut, **ERRORS, **INVALID}, summary="Change a season's dates")
def edit_season(request, season_id: uuid.UUID, payload: SeasonPatch):
    """Send only the dates to change. Same rules as creating. Club admins."""
    club = _club(request)
    season = get_object_or_404(Season.objects.filter(club=club), pk=season_id)
    values = payload.dict(exclude_unset=True)
    try:
        update_season(season, values.get("start_date") or season.start_date, values.get("end_date") or season.end_date)
    except SeasonError as error:
        raise _invalid(error) from error
    return _out(season)


@router.delete(PREFIX + "/{uuid:season_id}", response={204: None, **ERRORS, **CONFLICT}, summary="Delete a season")
def remove_season(request, season_id: uuid.UUID):
    """409 `season_in_use` while memberships, rosters, events ... reference it. Club admins."""
    club = _club(request)
    season = get_object_or_404(Season.objects.filter(club=club), pk=season_id)
    try:
        delete_season(season)
    except SeasonError as error:
        raise ApiError(409, error.code, str(error)) from error
    return 204, None
