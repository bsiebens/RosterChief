"""Schemas for the club settings, the document previews and the referee / official levels."""

from typing import Literal
from uuid import UUID

from ninja import Schema


class ClubSettingsOut(Schema):
    name: str
    legal_name: str
    contact_email: str
    website: str
    legal_address: str
    legal_zip_code: str
    legal_city: str
    legal_country: str  # ISO 3166-1 alpha-2 code, "" when not set
    vat_id: str
    payment_instructions: str
    logo_url: str | None = None
    event_background_url: str | None = None
    primary_color: str
    secondary_color: str
    primary_text_color: str
    secondary_text_color: str


class ClubSettingsPatch(Schema):
    """Only the fields that are sent are changed; an empty string clears an optional field."""

    name: str | None = None
    legal_name: str | None = None
    contact_email: str | None = None
    website: str | None = None
    legal_address: str | None = None
    legal_zip_code: str | None = None
    legal_city: str | None = None
    legal_country: str | None = None
    vat_id: str | None = None
    payment_instructions: str | None = None
    primary_color: str | None = None
    secondary_color: str | None = None
    primary_text_color: str | None = None
    secondary_text_color: str | None = None


class PreviewOut(Schema):
    key: str
    kind: Literal["email", "pdf"]
    title: str
    description: str


class LevelOut(Schema):
    id: UUID
    name: str
    team_ids: list[UUID]
    inherits_from_id: UUID | None = None
    member_count: int
    inherited_by_count: int


class LevelIn(Schema):
    name: str
    team_ids: list[UUID] = []
    inherits_from_id: UUID | None = None
