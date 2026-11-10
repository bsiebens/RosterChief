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
    requested_team_id: uuid.UUID | None = None
    requested_team_name: str | None = None
    requested_position_name: str | None = None
    requested_jersey_number: int | None = None
    base_price: Decimal
    #: The registrant discount (tiers) already in ``price``.
    discount_amount: Decimal
    price_override: Decimal | None = None
    excluded: bool
    #: What the line costs before the credits and the manual discount: the override, else base - discount; 0 when excluded. A credit line (negative
    #: base price): its negative amount.
    price: Decimal
    #: A line of a negative-priced product: it is not charged, it takes ``credit_total`` off the other lines.
    is_credit: bool = False
    #: This line's share of the credits (already out of ``payable``).
    credit_share: Decimal = Decimal("0.00")
    #: What the person pays before the manual discount: ``price`` - ``credit_share``, never below 0 (0 for a credit line).
    payable: Decimal = Decimal("0.00")
    fee_amount: Decimal
    early_payment_deadline: datetime.date | None = None
    early_payment_discount: Decimal


class RegistrationDetail(RegistrationItem):
    season_id: uuid.UUID
    lines: list[LineOut]
    #: The lines less the credits, before the manual discount.
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
    #: Email the invoice (PDF) to the contact.
    send: bool = True


class SentOut(Schema):
    sent: bool
