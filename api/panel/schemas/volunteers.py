"""Schemas of the management volunteers routes (api/panel/routers/volunteers.py)."""

import uuid

from ninja import Field, Schema

from api.panel.schemas.levels import RefOut
from api.panel.schemas.teams import SeasonOut


class AssignmentOut(Schema):
    id: uuid.UUID
    team: RefOut
    position: RefOut


class VolunteerOut(Schema):
    member_id: uuid.UUID
    first_name: str
    last_name: str
    assignments: list[AssignmentOut]


class PendingVolunteerOut(Schema):
    id: uuid.UUID = Field(description="The registration to place (`POST /manage/volunteers/{id}/place`).")
    member_id: uuid.UUID
    first_name: str
    last_name: str
    requested_team: RefOut | None
    requested_position: RefOut | None


class VolunteersOut(Schema):
    season: SeasonOut | None
    volunteers: list[VolunteerOut] = Field(description="Everyone holding a staff assignment this season.")
    pending: list[PendingVolunteerOut] = Field(description="Volunteer registrations not yet placed.")


class PlaceIn(Schema):
    team_id: uuid.UUID
    position_id: uuid.UUID = Field(description="A staff position of the club.")
