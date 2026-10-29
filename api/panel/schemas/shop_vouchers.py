"""Schemas of the management shop voucher routes (api/panel/routers/shop_vouchers.py).

Money is a decimal string, dates ``YYYY-MM-DD``, timestamps ISO 8601 (API.md §6).
"""

import datetime
import uuid
from decimal import Decimal
from typing import Literal

from ninja import Field, Schema


class PersonRef(Schema):
    id: uuid.UUID
    name: str


class VoucherIn(Schema):
    issued_to_id: uuid.UUID | None = Field(None, description="A member of this club the credit was issued to (a record only; the voucher can be redeemed by anyone). Null = nobody in particular.")
    amount: Decimal = Field(gt=0, description="The voucher's value.")
    expiry_date: datetime.date
    is_active: bool = True
    notes: str = ""


class VoucherPatch(Schema):
    issued_to_id: uuid.UUID | None = None
    amount: Decimal | None = Field(None, gt=0, description="Not below what is already consumed.")
    expiry_date: datetime.date | None = None
    is_active: bool | None = Field(None, description="False voids the voucher before its expiry date.")
    notes: str | None = None


class VoucherOut(Schema):
    id: uuid.UUID
    number: str = Field(description="Generated on creation, `VCH-<year>-<seq>`, unique within the club.")
    issued_to: PersonRef | None
    amount: Decimal
    consumed_amount: Decimal
    available_amount: Decimal
    expiry_date: datetime.date
    is_active: bool
    is_expired: bool
    is_usable: bool = Field(description="Active, not expired and with a balance left.")
    notes: str
    created: datetime.datetime


class VoucherListOut(Schema):
    items: list[VoucherOut]
    next_cursor: str | None


class HistoryRow(Schema):
    kind: Literal["manual", "order"] = Field(description="`manual`: a consumption recorded by staff (deletable); `order`: a confirmed voucher payment on a shop order.")
    amount: Decimal
    when: datetime.datetime
    detail: str = Field(description="The note of a manual row, the order number of an order row.")
    consumption_id: uuid.UUID | None = Field(description="Set on `manual` rows: the id for `DELETE .../consumptions/{id}`.")
    order_id: uuid.UUID | None = Field(description="Set on `order` rows.")
    recorded_by: PersonRef | None = Field(description="The account that recorded a manual row; null on order rows.")


class VoucherDetailOut(VoucherOut):
    history: list[HistoryRow] = Field(description="Every movement of the balance, manual consumptions and order payments together, newest first.")


class ConsumptionIn(Schema):
    amount: Decimal = Field(ge=Decimal("0.01"), description="At least 0.01 and not more than the voucher's available amount.")
    note: str = Field(min_length=1, description="What this was for (required).")


class ConsumptionOut(Schema):
    id: uuid.UUID
    amount: Decimal
    note: str
    recorded_at: datetime.datetime
    recorded_by: PersonRef | None


class ConsumptionCreatedOut(Schema):
    consumption: ConsumptionOut
    voucher: VoucherOut
