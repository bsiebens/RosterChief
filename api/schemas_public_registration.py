import datetime
import uuid
from decimal import Decimal
from typing import Any

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
    #: The variant's id; the product's own id for a product without variants (bought "as is": one option named like the product).
    id: uuid.UUID
    #: What the form sends back as ``option_id``: ``variant:<uuid>`` or ``product:<uuid>``.
    option_id: str = ""
    name: str
    #: The variant's own price, else the product's.
    price: Decimal
    #: The team registrants for this option are placed in (the variant's, else the product's); ``null``: the registrant may ask for a team. When set the
    #: team is fixed: the server uses it whatever the form sends.
    team_id: uuid.UUID | None = None
    team_name: str | None = None


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
    #: What they register for: ``option_id`` (``variant:<uuid>`` or ``product:<uuid>``, as listed by GET /public/registration), or the old ``variant_id``.
    option_id: str = ""
    variant_id: uuid.UUID | None = None
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
    #: Negative for a credit line (a negative-priced product).
    base_price: Decimal
    discount: Decimal
    #: What this person pays (credits and registrant discount already out; 0 for a credit line). The prices add up to ``total``.
    price: Decimal
    is_credit: bool = False
    #: This line's share of the registration's credits (already out of ``price``).
    credit_share: Decimal = Decimal("0.00")
    early_bird_discount: Decimal
    early_bird_deadline: datetime.date | None = None


class PreviewOut(Schema):
    season: Ref
    lines: list[PreviewLine]
    total: Decimal
    #: The credits (negative-priced products) in this registration, as a positive amount, and how much of it came off the other lines. Never more
    #: than the others cost, so ``total`` is never below 0.
    credit_total: Decimal = Decimal("0.00")
    credit_applied: Decimal = Decimal("0.00")
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
    #: document / question
    kind: str = "document"
    requires_document: bool
    #: open / complete / bypassed
    state: str
    #: missing / complete / expiring_soon / expired / bypassed
    display: str
    valid_until: datetime.date | None = None
    has_document: bool
    can_upload: bool
    #: Questions: the type (text / textarea / number / date / choice / multichoice / yes_no), the choices (strings or ``{value, label}``), a number's range,
    #: the current answer and whether the registrant may (still) answer or change it.
    question_type: str = ""
    options: list[Any] | None = None
    min_value: Decimal | None = None
    max_value: Decimal | None = None
    answer: Any = None
    can_answer: bool = False


class PersonStatus(Schema):
    member_id: uuid.UUID
    name: str
    entry_kind: str
    description: str
    requested_team: str | None = None
    membership_status: str
    #: A line of a negative-priced product (a credit): not charged, it is taken off the registration's other people.
    is_credit: bool = False
    #: A credit line: the amount it credits (positive); another person: the share of the credits already taken off their fee. Only once the invoice
    #: is confirmed, like ``fee``.
    credit: Decimal | None = None
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


class NumberPoolOut(Schema):
    name: str
    min: int
    max: int


class NumbersOut(Schema):
    """The jersey numbers a new player could wear on a team. ``pool`` is ``null`` for a team without a number pool: then ``numbers`` is ``null``
    too (any number, 0 to 999) and ``taken`` lists the numbers its roster holds. No names, ever."""

    pool: NumberPoolOut | None = None
    numbers: list[int] | None = None
    taken: list[int]


class AnswerIn(Schema):
    """The registrant's answer to a question, in the question's own shape (string, number, ISO date, list of option values or boolean);
    ``member_id`` says who it is for (optional when the registration has one person)."""

    answer: Any = None
    member_id: str = ""
