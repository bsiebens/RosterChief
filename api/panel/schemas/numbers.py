"""Schemas of the management jersey-number routes (api/panel/routers/numbers.py)."""

import uuid

from ninja import Field, Schema

from api.panel.schemas.positions import PoolOut
from api.panel.schemas.teams import SeasonOut


class NumberHolderOut(Schema):
    member_id: uuid.UUID
    name: str
    team: str = Field(description="The team's short name, else its name.")
    birth_year: int | None


class ReservationOut(Schema):
    id: uuid.UUID
    pool_id: uuid.UUID
    number: int
    note: str
    reserved_by: str | None = Field(description="Display name of the user who reserved it.")
    can_release: bool = Field(description="Whether the caller may release it (their own, or a member admin).")


class NumberTileOut(Schema):
    number: int
    state: str = Field(description="`available`, `taken`, `conflict`, `pending`, `previous` or `reserved`.")
    note: str = Field(description="The reservation note; only for `reserved`.")
    reservation: ReservationOut | None
    holders: list[NumberHolderOut]


class NumberOverviewOut(Schema):
    pools: list[PoolOut]
    pool: PoolOut | None = Field(description="The selected pool; the first by name when `pool_id` isn't given. `null` when the club has no pool.")
    seasons: list[SeasonOut] = Field(description="Newest first.")
    season: SeasonOut | None = Field(description="The selected season; the current one when `season_id` isn't given.")
    state_counts: dict[str, int] = Field(description="Tiles per state; a state without tiles reads 0.")
    tiles: list[NumberTileOut] = Field(description="The pool's whole range, ascending; empty without a pool or season.")


class ReservationIn(Schema):
    pool_id: uuid.UUID
    number: int = Field(ge=0, le=32767)
    note: str = Field("", max_length=255, description='e.g. "Retired -- Jane Doe, #23".')
