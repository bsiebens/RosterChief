"""Event subtypes (``/event-subtypes``): everyone reads, the settings permission writes (403 first). Writes go through
``events.services.subtypes``."""

import uuid

from ninja import Router
from ninja.errors import HttpError

from api.schemas_event_subtypes import EventSubtypeIn, EventSubtypeOrderIn, EventSubtypeOut, EventSubtypePatch
from events.services import subtypes as svc
from members.services import access

router = Router()


def _found(obj):
    if obj is None:
        raise HttpError(404, "Not found.")
    return obj


@router.get("/event-subtypes", response=list[EventSubtypeOut], summary="List event subtypes", operation_id="list_event_subtypes")
def list_subtypes(request):
    """Ordered by kind, then order, then name."""
    return svc.list_subtypes()


@router.post("/event-subtypes", response={201: EventSubtypeOut}, summary="Create an event subtype (settings permission)", operation_id="create_event_subtype")
def create_subtype(request, payload: EventSubtypeIn):
    access.require_settings(request.user)
    return 201, svc.subtype_out(svc.create_subtype(request.user, **payload.dict()))


@router.post("/event-subtypes/reorder", response=list[EventSubtypeOut], summary="Set the order of the event subtypes (settings permission)", operation_id="reorder_event_subtypes")
def reorder_subtypes(request, payload: EventSubtypeOrderIn):
    """``ids`` in the wanted order; each kind is numbered 1..n by it, unlisted ones follow. Returns the new list."""
    access.require_settings(request.user)
    return svc.reorder_subtypes(request.user, payload.ids)


@router.patch("/event-subtypes/{subtype_id}", response=EventSubtypeOut, summary="Edit an event subtype (settings permission)", operation_id="update_event_subtype")
def update_subtype(request, subtype_id: uuid.UUID, payload: EventSubtypePatch):
    """A subtype that is in use can't move to another kind (422)."""
    access.require_settings(request.user)
    subtype = _found(svc.get_subtype(subtype_id))
    svc.update_subtype(request.user, subtype, **payload.dict(exclude_unset=True))
    return svc.subtype_out(subtype)


@router.delete("/event-subtypes/{subtype_id}", response={204: None}, summary="Delete an event subtype (settings permission; events keep their title)", operation_id="delete_event_subtype")
def delete_subtype(request, subtype_id: uuid.UUID):
    access.require_settings(request.user)
    svc.delete_subtype(request.user, _found(svc.get_subtype(subtype_id)))
    return 204, None
