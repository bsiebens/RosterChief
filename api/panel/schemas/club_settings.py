"""Schemas of the management club-settings routes (api/panel/routers/club_settings.py)."""

import uuid

from ninja import Field, Schema


class ClubSettingsOut(Schema):
    id: uuid.UUID
    slug: str = Field(description="The club's subdomain; read-only (only platform staff change it).")
    name: str
    legal_name: str
    legal_address: str
    legal_zip_code: str
    legal_city: str
    vat_id: str = Field(description="Read-only here.")
    payment_instructions: str
    contact_email: str
    website: str
    logo_url: str | None = Field(description="Absolute URL, null when no logo is set.")
    event_background_url: str | None
    primary_color: str = Field(description="`#rrggbb` or empty.")
    secondary_color: str
    primary_text_color: str
    secondary_text_color: str
    public_api_url: str = Field(description="Base URL of the club's public read-only API.")


class ClubSettingsPatch(Schema):
    """Send only the fields to change. Logo and event background are uploaded on their own endpoints."""

    name: str | None = Field(None, max_length=255)
    legal_name: str | None = Field(None, max_length=255)
    legal_address: str | None = Field(None, max_length=255)
    legal_zip_code: str | None = Field(None, max_length=255)
    legal_city: str | None = Field(None, max_length=255)
    payment_instructions: str | None = None
    contact_email: str | None = None
    website: str | None = None
    primary_color: str | None = Field(None, description="`#rrggbb`, or empty to fall back to the default.")
    secondary_color: str | None = None
    primary_text_color: str | None = None
    secondary_text_color: str | None = None


class PreviewOut(Schema):
    key: str
    label: str
    description: str
    subject: str | None = Field(None, description="Rendered subject line; email previews only.")
    text: str | None = Field(None, description="Plain-text body; email previews only.")


class PreviewListOut(Schema):
    items: list[PreviewOut]


class OnboardingRequirementIn(Schema):
    name: str = Field(max_length=100, description="Unique within the club.")
    description: str = ""
    requires_document: bool = False
    blocked_event_kinds: list[str] = Field(default_factory=list, description="Event kinds (`events.Event.EventKind` values) a member can't be invited to while this is open.")
    is_active: bool = True


class OnboardingRequirementPatch(Schema):
    name: str | None = Field(None, max_length=100)
    description: str | None = None
    requires_document: bool | None = None
    blocked_event_kinds: list[str] | None = None
    is_active: bool | None = None


class OnboardingRequirementOut(Schema):
    id: uuid.UUID
    name: str
    description: str
    requires_document: bool
    blocked_event_kinds: list[str]
    is_active: bool


class OnboardingRequirementListOut(Schema):
    items: list[OnboardingRequirementOut]
