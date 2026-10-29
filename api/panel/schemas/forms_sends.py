"""Schemas of the management form-send routes (api/panel/routers/forms_sends.py).

Timestamps are ISO 8601, ids UUIDs (API.md §6)."""

import datetime
import uuid

from ninja import Field, Schema


class SendIn(Schema):
    team_ids: list[uuid.UUID] = Field([], description="Teams whose roster (in the send's season) is addressed. Non-admins: only teams they manage.")
    group_ids: list[uuid.UUID] = Field([], description="Groups whose members are addressed. Non-admins: only groups they belong to.")
    club_wide: bool = Field(False, description="Address every active club member of the season. Admins only; cannot be combined with teams or groups.")
    invited_member_ids: list[uuid.UUID] = []
    excluded_member_ids: list[uuid.UUID] = []
    opens_at: datetime.datetime | None = None
    closes_at: datetime.datetime | None = Field(None, description="The deadline; also named in the notification.")
    max_submissions_per_user: int | None = Field(None, ge=0)
    is_active: bool = True
    is_public: bool = Field(False, description="Anyone with the link may answer (only effective if the form does not require sign-in).")


class SendPatch(Schema):
    team_ids: list[uuid.UUID] | None = None
    group_ids: list[uuid.UUID] | None = None
    club_wide: bool | None = None
    invited_member_ids: list[uuid.UUID] | None = None
    excluded_member_ids: list[uuid.UUID] | None = None
    opens_at: datetime.datetime | None = None
    closes_at: datetime.datetime | None = None
    max_submissions_per_user: int | None = Field(None, ge=0)
    is_active: bool | None = Field(None, description="`false` pauses collection (the web's way to cancel a send); responses are kept.")
    is_public: bool | None = None


class SendOut(Schema):
    id: uuid.UUID
    form_id: uuid.UUID
    team_ids: list[uuid.UUID]
    group_ids: list[uuid.UUID]
    club_wide: bool
    invited_member_ids: list[uuid.UUID]
    excluded_member_ids: list[uuid.UUID]
    season_id: uuid.UUID | None
    opens_at: datetime.datetime | None
    closes_at: datetime.datetime | None
    max_submissions_per_user: int | None
    is_active: bool
    is_open: bool = Field(description="Accepting responses right now (active and inside its window).")
    is_public: bool
    public_token: str | None = Field(description="Token of the public link; null unless the send is public.")
    reminder_sent_at: datetime.datetime | None
    created_by: str | None = Field(description="Name of the member who sent it.")
    created: datetime.datetime
    submission_count: int


class SendDetailOut(SendOut):
    audience_count: int
    not_yet_count: int = Field(description="Audience members without a response.")


class SendListOut(Schema):
    items: list[SendOut] = Field(description="Newest first. A form has few sends, so not paginated.")


class AudienceMember(Schema):
    id: uuid.UUID
    name: str


class AudienceOut(Schema):
    count: int
    members: list[AudienceMember]
