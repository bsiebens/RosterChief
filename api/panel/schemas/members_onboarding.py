"""Schemas of the onboarding / signup / eligibility slice (api/panel/routers/members_onboarding.py)."""

import datetime
import uuid
from typing import Literal

from ninja import Field, Schema

from api.panel.schemas.members import FeeStatusValue, StatusValue


class RequirementOut(Schema):
    id: uuid.UUID
    name: str
    description: str
    requires_document: bool
    blocked_event_kinds: list[str] = Field(description="Event kinds (`training`, `game` ...) a member can't be invited to or selected for while this is open; empty = informational.")
    is_active: bool


class RequirementListOut(Schema):
    items: list[RequirementOut]


class RequirementIn(Schema):
    name: str
    description: str = ""
    requires_document: bool = False
    blocked_event_kinds: list[str] = []
    is_active: bool = True


class RequirementPatch(Schema):
    name: str | None = None
    description: str | None = None
    requires_document: bool | None = None
    blocked_event_kinds: list[str] | None = None
    is_active: bool | None = None


class ChecklistItemOut(Schema):
    requirement: RequirementOut
    state: Literal["open", "complete", "bypassed"]
    completed_at: datetime.datetime | None
    completed_by: str | None = Field(description="Name of the signed-in user's account that resolved it (their email).")
    note: str
    has_document: bool = Field(description="Download it with `GET /manage/members/{id}/requirements/{requirement_id}/document`.")


class ChecklistOut(Schema):
    member_id: uuid.UUID
    season_id: uuid.UUID
    items: list[ChecklistItemOut]


class BypassIn(Schema):
    note: str = Field(description="Why this isn't needed; required.")


class RefereeEligibilityOut(Schema):
    level_id: uuid.UUID | None
    valid_until: datetime.date | None
    excluded_team_ids: list[uuid.UUID]
    eligible_team_ids: list[uuid.UUID] = Field(description="Teams whose home games they can referee (level minus exclusions); empty when no level or expired.")


class RefereeEligibilityIn(Schema):
    level_id: uuid.UUID | None = Field(None, description="Null = no level (not eligible).")
    valid_until: datetime.date | None = None
    excluded_team_ids: list[uuid.UUID] = []


class OfficialEligibilityOut(Schema):
    level_id: uuid.UUID | None
    valid_until: datetime.date | None


class OfficialEligibilityIn(Schema):
    level_id: uuid.UUID | None = Field(None, description="Null = no level (not eligible).")
    valid_until: datetime.date | None = None


class SignupTeamOut(Schema):
    id: uuid.UUID
    name: str


class SignupDuplicateOut(Schema):
    member_id: uuid.UUID
    first_name: str
    last_name: str
    email: str


class SignupRowOut(Schema):
    member_id: uuid.UUID
    first_name: str
    last_name: str
    status: StatusValue
    fee_status: FeeStatusValue
    signed_up_at: datetime.date | None
    created: datetime.datetime
    onboarding_open: int = Field(description="Unresolved active requirements.")
    is_clean: bool = Field(description="Paid (or waived) and the checklist resolved: `approve` will work.")
    is_oldest_pending: bool
    blocked_event_kinds: list[str]
    checklist: list[ChecklistItemOut]
    teams: list[SignupTeamOut] = Field(description="Teams they are already rostered or staffed on this season.")
    requested_team_ids: list[uuid.UUID]
    possible_duplicates: list[SignupDuplicateOut]


class SignupQueueOut(Schema):
    season_id: uuid.UUID | None
    requirements_configured: bool
    items: list[SignupRowOut]


class ApproveAllOut(Schema):
    activated: int


class SignupApproveOut(Schema):
    member_id: uuid.UUID
    status: StatusValue


class PlaceInTeamIn(Schema):
    team_id: uuid.UUID


class PlacementOut(Schema):
    member_id: uuid.UUID
    team_id: uuid.UUID
    team: str
    jersey_number: int | None


class LinkIn(Schema):
    target_member_id: uuid.UUID = Field(description="The existing member this sign-up really belongs to (see `possible_duplicates`).")


class LinkOut(Schema):
    member_id: uuid.UUID


class CancelOut(Schema):
    result: Literal["cancelled"]
