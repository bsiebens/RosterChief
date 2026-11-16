import datetime
import uuid

from ninja import Schema


class PublicFormOption(Schema):
    value: str
    label: str


class PublicFormField(Schema):
    id: uuid.UUID
    key: str
    label: str
    type: str
    required: bool
    help_text: str
    section: str
    options: list[PublicFormOption]


class PublicFormState(Schema):
    #: ``open``, ``closed``, ``not_open_yet`` or ``inactive``; only ``open`` accepts a response.
    status: str
    opens_at: datetime.datetime | None = None
    closes_at: datetime.datetime | None = None


class PublicFormOut(Schema):
    title: str
    description: str
    state: PublicFormState
    fields: list[PublicFormField]
    max_file_mb: int
    allowed_file_extensions: list[str]


class PublicFormSubmittedOut(Schema):
    submitted: bool
