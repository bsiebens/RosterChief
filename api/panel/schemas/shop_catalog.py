"""Schemas of the management shop catalogue and inventory routes (api/panel/routers/shop_catalog.py)."""

import datetime
import uuid
from decimal import Decimal

from ninja import Field, Schema


class ShopStateOut(Schema):
    shop_open: bool = Field(description="Whether members can place orders (Club.shop_open).")


class CategoryIn(Schema):
    name: str = Field(max_length=255)


class CategoryOut(Schema):
    id: uuid.UUID
    name: str
    is_system: bool = Field(description="Player/Volunteer categories used by registration; they can be renamed but not deleted.")
    product_count: int


class CategoryListOut(Schema):
    items: list[CategoryOut]


class VariantIn(Schema):
    name: str = Field(max_length=255)
    price: Decimal | None = Field(None, max_digits=10, decimal_places=2, description="Blank uses the product's price.")
    is_active: bool = True


class VariantRowIn(Schema):
    name: str = Field(max_length=255)
    price: Decimal | None = None


class TierIn(Schema):
    min_registrants: int
    discount_type: str = Field(description="`percentage` or `fixed_amount`.")
    discount_amount: Decimal


class VariantOut(Schema):
    id: uuid.UUID
    name: str
    price: Decimal | None
    is_active: bool
    sold_count: int | None = Field(None, description="Paid units; only on the product detail.")


class TierOut(Schema):
    min_registrants: int
    discount_type: str
    discount_amount: Decimal


class ProductOut(Schema):
    id: uuid.UUID
    name: str
    slug: str
    category_id: uuid.UUID | None
    category_name: str | None
    product_type: str
    season_id: uuid.UUID | None
    description: str
    image_url: str | None = Field(description="Absolute URL, null when none is set.")
    price: Decimal
    is_active: bool
    is_public: bool
    personalization_enabled: bool
    inventory_managed: bool
    early_bird_discount_enabled: bool
    early_bird_discount_deadline: datetime.date | None
    early_bird_discount_type: str
    early_bird_discount_amount: Decimal
    registrant_discount_scope: str
    sold_count: int = Field(description="Units on paid orders.")
    stock_count: int | None = Field(description="On hand across variants; null unless inventory managed.")
    variants: list[VariantOut]
    tiers: list[TierOut]


class ProductListOut(Schema):
    items: list[ProductOut]
    next_cursor: str | None = None


class StockRowOut(Schema):
    product_id: uuid.UUID
    product_name: str
    variant_id: uuid.UUID | None
    variant_name: str | None
    is_active: bool = Field(description="Whether the product (and its variant) can still be ordered; inactive rows may still hold stock.")
    on_hand: int
    committed: int = Field(description="Units on open (not delivered, not cancelled) orders.")


class InventoryOut(Schema):
    items: list[StockRowOut]
    out_of_stock_count: int


class ReceiveItemIn(Schema):
    product_id: uuid.UUID
    variant_id: uuid.UUID | None = None
    quantity: int = Field(ge=1)


class ReceiveIn(Schema):
    items: list[ReceiveItemIn] = Field(min_length=1)
    note: str = Field("", max_length=255)


class ReceiveOut(Schema):
    rows: int
    units: int


class AdjustIn(Schema):
    counted: int = Field(description="What is actually on the shelf; the difference is recorded as an adjustment.")
    note: str = Field("", max_length=255)


class AdjustOut(Schema):
    changed: bool = Field(description="False when the count already matched.")
    on_hand: int


class MovementOut(Schema):
    id: uuid.UUID
    product_id: uuid.UUID
    product_name: str
    variant_id: uuid.UUID | None
    variant_name: str | None
    quantity: int = Field(description="Signed: positive adds to stock.")
    reason: str = Field(description="`received`, `adjustment`, `order`, `order_returned`.")
    note: str
    order_id: uuid.UUID | None
    created_by: str | None
    created: datetime.datetime


class MovementListOut(Schema):
    items: list[MovementOut]
    next_cursor: str | None = None


class ProductFormIn(Schema):
    """The multipart form fields of a product create or update (the `image` file travels beside them)."""

    name: str = Field(max_length=255)
    category_id: uuid.UUID | None = None
    product_type: str = Field("merchandise", description="`merchandise`, `membership` or `event_fee`.")
    season_id: uuid.UUID | None = Field(None, description="A current or upcoming season.")
    description: str = ""
    price: Decimal = Decimal("0")
    is_active: bool = True
    is_public: bool = True
    personalization_enabled: bool = False
    inventory_managed: bool = Field(False, description="Merchandise only.")
    early_bird_discount_enabled: bool = False
    early_bird_discount_deadline: datetime.date | None = None
    early_bird_discount_type: str = "percentage"
    early_bird_discount_amount: Decimal = Decimal("0")
    registrant_discount_scope: str = Field("per_person", description="`per_person` or `per_order`.")
    clear_image: bool = Field(False, description="Update only: remove the current image (ignored when a new `image` part is sent).")
    variants: str = Field("", description='Create only: JSON list of `{name, price?}` for the first options, e.g. `[{"name": "Small"}]`.')
    tiers: str | None = Field(None, description="JSON list of registrant-discount tiers `{min_registrants, discount_type, discount_amount}`. On update the list replaces all tiers; omit to keep them.")


class VariantPatch(Schema):
    name: str | None = Field(None, max_length=255)
    price: Decimal | None = Field(None, max_digits=10, decimal_places=2, description="Send null to use the product's price.")
    is_active: bool | None = None


class CategoryPatch(Schema):
    name: str | None = Field(None, max_length=255)
