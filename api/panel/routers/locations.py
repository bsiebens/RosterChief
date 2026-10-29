"""Management locations (``/manage/locations``): the API behind the /manage location screens.

Same rule as management.views (``ManagementPositionRequiredMixin``): a club admin or a coach
manager. Validation is ``management.forms.LocationForm``. ``is_home`` is set from the control panel
and never written here. The read-only picker stays at ``GET /manage/events/locations``.
"""

import uuid

from django.utils.translation import gettext_lazy as _
from ninja import Router

from api.core.errors import ApiError
from api.core.forms import form_error
from api.core.idempotency import idempotent
from api.core.schemas import ErrorOut
from api.panel.guards import panel_club, require
from api.panel.schemas.locations import LocationIn, LocationListOut, LocationOut
from club.services.access import is_club_admin, is_coach_manager
from events.models import Location
from management.forms import LocationForm

router = Router(tags=["panel: locations"])

PREFIX = "/manage/locations"
ERRORS = {401: ErrorOut, 403: ErrorOut, 404: ErrorOut}


def _allowed(user, club) -> bool:
    return is_club_admin(user, club) or is_coach_manager(user, club)


def _gate(request):
    club = panel_club(request)
    require(request, club, _allowed)
    return club


def _get(club, location_id: uuid.UUID) -> Location:
    location = Location.objects.filter(club=club, pk=location_id).first()
    if location is None:
        raise ApiError(404, "not_found", _("Not found."))
    return location


def _out(location: Location) -> dict:
    return {
        "id": location.pk,
        "name": location.name,
        "address": location.address,
        "city": location.city,
        "zip_code": location.zip_code,
        "country": location.country.code,
        "is_home": location.is_home,
    }


def _save(payload: LocationIn, instance: Location) -> Location:
    form = LocationForm(payload.dict(), instance=instance)
    if not form.is_valid():
        raise form_error(form)
    return form.save()


@router.get(PREFIX, response={200: LocationListOut, **ERRORS}, summary="The club's locations")
def list_locations(request):
    """Every location of the club by name. No pagination."""
    club = _gate(request)
    return {"items": [_out(location) for location in Location.objects.filter(club=club).order_by("name")]}


@router.post(PREFIX, response={201: LocationOut, **ERRORS, 422: ErrorOut}, summary="Create a location")
def create_location(request, payload: LocationIn):
    """Admin or coach manager. Honours `Idempotency-Key`."""
    club = _gate(request)

    def work():
        return 201, _out(_save(payload, Location(club=club)))

    return idempotent(request, work)


@router.get(PREFIX + "/{uuid:location_id}", response={200: LocationOut, **ERRORS}, summary="One location")
def get_location(request, location_id: uuid.UUID):
    club = _gate(request)
    return _out(_get(club, location_id))


@router.put(PREFIX + "/{uuid:location_id}", response={200: LocationOut, **ERRORS, 422: ErrorOut}, summary="Update a location")
def update_location(request, location_id: uuid.UUID, payload: LocationIn):
    club = _gate(request)
    return _out(_save(payload, _get(club, location_id)))


@router.delete(PREFIX + "/{uuid:location_id}", response={204: None, **ERRORS}, summary="Delete a location")
def delete_location(request, location_id: uuid.UUID):
    """Events and series that used it keep existing without a location."""
    club = _gate(request)
    _get(club, location_id).delete()
    return 204, None
