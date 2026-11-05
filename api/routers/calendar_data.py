"""Locations and opponents for the calendar (``/api/v1/locations``, ``/api/v1/opponents``). Everyone who got into the
management app reads; changing needs the settings permission or a management position on any team (403). Writes go
through ``events.services_calendar``."""

import uuid

from ninja import File, Router, UploadedFile
from ninja.errors import HttpError

from api.schemas_calendar import CountryOut, LocationIn, LocationOut, LocationPatch, OpponentIn, OpponentOut, OpponentPatch
from events import services_calendar as svc

locations_router = Router()
opponents_router = Router()


def _found(obj):
    if obj is None:
        raise HttpError(404, "Not found.")
    return obj


# -- locations --


@locations_router.get("", response=list[LocationOut], summary="List locations", operation_id="list_locations")
def list_locations(request, q: str = ""):
    return svc.list_locations(q)


@locations_router.get("/countries", response=list[CountryOut], summary="Countries for the address field", operation_id="location_countries")
def countries(request):
    return svc.country_choices()


@locations_router.post("", response={201: LocationOut}, summary="Add a location", operation_id="create_location")
def create_location(request, payload: LocationIn):
    return 201, svc.location_out(svc.create_location(request.user, **payload.dict()))


@locations_router.patch("/{location_id}", response=LocationOut, summary="Edit a location", operation_id="update_location")
def update_location(request, location_id: uuid.UUID, payload: LocationPatch):
    loc = _found(svc.get_location(location_id))
    return svc.location_out(svc.update_location(request.user, loc, **payload.dict(exclude_unset=True)))


@locations_router.delete("/{location_id}", response={204: None}, summary="Delete a location (not the home location)", operation_id="delete_location")
def delete_location(request, location_id: uuid.UUID):
    svc.delete_location(request.user, _found(svc.get_location(location_id)))
    return 204, None


# -- opponents --


@opponents_router.get("", response=list[OpponentOut], summary="List opponents", operation_id="list_opponents")
def list_opponents(request, q: str = ""):
    return svc.list_opponents(q)


@opponents_router.post("", response={201: OpponentOut}, summary="Add an opponent", operation_id="create_opponent")
def create_opponent(request, payload: OpponentIn):
    return 201, svc.opponent_out(svc.create_opponent(request.user, **payload.dict()))


@opponents_router.patch("/{opponent_id}", response=OpponentOut, summary="Edit an opponent", operation_id="update_opponent")
def update_opponent(request, opponent_id: uuid.UUID, payload: OpponentPatch):
    o = _found(svc.get_opponent(opponent_id))
    return svc.opponent_out(svc.update_opponent(request.user, o, **payload.dict(exclude_unset=True)))


@opponents_router.delete("/{opponent_id}", response={204: None}, summary="Delete an opponent (its events lose it)", operation_id="delete_opponent")
def delete_opponent(request, opponent_id: uuid.UUID):
    svc.delete_opponent(request.user, _found(svc.get_opponent(opponent_id)))
    return 204, None


@opponents_router.post("/{opponent_id}/logo", response=OpponentOut, summary="Upload an opponent's logo", operation_id="set_opponent_logo")
def set_logo(request, opponent_id: uuid.UUID, file: File[UploadedFile]):
    """Multipart, field ``file``: JPEG, PNG or WebP up to 5 MB."""
    o = _found(svc.get_opponent(opponent_id))
    return svc.opponent_out(svc.set_opponent_logo(request.user, o, file))


@opponents_router.delete("/{opponent_id}/logo", response=OpponentOut, summary="Remove an opponent's logo", operation_id="remove_opponent_logo")
def remove_logo(request, opponent_id: uuid.UUID):
    o = _found(svc.get_opponent(opponent_id))
    return svc.opponent_out(svc.remove_opponent_logo(request.user, o))
