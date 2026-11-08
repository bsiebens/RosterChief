import datetime
import uuid

from ninja import Schema


class MemberRef(Schema):
    id: uuid.UUID
    name: str


class NamedRef(Schema):
    id: uuid.UUID
    name: str


class OptionObj(Schema):
    value: str
    label: str | None = None


class FieldOut(Schema):
    id: uuid.UUID
    key: str
    label: str
    field_type: str
    required: bool
    help_text: str
    section: str
    order: int
    is_active: bool
    options: list[str | OptionObj] | None = None


class FieldIn(Schema):
    id: uuid.UUID | None = None
    key: str | None = None
    label: str
    field_type: str
    required: bool = True
    help_text: str = ""
    section: str = ""
    is_active: bool = True
    options: list[str | OptionObj] | None = None


class FieldsIn(Schema):
    fields: list[FieldIn]


class FormListItem(Schema):
    id: uuid.UUID
    title: str
    slug: str
    description: str
    is_active: bool
    login_required: bool
    field_count: int
    send_count: int
    response_count: int
    created: datetime.datetime


class FormDetail(FormListItem):
    fields: list[FieldOut]


class FormListOut(Schema):
    count: int
    results: list[FormListItem]


class FormIn(Schema):
    title: str
    description: str = ""
    login_required: bool = False
    is_active: bool = True


class FormPatch(Schema):
    title: str | None = None
    description: str | None = None
    login_required: bool | None = None
    is_active: bool | None = None


class DuplicateIn(Schema):
    title: str | None = None


class SendListItem(Schema):
    id: uuid.UUID
    form_id: uuid.UUID
    form_title: str
    audience_label: str
    opens_at: datetime.datetime | None = None
    closes_at: datetime.datetime | None = None
    is_active: bool
    is_public: bool
    status: str
    audience_count: int
    response_count: int
    created: datetime.datetime
    created_by: MemberRef | None = None


class SendDetail(SendListItem):
    teams: list[NamedRef]
    groups: list[NamedRef]
    invited_members: list[MemberRef]
    excluded_members: list[MemberRef]
    club_wide: bool
    season: NamedRef | None = None
    max_submissions_per_user: int | None = None
    public_token: str
    public_url_path: str
    reminder_sent_at: datetime.datetime | None = None


class SendListOut(Schema):
    count: int
    results: list[SendListItem]


class SendIn(Schema):
    team_ids: list[uuid.UUID] = []
    group_ids: list[uuid.UUID] = []
    club_wide: bool = False
    invited_member_ids: list[uuid.UUID] = []
    excluded_member_ids: list[uuid.UUID] = []
    season_id: uuid.UUID | None = None
    opens_at: datetime.datetime | None = None
    closes_at: datetime.datetime | None = None
    max_submissions_per_user: int | None = None
    is_public: bool = False
    notify: bool = True


class SendPatch(Schema):
    team_ids: list[uuid.UUID] | None = None
    group_ids: list[uuid.UUID] | None = None
    club_wide: bool | None = None
    invited_member_ids: list[uuid.UUID] | None = None
    excluded_member_ids: list[uuid.UUID] | None = None
    season_id: uuid.UUID | None = None
    opens_at: datetime.datetime | None = None
    closes_at: datetime.datetime | None = None
    max_submissions_per_user: int | None = None
    is_active: bool | None = None
    is_public: bool | None = None
    notify: bool = False


class FileRef(Schema):
    answer_id: uuid.UUID
    name: str


class ReportRowOut(Schema):
    submission_id: uuid.UUID
    member: MemberRef | None = None
    submitted_by: MemberRef | None = None
    on_behalf: bool
    submitted_at: datetime.datetime
    values: dict[str, object]
    files: dict[str, FileRef]


class ReportOut(Schema):
    send: SendListItem
    columns: list[FieldOut]
    rows: list[ReportRowOut]
    summaries: dict[str, dict[str, int]]
    count: int
    not_responded: list[MemberRef]
