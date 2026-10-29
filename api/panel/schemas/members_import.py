"""Schemas of the member import slice (api/panel/routers/members_import.py)."""

import uuid

from ninja import Field, Schema


class ImportRowOut(Schema):
    line_number: int = Field(description="The spreadsheet row (the header is row 1).")
    valid: bool
    errors: list[str]
    first_name: str
    last_name: str
    email: str
    family_group: str | None
    family_role: str | None
    membership_kind: str


class FamilyCandidateOut(Schema):
    family_id: uuid.UUID
    name: str
    members: list[str]


class FamilyGroupOut(Schema):
    family_group: str
    candidates: list[FamilyCandidateOut]


class ImportPreviewOut(Schema):
    season: str | None = Field(description="The season the new people are signed up for; null when there is none (they would be created unrostered).")
    valid_count: int
    skipped_count: int
    rows: list[ImportRowOut]
    family_groups: list[FamilyGroupOut] = Field(description='Existing families a `family_group` might be; send the choice back as `family_choices` (`{group: family_id | "new"}`).')


class ImportResultSkippedOut(Schema):
    line_number: int
    errors: list[str]


class ImportResultOut(Schema):
    """The `result` of a succeeded import job."""

    created: int
    skipped: int
    rostered: bool
    skipped_rows: list[ImportResultSkippedOut]
