"""Schemas of the management season-fees slice (api/panel/routers/fees.py).

Money is a decimal string, dates ``YYYY-MM-DD``, enums their stored values (API.md §6).
"""

import datetime
import uuid
from decimal import Decimal
from typing import Literal

from ninja import Field, Schema

FeeStatusValue = Literal["unpaid", "paid", "partially_paid", "waived"]
PaymentMethodValue = Literal["bank_transfer", "cash", "card", "other"]
InvoiceKindValue = Literal["order", "dues", "registration"]


class FeeSummaryOut(Schema):
    season_id: uuid.UUID | None = Field(description="The current season the figures are for; null when the club has none.")
    total: int = Field(description="Member-kind, non-cancelled memberships: paid + partial + unpaid + waived.")
    paid: int
    partial: int
    unpaid: int
    waived: int
    paid_rate: int | None = Field(description="Percent of `total` paid or waived; null when `total` is 0.")
    overdue_invoices: int = Field(description="Dues and registration invoices past due and still owing: what send-invoice-reminders would cover.")


class MembershipIdsIn(Schema):
    membership_ids: list[uuid.UUID] = Field(min_length=1, description="Ids of other clubs' memberships are ignored.")


class MarkPaidOut(Schema):
    updated: int


class MembershipFeeOut(Schema):
    id: uuid.UUID
    member_id: uuid.UUID
    first_name: str
    last_name: str
    status: str
    fee_status: FeeStatusValue
    fee_amount: Decimal
    amount_paid: Decimal
    remaining_balance: Decimal = Field(description="What is still owed after any early-payment discount; never negative.")


class RecordPaymentIn(Schema):
    amount: Decimal = Field(description="At least 0.01; partial or in full.")
    method: PaymentMethodValue = "bank_transfer"
    reference: str = ""
    note: str = ""


class PaymentOut(Schema):
    id: uuid.UUID
    amount: Decimal
    method: PaymentMethodValue
    reference: str
    note: str
    paid_at: datetime.datetime


class RecordPaymentOut(Schema):
    payment: PaymentOut
    membership: MembershipFeeOut


class SendInvoicesIn(MembershipIdsIn):
    due_in_days: int = Field(14, ge=1, le=365, description="Days from today, shared by every invoice of the batch.")


class DuesInvoiceOut(Schema):
    id: uuid.UUID
    number: str
    amount: Decimal = Field(description="The outstanding balance when the invoice was sent; not re-read afterwards.")
    due_date: datetime.date
    sent_at: datetime.datetime | None
    sent_to_email: str
    sent_to_guardian: bool
    last_reminder_sent_at: datetime.datetime | None
    reminder_count: int
    is_paid: bool
    is_overdue: bool
    membership: MembershipFeeOut


class InvoiceRowOut(Schema):
    kind: InvoiceKindValue
    number: str
    billed_to_member_id: uuid.UUID | None
    billed_to_name: str
    amount: Decimal
    date: datetime.date | None
    status: str = Field(description="Localised label.")
    status_css: str = Field(description="`badge-success`, `badge-warning`, `badge-error` or `badge-neutral`.")
    ref_id: uuid.UUID = Field(description="Order id (kind `order`), membership id (`dues`: `/manage/members/memberships/{id}/invoice`) or registration batch id (`registration`).")


class InvoiceListOut(Schema):
    items: list[InvoiceRowOut]
    total: int
    next_cursor: str | None
    kinds: list[InvoiceKindValue] = Field(description="The kinds the caller may see; dues and registration invoices are club-admin only.")
