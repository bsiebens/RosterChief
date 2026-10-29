"""Schemas of the management shop discount routes (api/panel/routers/shop_discounts.py).

Money is a decimal string, dates ``YYYY-MM-DD``, enums their stored values (API.md §6).
"""

import datetime
import uuid
from decimal import Decimal
from typing import Literal

from ninja import Field, Schema

DiscountTypeValue = Literal["percentage", "fixed_amount"]


class DiscountIn(Schema):
    name: str = Field(max_length=255)
    description: str = ""
    code: str = Field(max_length=50, description="What members type in at checkout; stored uppercased and unique within the club (case-insensitive).")
    discount_type: DiscountTypeValue = "percentage"
    discount_amount: Decimal = Field(Decimal("0"), ge=0, description="A percentage rate (10 = 10%) or a fixed euro amount, per `discount_type`.")
    is_active: bool = True
    valid_from: datetime.date | None = Field(None, description="First day the code can be used (inclusive).")
    valid_until: datetime.date | None = Field(None, description="Last day the code can be used (inclusive); not before `valid_from`.")
    max_uses: int | None = Field(None, ge=0, description="Total orders that may use the code; cancelled orders do not count. Null = unlimited.")
    once_per_member: bool = True


class DiscountPatch(Schema):
    name: str | None = Field(None, max_length=255)
    description: str | None = None
    code: str | None = Field(None, max_length=50)
    discount_type: DiscountTypeValue | None = None
    discount_amount: Decimal | None = Field(None, ge=0)
    is_active: bool | None = None
    valid_from: datetime.date | None = None
    valid_until: datetime.date | None = None
    max_uses: int | None = None
    once_per_member: bool | None = None


class DiscountOut(Schema):
    id: uuid.UUID
    name: str
    slug: str
    description: str
    code: str
    discount_type: DiscountTypeValue
    discount_amount: Decimal
    is_active: bool
    valid_from: datetime.date | None
    valid_until: datetime.date | None
    max_uses: int | None
    once_per_member: bool
    use_count: int = Field(description="Orders that used the code and were not cancelled: what `max_uses` counts against.")


class DiscountListOut(Schema):
    items: list[DiscountOut]
    next_cursor: str | None
