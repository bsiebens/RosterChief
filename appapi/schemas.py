"""Request/response schemas of the app API. Conventions (API.md §6): snake_case, UUID
strings, ISO-8601 timestamps with offset, ``YYYY-MM-DD`` dates, enums as stored values
(clients localise labels and must tolerate unknown values and unknown fields)."""

import datetime
import uuid
from typing import Literal

from ninja import Field, Schema

from events.timeline_schemas import TimelineOut


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


class AccountSettingsOut(Schema):
    email_notifications: Literal["always", "unless_app"] = Field(
        description="`always`: notifications are also emailed. `unless_app`: not emailed while this account has the app installed (mails with an attachment, such as an invoice, are always sent)."
    )


class AccountSettingsIn(Schema):
    email_notifications: Literal["always", "unless_app"]


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
    settings: AccountSettingsOut


class ClubFeatures(Schema):
    shop: bool = Field(description="The shop is licensed for this club.")
    shop_open: bool = Field(description="The shop is licensed *and* currently taking orders.")
    forms: bool
    officials: bool
    evaluations: bool
    game_timeline: bool = Field(description="Games carry a timeline (goals, penalties, ...) on `GET /events/{id}`.")


class ClubOut(Schema):
    id: uuid.UUID
    slug: str
    name: str
    api_base_url: str = Field(description="Base URL for this club's resource endpoints, e.g. `https://hc-example.rosterchief.app/api/app/v1`.")
    public_api_base_url: str = Field(
        description="Base URL of the club's public, read-only API (no token): news, teams and rosters, games with timelines, league standings, sponsors -- e.g."
        " `https://hc-example.rosterchief.app/api/v1`. Its reference is at `<public_api_base_url>/docs`."
    )
    logo_url: str | None
    event_background_url: str | None = Field(
        description="The club's photo for event screens (the next-event card on Home, an event's page), as the web app shows it: in black & white, cropped to fill, under a dark"
        " gradient (`#0b1220` from about 45% at the top to 97% at the bottom) so white text reads. Null: use a plain dark background."
    )
    primary_color: str = Field(description="Hex, e.g. `#1e40af`: headers and the navigation bar. The club's own colour, or the app default when it set none.")
    primary_content_color: str = Field(description="Hex: text and icons on `primary_color` -- the club's own text colour when it set one, else `#000000` or `#ffffff`, whichever reads best.")
    secondary_color: str = Field(description="Hex: buttons, active tabs and highlights. The club's own colour, or the app default.")
    secondary_content_color: str = Field(description="Hex: text on `secondary_color` -- the club's own text colour when it set one, else `#000000` or `#ffffff`.")
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
    event_background_url: str | None = Field(
        description="The club's photo for event screens (the next-event card on Home, an event's page), as the web app shows it: in black & white, cropped to fill, under a dark"
        " gradient (`#0b1220` from about 45% at the top to 97% at the bottom) so white text reads. Null: use a plain dark background."
    )
    primary_color: str = Field(description="As in `/clubs`: never null.")
    primary_content_color: str
    secondary_color: str
    secondary_content_color: str


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
    game_status: str | None = Field(description="For games: `scheduled`, `live`, `finished` or `cancelled`. Null for other kinds. Ignore unknown values.")


class AttendanceOut(Schema):
    member_id: uuid.UUID
    status: str = Field(description="`no_response`, `present`, `absent`, `maybe`, `selected`, `not_selected`, `excused`.")
    note: str = Field(description="The reason given with `absent`/`maybe`. Only ever returned for the account's own people.")


class SignupOut(Schema):
    id: uuid.UUID
    member_id: uuid.UUID
    status: str = Field(description="`invited`, `accepted` or `declined`.")


class AuthorOut(Schema):
    first_name: str
    last_name: str


class NewsSummaryOut(Schema):
    id: uuid.UUID
    slug: str
    title: str
    author: AuthorOut | None = Field(description="Who wrote it; null when unknown (e.g. the author's record was removed).")
    excerpt: str = Field(description="Plain text, a few dozen words.")
    published_at: datetime.datetime
    image_url: str | None
    teams: list[TeamRefOut]


# --- home ------------------------------------------------------------------------------


class HeroOut(Schema):
    event: EventOut
    attendance: AttendanceOut
    rsvp_closed: bool


