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


# --- shared building blocks (phase 2) --------------------------------------------------

#: Every amount is in euro today; the currency is spelled out so a client never assumes.
CURRENCY = "EUR"


class Money(Schema):
    amount: str = Field(description="Decimal string with two places, e.g. `12.50`. Never a float.")
    currency: str = Field(description="ISO 4217, e.g. `EUR`.")


class LocationOut(Schema):
    name: str
    address: str
    city: str
    zip_code: str
    is_home: bool


class EventOut(Schema):
    id: uuid.UUID
    title: str
    kind: str = Field(description="`training`, `game`, `tournament`, `meeting`, `social`, `other`.")
    start: datetime.datetime
    end: datetime.datetime | None
    gathering: datetime.datetime | None = Field(description="When to be there, if earlier than the start.")
    deadline: datetime.datetime | None = Field(description="Reply deadline. After it, answers are closed (a line-up dropout still works).")
    cancelled: bool
    location: LocationOut | None
    opponent: str | None = Field(description="Opponent's name, for games.")
    is_home_game: bool
    teams: list[TeamRefOut]
    competition: str
    score_for: int | None
    score_against: int | None
    is_live: bool


class AttendanceOut(Schema):
    member_id: uuid.UUID
    status: str = Field(description="`no_response`, `present`, `absent`, `maybe`, `selected`, `not_selected`, `excused`.")
    note: str = Field(description="The reason given with `absent`/`maybe`. Only ever returned for the account's own people.")


class SignupOut(Schema):
    id: uuid.UUID
    member_id: uuid.UUID
    status: str = Field(description="`invited`, `accepted` (`declined` ones are left out).")


class NewsSummaryOut(Schema):
    id: uuid.UUID
    slug: str
    title: str
    excerpt: str = Field(description="Plain text, a few dozen words.")
    published_at: datetime.datetime
    image_url: str | None
    teams: list[TeamRefOut]


# --- home ------------------------------------------------------------------------------


class HeroOut(Schema):
    event: EventOut
    attendance: AttendanceOut
    rsvp_closed: bool


class NeedsAnswerOut(Schema):
    type: str = Field(description="`rsvp` (answer with POST /events/{id}/rsvp), `referee` or `official` (answer the sign-up).")
    event: EventOut
    member_id: uuid.UUID
    status: str
    signup_id: uuid.UUID | None = Field(description="For `referee`/`official`: the sign-up to respond to.")


class OpenTaskOut(Schema):
    id: uuid.UUID
    event_id: uuid.UUID
    event_title: str
    event_start: datetime.datetime
    title: str
    open_count: int = Field(description="Slots still free.")


class DuesOut(Schema):
    member_id: uuid.UUID
    membership_id: uuid.UUID
    season: SeasonOut
    balance: Money
    early_payment_deadline: datetime.date | None = Field(description="Pay by this date for the early-payment discount (then `early_payment_total` applies).")
    early_payment_total: Money | None


class FormToCompleteOut(Schema):
    send_id: uuid.UUID
    title: str
    member_id: uuid.UUID
    closes_at: datetime.datetime | None


class HomeOut(Schema):
    rsvp_needed: HeroOut | None = Field(description="The soonest invitation nobody has answered yet. Shown read-only when `rsvp_closed`.")
    next_going: HeroOut | None = Field(description="The soonest event someone is going to.")
    needs_answer: list[NeedsAnswerOut] = Field(description="Other open asks (excluding `rsvp_needed`), soonest first, at most 5.")
    needs_answer_more: int = Field(description="How many more there are beyond this list (see the calendar).")
    open_tasks: list[OpenTaskOut]
    open_tasks_more: int
    dues: list[DuesOut]
    news: list[NewsSummaryOut]
    forms_to_complete: list[FormToCompleteOut]


# --- calendar & events -----------------------------------------------------------------


class CalendarItemOut(Schema):
    type: str = Field(description="`rsvp`, `referee`, `official` or `blocked`. Ignore unknown types.")
    event: EventOut
    member_id: uuid.UUID
    status: str | None = Field(description="Attendance status for `rsvp`, sign-up status for `referee`/`official`, null for `blocked`.")
    signup_id: uuid.UUID | None
    blocked_requirements: list[str] = Field(description="For `blocked`: the club requirements (e.g. a medical form) that keep this person off the event.")


class CalendarOut(Schema):
    items: list[CalendarItemOut]


class LineupMemberOut(Schema):
    id: uuid.UUID
    first_name: str
    last_name: str


class LineupCategoryOut(Schema):
    label: str = Field(description="The roster position (localised), or 'No position set'.")
    members: list[LineupMemberOut]


class LineupOut(Schema):
    published_at: datetime.datetime
    categories: list[LineupCategoryOut]


