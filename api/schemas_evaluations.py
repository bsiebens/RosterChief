import datetime
import uuid
from typing import Any

from ninja import Schema

from api.schemas_forms import FieldIn, FieldOut, MemberRef, NamedRef


class ChecklistItem(Schema):
    id: uuid.UUID
    name: str
    slug: str
    description: str
    is_active: bool
    criteria_count: int
    evaluation_count: int
    created: datetime.datetime


class ChecklistDetail(ChecklistItem):
    fields: list[FieldOut]


class ChecklistListOut(Schema):
    count: int
    results: list[ChecklistItem]


class ChecklistIn(Schema):
    name: str
    description: str = ""
    is_active: bool = True


class ChecklistPatch(Schema):
    name: str | None = None
    description: str | None = None
    is_active: bool | None = None


class CriteriaIn(Schema):
    fields: list[FieldIn]


class EvaluateIn(Schema):
    player_id: uuid.UUID
    season_id: uuid.UUID | None = None
    #: {criterion key: value}; a multichoice value is a list, a checkbox a boolean.
    answers: dict[str, Any] = {}
    #: Written for the player and their family; shown only when the evaluation is released.
    public_note: str = ""


class AnswerOut(Schema):
    field_id: uuid.UUID
    key: str
    label: str
    field_type: str
    section: str
    order: int
    is_active: bool
    options: list[Any] | None = None
    value: Any = None


class EvaluationOut(Schema):
    id: uuid.UUID
    checklist: NamedRef
    season: NamedRef
    player: MemberRef
    evaluator: MemberRef | None = None
    submitted_at: datetime.datetime
    answers: list[AnswerOut]
    public_note: str = ""
    #: "" (private), "notes" or "full".
    release: str = ""
    released_at: datetime.datetime | None = None


class ReleaseIn(Schema):
    release: str
    public_note: str | None = None


class ReleaseItemIn(Schema):
    evaluation_id: uuid.UUID
    release: str
    public_note: str | None = None


class ReleaseItemsIn(Schema):
    items: list[ReleaseItemIn]


class ReleaseAnswerOut(Schema):
    label: str
    field_type: str
    options: list[Any] | None = None
    value: Any = None
    visible: bool


class ReleaseRowOut(Schema):
    id: uuid.UUID
    player: MemberRef
    evaluator: MemberRef | None = None
    submitted_at: datetime.datetime
    release: str
    public_note: str
    answers: list[ReleaseAnswerOut]


class ReleasePreviewOut(Schema):
    checklist: NamedRef
    season: NamedRef | None = None
    results: list[ReleaseRowOut]


class PlayerEvaluationsOut(Schema):
    count: int
    results: list[EvaluationOut]


class StatRow(Schema):
    field: FieldOut
    kind: str
    response_count: int
    average: float | None = None
    minimum: float | None = None
    maximum: float | None = None
    distribution: list[tuple[str, int]] = []
    recent_answers: list[str] = []


class StatsOut(Schema):
    season: NamedRef | None = None
    evaluation_count: int
    rows: list[StatRow]


class MatrixRow(Schema):
    player: MemberRef
    evaluation_id: uuid.UUID
    evaluated_at: datetime.datetime
    values: dict[str, Any]


class MatrixOut(Schema):
    season: NamedRef | None = None
    fields: list[FieldOut]
    rows: list[MatrixRow]


class PlayerListOut(Schema):
    count: int
    results: list[MemberRef]


class EntryOut(Schema):
    evaluation_id: uuid.UUID
    evaluator: MemberRef | None = None
    at: datetime.datetime
    season: NamedRef
    value: Any = None


class TrendPoint(Schema):
    evaluation_id: uuid.UUID
    at: datetime.datetime
    value: float


class QuestionOut(Schema):
    field: FieldOut
    entries: list[EntryOut]
    trend: list[TrendPoint]


class NoteOut(Schema):
    id: uuid.UUID
    note: str
    author: MemberRef | None = None
    created: datetime.datetime


class NoteIn(Schema):
    note: str


class AttendanceBar(Schema):
    #: present / absent / excused / no_reply / upcoming
    state: str
    kind: str
    date: datetime.datetime
    title: str


class AttendanceCounts(Schema):
    present: int = 0
    absent: int = 0
    excused: int = 0
    no_reply: int = 0
    no_shows: int = 0
    total: int = 0
    rate: int | None = None


class AttendanceOut(AttendanceCounts):
    season: NamedRef | None = None
    games: AttendanceCounts
    practices: AttendanceCounts
    #: The most recent events, oldest first.
    recent: list[AttendanceBar] = []
    recent_limit: int = 40


class ReviewOut(Schema):
    player: MemberRef
    evaluations: list[EvaluationOut]
    questions: list[QuestionOut]
    notes: list[NoteOut]
    teams: list[NamedRef]
    attendance: AttendanceOut


class SessionOut(Schema):
    id: uuid.UUID
    started_at: datetime.datetime
    ended_at: datetime.datetime | None = None
    is_open: bool
    started_by: MemberRef | None = None


class OutcomeOut(Schema):
    player: MemberRef
    decision: str
    recorded_by: MemberRef | None = None
    modified: datetime.datetime


class MeetingOut(Schema):
    """``active`` is the open meeting; ``latest`` the newest one (the same while it is open). ``outcomes`` belong to ``latest``."""

    active: SessionOut | None = None
    latest: SessionOut | None = None
    outcomes: list[OutcomeOut]


class OutcomeIn(Schema):
    decision: str = ""


class SeasonOut(Schema):
    id: uuid.UUID
    name: str
    start_date: datetime.date
    end_date: datetime.date
    is_current: bool
