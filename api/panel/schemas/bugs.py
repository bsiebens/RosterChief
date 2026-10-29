"""Schemas of the management bug reports (``/manage/bugs``, api/panel/routers/bugs.py)."""

import datetime
import uuid
from typing import Literal

from ninja import Field, Schema


class BugNoteOut(Schema):
    id: uuid.UUID
    body: str
    created: datetime.datetime


class BugOut(Schema):
    id: uuid.UUID
    title: str
    description: str
    status: Literal["submitted", "in_progress", "fixed", "wont_fix"]
    created: datetime.datetime
    fixed_at: datetime.datetime | None
    fixed_version: str
    notes: list[BugNoteOut] = Field(description="The platform team's notes, oldest first. Priority and reporter stay internal.")


class BugListOut(Schema):
    items: list[BugOut]
    next_cursor: str | None = None


class BugIn(Schema):
    title: str = Field(max_length=255)
    description: str
