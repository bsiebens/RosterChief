"""Management API: the jersey-number overview and reservations (API.md §17).

The JSON counterpart of ``/manage/numbers`` (management.views.NumberListView, the PDF export and
NumberReservation*View). Everything is open to management staff (``panel_club``); releasing a
reservation additionally needs to be whoever reserved it or ``can_manage_members``, as on the web.
The tile logic is teams.services.numbers.
"""

import uuid
from collections import Counter

from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _
from ninja import Router

from api.core.errors import ApiError
from api.core.idempotency import idempotent
from api.core.schemas import ErrorOut
from api.panel.guards import panel_club
from api.panel.routers.number_pools import pool_out, pools_with_counts
from api.panel.schemas import numbers as s
from club.models import Season
from club.services.access import current_season
from management.pdf import PDFExportError, number_list_colors, number_list_pdf
from teams.models import NumberReservation
from teams.services.numbers import NumberReservationError, build_number_tiles, can_release_reservation, reserve_number

router = Router(tags=["panel: numbers"])

PREFIX = "/manage/numbers"
ERRORS = {401: ErrorOut, 403: ErrorOut, 404: ErrorOut}
INVALID = {422: ErrorOut}
CONFLICT = {409: ErrorOut}


def _selection(club, pool_id, season_id):
    """The web's pool/season choice: the named one of this club (404 otherwise), else the first pool / the current season."""
    pools = pools_with_counts(club)
    pool = get_object_or_404(pools, pk=pool_id) if pool_id else pools.first()
    season = get_object_or_404(Season.objects.filter(club=club), pk=season_id) if season_id else current_season(club)
    return pools, pool, season


def _season_out(season, current) -> dict:
    return {"id": season.pk, "name": season.name, "start_date": season.start_date, "end_date": season.end_date, "is_current": current is not None and season.pk == current.pk}


def _reservation_out(request, club, reservation) -> dict:
    reserver = reservation.reserved_by
    return {
        "id": reservation.pk,
        "pool_id": reservation.pool_id,
        "number": reservation.number,
        "note": reservation.note,
        "reserved_by": (reserver.get_full_name() or reserver.email) if reserver else None,
        "can_release": can_release_reservation(request.user, club, reservation),
    }


def _tile_out(request, club, tile) -> dict:
    return {
        "number": tile["number"],
        "state": tile["state"],
        "note": tile["note"],
        "reservation": _reservation_out(request, club, tile["reservation"]) if tile["reservation"] else None,
        "holders": [{"member_id": p["member"].pk, "name": str(p["member"]), "team": p["team"], "birth_year": p["birth_year"]} for p in tile["people"]],
    }


@router.get(PREFIX, response={200: s.NumberOverviewOut, **ERRORS}, summary="One pool's numbers in a season")
def overview(request, pool_id: uuid.UUID | None = None, season_id: uuid.UUID | None = None):
    """Every number of the pool with its state and holders. Any management staff."""
    club = panel_club(request)
    pools, pool, season = _selection(club, pool_id, season_id)
    tiles = build_number_tiles(pool, season) if pool is not None and season is not None else []
    counts = Counter(tile["state"] for tile in tiles)
    current = current_season(club)
    return {
        "pools": [pool_out(p) for p in pools],
        "pool": pool_out(pool) if pool else None,
        "seasons": [_season_out(row, current) for row in Season.objects.filter(club=club).order_by("-start_date")],
        "season": _season_out(season, current) if season else None,
        "state_counts": {state: counts[state] for state in ("available", "taken", "conflict", "pending", "previous", "reserved")},
        "tiles": [_tile_out(request, club, tile) for tile in tiles],
    }


@router.get(PREFIX + "/export", response={200: None, **ERRORS, **CONFLICT}, summary="The overview as a PDF")
def export_pdf(request, pool_id: uuid.UUID | None = None, season_id: uuid.UUID | None = None):
    """`application/pdf` attachment, authenticated by the bearer token. 409 `no_selection` without a pool or season,
    503 `pdf_unavailable` when the server can't render PDFs. Any management staff."""
    club = panel_club(request)
    _pools, pool, season = _selection(club, pool_id, season_id)
    if pool is None or season is None:
        raise ApiError(409, "no_selection", _("Pick a number pool and season first."))
    context = {"club": club, "generated_at": timezone.now(), "colors": number_list_colors(club), "pool": pool, "season": season, "tiles": build_number_tiles(pool, season)}
    context["state_counts"] = Counter(tile["state"] for tile in context["tiles"])
    try:
        pdf = number_list_pdf(context)
    except PDFExportError as error:
        raise ApiError(503, "pdf_unavailable", str(error)) from error
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="numbers-{slugify(pool.name)}-{season.start_date:%Y}.pdf"'
    return response


@router.post(PREFIX + "/reservations", response={201: s.ReservationOut, **ERRORS, **INVALID, **CONFLICT}, summary="Reserve a number")
def create_reservation(request, payload: s.ReservationIn):
    """Blocks a number of a pool (e.g. a retired shirt). 422 `invalid_number` outside the pool's range,
    409 `already_reserved`. Any management staff."""
    club = panel_club(request)
    pool = get_object_or_404(pools_with_counts(club), pk=payload.pool_id)

    def work():
        try:
            reservation = reserve_number(club, pool, payload.number, note=payload.note, user=request.user)
        except NumberReservationError as error:
            if error.code == "invalid_number":
                raise ApiError(422, "validation_error", fields={"number": [str(error)]}) from error
            raise ApiError(409, error.code, str(error)) from error
        return 201, _reservation_out(request, club, reservation)

    return idempotent(request, work)


@router.delete(PREFIX + "/reservations/{uuid:reservation_id}", response={204: None, **ERRORS}, summary="Release a reserved number")
def release_reservation(request, reservation_id: uuid.UUID):
    """Whoever reserved it, or a club admin / member admin (403 otherwise)."""
    club = panel_club(request)
    reservation = get_object_or_404(NumberReservation.objects.filter(club=club), pk=reservation_id)
    if not can_release_reservation(request.user, club, reservation):
        raise ApiError(403, "forbidden", _("Only whoever reserved #%(number)s, or an admin, can release it.") % {"number": reservation.number})
    reservation.delete()
    return 204, None
