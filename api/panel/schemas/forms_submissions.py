"""Schemas of the management form-responses slice (api/panel/routers/forms_submissions.py).

Timestamps are ISO 8601, ids UUIDs (API.md §6). An answer ``value`` is whatever the field stored: a
string, a number (decimal string), a list of strings (multichoice), a boolean (checkbox) or, for a
file field, the upload's metadata ``{name, size, content_type}`` -- never a storage path.
"""

import datetime
import uuid
from typing import Any

from ninja import Field, Schema


class PersonRef(Schema):
    id: uuid.UUID
    name: str


class AnswerOut(Schema):
    id: uuid.UUID
    field_id: uuid.UUID
    key: str
    label: str
    field_type: str
    value: Any = Field(description="The stored answer; file fields hold `{name, size, content_type}`.")
    display: str = Field(description="The answer as plain text (a file answer is its original file name, a list is comma-separated).")
    file_url: str | None = Field(description="Path of the authenticated download for a file answer; null otherwise.")


class SubmissionOut(Schema):
    id: uuid.UUID
    send_id: uuid.UUID
    member: PersonRef | None = Field(description="Who the response is for; null for an anonymous public-link response.")
    submitted_by: PersonRef | None = Field(description="Who filled it in when that is someone else than `member` (a parent); otherwise null.")
    submitted_at: datetime.datetime
    values: dict[str, Any] = Field(description="Field id -> stored answer value, for the fields that were answered.")


class SubmissionPage(Schema):
    items: list[SubmissionOut]
    next_cursor: str | None


class SubmissionDetailOut(Schema):
    id: uuid.UUID
    send_id: uuid.UUID
    form_id: uuid.UUID
    member: PersonRef | None
    submitted_by: PersonRef | None
    submitted_at: datetime.datetime
    answers: list[AnswerOut] = Field(description="In the form's field order.")


class ColumnOut(Schema):
    field_id: uuid.UUID
    key: str
    label: str
    field_type: str
    section: str
    is_active: bool
    summary: dict[str, int] | None = Field(description="For choice, multichoice and checkbox fields: answer -> how many gave it; otherwise null.")


class ReportOut(Schema):
    send_id: uuid.UUID
    form_id: uuid.UUID
    title: str
    submission_count: int
    audience_count: int = Field(description="Members the send is addressed to.")
    not_yet_count: int = Field(description="`audience_count` minus `submission_count`, never below 0.")
    columns: list[ColumnOut]


class PendingMemberOut(Schema):
    id: uuid.UUID
    name: str


class PendingOut(Schema):
    items: list[PendingMemberOut]
    total: int
