"""Schemas of the management groups routes (api/panel/routers/groups.py)."""

import uuid

from ninja import Field, Schema


class GroupIn(Schema):
    name: str = Field(max_length=255, description="Unique within the club.")


class GroupPatch(Schema):
    name: str | None = Field(None, max_length=255)


class GroupOut(Schema):
    id: uuid.UUID
    name: str
    member_count: int


class GroupListOut(Schema):
    items: list[GroupOut]
    next_cursor: str | None = None


class GroupMemberOut(Schema):
    membership_id: uuid.UUID = Field(description="Use it to remove the member from the group.")
    member_id: uuid.UUID
    first_name: str
    last_name: str


class GroupDetailOut(GroupOut):
    members: list[GroupMemberOut] = Field(description="By last name, first name.")


class GroupMembersIn(Schema):
    member_ids: list[uuid.UUID] = Field(min_length=1, description="Members the caller may see; none may be in the group already, nor repeated.")
