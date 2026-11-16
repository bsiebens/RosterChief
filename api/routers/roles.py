"""Roles (``/roles``): the club's roles, the permissions each holds and who holds them. Everything needs
``authorizations.manage_roles`` (403 otherwise). Rules live in ``authorization.management``."""

import uuid

from django_tenants.utils import get_public_schema_name
from ninja import Router
from ninja.errors import HttpError

from api.auth import AUTHENTICATORS
from api.schemas_roles import MemberAddIn, PermissionOut, RoleDetail, RoleIn, RoleOut, RolePatch
from authorization import role_management as svc
from authorization.catalog import CATALOG
from features.catalog import CATALOG as FEATURES
from members.services import access

router = Router(auth=AUTHENTICATORS)


def _gate(request):
    access.require_manage_roles(request.user)
    if request.tenant.schema_name == get_public_schema_name():
        raise HttpError(404, "Not found.")


def _role(role_id):
    role = svc.get_role(role_id)
    if role is None:
        raise HttpError(404, "Not found.")
    return role


def _item(role) -> dict:
    return {"id": role.pk, "name": role.group.name, "slug": role.slug, "description": role.description, "is_system": role.is_system, "is_owner": role.slug == "owner", "permissions": svc.role_codes(role), "member_count": svc.member_count(role)}


def _detail(role_id) -> dict:
    role = _role(role_id)
    return {**_item(role), "members": [{"id": m.pk, "name": str(m), "email": m.email} for m in svc.role_members(role)]}


@router.get("/permissions", response=list[PermissionOut], summary="Every permission a role can hold", operation_id="list_permissions")
def list_permissions(request):
    """``available`` is false when the club's plan lacks the feature the permission belongs to (it can still be set, it just has no effect)."""
    _gate(request)
    return [
        {"code": p.code, "label": p.label, "admin": p.admin, "feature": p.feature, "feature_name": FEATURES[p.feature].name if p.feature in FEATURES else None, "available": p.feature is None or request.features.has(p.feature)}
        for p in CATALOG.values()
    ]  # fmt: skip


@router.get("", response=list[RoleOut], summary="List roles", operation_id="list_roles")
def list_roles(request):
    _gate(request)
    return [_item(r) for r in svc.roles_queryset()]


@router.get("/assignments", response=list[RoleDetail], summary="Every role with the people holding it", operation_id="list_role_assignments")
def list_assignments(request):
    _gate(request)
    return [_detail(r.pk) for r in svc.roles_queryset()]


@router.post("", response={201: RoleDetail}, summary="Create a role", operation_id="create_role")
def create_role(request, payload: RoleIn):
    _gate(request)
    return 201, _detail(svc.create(request.user, **payload.dict()).pk)


@router.get("/{role_id}", response=RoleDetail, summary="One role with its permissions and people", operation_id="get_role")
def get_role(request, role_id: uuid.UUID):
    _gate(request)
    return _detail(_role(role_id).pk)


@router.patch("/{role_id}", response=RoleDetail, summary="Edit a role (the Owner role is locked)", operation_id="update_role")
def update_role(request, role_id: uuid.UUID, payload: RolePatch):
    _gate(request)
    return _detail(svc.update(request.user, _role(role_id), **payload.dict(exclude_unset=True)).pk)


@router.delete("/{role_id}", response={204: None}, summary="Delete a role (system roles cannot be)", operation_id="delete_role")
def delete_role(request, role_id: uuid.UUID):
    _gate(request)
    svc.delete(request.user, _role(role_id))
    return 204, None


@router.post("/{role_id}/members", response=RoleDetail, summary="Give the role to a member with a login", operation_id="add_role_member")
def add_member(request, role_id: uuid.UUID, payload: MemberAddIn):
    _gate(request)
    role = _role(role_id)
    svc.add_member(request.user, role, payload.member_id)
    return _detail(role.pk)


@router.delete("/{role_id}/members/{member_id}", response=RoleDetail, summary="Take the role from a member (a club's last Owner stays)", operation_id="remove_role_member")
def remove_member(request, role_id: uuid.UUID, member_id: uuid.UUID):
    _gate(request)
    role = _role(role_id)
    svc.remove_member(request.user, role, member_id)
    return _detail(role.pk)
