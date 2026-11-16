import uuid

from ninja import Schema


class PermissionOut(Schema):
    code: str
    label: str
    admin: bool
    feature: str | None = None
    feature_name: str | None = None
    available: bool


class RoleOut(Schema):
    id: uuid.UUID
    name: str
    slug: str
    description: str
    is_system: bool
    is_owner: bool
    permissions: list[str]
    member_count: int


class RolePerson(Schema):
    id: uuid.UUID
    name: str
    email: str


class RoleDetail(RoleOut):
    members: list[RolePerson]


class RoleIn(Schema):
    name: str
    description: str = ""
    permissions: list[str] = []


class RolePatch(Schema):
    name: str | None = None
    description: str | None = None
    permissions: list[str] | None = None


class MemberAddIn(Schema):
    member_id: uuid.UUID
