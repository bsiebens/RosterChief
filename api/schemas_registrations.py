import datetime
import uuid
from decimal import Decimal

from ninja import Field, Schema


class RegistrationSettingsOut(Schema):
    is_open: bool
    intro_text: str
    terms_text: str
    payment_instructions: str
    invoice_due_days: int


class RegistrationSettingsIn(Schema):
    is_open: bool | None = None
    intro_text: str | None = None
    terms_text: str | None = None
    payment_instructions: str | None = None
    invoice_due_days: int | None = None


class PersonRef(Schema):
    id: uuid.UUID
    name: str
    email: str = ""


class RegistrationItem(Schema):
    id: uuid.UUID
    #: awaiting (billing review) / confirmed / cancelled
    status: str
    contact: PersonRef | None = None
    season_name: str
    submitted_at: datetime.datetime
    people_count: int
    total: Decimal
    invoice_number: str | None = None
    confirmed_at: datetime.datetime | None = None


class RegistrationListOut(Schema):
    count: int
    awaiting_count: int
    results: list[RegistrationItem]


class LineOut(Schema):
    id: uuid.UUID
    member: PersonRef
    membership_id: uuid.UUID
    entry_kind: str
    description: str
    #: What was bought (``null`` if the product was deleted since): lines of one product share a per-registration discount.
    product_id: uuid.UUID | None = None
    product_name: str | None = None
    requested_team_id: uuid.UUID | None = None
    requested_team_name: str | None = None
    requested_position_name: str | None = None
    requested_jersey_number: int | None = None
    #: reserved | unavailable | assigned | dropped | null (see invoicing.number_state)
    number_state: str | None = None
    base_price: Decimal
    #: This line's own discount (a per-person registrant tier), already in ``price``. A per-registration discount is NOT here: it is its own entry in
    #: ``order_discounts`` (older registrations may still carry such a discount here).
    discount_amount: Decimal
    price_override: Decimal | None = None
    excluded: bool
    #: What the line shows as its price: the override, else base - ``discount_amount``; 0 when excluded. A credit line (negative base price): its
    #: negative amount. Credits and the per-registration discount are not part of it.
    price: Decimal
    #: A line of a negative-priced product: it is not charged, it takes ``credit_total`` off the other lines.
    is_credit: bool = False
    #: Internal shares that keep each person's fee right (already out of ``payable``): of the credits, and of the product's per-registration discount.
    #: Not shown on the line.
    credit_share: Decimal = Decimal("0.00")
    order_discount_share: Decimal = Decimal("0.00")
    #: What the person's membership costs before the manual discount: ``price`` less both shares, never below 0 (0 for a credit line).
    payable: Decimal = Decimal("0.00")
    fee_amount: Decimal
    early_payment_deadline: datetime.date | None = None
    early_payment_discount: Decimal


class OrderDiscountOut(Schema):
    """A per-registration (multi-registrant) discount, shown as its own line: one per product that has one."""

    product_id: uuid.UUID | None = None
    name: str
    #: Positive amount that comes off the registration.
    amount: Decimal
    people: int
    #: The tier's percentage when it is a percentage discount (e.g. 5), else null (a fixed amount).
    percent: Decimal | None = None


class RegistrationDetail(RegistrationItem):
    season_id: uuid.UUID
    lines: list[LineOut]
    #: The separate per-registration discount lines (positive amounts), and their sum.
    order_discounts: list[OrderDiscountOut] = []
    order_discount_total: Decimal = Decimal("0.00")
    #: What the registration costs before the manual discount: the person lines less the per-registration discounts and the credits (never below 0).
    subtotal: Decimal
    #: The credits of the registration (as a positive amount) and how much of that came off the other lines (the rest is unused).
    credit_total: Decimal = Decimal("0.00")
    credit_applied: Decimal = Decimal("0.00")
    manual_discount_amount: Decimal
    manual_discount_note: str
    invoice_due_date: datetime.date | None = None
    invoice_sent_at: datetime.datetime | None = None
    confirmation_sent_at: datetime.datetime | None = None
    cancelled_at: datetime.datetime | None = None
    #: Where the registrant's status page lives (for staff to copy / resend).
    status_path: str
    #: Right after confirming: the ids of the lines whose requested number could not be given (taken meanwhile); empty otherwise.
    numbers_dropped: list[uuid.UUID] = []


class LineEdit(Schema):
    id: uuid.UUID
    #: A number overrides the computed price; ``null`` goes back to it.
    price: Decimal | None = Field(None, max_digits=10, decimal_places=2)
    excluded: bool | None = None
    #: The team this person is placed in when the invoice is confirmed (a player; ``null`` for none). Only the keys present change.
    requested_team_id: uuid.UUID | None = None


class ReviewIn(Schema):
    lines: list[LineEdit] = []
    manual_discount_amount: Decimal | None = Field(None, max_digits=10, decimal_places=2)
    manual_discount_note: str | None = Field(None, max_length=255)


class ConfirmIn(Schema):
    #: Default: today + the settings' invoice due days.
    due_date: datetime.date | None = None
    #: Attach the invoice (PDF) to the confirmation mail. The contact is told the registration is confirmed either way.
    send: bool = True


class SentOut(Schema):
    sent: bool
