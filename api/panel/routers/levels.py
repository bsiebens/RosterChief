"""Management referee and official levels, and the referee / official lists
(``/manage/referee-levels``, ``/manage/referees``, ``/manage/official-levels``, ``/manage/officials``).

Same rules as management.views: any staff may read; creating, changing and deleting a level needs
``can_manage_members`` (``MemberAdminRequiredMixin``). Officials are a licensed module: with the
``officials`` feature off for the club, those endpoints answer 404 whoever the caller is.
Validation is ``RefereeLevelForm`` / ``OfficialLevelForm``; the lists are teams.services.levels.

A member's own referee / official profile (level and validity) is edited on the member, not here.
"""

import uuid

from django.utils.translation import gettext_lazy as _
from ninja import Router

from api.core.errors import ApiError
from api.core.forms import form_error, post_data
from api.core.idempotency import idempotent
from api.core.pagination import paginate_keyset
from api.core.schemas import ErrorOut
from api.panel.guards import panel_club, require
from api.panel.schemas import levels as s
from club.services.access import can_manage_members
from events.services.officials import officials_enabled_for
from management.forms import OfficialLevelForm, RefereeLevelForm
from teams.models import OfficialLevel, RefereeLevel
from teams.services.levels import attach_official_teams, attach_referee_teams, delete_level, official_profiles, referee_profiles

router = Router(tags=["panel: referee and official levels"])

ERRORS = {401: ErrorOut, 403: ErrorOut, 404: ErrorOut}
INVALID = {422: ErrorOut}
ALIASES = {"teams": "team_ids", "inherits_from": "inherits_from_id"}
LEVEL_FORM_KEYS = {"multiple": {"team_ids": "teams"}, "single": {"inherits_from_id": "inherits_from"}}


def _gate(request, *, officials: bool = False, write: bool = False):
    club = panel_club(request)
    if officials and not officials_enabled_for(club):
        raise ApiError(404, "not_found", _("Not found."))
    if write:
        require(request, club, can_manage_members)
    return club


def _ref(obj) -> dict:
    return {"id": obj.pk, "name": obj.name}


def _queryset(model, club):
    return model.objects.filter(club=club).select_related("inherits_from").prefetch_related("teams")


def _level_out(level) -> dict:
    return {"id": level.pk, "name": level.name, "teams": [_ref(team) for team in sorted(level.teams.all(), key=lambda t: t.name)], "inherits_from": _ref(level.inherits_from) if level.inherits_from_id else None}


def _get(model, club, level_id: uuid.UUID):
    level = _queryset(model, club).filter(pk=level_id).first()
    if level is None:
        raise ApiError(404, "not_found", _("Not found."))
    return level


def _save(model, form_class, club, instance, values: dict):
    form = form_class(post_data(values, **LEVEL_FORM_KEYS), club=club, instance=instance)
    if not form.is_valid():
        raise form_error(form, ALIASES)
    instance.club = club
    form.save()
    return _get(model, club, instance.pk)


def _create(request, model, form_class, club, payload):
    def work():
        return 201, _level_out(_save(model, form_class, club, model(club=club), payload.dict()))

    return idempotent(request, work)


def _update(model, form_class, club, level_id, payload):
    level = _get(model, club, level_id)
    values = {"name": level.name, "team_ids": [team.pk for team in level.teams.all()], "inherits_from_id": level.inherits_from_id}
    values.update(payload.dict(exclude_unset=True))
    if values["name"] is None or values["team_ids"] is None:
        raise ApiError(422, "validation_error", fields={"name" if values["name"] is None else "team_ids": [str(_("This field cannot be null."))]})
    return _level_out(_save(model, form_class, club, level, values))


def _delete(model, club, level_id) -> tuple[int, None]:
    level = _get(model, club, level_id)
    if not delete_level(level):
        raise ApiError(409, "level_in_use", _("This level is still held by someone or inherited by another level, and can't be deleted."))
    return 204, None


# --- referee levels -----------------------------------------------------------------------------


@router.get("/manage/referee-levels", response={200: s.LevelListOut, **ERRORS}, summary="Referee levels")
def list_referee_levels(request):
    """By name; a club has a handful, so no pagination. Any staff."""
    club = _gate(request)
    return {"items": [_level_out(level) for level in _queryset(RefereeLevel, club)]}


@router.post("/manage/referee-levels", response={201: s.LevelOut, **ERRORS, **INVALID}, summary="Create a referee level")
def create_referee_level(request, payload: s.LevelIn):
    """Club admins and member admins. Honours `Idempotency-Key`."""
    return _create(request, RefereeLevel, RefereeLevelForm, _gate(request, write=True), payload)


