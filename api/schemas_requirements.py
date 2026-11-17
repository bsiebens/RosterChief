import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from ninja import Field, Schema

Kind = Literal["document", "question"]
QuestionType = Literal["text", "textarea", "number", "date", "choice", "multichoice", "yes_no"]


class RequirementOut(Schema):
    id: UUID
    name: str
    description: str
    #: ``document`` (received, optionally with a file) or ``question`` (answered with a value)
    kind: Kind = "document"
    #: Questions only: the type of answer, the choices (strings or ``{value, label}``) and a number's range.
    question_type: str = ""
    options: list[Any] | None = None
    min_value: Decimal | None = None
    max_value: Decimal | None = None
    requires_document: bool
    #: Staff must send the member a file before the member can hand anything in.
    needs_staff_file: bool = False
    #: What the person completing it has to do, shown to the member and guardians.
    member_instructions: str = ""
    blocked_event_kinds: list[str]
    is_active: bool
    valid_for_months: int | None = None
    reminder_days_before: int = 30


class RequirementListItem(RequirementOut):
    #: How many memberships have a status row for it (what deleting would remove).
    usage_count: int = 0


class RequirementIn(Schema):
    name: str
    description: str = ""
    kind: Kind = "document"
    question_type: QuestionType | Literal[""] = ""
    options: list[Any] | None = None
    min_value: Decimal | None = None
    max_value: Decimal | None = None
    requires_document: bool = False
    needs_staff_file: bool = False
    member_instructions: str = ""
    blocked_event_kinds: list[str] = Field(default_factory=list)
    is_active: bool = True
    valid_for_months: int | None = None
    reminder_days_before: int = 30


class RequirementPatch(Schema):
    name: str | None = None
    description: str | None = None
    kind: Kind | None = None
    question_type: QuestionType | Literal[""] | None = None
    options: list[Any] | None = None
    min_value: Decimal | None = None
    max_value: Decimal | None = None
    requires_document: bool | None = None
    needs_staff_file: bool | None = None
    member_instructions: str | None = None
    blocked_event_kinds: list[str] | None = None
    is_active: bool | None = None
    valid_for_months: int | None = None
    reminder_days_before: int | None = None


class EventOut(Schema):
    """One step in a requirement's history."""

    id: UUID
    kind: Literal["submitted", "approved", "declined", "requested", "reopened", "bypassed", "sent_file", "completed"]
    actor_name: str = ""
    note: str = ""
    created: datetime.datetime


class ItemOut(Schema):
    requirement: RequirementOut
    #: open / complete / bypassed (an expired completion is open again)
    state: Literal["open", "complete", "bypassed"]
    #: missing / pending (handed in by the member, waiting for approval) / declined (member has to hand in a new version) / requested (staff asked
    #: for something) / complete / expiring_soon / expired / bypassed
    display: Literal["missing", "pending", "declined", "requested", "complete", "expiring_soon", "expired", "bypassed"]
    #: The latest reason (declined) or message (requested) the member sees.
    review_note: str = ""
    #: A personalised file the club sent this member (download through ``.../sent-file``), with the note that came with it.
    has_sent_file: bool = False
    #: Open, and the club still has to send its document first (the member cannot hand in until then).
    awaiting_file: bool = False
    sent_at: datetime.datetime | None = None
    sent_note: str = ""
    submitted_at: datetime.datetime | None = None
    #: Everything that happened to this requirement, oldest first.
    history: list[EventOut] = Field(default_factory=list)
    note: str = ""
    completed_at: datetime.datetime | None = None
    completed_by: str | None = None
    has_document: bool = False
    #: A question's current answer (text, a number as a string, an ISO date, a list, a boolean), null while open.
    answer: Any = None
    valid_until: datetime.date | None = None
    days_left: int | None = None

    @staticmethod
    def resolve_note(obj):
        return obj.status.note if obj.status else ""

    @staticmethod
    def resolve_completed_at(obj):
        return obj.status.completed_at if obj.status else None

    @staticmethod
    def resolve_completed_by(obj):
        user = obj.status.completed_by if obj.status else None
        return (user.get_full_name() or user.email) if user else None

    @staticmethod
    def resolve_has_document(obj):
        return bool(obj.status and obj.status.document)

    @staticmethod
    def resolve_review_note(obj):
        return obj.status.review_note if obj.status else ""

    @staticmethod
    def resolve_has_sent_file(obj):
        return bool(obj.status and obj.status.sent_file)

    @staticmethod
    def resolve_sent_at(obj):
        return obj.status.sent_at if obj.status else None

    @staticmethod
    def resolve_sent_note(obj):
        return obj.status.sent_note if obj.status else ""

    @staticmethod
    def resolve_submitted_at(obj):
        return obj.status.submitted_at if obj.status else None

    @staticmethod
    def resolve_history(obj):
        return list(obj.status.events.all()) if obj.status else []


class SeasonRef(Schema):
    id: UUID
    name: str


class MembershipRef(Schema):
    id: UUID
    season: SeasonRef
    status: str
    fee_status: str


class ChecklistOut(Schema):
    """A member's documents for one season. ``membership`` is null when they have none that season (then ``items`` is empty)."""

    membership: MembershipRef | None = None
    items: list[ItemOut]
    total: int
    done: int
    open: int
    expiring: int
    expired: int


class BypassIn(Schema):
    note: str


class ReasonIn(Schema):
    """Decline: ``note`` is the reason the member sees (required)."""

    note: str = ""


class AnswerIn(Schema):
    """The answer in the question's own shape (a string, a number, an ISO date, a list of option values or a boolean)."""

    answer: Any = None
    note: str = ""
    valid_until: datetime.date | None = None
