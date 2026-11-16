"""Referee / official levels API (``/api/v1/officiating/{referee,official}-levels``). Settings permission only (403 first), and only
for clubs with the ``referee_officials`` feature (404 ``Not found.`` otherwise). Writes go through ``members.services.levels``."""

import uuid

from ninja import Router
from ninja.errors import HttpError

from api.schemas_club_settings import LevelIn, LevelOut
from members.services import access, levels

router = Router()


def _gate(request, kind, level_id=None):
    access.require_settings(request.user)
    if not request.features.has("referee_officials"):
        raise HttpError(404, "Not found.")
    if level_id is None:
        return None
    level = levels.get_level(kind, level_id)
    if level is None:
        raise HttpError(404, "Not found.")
    return level


def _out(kind, level):
    return levels.level_out(level, kind)


def _register(kind: str):
    path = f"/{kind}-levels"

    @router.get(path, response=list[LevelOut], summary=f"List {kind} levels (admin; referee_officials feature)", operation_id=f"list_{kind}_levels")
    def list_(request):
        _gate(request, kind)
        return levels.list_levels(request.user, kind)

    @router.post(path, response={201: LevelOut}, summary=f"Create a {kind} level (admin)", operation_id=f"create_{kind}_level")
    def create(request, payload: LevelIn):
        _gate(request, kind)
        level = levels.create_level(request.user, kind, name=payload.name, team_ids=payload.team_ids, inherits_from_id=payload.inherits_from_id)
        return 201, _out(kind, level)

    @router.patch(path + "/{level_id}", response=LevelOut, summary=f"Edit a {kind} level (admin)", operation_id=f"update_{kind}_level")
    def update(request, level_id: uuid.UUID, payload: LevelIn):
        level = _gate(request, kind, level_id)
        levels.update_level(request.user, kind, level, name=payload.name, team_ids=payload.team_ids, inherits_from_id=payload.inherits_from_id)
        return _out(kind, level)

    @router.delete(path + "/{level_id}", response={204: None}, summary=f"Delete a {kind} level (admin)", operation_id=f"delete_{kind}_level")
    def delete(request, level_id: uuid.UUID):
        level = _gate(request, kind, level_id)
        levels.delete_level(request.user, kind, level)
        return 204, None


_register("referee")
_register("official")
