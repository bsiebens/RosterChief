"""Schemas for ``GET /members/{id}/stats`` (``api.routers.member_stats``)."""

import datetime
import uuid
from decimal import Decimal

from ninja import Schema

from api.schemas_analytics import MemberGameStatsOut
from api.schemas_events import SeasonRef


class StatGameRef(Schema):
    id: uuid.UUID
    title: str
    start: datetime.datetime
    opponent: str | None = None
    is_home_game: bool
    score_for: int | None = None
    score_against: int | None = None


class SeasonLine(Schema):
    season: SeasonRef | None = None
    games: int
    goals: int
    assists: int
    points: int
    penalty_minutes: int


class FormGame(Schema):
    event: StatGameRef
    goals: int
    assists: int
    points: int
    penalty_minutes: int


class Split(Schema):
    games: int
    goals: int
    assists: int
    points: int


class BestGame(Schema):
    event: StatGameRef
    points: int
    goals: int


class PlayerStats(Schema):
    seasons: list[SeasonLine]  # oldest first
    form: list[FormGame]  # the last games, oldest first
    home: Split
    away: Split
    goals_per_game: float
    points_per_game: float
    current_point_streak: int
    longest_point_streak: int
    multi_point_games: int
    best_game: BestGame | None = None


class GoalieSeason(Schema):
    season: SeasonRef | None = None
    games: int
    conceded: int
    minutes: int | None = None
    gaa: float | None = None
    shutouts: int


class GoalieForm(Schema):
    event: StatGameRef
    conceded: int
    minutes: int | None = None
    gaa: float | None = None


class GoalieStats(Schema):
    games: int
    conceded: int
    minutes: int | None = None
    gaa: float | None = None
    shutouts: int
    seasons: list[GoalieSeason]
    form: list[GoalieForm]


class MonthRate(Schema):
    month: str  # YYYY-MM
    present: int
    absent: int
    rate: int | None = None


class KindRate(Schema):
    kind: str
    present: int
    absent: int
    rate: int | None = None


class AttendanceStats(Schema):
    present: int
    absent: int
    rate: int | None = None
    months: list[MonthRate]
    kinds: list[KindRate]
    current_streak: int
    longest_streak: int


class StaffLine(Schema):
    season: SeasonRef | None = None
    team: dict
    position: str
    games: int
    won: int
    drawn: int
    lost: int
    gf: int
    ga: int


class OfficiatingMonth(Schema):
    month: str  # "2026-03"
    counts: list[int]  # per entry of ``types``


class OfficiatingSeason(Schema):
    season: SeasonRef
    games: int


class OfficiatingCompetition(Schema):
    competition: str  # the federation / source ("RBIHF"), "" for games without one
    teams: str  # our teams in the game, short names
    games: int


class OfficiatingKind(Schema):
    games: int
    games_this_season: int
    games_per_month: float  # over the last 12 months
    penalties_per_game: float | None = None  # from the recorded game timelines; None without any
    fees: Decimal  # payable this season
    types: list[str]  # referee / official types, most used first ("" = no type)
    months: list[OfficiatingMonth]
    seasons: list[OfficiatingSeason]
    competitions: list[OfficiatingCompetition]


class OfficiatingStats(Schema):
    referee: OfficiatingKind | None = None
    official: OfficiatingKind | None = None


class PublicPlayerStatsOut(Schema):
    first_name: str
    last_name: str
    game_stats: MemberGameStatsOut
    player: PlayerStats | None = None
    goalie: GoalieStats | None = None


class ShareOut(Schema):
    share_token: str


class MemberStatsOut(Schema):
    analytics_available: bool
    update_in_progress: bool = False  # game stats are being re-linked after the player joined a team
    player: PlayerStats | None = None
    goalie: GoalieStats | None = None
    attendance: AttendanceStats | None = None
    staff: list[StaffLine]
    officiating: OfficiatingStats | None = None
    share_token: str | None = None  # the personal public link's secret; only shown to members managers
