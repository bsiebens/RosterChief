"""Schemas of the management roles routes (api/panel/routers/roles.py)."""

import uuid
from typing import Literal

from ninja import Field, Schema

RoleName = Literal["admin", "editor", "member_admin", "shop_admin", "referee_admin", "evaluation_manager"]


class PersonRef(Schema):
    id: uuid.UUID
    name: str


class RoleHolderOut(Schema):
    id: uuid.UUID = Field(description="The grant's own id; use it to revoke.")
    member: PersonRef
    role: str | None = Field(None, description="`admin`, `editor` or `member_admin`; absent on the additive grants.")
    role_label: str | None = None


class RolesOut(Schema):
    roles: list[RoleHolderOut] = Field(description="Holders of a granted ClubRole (the implicit `member` role is not listed).")
    shop_admins: list[RoleHolderOut]
    referee_admins: list[RoleHolderOut]
    evaluation_managers: list[RoleHolderOut]


class RoleGrantIn(Schema):
    member_id: uuid.UUID = Field(description="A member affiliated with this club.")
    role: RoleName = Field(description="`admin`, `editor`, `member_admin` set the member's one ClubRole; `shop_admin`, `referee_admin`, `evaluation_manager` are additive grants.")


class RoleGrantOut(Schema):
    id: uuid.UUID | None = Field(description="Id of the ClubRole or grant row, to revoke it.")
    member: PersonRef
    role: str
    role_label: str | None = None
    created: bool = Field(description="`false` when the member already held it.")