class CardMemberOut(Schema):
    member_id: uuid.UUID
    attendance: AttendanceOut


class MemberCardOut(Schema):
    kind: str = Field(description="`rsvp_needed` (answer with POST /events/{id}/rsvp; the deadline has not passed) or `next_going` (someone's next event they are going to). Ignore unknown kinds.")
    event: EventOut
    members: list[CardMemberOut] = Field(description="Everyone this card is for: people whose next event of this kind is the same one share a card. Never empty.")


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
    member_cards: list[MemberCardOut] = Field(
        description=(
            "At least one card for every person who has something: all `rsvp_needed` cards first, then `next_going`, each soonest first. "
            "Replaces `rsvp_needed` and `next_going`, which stay for older app versions. Open asks shown here are left out of `needs_answer`."
        )
    )
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
    status: str | None = Field(description="Attendance status for `rsvp`, sign-up status (`invited`, `accepted`, `declined`) for `referee`/`official`, null for `blocked`.")
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
    game_timeline: TimelineOut | None = Field(None, description="For a game, when the club has game timelines: goals, penalties, goalie changes and time-outs as the federation reported them, with player names.")


class GameFollowTeamOut(Schema):
    id: uuid.UUID
    name: str
    short_name: str
    following: bool


class GameFollowsOut(Schema):
    teams: list[GameFollowTeamOut] = Field(description="Every team of the club, with whether this account follows it for live game pushes.")


class GameFollowsIn(Schema):
    team_ids: list[uuid.UUID] = Field(description="The full set of teams to follow -- replaces the current one; ids of other clubs' teams are ignored.")


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
    type: str = Field(description="`news`, `event`, `form_send`, `order` or `calendar`. Ignore unknown types.")
    id: uuid.UUID | None = Field(description="Null for `calendar`, which has no object: open the Calendar tab.")
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


# --- payments (phase 3) ----------------------------------------------------------------


class DuesLineOut(Schema):
    label: str = Field(description="What this line pays for (product and option, or the kind of registration), localised.")
    team: str | None
    price: Money
    discount: Money | None = Field(description="Multi-registrant discount on this line.")


class DuesMemberOut(Schema):
    member_id: uuid.UUID
    membership_id: uuid.UUID
    balance: Money
    lines: list[DuesLineOut]


class EarlyPaymentOut(Schema):
    deadline: datetime.date
    discount: Money
    total: Money = Field(description="What to pay by the deadline instead of `total_balance`.")


class PaymentItemOut(Schema):
    kind: str = Field(description="`registration` (one invoice covering several people) or `membership` (one person's dues).")
    registration_id: uuid.UUID | None = Field(description="For `registration`: the invoice is at `/payments/registrations/{registration_id}/invoice.pdf`.")
    due_date: datetime.date | None
    total_balance: Money
    members: list[DuesMemberOut]
    manual_discount: Money | None
    manual_discount_note: str
    early_payment: EarlyPaymentOut | None


class PaymentsOut(Schema):
    payment_instructions: str = Field(description="How to pay (bank account, reference), written by the club. Plain text. There's no in-app payment.")
    items: list[PaymentItemOut]


# --- forms -----------------------------------------------------------------------------


class FormRowOut(Schema):
    send_id: uuid.UUID
    title: str
    member_id: uuid.UUID
    is_open: bool
    closes_at: datetime.datetime | None
    submitted_at: datetime.datetime | None
    submission_id: uuid.UUID | None = Field(description="The latest response, readable with GET /forms/submissions/{id}.")


class FormListOut(Schema):
    items: list[FormRowOut]


class FormOptionOut(Schema):
    value: str
    label: str


class FormFieldOut(Schema):
    key: str = Field(description="The answer's key when submitting.")
    label: str
    type: str = Field(description="`text`, `textarea`, `number`, `email`, `date`, `choice`, `multichoice`, `checkbox`, `file`. Ignore a form with a type you don't know (open it on the website).")
    required: bool
    help_text: str
    section: str = Field(description="Heading to group fields under; empty for none.")
    options: list[FormOptionOut] = Field(description="For `choice` / `multichoice`.")


class FormAudienceOut(Schema):
    member_id: uuid.UUID
    submissions: int = Field(description="Responses already given for this person.")