class TeamSpotOut(Schema):
    team_id: uuid.UUID
    position: str | None
    jersey_number: int | None


class AnswerOut(AttendanceOut):
    can_drop_out: bool = Field(description="Selected in the published line-up: may still pull out with `dropout` (a reason is required).")
    team: TeamSpotOut | None


class BlockedOut(Schema):
    member_id: uuid.UUID
    requirements: list[str]


class TaskOut(Schema):
    id: uuid.UUID
    title: str
    description: str
    needed_quantity: int
    claimed_by: list[str] = Field(description="Who took a slot, as family labels (never which person).")
    is_full: bool
    claimed_by_me: bool = Field(description="Tasks are claimed by the account's own person, not per child.")


class SquadSummaryOut(Schema):
    total: int
    responded: int
    in_count: int
    out_count: int
    no_reply_count: int


class EventDetailOut(Schema):
    event: EventOut
    rsvp_closed: bool
    started: bool = Field(description="Answers can no longer change once the event has started.")
    lineup: LineupOut | None = Field(description="Published line-up. When present, `answers` are read-only except for `dropout`.")
    answers: list[AnswerOut] = Field(description="One per managed person invited to this event.")
    blocked: list[BlockedOut] = Field(description="Managed people kept off this event by open club requirements.")
    referee_signups: list[SignupOut]
    official_signups: list[SignupOut]
    tasks: list[TaskOut]
    squad_summary: SquadSummaryOut | None = Field(description="Counts only, never who answered what.")


class RsvpIn(Schema):
    member_id: uuid.UUID = Field(description="The managed person answering (see `/context`).")
    status: Literal["present", "absent", "maybe", "dropout"] = Field(description="`dropout`: pull out of a published line-up you were selected for.")
    note: str = Field("", max_length=500, description="Required for `absent` and `dropout`, optional for `maybe`, dropped for `present`.")


class SignupResponseIn(Schema):
    response: Literal["accept", "decline"]


# --- news & notifications --------------------------------------------------------------


class NewsPageOut(Schema):
    items: list[NewsSummaryOut]
    next_cursor: str | None


class NewsPhotoOut(Schema):
    url: str
    is_main: bool
    focal_x: int = Field(description="Focal point, 0-100 % from the left; crop around it.")
    focal_y: int


class NewsDetailOut(NewsSummaryOut):
    language: str = Field(description="`en` when the English text was served, `default` for the club's own language.")
    body_html: str = Field(description="Sanitised HTML (paragraphs, lists, links, tables, images).")
    photos: list[NewsPhotoOut]


class NotificationSourceOut(Schema):
    type: str = Field(description="`news`, `event` or `form_send`. Ignore unknown types.")
    id: uuid.UUID
    slug: str | None = Field(description="For `news`: open it with GET /news/{slug}.")


class NotificationOut(Schema):
    id: uuid.UUID
    member_id: uuid.UUID
    title: str
    body: str
    created: datetime.datetime
    read_at: datetime.datetime | None
    source: NotificationSourceOut | None


class NotificationPageOut(Schema):
    items: list[NotificationOut]
    next_cursor: str | None


class UnreadCountOut(Schema):
    unread: int


class CountOut(Schema):
    count: int = Field(description="How many notifications were changed.")


# --- people ----------------------------------------------------------------------------


class GuardianOut(Schema):
    first_name: str
    last_name: str
    role: str = Field(description="`parent`, `guardian`, `other`.")


class ProfileTeamOut(Schema):
    team: TeamRefOut
    position: str | None
    jersey_number: int | None


class ProfileOut(Schema):
    id: uuid.UUID
    first_name: str
    last_name: str
    date_of_birth: datetime.date | None
    email: str
    phone: str | None = Field(description="E.164, e.g. `+32470123456`.")
    emergency_phone: str | None
    photo_url: str | None = Field(description="GET it with the Bearer token; null without a photo.")
    photo_public_consent: bool = Field(description="The photo may appear on the club's public website.")
    guardians: list[GuardianOut]
    teams: list[ProfileTeamOut] = Field(description="Current season.")
    open_requirements: list[str] = Field(description="Club requirements still open this season (e.g. a medical form).")


class ProfilePatchIn(Schema):
    first_name: str | None = Field(None, min_length=1, max_length=150)
    last_name: str | None = Field(None, min_length=1, max_length=150)
    date_of_birth: datetime.date | None = None
    email: str | None = Field(None, max_length=254)
    phone: str | None = Field(None, max_length=32, description="Any format; stored as E.164. Empty string clears it.")
    emergency_phone: str | None = Field(None, max_length=32)
    photo_public_consent: bool | None = None
