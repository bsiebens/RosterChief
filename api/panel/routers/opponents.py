"""Management opponents (``/manage/opponents``): the API behind the /manage opponent screens.

Same rule as management.views (``ManagementPositionRequiredMixin``): a club admin or a coach
manager. Validation is ``management.forms.OpponentForm``. Create and update are multipart (the
optional `logo` image beside `name`; update is a POST on the item for the same reason: PUT multipart bodies are not parsed); the read-only picker stays at ``GET /manage/events/opponents``.
"""

import uuid

from django.utils.translation import gettext_lazy as _
from ninja import File, Form, Router, UploadedFile

from api.core.errors import ApiError
from api.core.forms import form_error
from api.core.idempotency import idempotent
from api.core.schemas import ErrorOut
from api.core.uploads import IMAGE_TYPES, validate_upload
from api.panel.guards import panel_club, require
from api.panel.schemas.opponents import OpponentListOut, OpponentOut
from club.services.access import is_club_admin, is_coach_manager
from events.models import Opponent
from management.forms import OpponentForm

router = Router(tags=["panel: opponents"])

PREFIX = "/manage/opponents"
ERRORS = {401: ErrorOut, 403: ErrorOut, 404: ErrorOut}
UPLOAD_ERRORS = {413: ErrorOut, 415: ErrorOut, 422: ErrorOut}


def _allowed(user, club) -> bool:
    return is_club_admin(user, club) or is_coach_manager(user, club)


def _gate(request):
    club = panel_club(request)
    require(request, club, _allowed)
    return club


def _get(club, opponent_id: uuid.UUID) -> Opponent:
    opponent = Opponent.objects.filter(club=club, pk=opponent_id).first()
    if opponent is None:
        raise ApiError(404, "not_found", _("Not found."))
    return opponent


def _out(request, opponent: Opponent) -> dict:
    return {
        "id": opponent.pk,
        "name": opponent.name,
        "logo_url": request.build_absolute_uri(opponent.logo.url) if opponent.logo else None,
        "alternative_names": opponent.alternative_names,
    }


def _save(name: str, logo: UploadedFile | None, instance: Opponent, *, clear_logo: bool = False) -> Opponent:
    files = {}
    if logo is not None:
        files["logo"] = validate_upload(logo, field="logo", content_types=IMAGE_TYPES)
    data = {"name": name}
    if clear_logo and logo is None:
        data["logo-clear"] = "on"
    form = OpponentForm(data, files, instance=instance)
    if not form.is_valid():
        raise form_error(form)
    return form.save()


@router.get(PREFIX, response={200: OpponentListOut, **ERRORS}, summary="The club's opponents")
def list_opponents(request):
    """Every opponent of the club by name. No pagination."""
    club = _gate(request)
    return {"items": [_out(request, opponent) for opponent in Opponent.objects.filter(club=club).order_by("name")]}


@router.post(PREFIX, response={201: OpponentOut, **ERRORS, **UPLOAD_ERRORS}, summary="Create an opponent")
def create_opponent(request, name: Form[str], logo: File[UploadedFile] = None):
    """Multipart: `name` and an optional `logo` image. Admin or coach manager. Honours `Idempotency-Key`."""
    club = _gate(request)

    def work():
        return 201, _out(request, _save(name, logo, Opponent(club=club)))

    return idempotent(request, work)


@router.get(PREFIX + "/{uuid:opponent_id}", response={200: OpponentOut, **ERRORS}, summary="One opponent")
def get_opponent(request, opponent_id: uuid.UUID):
    club = _gate(request)
    return _out(request, _get(club, opponent_id))


@router.post(PREFIX + "/{uuid:opponent_id}", response={200: OpponentOut, **ERRORS, **UPLOAD_ERRORS}, summary="Update an opponent")
def update_opponent(request, opponent_id: uuid.UUID, name: Form[str], logo: File[UploadedFile] = None, clear_logo: Form[bool] = False):
    """Multipart POST (not PUT: Django does not parse multipart bodies on PUT): `name`, a new `logo` image (replaces the old one) or `clear_logo=true` to remove it; no logo part keeps it."""
    club = _gate(request)
    return _out(request, _save(name, logo, _get(club, opponent_id), clear_logo=clear_logo))


@router.delete(PREFIX + "/{uuid:opponent_id}", response={204: None, **ERRORS}, summary="Delete an opponent")
def delete_opponent(request, opponent_id: uuid.UUID):
    """Events and series that used it keep existing without an opponent."""
    club = _gate(request)
    _get(club, opponent_id).delete()
    return 204, None
