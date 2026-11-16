import datetime
import uuid

from ninja import Field, Schema


class StockRowOut(Schema):
    product_id: uuid.UUID
    product_name: str
    variant_id: uuid.UUID | None
    variant_name: str | None
    on_hand: int
    committed: int
    available: int
    category_id: uuid.UUID | None = None
    category_name: str | None = None


class StockListOut(Schema):
    count: int
    results: list[StockRowOut]


class MovementOut(Schema):
    id: uuid.UUID
    product_id: uuid.UUID
    product_name: str
    variant_id: uuid.UUID | None
    variant_name: str | None
    quantity: int
    reason: str
    note: str
    order_id: uuid.UUID | None
    order_number: str | None
    created_by: str | None
    created: datetime.datetime


class MovementListOut(Schema):
    count: int
    results: list[MovementOut]


class ReceiveItemIn(Schema):
    product_id: uuid.UUID
    variant_id: uuid.UUID | None = None
    quantity: int


class ReceiveIn(Schema):
    note: str = Field("", max_length=255)
    items: list[ReceiveItemIn] = Field(min_length=1)


class ReceiveOut(Schema):
    movements: list[MovementOut]


class AdjustIn(Schema):
    product_id: uuid.UUID
    variant_id: uuid.UUID | None = None
    counted: int
    note: str = Field("", max_length=255)


class AdjustOut(Schema):
    movement: MovementOut | None


class CountLineIn(Schema):
    product_id: uuid.UUID
    variant_id: uuid.UUID | None = None
    counted: int = Field(ge=0)
    note: str = Field("", max_length=255)


class CountIn(Schema):
    note: str = Field("", max_length=255)
    counts: list[CountLineIn] = Field(min_length=1)


class CountLineOut(Schema):
    product_id: uuid.UUID
    variant_id: uuid.UUID | None
    name: str
    previous: int
    counted: int
    difference: int


class CountOut(Schema):
    applied: int
    unchanged: int
    total_difference: int
    lines: list[CountLineOut]
