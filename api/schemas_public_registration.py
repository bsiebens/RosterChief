import datetime
import uuid
from decimal import Decimal

from ninja import Field, Schema


class ClubBrand(Schema):
    name: str
    logo_url: str | None = None
    primary_color: str | None = None
    secondary_color: str | None = None
    primary_content_color: str | None = None
    secondary_content_color: str | None = None


class Ref(Schema):
    id: uuid.UUID
    name: str


class VariantOffer(Schema):
    id: uuid.UUID
    name: str
    #: The variant's own price, else the product's.
    price: Decimal


class EarlyBirdOffer(Schema):
    discount_type: str
    discount_amount: Decimal
    deadline: datetime.date


class TierOffer(Schema):
    min_registrants: int
    discount_type: str
    discount_amount: Decimal


class ProductOffer(Schema):
    id: uuid.UUID
    name: str
    description: str
    season_id: uuid.UUID
    season_name: str
    #: "player" / "volunteer" when the product is in that system category (offered only for that kind), null when it is offered for both.
    entry_kind: str | None = None
    variants: list[VariantOffer]
    early_bird: EarlyBirdOffer | None = None
    registrant_discount_scope: str
    registrant_tiers: list[TierOffer]


class PublicRegistrationOut(Schema):
    club: ClubBrand
    #: False: show a "registration is closed" page.
    is_open: bool
    intro_text: str
    terms_text: str
    products: list[ProductOffer]
    teams: list[Ref]
    positions: list[Ref]
    max_entries: int


class ContactIn(Schema):
    first_name: str = Field(max_length=150)
    last_name: str = Field(max_length=150)
    email: str = Field(max_length=254)
    phone: str = Field("", max_length=50)


class EntryIn(Schema):
    #: ``self``: the contact registers themselves (name from the contact); ``child``: a person the contact is a parent of.
    who: str = "child"
    first_name: str = Field("", max_length=150)
    last_name: str = Field("", max_length=150)
    date_of_birth: datetime.date | None = None
    email: str = Field("", max_length=254)
    kind: str = "player"
    variant_id: uuid.UUID
    requested_team_id: uuid.UUID | None = None
    requested_position_id: uuid.UUID | None = None
    requested_jersey_number: int | None = None
    photo_public_consent: bool = False


class PreviewIn(Schema):
    entries: list[EntryIn]


class RegistrationIn(Schema):
    contact: ContactIn
    entries: list[EntryIn]
    #: Honeypot: real visitors leave it empty (hidden field named ``website``); anything in it is silently dropped.
    website: str = ""
    language: str = ""


class PreviewLine(Schema):
    index: int
    description: str
    base_price: Decimal
    discount: Decimal
    price: Decimal
    early_bird_discount: Decimal
    early_bird_deadline: datetime.date | None = None


class PreviewOut(Schema):
    season: Ref
    lines: list[PreviewLine]
    total: Decimal
    #: Total if everything with an early-bird discount is paid on time.
    total_if_early: Decimal
    early_bird_discount: Decimal


class SubmittedOut(Schema):
    #: The secret of the status page: ``/join/status/{token}``.
    token: str


class FeeOut(Schema):
    fee_amount: Decimal
    amount_paid: Decimal
    amount_due: Decimal
    remaining_balance: Decimal
    fee_status: str
    early_bird: dict | None = None


class ChecklistOut(Schema):
    requirement_id: uuid.UUID
    name: str
    description: str
    requires_document: bool
    #: open / complete / bypassed
    state: str
    #: missing / complete / expiring_soon / expired / bypassed
    display: str
    valid_until: datetime.date | None = None
    has_document: bool
    can_upload: bool


class PersonStatus(Schema):
    member_id: uuid.UUID
    name: str
    entry_kind: str
    description: str
    requested_team: str | None = None
    membership_status: str
    #: Only once the invoice is confirmed.
    fee: FeeOut | None = None
    checklist: list[ChecklistOut]


class StatusOut(Schema):
    #: awaiting (under review) / confirmed / cancelled
    status: str
    season_name: str
    submitted_at: datetime.datetime
    invoice_number: str | None = None
    invoice_due_date: datetime.date | None = None
    people: list[PersonStatus]
    club: ClubBrand
