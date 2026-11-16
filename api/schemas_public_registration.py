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
    installment_offsets: list[int] = []


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
    use_installments: bool = False


class PreviewLine(Schema):
    index: int
    description: str
    #: Negative for a credit line (a negative-priced product).
    base_price: Decimal
    #: This line's own discount (a per-person registrant tier). A per-registration discount is not here: see ``PreviewOut.order_discounts``.
    discount: Decimal
    #: What the line shows as its price: ``base_price`` less its own ``discount``; a credit line: its negative amount. The per-registration discount and
    #: the credits are separate lines, so the displayed prices do not add up to ``total`` on their own.
    price: Decimal
    #: What this person's membership will cost (their fee): never below 0, 0 for a credit line. These add up to ``total``.
    payable: Decimal = Decimal("0.00")
    is_credit: bool = False
    #: Internal shares behind ``payable``: of the credits and of the product's per-registration discount. Not meant to be shown on the line.
    credit_share: Decimal = Decimal("0.00")
    order_discount_share: Decimal = Decimal("0.00")
    early_bird_discount: Decimal
    early_bird_deadline: datetime.date | None = None


class OrderDiscount(Schema):
    """A per-registration (multi-registrant) discount, shown as its own line: one per product that has one."""

    description: str
    #: Positive amount that comes off the registration.
    amount: Decimal
    people: int
    #: The tier's percentage when it is a percentage discount (e.g. 5), else null (a fixed amount).
    percent: Decimal | None = None


class PreviewOut(Schema):
    season: Ref
    lines: list[PreviewLine]
    order_discounts: list[OrderDiscount] = []
    order_discount_total: Decimal = Decimal("0.00")
    total: Decimal
    #: The credits (negative-priced products) in this registration, as a positive amount, and how much of it came off the other lines. Never more
    #: than the others cost, so ``total`` is never below 0; the unused part is ``credit_total - credit_applied``.
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
    payment_allows_activation: bool
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


class InstallmentOut(Schema):
    due_date: datetime.date
    amount: Decimal
    remaining: Decimal
    late: bool


class PersonStatus(Schema):
    member_id: uuid.UUID
    name: str
    entry_kind: str
    description: str
    requested_team: str | None = None
    membership_status: str
    #: A line of a negative-priced product (a credit): not charged, it is taken off the registration's other people.
    is_credit: bool = False
    #: A credit line: the amount it credits (positive); other people: always 0 (the credit is its own line, see ``StatusOut.invoice``). Only once the
    #: invoice is confirmed, like ``fee``.
    credit: Decimal | None = None
    #: Only once the invoice is confirmed.
    fee: FeeOut | None = None
    next_installment: InstallmentOut | None = None
    checklist: list[ChecklistOut]


class InvoiceLine(Schema):
    #: person / order_discount / credit
    kind: str
    #: The person, or for ``order_discount`` the product it is for.
    name: str
    description: str = ""
    #: Negative for a discount or credit.
    amount: Decimal
    #: For ``order_discount`` with a percentage tier: the percentage (e.g. 5).
    percent: Decimal | None = None


class InvoiceSummary(Schema):
    """What the registration costs, once the invoice is confirmed: a line per person (own price), then a separate line per multi-registration discount,
    then the credit lines, a manual discount and the total."""

    lines: list[InvoiceLine]
    manual_discount_amount: Decimal = Decimal("0.00")
    manual_discount_note: str = ""
    #: Credit that could not be used (more credit than there was to take it off); shown as a note.
    credit_unused: Decimal = Decimal("0.00")
    total: Decimal


class EarlyBirdOut(Schema):
    """The early-bird discount, only while it still applies: pay before ``deadline`` and ``discount`` comes off. ``amount_due`` is the invoice total
    with the discount, ``balance`` what is still open."""

    deadline: datetime.date
    discount: Decimal
    amount_due: Decimal
    balance: Decimal


class StatusOut(Schema):
    #: awaiting (under review) / confirmed / cancelled
    status: str
    season_name: str
    submitted_at: datetime.datetime
    invoice_number: str | None = None
    invoice_due_date: datetime.date | None = None
    #: Only once the invoice is confirmed.
    invoice: InvoiceSummary | None = None
    early_bird: EarlyBirdOut | None = None
    installment_offsets: list[int] = []
    installments: list[InstallmentOut] = []
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
