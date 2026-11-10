"""Schemas for the teams API (``/api/v1/teams``)."""

import datetime
from uuid import UUID

from ninja import Schema


class TeamBase(Schema):
    id: UUID
    name: str
    short_name: str
    pool_id: UUID | None = None
    pool_name: str | None = None
    age_min: int | None = None
    age_max: int | None = None
    tournament_only: bool
    ordering: int | None = None
    feeds_into_id: UUID | None = None
    feeds_into_name: str | None = None
    referee_management: str = "club"  # club | federation: who arranges referees for the team's home games
    official_management: str = "club"


class TeamListOut(TeamBase):
    player_count: int
    staff_count: int


class TeamOrderIn(Schema):
    ids: list[UUID]


class TeamIn(Schema):
    name: str
    short_name: str = ""
    pool_id: UUID | None = None
    age_min: int | None = None
    age_max: int | None = None
    tournament_only: bool = False
    ordering: int | None = None
    feeds_into_id: UUID | None = None
    referee_management: str = "club"
    official_management: str = "club"


class TeamPatch(Schema):
    """Only the fields that are sent are changed (null clears pool_id, ages, ordering and feeds_into_id)."""

    name: str | None = None
    short_name: str | None = None
    pool_id: UUID | None = None
    age_min: int | None = None
    age_max: int | None = None
    tournament_only: bool | None = None
    ordering: int | None = None
    feeds_into_id: UUID | None = None
    referee_management: str | None = None
    official_management: str | None = None


class RosterEntryOut(Schema):
    id: UUID
    member_id: UUID
    first_name: str
    last_name: str
    photo_url: str | None = None
    date_of_birth: datetime.date | None = None
    position_id: UUID | None = None
    position_name: str | None = None
    jersey_number: int | None = None
    is_captain: bool
    is_alternate_captain: bool


class StaffEntryOut(Schema):
    id: UUID
    member_id: UUID
    first_name: str
    last_name: str
    photo_url: str | None = None
    position_id: UUID
    position_name: str


class TeamDetailOut(TeamBase):
    season_id: UUID | None = None
    photo_url: str | None = None  # the team photo of that season
    analytics_available: bool = False  # game analytics exist for this club (hockey + the analytics feature): GET /teams/{id}/analytics
    roster: list[RosterEntryOut]
    staff: list[StaffEntryOut]


class RosterIn(Schema):
    member_id: UUID
    season_id: UUID | None = None
    position_id: UUID | None = None
    jersey_number: int | None = None
    is_captain: bool = False
    is_alternate_captain: bool = False


class RosterPatch(Schema):
    position_id: UUID | None = None
    jersey_number: int | None = None
    is_captain: bool | None = None
    is_alternate_captain: bool | None = None


class PersonRowIn(Schema):
    member_id: UUID
    position_id: UUID | None = None
    jersey_number: int | None = None
    is_captain: bool = False
    is_alternate_captain: bool = False


class PeopleIn(Schema):
    season_id: UUID | None = None
    rows: list[PersonRowIn]


class PersonRowOut(Schema):
    index: int
    ok: bool
    role: str | None = None  # player | staff
    error: str | None = None


class PeopleOut(Schema):
    added: int
    rows: list[PersonRowOut]


class StaffIn(Schema):
    member_id: UUID
    season_id: UUID | None = None
    position_id: UUID


class StaffPatch(Schema):
    position_id: UUID


class HolderOut(Schema):
    member_id: UUID
    first_name: str
    last_name: str
    date_of_birth: datetime.date | None = None
    team_id: UUID
    team_name: str
    #: True for a number held by a registration that still waits for the billing review (not a roster spot yet).
    pending: bool = False


class PoolOut(Schema):
    id: UUID
    name: str
    min_number: int
    max_number: int
    min_age_gap_years: int


class ReservationOut(Schema):
    id: UUID
    note: str
    reserved_by_name: str | None = None
    can_release: bool = False


class NumberOut(Schema):
    number: int
    status: str  # free | taken | shared | reserved | conflict | held (by a registration waiting for review)
    holders: list[HolderOut]
    reservation: ReservationOut | None = None


class TeamRefOut(Schema):
    id: UUID
    name: str


class PoolOverviewOut(Schema):
    pool: PoolOut | None = None  # null: the teams without a pool
    teams: list[TeamRefOut]
    numbers: list[NumberOut]


class TeamNumbersOut(Schema):
    season_id: UUID | None = None
    pool: PoolOut | None = None
    players: list[RosterEntryOut]
    free_numbers: list[int]
    taken_by_others: dict[str, list[HolderOut]]  # number -> holders on other teams (that would clash for this team)
    reserved: dict[str, str]  # number -> reservation note


class EligibleRefereeOut(Schema):
    member_id: UUID
    first_name: str
    last_name: str
    photo_url: str | None = None
    level_name: str
    valid_until: datetime.date | None = None
