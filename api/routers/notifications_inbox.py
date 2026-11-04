"""In-app notifications of the signed-in user in this club (``/api/v1/notifications``): the management app's bell.
Always the caller's own; nothing here can see anyone else's."""

import uuid

from django_tenants.utils import get_public_schema_name
from ninja import Router, Schema
from ninja.errors import HttpError

from notifications import inbox

router = Router()


class InboxOut(Schema):
    id: uuid.UUID
    kind: str
    title: str
    body: str
    href: str | None = None
    created: str
    read: bool


def _out(n) -> dict:
    return {"id": n.pk, "kind": n.kind, "title": n.title, "body": n.body, "href": n.href or None, "created": n.created.isoformat(), "read": n.read_at is not None}


def _club(request):
    if request.tenant.schema_name == get_public_schema_name():
        raise HttpError(404, "Not found.")
    return request.tenant


@router.get("", response=list[InboxOut], summary="My notifications in this club, newest first", operation_id="list_inbox")
def list_inbox(request):
    return [_out(n) for n in inbox.list_for(request.user, _club(request))]


@router.post("/read-all", response={204: None}, summary="Mark all my notifications read", operation_id="inbox_read_all")
def read_all(request):
    inbox.mark_all_read(request.user, _club(request))
    return 204, None


@router.delete("", response={204: None}, summary="Clear all my notifications", operation_id="inbox_clear_all")
def clear_all(request):
    inbox.clear_all(request.user, _club(request))
    return 204, None


@router.post("/{notification_id}/read", response={204: None}, summary="Mark one notification read", operation_id="inbox_read")
def read_one(request, notification_id: uuid.UUID):
    if not inbox.mark_read(request.user, _club(request), notification_id):
        raise HttpError(404, "Not found.")
    return 204, None
