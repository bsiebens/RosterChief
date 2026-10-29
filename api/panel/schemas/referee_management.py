"""Shapes of the referee management dashboard (``GET /manage/referees/management``)."""

import datetime
import uuid

from ninja import Field, Schema

from api.panel.schemas.event_crew import CrewAssignedOut, CrewCandidateOut, CrewSignupsOut


class StaffingTeamOut(Schema):
    id: uuid.UUID
    name: str


class StaffingCrewOut(Schema):
    needed: bool = Field(description="The club arranges this crew for the game.")
    max: int = Field(description="Capacity of the game.")
    full: bool
    fees_pending: bool = Field(description="At least one assignment has no fee set yet.")
    assigned: list[CrewAssignedOut]
    candidates: list[CrewCandidateOut] = Field(description="Who could be assigned; empty while the game is full or not needed.")
    signups: CrewSignupsOut = Field(description="Not-yet-confirmed sign-ups.")


class StaffingGameOut(Schema):
    id: uuid.UUID
    title: str
    kind: str
    start: datetime.datetime
    location: str | None
    opponent: str | None
    teams: list[StaffingTeamOut]
    referees: StaffingCrewOut
    officials: StaffingCrewOut | None = Field(description="Null when the club has no officials module.")


class StaffingNeedCountsOut(Schema):
    all: int
    both: int = Field(description="Short a referee or an official.")
    referees: int = Field(description="Short a referee.")
    officials: int = Field(description="Short an official (always 0 without the officials module).")


class StaffingStatusOut(Schema):
    without_anyone: int
    understaffed: int
    fully_staffed: int
    fees_pending: int


class WorkloadOut(Schema):
    member_id: uuid.UUID
    first_name: str
    last_name: str
    games: int
    total_fees: float | None


class StaffingDashboardOut(Schema):
    range: str = Field(description="The applied window: `week`, `two_weeks`, `10`, `25` or `50` (an unknown value falls back to `10`).")
    need: str = Field(description="The applied filter: `all`, `both`, `referees` or `officials` (an unknown value falls back to `all`).")
    officials_enabled: bool
    need_counts: StaffingNeedCountsOut = Field(description="Games per `need` filter inside the window.")
    games: list[StaffingGameOut] = Field(description="The games in the window that match the `need` filter, by start.")
    referee_status: StaffingStatusOut = Field(description="Season-wide, not bound to the window or filter.")
    official_status: StaffingStatusOut | None = Field(description="Season-wide; null without the officials module.")
    active_referees: int
    total_assignments: int
    avg_games_per_referee: float
    workload: list[WorkloadOut] = Field(description="Games refereed this season per member, busiest first.")
