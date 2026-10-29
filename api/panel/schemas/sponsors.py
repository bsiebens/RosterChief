"""Schemas of the management sponsors routes (api/panel/routers/sponsors.py)."""

import datetime
import uuid

from ninja import Field, Schema


class SponsorOut(Schema):
    id: uuid.UUID
    name: str
    url: str
    start_date: datetime.date
    end_date: datetime.date | None
    logo_url: str | None = Field(description="Absolute URL, null when none is set.")
    logo_width: int | None
    logo_height: int | None
    is_active: bool = Field(description="Started and not ended today; the same rule as the public sponsors strip.")


class SponsorListOut(Schema):
    items: list[SponsorOut]
    next_cursor: str | None = None
