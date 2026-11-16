import datetime
import uuid
from decimal import Decimal
from typing import Annotated

from ninja import Field, Schema
from pydantic import PlainSerializer

Money = Annotated[Decimal, PlainSerializer(lambda d: f"{d:.2f}", return_type=str)]


class DiscountOut(Schema):
    id: uuid.UUID
    name: str
    slug: str
    description: str
    code: str
    discount_type: str
    discount_amount: Money
    is_active: bool
    valid_from: datetime.date | None
    valid_until: datetime.date | None
    max_uses: int | None
    once_per_member: bool
    status: str
    used_count: int
    created: datetime.datetime


class DiscountListOut(Schema):
    count: int
    results: list[DiscountOut]


class DiscountIn(Schema):
    name: str = Field(max_length=255)
    description: str = ""
    code: str = Field(max_length=50)
    discount_type: str = "percentage"
    discount_amount: Decimal = Field(max_digits=10, decimal_places=2)
    is_active: bool = True
    valid_from: datetime.date | None = None
    valid_until: datetime.date | None = None
    max_uses: int | None = Field(None, ge=1)
    once_per_member: bool = True


class DiscountPatch(Schema):
    name: str | None = Field(None, max_length=255)
    description: str | None = None
    code: str | None = Field(None, max_length=50)
    discount_type: str | None = None
    discount_amount: Decimal | None = Field(None, max_digits=10, decimal_places=2)
    is_active: bool | None = None
    valid_from: datetime.date | None = None
    valid_until: datetime.date | None = None
    max_uses: int | None = Field(None, ge=1)
    once_per_member: bool | None = None


class MemberRef(Schema):
    id: uuid.UUID
    name: str


class VoucherOut(Schema):
    id: uuid.UUID
    number: str
    issued_to: MemberRef | None
    amount: Money
    consumed_amount: Money
    available_amount: Money
    expiry_date: datetime.date
    is_active: bool
    notes: str
    status: str
    created: datetime.datetime


class HistoryRow(Schema):
    kind: str
    amount: Money
    when: datetime.datetime
    detail: str
    order_id: uuid.UUID | None
    consumption_id: uuid.UUID | None
    recorded_by: str | None


class VoucherDetail(VoucherOut):
    history: list[HistoryRow]


class VoucherListOut(Schema):
    count: int
    results: list[VoucherOut]


class VoucherIn(Schema):
    amount: Decimal = Field(max_digits=10, decimal_places=2)
    expiry_date: datetime.date
    issued_to_id: uuid.UUID | None = None
    notes: str = ""
    count: int = 1


class VoucherPatch(Schema):
    amount: Decimal | None = Field(None, max_digits=10, decimal_places=2)
    expiry_date: datetime.date | None = None
    issued_to_id: uuid.UUID | None = None
    notes: str | None = None
    is_active: bool | None = None


class ConsumptionIn(Schema):
    amount: Decimal = Field(max_digits=10, decimal_places=2)
    note: str = ""
