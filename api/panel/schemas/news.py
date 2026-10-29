"""Schemas of the management news routes (``/manage/news``)."""

import datetime
import uuid
from typing import Literal

from ninja import Field, Schema

from api.protected.schemas import TeamRefOut


class NewsPhotoOut(Schema):
    id: uuid.UUID
    url: str
    is_main: bool
    focal_x: int
    focal_y: int


class NewsSummaryOut(Schema):
    id: uuid.UUID
    title: str
    status: Literal["draft", "pending_review", "published"]
    visibility: Literal["internal", "external", "both"]
    published_at: datetime.datetime | None
    is_scheduled: bool = Field(description="Published, but its publish date is still ahead: not live yet.")
    created: datetime.datetime
    created_by: str | None = Field(None, description="The author's name.")
    teams: list[TeamRefOut] = Field(description="Empty for club-wide news.")
    can_edit: bool = Field(description="Whether the caller may edit, delete or submit this item (broad while a draft, editors and admins once published).")


class NewsOut(NewsSummaryOut):
    title_en: str
    body: str = Field(description="Markdown.")
    body_en: str = Field(description="Markdown; empty means the main body is used.")
    body_html: str = Field(description="`body` rendered to sanitised HTML.")
    body_en_html: str = Field(description="`body_en` (or `body` when empty) rendered to sanitised HTML.")
    notified_at: datetime.datetime | None
    photos: list[NewsPhotoOut]
    can_publish: bool


class NewsListOut(Schema):
    items: list[NewsSummaryOut]
    next_cursor: str | None


class NewsCountsOut(Schema):
    all: int
    draft: int = Field(description="Drafts and items waiting for review.")
    scheduled: int
    published: int


class NewsIn(Schema):
    title: str
    body: str = Field(description="Markdown.")
    title_en: str = ""
    body_en: str = ""
    teams: list[uuid.UUID] = Field(default_factory=list, description="Empty for club-wide news.")
    visibility: Literal["internal", "external", "both"] = "internal"


class NewsPublishIn(Schema):
    published_at: datetime.datetime | None = Field(None, description="Omit to publish now; a future time schedules it.")
    notify_members: bool = Field(False, description="Email the audience when it goes live.")
