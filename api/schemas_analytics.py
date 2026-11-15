"""Schemas for the game analytics API (``api.routers.analytics``): a team's analytics document
(``events.services.game_stats.team_analytics``), a member's own game numbers (``member_stats``) and the goal-strength
corrections. Games are serialized as ``GameRef``; ``*_pct`` fields are 0-100 shares of the largest value of their chart."""

import datetime
import uuid

from ninja import Schema

from api.schemas_events import SeasonRef


class GameRef(Schema):
    id: uuid.UUID
    title: str
    start: datetime.datetime
    opponent: str | None = None
    is_home_game: bool
    competition: str = ""
    is_friendly: bool = False
    score_for: int | None = None
    score_against: int | None = None


class RecordOut(Schema):
    won: int
    drawn: int
    lost: int


class FormOut(Schema):
    result: str  # W / D / L
    event: GameRef


class SplitRowOut(Schema):
    games: int
    won: int
    drawn: int
    lost: int
    gf: int
    ga: int
    diff: int
    avg_for: float | None = None
    avg_against: float | None = None
    share_for: int  # goals for as a share of all goals in these games (50 without goals)


class SplitOut(Schema):
    home: SplitRowOut
    away: SplitRowOut


class LeaderOut(Schema):
    """One of our players on a leaderboard. ``member_id`` when the timeline name / number matched the roster."""

    name: str
    number: int | None = None
    member_id: uuid.UUID | None = None
    initials: str = ""
    photo_url: str | None = None  # the matched member's player picture (the file view enforces who may see it)
    total: int
    goals: int = 0
    pct: int  # share of the leader's total, for a bar


class GoalieOut(LeaderOut):
    """``total`` = goals conceded while in net; ``minutes`` / ``gaa`` only when the timeline says how long he played."""

    seconds: int = 0
    minutes: int | None = None
    gaa: float | None = None


class ForAgainstOut(Schema):
    """Goals for / against in one period or one 5-minute bucket of the game clock (``60+``: overtime)."""

    label: str
    goals_for: int
    goals_against: int
    for_pct: int
    against_pct: int


class TrendPointOut(Schema):
    x: float
    y: float
    value: int
    game: GameRef | None = None  # None for the starting point


class TrendOut(Schema):
    """The running goal difference in a 0-100 box (y grows downwards); ``line`` / ``area`` are SVG point lists."""

    points: list[TrendPointOut]
    line: str
    area: str
    zero_y: float
    last: int  # the change the last game made


class OpponentRowOut(Schema):
    name: str
    games: int
    won: int
    drawn: int
    lost: int
    gf: int
    ga: int


class GameRowOut(Schema):
    """A played game, newest first. ``pim_*`` are None when the game has no penalty data."""

    event: GameRef
    result: str
    goals_for: int
    goals_against: int
    pim_for: int | None = None
    pim_against: int | None = None
    pim_for_excl: int | None = None
    pp_goals: int | None = None
    pp_opportunities: int | None = None
    pk_goals_against: int | None = None
    pk_opportunities: int | None = None
    pim_for_pct: int = 0
    pim_against_pct: int = 0
    total_pct: int = 0


class GameBarOut(Schema):
    """A played game, oldest first, for the game-by-game bars."""

    event: GameRef
    result: str
    goals_for: int
    goals_against: int
    for_pct: int = 0
    against_pct: int = 0


class PenaltyRefOut(Schema):
    time: str = ""
    player: str = ""
    kind: str = ""
    side: str = ""
    ours: bool = False


class StrengthWarningOut(Schema):
    """A goal whose recorded strength conflicts with the penalties at that moment. ``keep`` is what "keep as recorded"
    stores; ``can_fix`` whether the caller may correct it (PUT/DELETE ``/events/{id}/strength-overrides/{goal}``)."""

    event: GameRef
    goal: str
    ours: bool
    player: str
    period: str
    time: str
    recorded: str
    recorded_label: str
    keep: str
    expected: str
    expected_label: str
    penalties: list[PenaltyRefOut]
    can_fix: bool = False


class TeamRefOut(Schema):
    id: uuid.UUID
    name: str


class AnalyticsCapabilities(Schema):
    analytics: bool


class TeamAnalyticsOut(Schema):
    team: TeamRefOut
    season: SeasonRef
    scope: str
    friendlies: bool
    competition: str
    competitions: list[str]
    capabilities: AnalyticsCapabilities
    games: int
    timeline_games: int  # games with a game sheet (stat rows); player and period figures only cover these
    record: RecordOut
    points_table: int
    goals_for: int
    goals_against: int
    goal_difference: int
    avg_for: float | None = None
    avg_against: float | None = None
    clean_sheets: int
    form: list[FormOut]
    split: SplitOut
    pim_for: int
    pim_against: int
    avg_pim: float | None = None
    game_misconduct_minutes: int
    avg_pim_excl_game_misconducts: float | None = None
    pp_goals_for: int
    pp_goals_against: int
    pp_opportunities: int
    pk_opportunities: int
    pk_killed: int
    pp_pct: float | None = None
    pk_pct: float | None = None
    game_winning_goals: int
    empty_net_goals: int
    shots_for: int | None = None
    shots_against: int | None = None
    shot_games: int
    shooting_pct: float | None = None
    save_pct: float | None = None
    top_scorers: list[LeaderOut]
    top_assists: list[LeaderOut]
    top_points: list[LeaderOut]
    top_offenders: list[LeaderOut]
    goalies: list[GoalieOut]
    goalie_unattributed: int
    periods: list[ForAgainstOut]
    goal_minutes: list[ForAgainstOut]
    busiest_minutes: ForAgainstOut | None = None
    opponents: list[OpponentRowOut]
    game_rows: list[GameRowOut]
    game_bars: list[GameBarOut]
    trend: TrendOut | None = None
    strength_warnings: list[StrengthWarningOut]


class GoalieTotalsOut(Schema):
    games: int
    conceded: int
    seconds: int
    minutes: int | None = None
    gaa: float | None = None


class MemberStatsCounters(Schema):
    games: int
    goals: int
    assists: int
    points: int
    penalties: int = 0  # number of penalties taken
    penalty_minutes: int
    pp_goals: int
    game_winning_goals: int
    goalie: GoalieTotalsOut | None = None


class MemberStatsRowOut(MemberStatsCounters):
    team: TeamRefOut
    season: SeasonRef | None = None


class MemberGameStatsOut(MemberStatsCounters):
    """A member's own numbers from the game sheets matched to them, for ``season`` (every season when None)."""

    season: SeasonRef | None = None
    teams: list[MemberStatsRowOut]


class StrengthIn(Schema):
    strength: str  # EQ / PP1 / PP2 / SH1 / SH2


class StrengthOverrideOut(Schema):
    event_id: uuid.UUID
    goal: str
    overrides: dict[str, str]
