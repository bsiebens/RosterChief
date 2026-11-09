"""Team settings API: everyone reads; changes need the settings permission (403 first). Positions (``/team-positions``), number pools and their reservations
(``/number-pools``) and the club-wide referee / official overview (``/officiating/people``, feature ``referee_officials``).
Writes go through ``teams.services.settings``."""

import uuid
from typing import Literal

from ninja import Router
from ninja.errors import HttpError

from api.schemas_team_settings import (
    OfficiatingPersonOut,
    PoolIn,
    PoolOut,
    PoolPatch,
    PositionIn,
    PositionOrderIn,
    PositionOut,
    PositionPatch,
    ReservationIn,
    ReservationOut,
)
from members.services import access
from members.services.errors import NotAllowed
from teams.services import settings as svc

router = Router()


def _admin(request):
    access.require_settings(request.user)


def _found(obj):
    if obj is None:
        raise HttpError(404, "Not found.")
    return obj


# -- positions --


@router.get("/team-positions", response=list[PositionOut], summary="List positions", operation_id="list_team_positions")
def list_positions(request):
    return svc.list_positions(request.user)


@router.post("/team-positions", response={201: PositionOut}, summary="Create a position", operation_id="create_team_position")
def create_position(request, payload: PositionIn):
    _admin(request)
    return 201, svc.position_out(svc.create_position(request.user, **payload.dict()))


@router.post("/team-positions/reorder", response=list[PositionOut], summary="Set the order of the positions", operation_id="reorder_team_positions")
def reorder_positions(request, payload: PositionOrderIn):
    """``ids`` in the wanted order (ordering becomes 1..n); positions not listed follow after. Returns the new list."""
    return svc.reorder_positions(request.user, payload.ids)


@router.patch("/team-positions/{position_id}", response=PositionOut, summary="Edit a position", operation_id="update_team_position")
def update_position(request, position_id: uuid.UUID, payload: PositionPatch):
    _admin(request)
    position = _found(svc.get_position(position_id))
    svc.update_position(request.user, position, **payload.dict(exclude_unset=True))
    return svc.position_out(position)


@router.delete("/team-positions/{position_id}", response={204: None}, summary="Delete a position (settings permission; 422 while in use)", operation_id="delete_team_position")
def delete_position(request, position_id: uuid.UUID):
    _admin(request)
    svc.delete_position(request.user, _found(svc.get_position(position_id)))
    return 204, None


# -- number pools --


@router.get("/number-pools", response=list[PoolOut], summary="List number pools", operation_id="list_number_pools")
def list_pools(request):
    return svc.list_pools(request.user)


@router.post("/number-pools", response={201: PoolOut}, summary="Create a number pool ; optional team_ids assigns teams", operation_id="create_number_pool")
def create_pool(request, payload: PoolIn):
    _admin(request)
    return 201, svc.pool_out(svc.create_pool(request.user, **payload.dict()))


@router.patch("/number-pools/{pool_id}", response=PoolOut, summary="Edit a number pool ; team_ids, when sent, is the full list of teams using it", operation_id="update_number_pool")
def update_pool(request, pool_id: uuid.UUID, payload: PoolPatch):
    _admin(request)
    pool = _found(svc.get_pool(pool_id))
    svc.update_pool(request.user, pool, **payload.dict(exclude_unset=True))
    return svc.pool_out(pool)


@router.delete("/number-pools/{pool_id}", response={204: None}, summary="Delete a number pool ; its teams are detached", operation_id="delete_number_pool")
def delete_pool(request, pool_id: uuid.UUID):
    _admin(request)
    svc.delete_pool(request.user, _found(svc.get_pool(pool_id)))
    return 204, None


@router.get("/number-pools/{pool_id}/reservations", response=list[ReservationOut], summary="List a pool's reserved numbers", operation_id="list_number_reservations")
def list_reservations(request, pool_id: uuid.UUID):
    return svc.list_reservations(request.user, _found(svc.get_pool(pool_id)))


@router.post("/number-pools/{pool_id}/reservations", response={201: ReservationOut}, summary="Reserve a number (settings permission or a team manager)", operation_id="create_number_reservation")
def create_reservation(request, pool_id: uuid.UUID, payload: ReservationIn):
    if not access.can_manage_any_team(request.user):
        raise NotAllowed("You are not allowed to reserve numbers.")
    pool = _found(svc.get_pool(pool_id))
    return 201, svc.reservation_out(svc.create_reservation(request.user, pool, number=payload.number, note=payload.note), request.user)


@router.delete("/number-pools/{pool_id}/reservations/{reservation_id}", response={204: None}, summary="Release a reserved number (settings permission or the person who reserved it)", operation_id="delete_number_reservation")
def delete_reservation(request, pool_id: uuid.UUID, reservation_id: uuid.UUID):
    pool = _found(svc.get_pool(pool_id))
    svc.delete_reservation(request.user, _found(svc.get_reservation(pool, reservation_id)))
    return 204, None


# -- officiating overview --


@router.get("/officiating/people", response=list[OfficiatingPersonOut], summary="Referees and officials across the club (referee_officials feature)", operation_id="list_officiating_people")
def officiating_people(
    request,
    kind: Literal["referee", "official", "all"] = "all",
    q: str = "",
    level: uuid.UUID | None = None,
    valid: Literal["all", "valid", "expired"] = "all",
):
    if not request.features.has("referee_officials"):
        raise HttpError(404, "Not found.")
    return svc.officiating_people(request.user, kind=kind, q=q, level=level, valid=valid)
