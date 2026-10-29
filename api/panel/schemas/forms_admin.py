"""Schemas of the management form-definition routes (api/panel/routers/forms_admin.py)."""

import datetime
import uuid
from typing import Literal

from ninja import Field, Schema

FieldType = Literal["text", "textarea", "number", "email", "date", "choice", "multichoice", "checkbox", "file"]


class FormFieldOut(Schema):
    id: uuid.UUID
    key: str = Field(description="Slug of the label made when the question was created; answers and reports join on it. Never changes.")
    label: str
    field_type: FieldType
    required: bool
    help_text: str
    section: str = Field(description="Optional heading; consecutive questions sharing it are grouped. Blank = ungrouped.")
    order: int
    is_active: bool = Field(description="False = retired: no longer asked on new responses, but old answers keep it.")
    options: list[str] = Field(description="Choices of a `choice` / `multichoice` question (the option values); empty otherwise.")


class FormOut(Schema):
    id: uuid.UUID
    title: str
    slug: str
    description: str
    is_active: bool = Field(description="Whether the form can still be picked for a new send. Does not affect sends already out.")
    login_required: bool
    send_count: int = Field(description="Times the form was sent to an audience.")
    created: datetime.datetime


class FormDetailOut(FormOut):
    fields: list[FormFieldOut] = Field(description="Every question, retired ones included, in `order`.")


class FormListOut(Schema):
    items: list[FormOut] = Field(description="Ordered by title. A club has few forms, so not paginated.")


class FieldIn(Schema):
    label: str = Field(max_length=255)
    field_type: FieldType = "text"
    required: bool = True
    help_text: str = ""
    section: str = Field("", max_length=255)
    options: list[str] = Field([], description="One value per choice; at least one for `choice` / `multichoice`, ignored otherwise.")


class FieldPatch(Schema):
    label: str | None = Field(None, max_length=255)
    field_type: FieldType | None = None
    required: bool | None = None
    help_text: str | None = None
    section: str | None = Field(None, max_length=255)
    order: int | None = Field(None, ge=0, le=2147483647)
    options: list[str] | None = Field(None, description="Replaces the choices. Omit to leave them (and their labels) untouched.")
    is_active: bool | None = Field(None, description="`false` retires the question; `true` brings it back.")


class FormIn(Schema):
    title: str = Field(max_length=255)
    description: str = ""
    login_required: bool = False
    is_active: bool = True
    fields: list[FieldIn] = Field([], description="The first questions, in order. Two with the same label (case-insensitive) are a 422.")


class FormPatch(Schema):
    title: str | None = Field(None, max_length=255)
    description: str | None = None
    login_required: bool | None = None
    is_active: bool | None = Field(None, description="`false` archives the form (no new sends); `true` publishes it again.")


class FieldOrderIn(Schema):
    field_ids: list[uuid.UUID] = Field(description="Every question of the form exactly once, in the new order.")
