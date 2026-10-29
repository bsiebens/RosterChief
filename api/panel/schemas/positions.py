"""Schemas of the management positions and number-pool routes (api/panel/routers/positions.py, number_pools.py)."""

import uuid

from ninja import Field, Schema


class PositionOut(Schema):
    id: uuid.UUID
    name: str
    short_name: str
    ordering: int
    staff_position: bool
    management_position: bool


class PositionListOut(Schema):
    items: list[PositionOut] = Field(description="Ordered like the web list (`ordering`, then name). Few per club, so not paginated.")


class PositionIn(Schema):
    name: str = Field(max_length=255, description="Unique within the club.")
    short_name: str = Field(max_length=255)
    ordering: int = Field(0, ge=0, le=32767, description="Lower numbers are listed first.")
    staff_position: bool = Field(False, description="Assigned through a staff assignment rather than a roster spot.")
    management_position: bool = Field(False, description="Management authority over the team; requires `staff_position`.")


class PositionPatch(Schema):
    name: str | None = Field(None, max_length=255)
    short_name: str | None = Field(None, max_length=255)
    ordering: int | None = Field(None, ge=0, le=32767)
    staff_position: bool | None = None
    management_position: bool | None = None


class PoolOut(Schema):
    id: uuid.UUID
    name: str
    min_number: int
    max_number: int
    min_age_gap_years: int = Field(description="Players born at least this many years apart may share a number; 0 = never shared.")
    team_count: int = Field(description="Teams using this pool.")


class PoolListOut(Schema):
    items: list[PoolOut]


class PoolIn(Schema):
    name: str = Field(max_length=255, description="Unique within the club.")
    min_number: int = Field(ge=0, le=32767)
    max_number: int = Field(ge=0, le=32767, description="At least `min_number`.")
    min_age_gap_years: int = Field(5, ge=0, le=32767)


class PoolPatch(Schema):
    name: str | None = Field(None, max_length=255)
    min_number: int | None = Field(None, ge=0, le=32767)
    max_number: int | None = Field(None, ge=0, le=32767)
    min_age_gap_years: int | None = Field(None, ge=0, le=32767)
