"""Schemas for a member's referee / official eligibility (``/members/{id}/officiating`` and friends)."""

import datetime
from uuid import UUID

from ninja import Schema


class RefOut(Schema):
    id: UUID
    name: str


class ProfileOut(Schema):
    id: UUID
    level: RefOut | None = None
    valid_until: datetime.date | None = None
    is_valid: bool
    is_eligible: bool
    eligible_teams: list[RefOut]
    excluded_teams: list[RefOut]


class OfficiatingOut(Schema):
    referee: ProfileOut | None = None
    official: ProfileOut | None = None


class OfficiatingOptionsOut(Schema):
    referee_levels: list[RefOut]
    official_levels: list[RefOut]
    teams: list[RefOut]


class RefereeIn(Schema):
    level_id: UUID | None = None
    valid_until: datetime.date | None = None
    excluded_team_ids: list[UUID] = []


class OfficialIn(Schema):
    level_id: UUID | None = None
    valid_until: datetime.date | None = None
