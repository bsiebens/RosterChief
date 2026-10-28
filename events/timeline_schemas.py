"""Response schemas for a game's timeline, served by the member app API
(api/app/routers/member.py) -- built by
events.services.game_timeline.timeline_for_api from the stored document
(GAME_TIMELINE.md §2.2).
"""

from datetime import datetime

from ninja import Field, Schema


class TimelinePlayerOut(Schema):
    name: str | None = Field(None, description="The player's name, when known.")
    number: int | None = Field(None, description="Jersey number, when the source gives one.")


class ScoreOut(Schema):
    home: int
    away: int


class PeriodScoreOut(Schema):
    label: str = Field(description='"1", "2", "3", then "OT"/"OT2"/"SO".')
    home: int
    away: int


class TimelineGoalOut(Schema):
    scorer: TimelinePlayerOut
    assists: list[TimelinePlayerOut]
    strength: str | None = Field(None, description="EQ, PP1, PP2, SH1, SH2, EN or PS, when the source says.")
    empty_net: bool = False
    penalty_shot: bool = False
    game_winning: bool = False
    score_after: ScoreOut | None = Field(None, description="The score right after this goal.")


class TimelinePenaltyOut(Schema):
    player: TimelinePlayerOut
    code: str = Field("", description="IIHF penalty code, e.g. TRIP.")
    description: str = ""
    minutes: int | None = None
    kind: str | None = Field(None, description="minor | bench_minor | double_minor | major | misconduct | game_misconduct | match, when the source says (or its length implies it). Anything from major up is serious.")


class TimelineGoalieChangeOut(Schema):
    player: TimelinePlayerOut
    action: str = Field(description='"in" or "out".')


class TimelineEventOut(Schema):
    id: str = Field(description="Stable across fetches -- use it to tell new events from ones already shown.")
    type: str = Field(description="goal | penalty | penalty_shot | goalie_change | timeout | period_start | period_end. A penalty_shot has no detail object: `side`/`ours` say who was awarded it.")
    period: str | None
    time: str | None = Field(description="Game clock as the source shows it, mm:ss.")
    seconds: int | None = Field(description="Game clock in seconds, for sorting.")
    side: str | None = Field(description='"home" or "away"; null for period markers.')
    ours: bool | None = Field(description="Whether this was our club's side; null for period markers.")
    goal: TimelineGoalOut | None = None
    penalty: TimelinePenaltyOut | None = None
    goalie_change: TimelineGoalieChangeOut | None = None


class TimelineOut(Schema):
    status: str = Field(description="scheduled | live | final")
    our_side: str = Field(description='Which side ("home"/"away") is our club in this game.')
    home_score: int | None
    away_score: int | None
    periods: list[PeriodScoreOut]
    decided_in: str | None = Field(description="regulation | overtime | shootout, when known.")
    updated_at: datetime | None = Field(description="Last time the timeline changed.")
    final: bool = Field(description="True once the federation stopped correcting it.")
    events: list[TimelineEventOut] = Field(description="Chronological.")
