"""Management API: positions, the club's roster and staff roles (API.md §17).

The JSON counterpart of ``/manage/positions`` (management.views.Position*View): any management
staff may read them (a coach needs them to make sense of a roster), creating, editing and
deleting is for club admins only (``is_club_admin``). Validation is management.forms.PositionForm.
"""

import uuid

from django.shortcuts import get_object_or_404
from django.utils.translation import gettext_lazy as _
from ninja import Router

from api.core.errors import ApiError
from api.core.forms import form_error, post_data
from api.core.idempotency import idempotent
from api.core.schemas import ErrorOut
from api.panel.guards import panel_club, require
from api.panel.schemas import positions as s
from club.services.access import is_club_admin
from management.forms import PositionForm
from teams.models import Position
from teams.services.positions import delete_position

router = Router(tags=["panel: positions"])

PREFIX = "/manage/positions"
ERRORS = {401: ErrorOut, 403: ErrorOut, 404: ErrorOut}
INVALID = {422: ErrorOut}
CONFLICT = {409: ErrorOut}
CHECKBOXES = ("staff_position", "management_position")


def _position(club, position_id) -> Position:
    return get_object_or_404(Position.objects.filter(club=club), pk=position_id)


def _out(position) -> dict:
    return {
        "id": position.pk,
        "name": position.name,
        "short_name": position.short_name,
        "ordering": position.ordering,
        "staff_position": position.staff_position,
        "management_position": position.management_position,
    }


def _save(club, values: dict, instance) -> Position:
    form = PositionForm(post_data(values, checkboxes=CHECKBOXES), club=club, instance=instance)
    if not form.is_valid():
        raise form_error(form)
    return form.save()


@router.get(PREFIX, response={200: s.PositionListOut, **ERRORS}, summary="Positions")
def list_positions(request):
    """Any management staff."""
    club = panel_club(request)
    return {"items": [_out(position) for position in Position.objects.filter(club=club)]}


@router.post(PREFIX, response={201: s.PositionOut, **ERRORS, **INVALID}, summary="Create a position")
def create_position(request, payload: s.PositionIn):
    """Club admins."""
    club = panel_club(request)
    require(request, club, is_club_admin)
    return idempotent(request, lambda: (201, _out(_save(club, payload.dict(), Position(club=club)))))


@router.get(PREFIX + "/{uuid:position_id}", response={200: s.PositionOut, **ERRORS}, summary="One position")
def get_position(request, position_id: uuid.UUID):
    club = panel_club(request)
    return _out(_position(club, position_id))


@router.patch(PREFIX + "/{uuid:position_id}", response={200: s.PositionOut, **ERRORS, **INVALID}, summary="Edit a position")
def update_position(request, position_id: uuid.UUID, payload: s.PositionPatch):
    """Send only the fields to change. Club admins."""
    club = panel_club(request)
    require(request, club, is_club_admin)
    position = _position(club, position_id)
    values = {**_out(position), **payload.dict(exclude_unset=True)}
    values.pop("id")
    return _out(_save(club, values, position))


@router.delete(PREFIX + "/{uuid:position_id}", response={204: None, **ERRORS, **CONFLICT}, summary="Delete a position")
def remove_position(request, position_id: uuid.UUID):
    """409 `position_in_use` while a roster or staff assignment still uses it. Club admins."""
    club = panel_club(request)
    require(request, club, is_club_admin)
    position = _position(club, position_id)
    if not delete_position(position):
        raise ApiError(409, "position_in_use", _("This position is still assigned on a team roster or staff, and can't be deleted."))
    return 204, None