class FormOut(Schema):
    send_id: uuid.UUID
    title: str
    description: str
    opens_at: datetime.datetime | None
    closes_at: datetime.datetime | None
    is_open: bool
    max_submissions_per_user: int | None
    addressed: list[FormAudienceOut] = Field(description="The managed people this form is for; submit as one of them.")
    fields: list[FormFieldOut]
    file_accept: str = Field(description="Accepted file types for `file` fields (extensions and MIME types).")
    max_file_bytes: int


class AnswerValueOut(Schema):
    key: str
    label: str
    type: str
    value: object | None = Field(description="As stored: string, number as string, `YYYY-MM-DD`, bool, list of strings, or for files `{name, size, content_type}`. Null when left blank.")


class SubmissionOut(Schema):
    id: uuid.UUID
    send_id: uuid.UUID
    title: str
    member_id: uuid.UUID
    submitted_at: datetime.datetime
    answers: list[AnswerValueOut]


# --- support & calendar feed -----------------------------------------------------------


class BugNoteOut(Schema):
    body: str
    created: datetime.datetime


class BugReportOut(Schema):
    id: uuid.UUID
    title: str
    description: str
    status: str = Field(description="`submitted`, `in_progress`, `fixed`, `wont_fix`.")
    created: datetime.datetime
    fixed_version: str | None = Field(description="Only once `fixed`.")
    notes: list[BugNoteOut] = Field(description="Replies from the RosterChief team.")


class BugReportListOut(Schema):
    items: list[BugReportOut]


class BugReportIn(Schema):
    title: str = Field(min_length=1, max_length=255)
    description: str = Field(min_length=1, max_length=10000)


class CalendarFeedStateOut(Schema):
    has_feed: bool = Field(description="A subscription link exists. It can't be shown again: create a new one to see it.")


class CalendarFeedOut(Schema):
    feed_url: str = Field(description="https URL of the .ics feed, for the family's events in every club. Treat it like a password.")
    webcal_url: str = Field(description="The same as `webcal://`: open it to subscribe in the Calendar app.")


# --- shop ------------------------------------------------------------------------------


class VariantOut(Schema):
    id: uuid.UUID
    name: str = Field(description="One free-text option, e.g. `M` or `Medium — Red`.")
    price: Money


class ProductOut(Schema):
    id: uuid.UUID
    slug: str
    name: str
    description: str
    image_url: str | None
    price: Money = Field(description="Base price; a variant may have its own.")
    category_id: uuid.UUID | None
    personalization_enabled: bool = Field(description="Offer a number and/or name to print (e.g. on a jersey).")
    variants: list[VariantOut] = Field(description="Active options; when not empty, pick one.")


class CategoryOut(Schema):
    id: uuid.UUID
    name: str


class CatalogOut(Schema):
    categories: list[CategoryOut] = Field(description="In display order; only categories with products.")
    items: list[ProductOut]


class CartProductOut(Schema):
    id: uuid.UUID
    slug: str
    name: str
    image_url: str | None


class CartItemOut(Schema):
    id: uuid.UUID
    product: CartProductOut
    variant: VariantOut | None
    quantity: int
    unit_price: Money = Field(description="Current price; the cart is re-priced on every read.")
    line_total: Money
    beneficiary_id: uuid.UUID | None = Field(description="The managed person it's for, if chosen.")
    personalization_number: str
    personalization_name: str
    unavailable: bool = Field(description="Can't be ordered any more (product withdrawn or option gone): remove it before checking out.")


class CartDiscountOut(Schema):
    code: str
    name: str
    amount: Money


class CartOut(Schema):
    items: list[CartItemOut]
    subtotal: Money
    discount: CartDiscountOut | None = Field(description="The `code` passed, when it's valid now.")
    discount_error: str | None = Field(description="Why the `code` passed can't be used.")
    total: Money
    has_unavailable: bool
    prices_changed: bool = Field(description="Some prices changed since they were added: tell the user.")
    max_quantity: int


class CartItemIn(Schema):
    product_id: uuid.UUID
    variant_id: uuid.UUID | None = None
    quantity: int = Field(1, ge=1, le=99)
    beneficiary_id: uuid.UUID | None = Field(None, description="Who it's for: one of the managed people. Leave empty when ordering for yourself only.")
    personalization_number: str = Field("", max_length=20)
    personalization_name: str = Field("", max_length=100)


