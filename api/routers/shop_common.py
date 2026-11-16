"""Shared gate for the /shop routers: permission (403) first, then the ``shop`` feature / platform host (404)."""

from django_tenants.utils import get_public_schema_name
from ninja.errors import HttpError

from members.services import access


def gate(request, require):
    require(request.user)
    if request.tenant.schema_name == get_public_schema_name() or not request.features.has("shop"):
        raise HttpError(404, "Not found.")


def page(qs, limit, offset, convert):
    return {"count": qs.count(), "results": [convert(x) for x in qs[offset : offset + limit]]}


def member_ref(member):
    return {"id": member.pk, "name": str(member)} if member is not None else None


__all__ = ["access", "gate", "member_ref", "page"]
