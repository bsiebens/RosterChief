"""Shapes of the management game timeline and check-in routes (``/manage/events/{id}/timeline|check-ins|remind-silent``)."""

import datetime
import uuid

from ninja import Field, Schema

from api.panel.schemas.events import PanelAttendanceRowOut
from api.protected.schemas import EventOut
from events.timeline_schemas import TimelineOut


class MomentOut(Schema):
    id: uuid.UUID
    kind: str = Field(description="game_started, goal_for, goal_against, goal_cancelled, penalty_for, penalty_against, penalty_shot_for, penalty_shot_against, period_started, period_ended or game_final.")
    text: str = Field(description="One localised line, from the club's side.")
    game_seconds: int | None
    detected_at: datetime.datetime
    status: str = Field(description="`pushed` (sent to followers), `catch_up` (too old to be news, never pushed) or `pending`.")


class GameTimelineOut(Schema):
    event_id: uuid.UUID
    source: str | None = Field(description="The competition the timeline was fetched from; null when the game has no timeline.")
    timeline: TimelineOut | None = Field(description="The stored timeline with player names; null for a non-game or a game never fetched.")
    fetched_at: datetime.datetime | None = Field(description="Last successful fetch.")
    changed_at: datetime.datetime | None
    finalized_at: datetime.datetime | None
    last_error: str = Field(description="The last fetch or parse error (the previous data is kept); empty when fine.")
    moments: list[MomentOut] = Field(description="What was detected as news, oldest first.")


class CheckInItemIn(Schema):
    attendance_id: uuid.UUID
    showed_up: bool | None = Field(description="`true`/`false` records the check-in, `null` clears it.")


class CheckInsIn(Schema):
    items: list[CheckInItemIn] = Field(max_length=200, description="Any subset of the event's rows. Sending the same item twice is harmless.")


class CheckInSheetOut(Schema):
    event: EventOut
    counts: dict[str, int] = Field(description="Per attendance status plus `total`, and `checked_in` (rows with `showed_up` set).")
    items: list[PanelAttendanceRowOut] = Field(description="Ordered by last name, first name.")


class RemindSilentOut(Schema):
    notified: int = Field(description="People who got the reminder; 0 when everybody has answered.")