class CartItemPatchIn(Schema):
    quantity: int = Field(ge=0, le=99, description="0 removes the line.")


class CheckoutIn(Schema):
    discount_code: str = Field("", max_length=50)


class OrderSummaryOut(Schema):
    id: uuid.UUID
    number: str
    created: datetime.datetime
    total: Money
    purchaser_id: uuid.UUID
    payment_status: str = Field(description="`pending`, `partially_paid`, `paid`, `refunded`.")
    status: str = Field(description="`not_ready`, `in_production`, `ready_for_pickup`, `completed`, `cancelled`.")


class OrderListOut(Schema):
    items: list[OrderSummaryOut]
    next_cursor: str | None


class OrderLineOut(Schema):
    product_name: str
    variant_name: str | None
    quantity: int
    unit_price: Money
    line_total: Money
    beneficiary_id: uuid.UUID | None
    personalization_number: str
    personalization_name: str


class OrderDiscountOut(Schema):
    description: str
    amount: Money


class OrderOut(OrderSummaryOut):
    lines: list[OrderLineOut]
    discounts: list[OrderDiscountOut]
    pickup_instructions: str
    has_invoice: bool = Field(description="The PDF is at `/shop/orders/{id}/invoice.pdf`.")


# --- re-registration -------------------------------------------------------------------


class RegistrationSeasonOut(SeasonOut):
    name: str = Field(description="Short label, e.g. `26-27`.")


class RegistrationVariantOut(Schema):
    id: uuid.UUID
    name: str
    price: Money


class RegistrationProductOut(Schema):
    id: uuid.UUID
    name: str
    registration_kind: str = Field(description="`player` or `volunteer`: only for entries of that kind. Empty: for either.")
    variants: list[RegistrationVariantOut]


class RegistrationTeamOut(TeamRefOut):
    jersey_numbers: list[int] | None = Field(description="Free jersey numbers for players of this team; null when the team has no number pool (no number step).")


class CurrentNumberOut(Schema):
    member_id: uuid.UUID
    team_id: uuid.UUID
    number: int = Field(description="The person's current number in this team's pool: offer 'keep #N' (it's valid even though it isn't in `jersey_numbers`).")


class RegistrationOptionsOut(Schema):
    seasons: list[RegistrationSeasonOut] = Field(description="Seasons open for registration. Empty: registration is closed.")
    season: RegistrationSeasonOut | None = Field(description="The season these options are for. Null when there are several: ask, then call again with `season_id`.")
    products: list[RegistrationProductOut]
    teams: list[RegistrationTeamOut]
    current_numbers: list[CurrentNumberOut]
    max_entries: int


class RegistrationEntryIn(Schema):
    member_id: uuid.UUID | None = Field(None, description="A managed person. Leave empty to register someone new to the club (then give their name and date of birth).")
    first_name: str = Field("", max_length=150)
    last_name: str = Field("", max_length=150)
    date_of_birth: datetime.date | None = None
    entry_kind: Literal["player", "volunteer"] = "player"
    variant_id: uuid.UUID = Field(description="What they register for (a variant from `products`).")
    team_id: uuid.UUID | None = Field(None, description="Requested team (optional).")
    jersey_number: int | None = Field(None, description="Players of a team with a number pool only.")


class RegistrationIn(Schema):
    season_id: uuid.UUID
    entries: list[RegistrationEntryIn] = Field(min_length=1, description="One per registration; the same person may appear more than once (e.g. two teams, or player and volunteer).")


class QuoteLineOut(Schema):
    member_id: uuid.UUID | None
    first_name: str
    last_name: str
    entry_kind: str
    product_name: str
    variant_name: str
    team_id: uuid.UUID | None
    jersey_number: int | None
    price: Money
    multi_registrant_discount: Money
    early_payment_deadline: datetime.date | None
    early_payment_discount: Money


class QuoteOut(Schema):
    lines: list[QuoteLineOut]
    total: Money = Field(description="What will be invoiced.")
    early_payment_total: Money = Field(description="The total when every line with an early-payment deadline is paid in time.")


class RegistrationOut(Schema):
    registration_id: uuid.UUID
    total: Money
    status: str = Field(description="`received`: the club reviews it and then sends the invoice (it appears in `/payments`).")
