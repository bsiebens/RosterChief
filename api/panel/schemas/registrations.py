"""Schemas of the management registrations slice (api/panel/routers/registrations.py).

Money is a decimal string, dates ``YYYY-MM-DD``, enums their stored values (API.md §6).
"""

import datetime
import uuid
from decimal import Decimal
from typing import Literal

from ninja import Field, Schema

EntryKindValue = Literal["player", "volunteer"]
RegistrationStatusValue = Literal["pending", "confirmed"]


class ContactOut(Schema):
    first_name: str
    last_name: str
    email: str
    phone: str
    member_id: uuid.UUID | None = Field(description="The member the submission resolved as the person filling in the form; null when that member was removed.")


class LineOut(Schema):
    id: uuid.UUID
    entry_kind: EntryKindValue
    membership_id: uuid.UUID
    member_id: uuid.UUID
    member_name: str
    membership_status: str
    fee_status: str
    product_variant_id: uuid.UUID | None
    product_name: str
    variant_name: str
    price: Decimal
    discount_amount: Decimal = Field(description="This line's share of the multi-registrant discount.")
    excluded_from_invoice: bool
    requested_team_id: uuid.UUID | None
    requested_team_name: str
    requested_jersey_number: int | None
    requested_position_id: uuid.UUID | None
    requested_position_name: str


class VariantOptionOut(Schema):
    id: uuid.UUID
    product_name: str
    name: str
    price: Decimal = Field(description="The effective price; the review UI may pre-fill a line's price with it.")


class ReviewOptionsOut(Schema):
    variants: list[VariantOptionOut]
    team_number_pools: dict[str, list[int]] = Field(description="`{team_id: [available jersey numbers]}` for teams with a number pool; a team without an entry has no number step.")


class RegistrationRowOut(Schema):
    id: uuid.UUID
    created: datetime.datetime
    season_id: uuid.UUID
    contact_name: str
    contact_email: str
    billed_to_member_id: uuid.UUID | None
    total: Decimal = Field(description="After exclusions and the manual discount, over the billable lines.")
    lines: list[LineOut]


class RegistrationQueueOut(Schema):
    items: list[RegistrationRowOut]
    total: int


class RegistrationOut(Schema):
    id: uuid.UUID
    status: RegistrationStatusValue = Field(description="`pending` until the invoice is confirmed, then `confirmed`.")
    created: datetime.datetime
    season_id: uuid.UUID
    season_name: str
    contact: ContactOut
    invoice_number: str
    invoice_sent_at: datetime.datetime | None
    invoice_due_date: datetime.date | None
    manual_discount_amount: Decimal
    manual_discount_note: str
    subtotal: Decimal = Field(description="Recomputed over the billable lines (not the figure stored at submission).")
    discount_amount: Decimal
    total: Decimal
    payment_status: str | None = Field(description="Localised label (Paid, Partially paid, Unpaid) of a confirmed registration; null while pending.")
    payment_status_code: Literal["paid", "partial", "unpaid"] | None = Field(description="Machine code of `payment_status` (stable across languages); null while pending.")
    all_paid: bool
    lines: list[LineOut] = Field(description="Pending: every line of a non-cancelled membership, excluded ones included (editable). Confirmed: the billed lines only.")
    options: ReviewOptionsOut | None = Field(description="What a pending line may be changed to; null once confirmed.")


class LineIn(Schema):
    """One line of the review. Only the fields sent change; the others keep their current value."""

    id: uuid.UUID
    product_variant_id: uuid.UUID | None = None
    price: Decimal | None = Field(default=None, description="May be negative (a credit).")
    excluded_from_invoice: bool | None = None
    requested_team_id: uuid.UUID | None = Field(default=None, description="Players with a pooled team may also send `requested_jersey_number`; volunteers `requested_position_id`.")
    requested_jersey_number: int | None = None
    requested_position_id: uuid.UUID | None = None


class ConfirmIn(Schema):
    lines: list[LineIn] = Field(default_factory=list, description="Lines not listed stay as they are.")
    manual_discount_amount: Decimal = Field(default=Decimal("0"), ge=0)
    manual_discount_note: str = Field(default="", max_length=255)
    due_in_days: int = Field(default=14, ge=1, le=365)


class ConfirmedOut(Schema):
    email_sent: bool = Field(description="Confirmation and team placement happen either way.")
    registration: RegistrationOut


class CancelledOut(Schema):
    cancelled: int = Field(description="How many people's registrations were cancelled.")
