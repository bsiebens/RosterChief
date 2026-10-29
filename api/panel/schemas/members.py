"""Schemas of the management members slice (api/panel/routers/members.py).

Conventions as the rest of the API (API.md §6): snake_case, UUID strings, ``YYYY-MM-DD`` dates,
enums as stored values. Money is a decimal string.
"""

import datetime
import uuid
from decimal import Decimal
from typing import Literal

from ninja import Field, Schema

KindValue = Literal["member", "guardian", "staff"]
StatusValue = Literal["active", "pending", "lapsed", "cancelled"]
FeeStatusValue = Literal["unpaid", "paid", "partially_paid", "waived"]
FamilyRoleValue = Literal["parent", "child", "guardian", "other"]


class MembershipOut(Schema):
    id: uuid.UUID
    season_id: uuid.UUID
    season: str
    kind: KindValue
    license: str
    status: StatusValue
    fee_status: FeeStatusValue
    fee_amount: Decimal
    amount_paid: Decimal
    signed_up_at: datetime.date | None


class FamilyRefOut(Schema):
    family_id: uuid.UUID
    name: str
    role: FamilyRoleValue


class MemberRowOut(Schema):
    id: uuid.UUID
    first_name: str
    last_name: str
    email: str
    has_login: bool
    membership: MembershipOut | None = Field(description="The current season's membership, if any.")
    families: list[FamilyRefOut]


class MemberListOut(Schema):
    items: list[MemberRowOut]
    next_cursor: str | None


class FamilyPersonOut(Schema):
    member_id: uuid.UUID
    first_name: str
    last_name: str
    role: FamilyRoleValue
    has_login: bool


class InviteOut(Schema):
    email: str
    first_name: str
    last_name: str
    expires_at: datetime.datetime


class FamilyOut(Schema):
    id: uuid.UUID
    name: str
    guardians: list[FamilyPersonOut]
    children: list[FamilyPersonOut]
    others: list[FamilyPersonOut]
    pending_invites: list[InviteOut]


class FamilyRowOut(Schema):
    id: uuid.UUID
    name: str
    guardians: list[FamilyPersonOut]
    children: list[FamilyPersonOut]


class FamilyListOut(Schema):
    items: list[FamilyRowOut]
    next_cursor: str | None


class MemberDetailOut(Schema):
    id: uuid.UUID
    first_name: str
    last_name: str
    date_of_birth: datetime.date | None
    email: str
    phone: str | None
    emergency_phone: str | None
    photo_public_consent: bool
    player_photo_url: str | None
    has_login: bool
    person_fields_locked: bool = Field(description="True when the record is shared with an account or another club: the person's own fields are read-only here.")
    membership: MembershipOut | None = Field(description="The current season's membership, if any.")
    families: list[FamilyOut]
    guardians: list[FamilyPersonOut] = Field(description="Parents/guardians of this person, when they are a child in a family of this club.")


class MembershipHistoryOut(Schema):
    items: list[MembershipOut]


class MembershipRowOut(MembershipOut):
    member_id: uuid.UUID
    first_name: str
    last_name: str


class MembershipListOut(Schema):
    season_id: uuid.UUID | None
    items: list[MembershipRowOut]
    next_cursor: str | None


class MembershipIn(Schema):
    """This season's standing. `status`, `fee_status` and `fee_amount` are club-admin only (403 for a member admin)."""

    kind: KindValue | None = None
    license: str | None = None
    status: StatusValue | None = None
    fee_status: FeeStatusValue | None = None
    fee_amount: Decimal | None = None


class MemberCreateIn(Schema):
    first_name: str
    last_name: str
    date_of_birth: datetime.date | None = None
    email: str = ""
    phone: str | None = None
    emergency_phone: str | None = None
    photo_public_consent: bool = False


class MemberPatch(Schema):
    """Only the fields sent change. The person fields are refused (409 `person_locked`) while `person_fields_locked`."""

    first_name: str | None = None
    last_name: str | None = None
    date_of_birth: datetime.date | None = None
    email: str | None = None
    phone: str | None = None
    emergency_phone: str | None = None
    photo_public_consent: bool | None = None
    membership: MembershipIn | None = None


class DeleteOut(Schema):
    result: Literal["deleted", "removed_from_club"] = Field(description="`removed_from_club`: the record is shared, so only this club's data about the person was removed.")


class GrantLoginIn(Schema):
    email: str


class AttachFamilyIn(Schema):
    role: FamilyRoleValue
    family_id: uuid.UUID | None = Field(None, description="An existing family of this club; omit to start a new one.")


class FamilyRolePatch(Schema):
    role: FamilyRoleValue


class FamilyCreateIn(Schema):
    parent_first_name: str
    parent_last_name: str
    parent_email: str
    parent_is_member: bool = False
    child_first_name: str
    child_last_name: str
    child_date_of_birth: datetime.date | None = None


class AddChildIn(Schema):
    first_name: str
    last_name: str
    date_of_birth: datetime.date | None = None


class AddParentIn(Schema):
    email: str
    first_name: str
    last_name: str
    parent_is_member: bool = False


class AddParentOut(Schema):
    result: Literal["added", "invited", "already_in_family"]
    family: FamilyOut
