"""Schemas of the management referee / official level and list routes (api/panel/routers/levels.py)."""

import datetime
import uuid

from ninja import Field, Schema


class RefOut(Schema):
    id: uuid.UUID
    name: str


class LevelIn(Schema):
    name: str = Field(max_length=255, description="Unique within the club.")
    team_ids: list[uuid.UUID] = Field(default_factory=list, description="Teams this level qualifies for (the club's teams).")
    inherits_from_id: uuid.UUID | None = Field(None, description="Another level of the same kind whose teams this one also covers; loops are refused.")


class LevelPatch(Schema):
    name: str | None = Field(None, max_length=255)
    team_ids: list[uuid.UUID] | None = None
    inherits_from_id: uuid.UUID | None = None


class LevelOut(Schema):
    id: uuid.UUID
    name: str
    teams: list[RefOut]
    inherits_from: RefOut | None


class LevelListOut(Schema):
    items: list[LevelOut]


class ProfileOut(Schema):
    member_id: uuid.UUID
    first_name: str
    last_name: str
    level: RefOut | None
    valid_until: datetime.date | None
    is_eligible: bool = Field(description="A level is set and its validity has not passed.")
    eligible_teams: list[RefOut] = Field(description="Teams the person may be assigned to, by name.")


class ProfileListOut(Schema):
    items: list[ProfileOut]
    next_cursor: str | None = None
