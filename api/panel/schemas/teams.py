"""Schemas of the management teams and seasons routes (api/panel/routers/teams.py).

Conventions of API.md §6: snake_case, UUID strings, ``YYYY-MM-DD`` dates, enums as stored values.
"""

import datetime
import uuid

from ninja import Field, Schema

# --- seasons -----------------------------------------------------------------------------


class SeasonOut(Schema):
    id: uuid.UUID
    name: str = Field(description='Short label, e.g. "25-26".')
    start_date: datetime.date
    end_date: datetime.date
    is_current: bool = Field(description="The season covering today.")


class SeasonListOut(Schema):
    items: list[SeasonOut] = Field(description="Newest first. A club has few seasons, so the list is not paginated.")


class CurrentSeasonOut(Schema):
    season: SeasonOut | None = Field(None, description="`null` when no season covers today.")


# --- teams -------------------------------------------------------------------------------


class TeamOut(Schema):
    id: uuid.UUID
    name: str
    short_name: str
    referee_management: str
    pool_id: uuid.UUID | None
    age_min: int | None
    age_max: int | None
    feeds_into_id: uuid.UUID | None
    tournament_only: bool
    ordering: int | None
    player_count: int = Field(description="Players on the roster in the current season.")
    staff_count: int = Field(description="Staff assigned in the current season.")


class TeamDetailOut(TeamOut):
    can_manage: bool = Field(description="The caller may change this team's roster and staff.")
    can_edit: bool = Field(description="The caller may edit or delete the team itself.")


class TeamListOut(Schema):
    items: list[TeamOut]
    next_cursor: str | None


class TeamIn(Schema):
    name: str
    short_name: str
    referee_management: str = "club"
    pool_id: uuid.UUID | None = None
    age_min: int | None = None
    age_max: int | None = None
    feeds_into_id: uuid.UUID | None = None
    tournament_only: bool = False
    ordering: int | None = None


class TeamPatch(Schema):
    """Every field optional; only the ones sent change."""

    name: str | None = None
    short_name: str | None = None
    referee_management: str | None = None
    pool_id: uuid.UUID | None = None
    age_min: int | None = None
    age_max: int | None = None
    feeds_into_id: uuid.UUID | None = None
    tournament_only: bool | None = None
    ordering: int | None = None


# --- roster ------------------------------------------------------------------------------


class RosterEntryOut(Schema):
    id: uuid.UUID = Field(description="The roster entry (TeamMembership) id.")
    member_id: uuid.UUID
    first_name: str
    last_name: str
    position_id: uuid.UUID | None
    position: str
    jersey_number: int | None
    is_captain: bool
    is_alternate_captain: bool
    sign_up_pending: bool = Field(description="Placed here, but the club sign-up is not approved yet.")


class StaffEntryOut(Schema):
    id: uuid.UUID = Field(description="The staff assignment id.")
    member_id: uuid.UUID
    first_name: str
    last_name: str
    position_id: uuid.UUID
    position: str
    is_manager: bool


class RosterOut(Schema):
    season_id: uuid.UUID | None
    players: list[RosterEntryOut]
    staff: list[StaffEntryOut]


class RosterAddIn(Schema):
    member_id: uuid.UUID
    position_id: uuid.UUID
    season_id: uuid.UUID | None = Field(None, description="Defaults to the current season.")
    jersey_number: int | None = None
    is_captain: bool = False
    is_alternate_captain: bool = False
    override_conflict: bool = Field(False, description="Ignore a number-pool clash. Honoured only for members administrators.")


class RosterPatch(Schema):
    position_id: uuid.UUID | None = None
    jersey_number: int | None = None
    is_captain: bool | None = None
    is_alternate_captain: bool | None = None
    override_conflict: bool = False


class StaffAddIn(Schema):
    member_id: uuid.UUID
    position_id: uuid.UUID
    season_id: uuid.UUID | None = Field(None, description="Defaults to the current season.")


class StaffListOut(Schema):
    season_id: uuid.UUID | None
    items: list[StaffEntryOut]
