"""Schemas for the fixture imports (``/api/v1/teams/{id}/fixture-sources``, ``/api/v1/fixture-imports/json``)."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from ninja import Schema

from api.schemas_calendar import LocationIn

# -- sources --


class LastCheckOut(Schema):
    rescheduled: int = 0
    new_games: int = 0
    removed_games: int = 0
    error: str = ""


class FixtureSourceOut(Schema):
    id: str
    source: str
    url: str
    season_id: UUID | None = None
    season_name: str
    season_exists: bool
    competition_label: str
    last_imported: datetime | None = None
    last_checked: datetime | None = None
    last_check: LastCheckOut | None = None
    enabled: bool
    has_standings: bool


class AvailableSourceOut(Schema):
    source: str
    name: str
    feature: str
    feature_name: str
    help: str
    example: str
    enabled: bool


class FixtureSourcesOut(Schema):
    sources: list[FixtureSourceOut]
    available: list[AvailableSourceOut]
    can_import: bool


# -- preview --


class RefOut(Schema):
    id: UUID
    name: str


class LocationRefOut(RefOut):
    city: str = ""


class ChangeOut(Schema):
    old: str | None = None
    new: str | None = None


class PlannedGameOut(Schema):
    action: Literal["create", "update", "remove", "unchanged"]
    game_id: str
    kickoff: datetime
    is_home: bool
    our_side: Literal["home", "away"]
    home: str
    away: str
    title: str = ""
    opponent_name: str
    venue: str
    opponent: RefOut | None = None
    location: RefOut | None = None
    event_id: UUID | None = None
    score_for: int | None = None
    score_against: int | None = None
    changes: dict[str, ChangeOut]


class UnknownOpponentOut(Schema):
    source_name: str
    game_ids: list[str]
    current: RefOut | None = None
    default: Literal["keep", "create"]
    suggestions: list[RefOut]


class UnknownLocationOut(Schema):
    source_text: str
    game_ids: list[str]
    suggestions: list[LocationRefOut]


class PlanCountsOut(Schema):
    create: int
    update: int
    remove: int
    unchanged: int


class FixturePreviewOut(Schema):
    token: str
    source: str
    url: str
    season_id: UUID | None = None
    season_name: str
    competition_label: str
    scraped_team_name: str
    team: RefOut
    counts: PlanCountsOut
    has_changes: bool
    games: list[PlannedGameOut]
    unknown_opponents: list[UnknownOpponentOut]
    unknown_locations: list[UnknownLocationOut]
    warnings: list[str]


class FixturePreviewIn(Schema):
    source: Literal["RBIHF", "DFEL", "IJNL"]
    url: str
    season_id: UUID | None = None
    competition_label: str = ""


# -- import --


class OpponentChoiceIn(Schema):
    opponent_id: UUID | None = None
    create: bool = False
    name: str | None = None


class LocationChoiceIn(Schema):
    location_id: UUID | None = None
    create: LocationIn | None = None


class FixtureImportIn(Schema):
    token: str
    opponents: dict[str, OpponentChoiceIn] = {}
    locations: dict[str, LocationChoiceIn | None] = {}
    game_locations: dict[str, UUID | None] = {}


class FixtureImportOut(Schema):
    created: int
    updated: int
    deleted: int
    opponents_created: int
    locations_created: int
    sources: list[FixtureSourceOut]


# -- re-check --


class RescheduledOut(Schema):
    game_id: str
    old_start: datetime
    new_start: datetime


class NewGameOut(Schema):
    game_id: str
    start: datetime
    opponent_name: str


class RemovedGameOut(Schema):
    game_id: str
    start: datetime
    title: str


class CheckOut(Schema):
    rescheduled: list[RescheduledOut]
    new_games: list[NewGameOut]
    removed_games: list[RemovedGameOut]
    error: str
    has_changes: bool


class RecheckOut(Schema):
    source: FixtureSourceOut
    check: CheckOut
    preview: FixturePreviewOut | None = None


class SkippedSourceOut(Schema):
    id: str
    source: str
    reason: str


class RecheckAllOut(Schema):
    season_id: UUID | None = None
    results: list[RecheckOut]
    skipped: list[SkippedSourceOut]


# -- standings --


class StandingsCompetitionOut(Schema):
    league: str
    league_url: str
    competition: str
    competition_label: str
    points_rule: str
    has_overtime: bool
    updated_at: datetime | None = None
    error: str
    rows: list[dict]


# -- JSON play days --


class TeamRefOut(Schema):
    id: UUID
    name: str


class PlayDayOut(Schema):
    action: Literal["create", "update"]
    game_id: str
    start: datetime
    end: datetime | None = None
    city: str
    teams: list[TeamRefOut]
    hosted_by_us: bool
    label: str
    location: RefOut | None = None
    warning: str | None = None
    event_id: UUID | None = None
    changes: dict[str, ChangeOut]


class PlayDayCountsOut(Schema):
    create: int
    update: int
    unchanged: int


class PlayDayPreviewOut(Schema):
    token: str
    source: str
    competition: str
    title: str
    counts: PlayDayCountsOut
    has_changes: bool
    games: list[PlayDayOut]


class PlayDayImportIn(Schema):
    token: str
    locations: dict[str, UUID | None] = {}
    acknowledge_warnings: bool = False


class PlayDayImportOut(Schema):
    created: int
    updated: int
