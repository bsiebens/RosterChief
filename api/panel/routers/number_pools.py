"""Management API: jersey-number pools (API.md §17).

The JSON counterpart of ``/manage/number-pools`` (management.views.NumberPool*View): any
management staff may read them, creating, editing and deleting is for club admins
(``is_club_admin``). A team is assigned to a pool through the team's own ``pool_id``.
Validation is management.forms.NumberPoolForm plus NumberPool.clean (max at least min).
"""

import uuid

from django.db.models import Count
from django.shortcuts import get_object_or_404
from ninja import Router

from api.core.forms import form_error, post_data
from api.core.idempotency import idempotent
from api.core.schemas import ErrorOut
from api.panel.guards import panel_club, require
from api.panel.schemas import positions as s
from club.services.access import is_club_admin
from management.forms import NumberPoolForm
from teams.models import NumberPool

router = Router(tags=["panel: number pools"])

PREFIX = "/manage/number-pools"
ERRORS = {401: ErrorOut, 403: ErrorOut, 404: ErrorOut}
INVALID = {422: ErrorOut}


def pools_with_counts(club):
    return NumberPool.objects.filter(club=club).annotate(team_count=Count("teams", distinct=True)).order_by("name")


def pool_out(pool) -> dict:
    return {
        "id": pool.pk,
        "name": pool.name,
        "min_number": pool.min_number,
        "max_number": pool.max_number,
        "min_age_gap_years": pool.min_age_gap_years,
        "team_count": pool.team_count if hasattr(pool, "team_count") else pool.teams.count(),
    }


def _pool(club, pool_id) -> NumberPool:
    return get_object_or_404(pools_with_counts(club), pk=pool_id)


def _save(club, values: dict, instance) -> NumberPool:
    form = NumberPoolForm(post_data(values), club=club, instance=instance)
    if not form.is_valid():
        raise form_error(form)
    return form.save()


@router.get(PREFIX, response={200: s.PoolListOut, **ERRORS}, summary="Number pools")
def list_pools(request):
    """By name. Any management staff."""
    club = panel_club(request)
    return {"items": [pool_out(pool) for pool in pools_with_counts(club)]}


@router.post(PREFIX, response={201: s.PoolOut, **ERRORS, **INVALID}, summary="Create a number pool")
def create_pool(request, payload: s.PoolIn):
    """Club admins."""
    club = panel_club(request)
    require(request, club, is_club_admin)

    def work():
        pool = _save(club, payload.dict(), NumberPool(club=club))
        return 201, pool_out(_pool(club, pool.pk))

    return idempotent(request, work)


@router.get(PREFIX + "/{uuid:pool_id}", response={200: s.PoolOut, **ERRORS}, summary="One number pool")
def get_pool(request, pool_id: uuid.UUID):
    club = panel_club(request)
    return pool_out(_pool(club, pool_id))


@router.patch(PREFIX + "/{uuid:pool_id}", response={200: s.PoolOut, **ERRORS, **INVALID}, summary="Edit a number pool")
def update_pool(request, pool_id: uuid.UUID, payload: s.PoolPatch):
    """Send only the fields to change. Club admins."""
    club = panel_club(request)
    require(request, club, is_club_admin)
    pool = _pool(club, pool_id)
    values = {**pool_out(pool), **payload.dict(exclude_unset=True)}
    for key in ("id", "team_count"):
        values.pop(key)
    _save(club, values, pool)
    return pool_out(_pool(club, pool_id))


@router.delete(PREFIX + "/{uuid:pool_id}", response={204: None, **ERRORS}, summary="Delete a number pool")
def remove_pool(request, pool_id: uuid.UUID):
    """Teams in the pool are left without one and its number reservations go with it. Club admins."""
    club = panel_club(request)
    require(request, club, is_club_admin)
    _pool(club, pool_id).delete()
    return 204, None
