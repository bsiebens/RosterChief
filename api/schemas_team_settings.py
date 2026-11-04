"""Schemas for the team settings: positions, number pools, reservations and the club-wide officiating people list."""

import datetime
from uuid import UUID

from ninja import Schema


class PositionOut(Schema):
    id: UUID
    name: str
    short_name: str
    ordering: int
    staff_position: bool
    management_position: bool
    in_use_count: int


class PositionIn(Schema):
    name: str
    short_name: str = ""
    ordering: int = 0
    staff_position: bool = False
    management_position: bool = False


class PositionOrderIn(Schema):
    ids: list[UUID]


class PositionPatch(Schema):
    """Only the fields that are sent are changed."""

    name: str | None = None
    short_name: str | None = None
    ordering: int | None = None
    staff_position: bool | None = None
    management_position: bool | None = None


class TeamRef(Schema):
    id: UUID
    name: str


class PoolOut(Schema):
    id: UUID
    name: str
    min_number: int
    max_number: int
    min_age_gap_years: int
    team_ids: list[UUID]
    teams: list[TeamRef]
    reservation_count: int


class PoolIn(Schema):
    name: str
    min_number: int
    max_number: int
    min_age_gap_years: int = 5
    team_ids: list[UUID] | None = None  # None = leave the assignment alone (POST: no teams)


class PoolPatch(Schema):
    """Only the fields that are sent are changed. ``team_ids`` (when sent) is the complete list of teams using the pool."""

    name: str | None = None
    min_number: int | None = None
    max_number: int | None = None
    min_age_gap_years: int | None = None
    team_ids: list[UUID] | None = None


class ReservationIn(Schema):
    number: int
    note: str = ""


class ReservationOut(Schema):
    id: UUID
    number: int
    note: str
    reserved_by_name: str | None = None
    can_release: bool = False


class OfficiatingPersonOut(Schema):
    member_id: UUID
    first_name: str
    last_name: str
    photo_url: str | None = None
    kind: str
    level_id: UUID | None = None
    level_name: str | None = None
    valid_until: datetime.date | None = None
    is_valid: bool
    eligible_team_names: list[str]
    inherited_team_names: list[str] = []  # the subset of eligible teams that only come through an inherited level
    excluded_team_names: list[str]
