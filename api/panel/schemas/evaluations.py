"""Schemas of the management evaluation routes (api/panel/routers/evaluations.py)."""

import datetime
import uuid
from typing import Any, Literal

from ninja import Field, Schema

from api.protected.coach_schemas import EvaluationDetailOut

#: The subset of form field types a rubric criterion may use (management.forms.RUBRIC_FIELD_TYPES).
CriterionType = Literal["number", "choice", "textarea", "checkbox"]


class ChecklistOut(Schema):
    id: uuid.UUID
    slug: str = Field(description="Unique per club. Every `{checklist_ref}` path accepts this slug or the `id`.")
    name: str
    description: str
    is_active: bool = Field(description="False = archived: no longer offered for new evaluations; existing ones are unaffected.")
    has_rubric: bool = Field(description="False until a rubric (criteria) was saved for it.")
    evaluation_count: int
    created: datetime.datetime


class ChecklistListOut(Schema):
    items: list[ChecklistOut] = Field(description="Active and archived, by name. A club has few checklists, so not paginated.")


class ChecklistIn(Schema):
    name: str = Field(max_length=255)
    description: str = ""
    is_active: bool = True


class CriterionIn(Schema):
    label: str = Field(max_length=255)
    field_type: CriterionType = "number"
    required: bool = True
    help_text: str = ""
    section: str = Field("", max_length=255)
    options: list[str] = Field([], description="One value per choice; at least one for `choice`, ignored otherwise.")


class RubricIn(Schema):
    criteria: list[CriterionIn] = Field(max_length=200, description="The complete rubric, in the wanted order (list position is the order). Replaces the current criteria.")


class CriterionOut(Schema):
    id: uuid.UUID
    key: str = Field(description="Stable across rubric versions; answers and statistics join on it.")
    label: str
    field_type: str
    required: bool
    help_text: str
    section: str
    options: list[str]
    order: int


class RubricOut(Schema):
    checklist: ChecklistOut
    version_id: uuid.UUID | None = Field(description="Id of the form backing the current rubric version; changes on every save. Null before the first save.")
    criteria: list[CriterionOut]


class DistributionOut(Schema):
    value: Any
    count: int


class QuestionStatOut(Schema):
    key: str
    label: str
    section: str
    field_type: str
    kind: Literal["numeric", "distribution", "text"]
    response_count: int
    average: float | None = Field(None, description="`numeric` only.")
    minimum: float | None = None
    maximum: float | None = None
    distribution: list[DistributionOut] | None = Field(None, description="`distribution` only; most chosen first.")
    recent_answers: list[Any] | None = Field(None, description="`text` only; the five latest.")


class StatsOut(Schema):
    season_id: uuid.UUID | None = Field(description="The season the figures are limited to; null for all seasons.")
    show_all_seasons: bool
    total_evaluations: int
    unique_players: int
    unique_evaluators: int
    rows: list[QuestionStatOut]


class PlayerRefOut(Schema):
    id: uuid.UUID
    first_name: str
    last_name: str


class MatrixFieldOut(Schema):
    key: str
    label: str
    section: str
    field_type: str


class MatrixRowOut(Schema):
    player: PlayerRefOut
    evaluation_id: uuid.UUID = Field(description="The player's latest evaluation on this checklist.")
    evaluated_at: datetime.datetime | None
    values: list[Any] = Field(description="Latest answer per column of `fields`, same order; null where never answered.")


class MatrixOut(Schema):
    fields: list[MatrixFieldOut]
    rows: list[MatrixRowOut]


class SessionOut(Schema):
    id: uuid.UUID
    checklist_id: uuid.UUID
    started_at: datetime.datetime
    ended_at: datetime.datetime | None
    is_open: bool


class OutcomeOut(Schema):
    player: PlayerRefOut
    decision: str
    modified: datetime.datetime


class MembershipRefOut(Schema):
    team_id: uuid.UUID
    team_name: str
    position: str | None = None
    jersey_number: int | None = None


class AttendanceBarOut(Schema):
    event_id: uuid.UUID
    start: datetime.datetime
    state: Literal["present", "absent", "upcoming"]


class AttendanceCountsOut(Schema):
    present: int
    absent: int
    no_reply: int
    no_shows: int


class HistoryEntryOut(Schema):
    evaluation_id: uuid.UUID
    date: datetime.datetime | None
    evaluator: str | None
    value: Any


class TrendPointOut(Schema):
    date: str
    value: float


class HistoryQuestionOut(Schema):
    key: str
    label: str
    section: str
    kind: Literal["numeric", "distribution", "text"]
    entries: list[HistoryEntryOut] = Field(description="Every answer given, newest first.")
    trend: list[TrendPointOut] | None = Field(None, description="`numeric` only; chronological.")


class NoteOut(Schema):
    id: uuid.UUID
    note: str
    author: str | None
    created: datetime.datetime


class WalkthroughPlayerOut(Schema):
    player: PlayerRefOut
    previous_player_id: uuid.UUID | None
    next_player_id: uuid.UUID | None
    season_id: uuid.UUID | None
    memberships: list[MembershipRefOut]
    attendance_bars: list[AttendanceBarOut]
    attendance_counts: AttendanceCountsOut | None
    evaluations: list[uuid.UUID] = Field(description="Ids of the player's evaluations on this checklist, newest first (`GET /manage/evaluations/entries/{id}`).")
    questions: list[HistoryQuestionOut]
    notes: list[NoteOut]
    outcome: OutcomeOut | None = Field(description="This player's decision in the open meeting, if one is running and one was recorded.")


class WalkthroughOut(Schema):
    players: list[PlayerRefOut] = Field(description="Everyone ever evaluated on this checklist (narrowed by `since`), by first name.")
    since: datetime.date | None
    session: SessionOut | None = Field(description="The open meeting, if any.")
    latest_session: SessionOut | None = Field(description="The open meeting, else the most recent ended one; its outcomes PDF is `GET /manage/evaluations/sessions/{id}/outcomes.pdf`.")
    current: WalkthroughPlayerOut | None = Field(description="The player selected with `?player=` (default: the first); null when nobody was evaluated yet.")


class OutcomeIn(Schema):
    decision: str = Field(max_length=5000, description="What the meeting decided; must not be blank.")


class NoteIn(Schema):
    note: str = Field(max_length=5000, description="Must not be blank.")


class EntryOut(EvaluationDetailOut):
    player: PlayerRefOut