@router.get("/manage/referee-levels/{uuid:level_id}", response={200: s.LevelOut, **ERRORS}, summary="One referee level")
def get_referee_level(request, level_id: uuid.UUID):
    return _level_out(_get(RefereeLevel, _gate(request), level_id))


@router.patch("/manage/referee-levels/{uuid:level_id}", response={200: s.LevelOut, **ERRORS, **INVALID}, summary="Edit a referee level")
def update_referee_level(request, level_id: uuid.UUID, payload: s.LevelPatch):
    """Send only the fields to change (`inherits_from_id: null` clears the link). Club admins and member admins."""
    return _update(RefereeLevel, RefereeLevelForm, _gate(request, write=True), level_id, payload)


@router.delete("/manage/referee-levels/{uuid:level_id}", response={204: None, 409: ErrorOut, **ERRORS}, summary="Delete a referee level")
def delete_referee_level(request, level_id: uuid.UUID):
    """409 `level_in_use` while a referee holds the level or another level inherits from it. Club admins and member admins."""
    return _delete(RefereeLevel, _gate(request, write=True), level_id)


@router.get("/manage/referees", response={200: s.ProfileListOut, **ERRORS}, summary="Referees of the club")
def list_referees(request, cursor: str | None = None, limit: int | None = None):
    """Everyone with a referee profile that the caller may see (a coach sees their teams' people), by name, with the teams each may referee. Any staff."""
    club = _gate(request)
    page = paginate_keyset(referee_profiles(request.user, club), fields=["last_name", "first_name", "pk"], cursor=cursor, limit=limit)
    attach_referee_teams(club, page.items)
    return {"items": [_profile_out(m, m.referee_profile) for m in page.items], "next_cursor": page.next_cursor}


def _profile_out(member, profile) -> dict:
    return {
        "member_id": member.pk,
        "first_name": member.first_name,
        "last_name": member.last_name,
        "level": _ref(profile.level) if profile.level_id else None,
        "valid_until": profile.valid_until,
        "is_eligible": profile.is_eligible,
        "eligible_teams": [_ref(team) for team in sorted(profile.cached_eligible_teams, key=lambda t: t.name)],
    }


# --- official levels ----------------------------------------------------------------------------


@router.get("/manage/official-levels", response={200: s.LevelListOut, **ERRORS}, summary="Official levels")
def list_official_levels(request):
    """404 when the club has no officials module. Any staff."""
    club = _gate(request, officials=True)
    return {"items": [_level_out(level) for level in _queryset(OfficialLevel, club)]}


@router.post("/manage/official-levels", response={201: s.LevelOut, **ERRORS, **INVALID}, summary="Create an official level")
def create_official_level(request, payload: s.LevelIn):
    """Club admins and member admins. Honours `Idempotency-Key`."""
    return _create(request, OfficialLevel, OfficialLevelForm, _gate(request, officials=True, write=True), payload)


@router.get("/manage/official-levels/{uuid:level_id}", response={200: s.LevelOut, **ERRORS}, summary="One official level")
def get_official_level(request, level_id: uuid.UUID):
    return _level_out(_get(OfficialLevel, _gate(request, officials=True), level_id))


@router.patch("/manage/official-levels/{uuid:level_id}", response={200: s.LevelOut, **ERRORS, **INVALID}, summary="Edit an official level")
def update_official_level(request, level_id: uuid.UUID, payload: s.LevelPatch):
    """Send only the fields to change. Club admins and member admins."""
    return _update(OfficialLevel, OfficialLevelForm, _gate(request, officials=True, write=True), level_id, payload)


@router.delete("/manage/official-levels/{uuid:level_id}", response={204: None, 409: ErrorOut, **ERRORS}, summary="Delete an official level")
def delete_official_level(request, level_id: uuid.UUID):
    """409 `level_in_use` while an official holds the level or another level inherits from it. Club admins and member admins."""
    return _delete(OfficialLevel, _gate(request, officials=True, write=True), level_id)


@router.get("/manage/officials", response={200: s.ProfileListOut, **ERRORS}, summary="Officials of the club")
def list_officials(request, cursor: str | None = None, limit: int | None = None):
    """The officials counterpart to `GET /manage/referees`. 404 when the club has no officials module. Any staff."""
    club = _gate(request, officials=True)
    page = paginate_keyset(official_profiles(request.user, club), fields=["last_name", "first_name", "pk"], cursor=cursor, limit=limit)
    attach_official_teams(club, page.items)
    return {"items": [_profile_out(m, m.official_profile) for m in page.items], "next_cursor": page.next_cursor}
