"""Schemas of the management opponents routes (``/manage/opponents``)."""

import uuid

from ninja import Field, Schema


class OpponentOut(Schema):
    id: uuid.UUID
    name: str
    logo_url: str | None = Field(description="Absolute URL of the crest, null when none is set.")
    alternative_names: list[str] = Field(description="Other spellings recorded by fixture imports; read-only.")


class OpponentListOut(Schema):
    items: list[OpponentOut]
