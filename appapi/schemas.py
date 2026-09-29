"""Request/response schemas of the app API. Conventions (API.md §6): snake_case, UUID
strings, ISO-8601 timestamps with offset, ``YYYY-MM-DD`` dates, enums as stored values
(clients localise labels and must tolerate unknown values and unknown fields)."""

import datetime
import uuid
from typing import Literal

from ninja import Field, Schema


class ErrorDetail(Schema):
    code: str = Field(description="Stable machine-readable code, e.g. `not_a_member`. Never translated.")
    message: str = Field(description="Localised (Accept-Language), safe to show to the user.")
    fields: dict[str, list[str]] | None = Field(None, description="Validation messages per field; only on 422.")


class ErrorOut(Schema):
    error: ErrorDetail


# --- account plane ---------------------------------------------------------------------


class AppVersions(Schema):
    ios: str = Field(description="Oldest iOS app version still supported; below it, show the 'update required' screen.")
    android: str


class MetaOut(Schema):
    api_version: str
    server_time: datetime.datetime
    min_supported_app_version: AppVersions
    docs_url: str


class MfaState(Schema):
    enabled: bool = Field(description="The account has at least one second factor.")
    types: list[str] = Field(description="Enrolled factor types: `totp`, `recovery_codes`, `webauthn`.")


class PersonOut(Schema):
    id: uuid.UUID
    first_name: str
    last_name: str


class UserOut(Schema):
    id: uuid.UUID
    email: str


class MeOut(Schema):
    user: UserOut
    member: PersonOut | None = Field(description="The account's own person record, shared by every club it belongs to.")
    mfa: MfaState


class ClubFeatures(Schema):
    shop: bool = Field(description="The shop is licensed for this club.")
    shop_open: bool = Field(description="The shop is licensed *and* currently taking orders.")
    forms: bool
    officials: bool
    evaluations: bool


class ClubOut(Schema):
    id: uuid.UUID
    slug: str
    name: str
    api_base_url: str = Field(description="Base URL for this club's resource endpoints, e.g. `https://hc-example.rosterchief.app/api/app/v1`.")
    logo_url: str | None
    primary_color: str | None
    secondary_color: str | None
    relationship: list[str] = Field(
        description="How the account relates to the club: `member` (plays or is signed up), `guardian`"
        " (acts for children), `coach` (staffs a team this season), `staff` (club admin, editor or member admin). More values may appear: ignore unknown ones."
    )
    features: ClubFeatures


class ClubListOut(Schema):
    items: list[ClubOut]


class DeviceIn(Schema):
    installation_id: str = Field(min_length=8, max_length=64, description="A random id the app generates once per install (e.g. a UUID) and keeps in the Keychain.")
    platform: Literal["ios", "android"]
    environment: Literal["production", "sandbox"] = Field("production", description="`sandbox` for debug builds run from Xcode (APNs sandbox tokens).")
    push_token: str = Field(min_length=1, max_length=512, description="The APNs device token as a hex string (iOS) or the FCM registration token (Android).")
    app_version: str = Field("", max_length=32)
    locale: str = Field("", max_length=16, description="The app's language, e.g. `nl` or `en`; push texts are written in it.")


class DeviceOut(Schema):
    installation_id: str
    platform: str
    environment: str
    push_enabled: bool = Field(description="False until the server can deliver pushes to this device (SNS configured, token accepted).")


# --- club plane ------------------------------------------------------------------------


class ManagedPersonOut(PersonOut):
    date_of_birth: datetime.date | None
    is_self: bool = Field(description="True for the account's own record; the others are children the account is a parent/guardian of.")


class SeasonOut(Schema):
    id: uuid.UUID
    start_date: datetime.date
    end_date: datetime.date


class TeamRefOut(Schema):
    id: uuid.UUID
    name: str
    short_name: str


class PermissionsOut(Schema):
    has_management_access: bool = Field(description="Can use the club's web management pages. Management itself is not in the app API.")
    can_add_news: bool
    can_manage_evaluations: bool


class ClubRefOut(Schema):
    id: uuid.UUID
    slug: str
    name: str
    logo_url: str | None
    primary_color: str | None
    secondary_color: str | None


class ContextOut(Schema):
    club: ClubRefOut
    season: SeasonOut | None = Field(description="The club's current season, if one covers today.")
    me: PersonOut | None
    managed_people: list[ManagedPersonOut] = Field(description="Who the account acts for here: themselves first, then their children. Pass one of these ids as `member_id`.")
    staffed_teams: list[TeamRefOut] = Field(description="Teams the account coaches or staffs this season (coach endpoints take a team id from here).")
    permissions: PermissionsOut
    features: ClubFeatures
    unread_notifications: int = Field(description="Unread notifications across all managed people, for the app badge.")
