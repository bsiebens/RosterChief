import datetime
import uuid
from decimal import Decimal
from typing import Annotated

from ninja import Field, Schema
from pydantic import PlainSerializer

Money = Annotated[Decimal, PlainSerializer(lambda d: f"{d:.2f}", return_type=str)]


class CategoryRef(Schema):
    id: uuid.UUID
    name: str
    #: "player" / "volunteer" for the two fixed system categories, else null.
    system_key: str | None = None


class CategoryOut(Schema):
    id: uuid.UUID
    name: str
    product_count: int
    system_key: str | None = None
    #: True for Player and Volunteer: they can't be renamed or deleted.
    is_system: bool = False


class CategoryIn(Schema):
    name: str = Field(max_length=255)


class CategoryListOut(Schema):
    count: int
    results: list[CategoryOut]


class VariantOut(Schema):
    id: uuid.UUID
    product_id: uuid.UUID
    name: str
    price: Money | None
    effective_price: Money
    is_active: bool
    ordering: int
    stock: int | None
    in_stock: bool
    #: Membership products: the team registrants for this option are placed in (``None``: the product's team, if any).
    team_id: uuid.UUID | None = None
    team_name: str | None = None


class VariantIn(Schema):
    name: str = Field(max_length=255)
    price: Decimal | None = Field(None, max_digits=10, decimal_places=2)
    is_active: bool = True
    ordering: int | None = Field(None, ge=0)
    team_id: uuid.UUID | None = None


class VariantPatch(Schema):
    name: str | None = Field(None, max_length=255)
    price: Decimal | None = Field(None, max_digits=10, decimal_places=2)
    is_active: bool | None = None
    ordering: int | None = Field(None, ge=0)
    #: ``null`` removes the variant's own team.
    team_id: uuid.UUID | None = None


class VariantRowIn(Schema):
    name: str = Field(max_length=255)
    price: Decimal | None = Field(None, max_digits=10, decimal_places=2)
    team_id: uuid.UUID | None = None


class OrderingWindow(Schema):
    deadline: datetime.date
    estimated_delivery: datetime.date


class ProductItem(Schema):
    id: uuid.UUID
    name: str
    slug: str
    category: CategoryRef | None
    description: str
    image_url: str | None
    price: Money
    vat_rate: Decimal = Decimal("0")
    product_type: str
    is_active: bool
    is_public: bool
    personalization_enabled: bool
    inventory_managed: bool
    variant_count: int
    in_stock: bool
    ordering_closed: bool
    next_ordering_window: OrderingWindow | None
    created: datetime.datetime


class ProductImageOut(Schema):
    id: uuid.UUID
    url: str
    is_main: bool
    ordering: int
    alt: str


class ProductAttachmentOut(Schema):
    id: uuid.UUID
    title: str
    #: The file name as uploaded.
    name: str
    url: str
    size: int
    content_type: str
    created: datetime.datetime


class RegistrantTier(Schema):
    min_registrants: int = Field(ge=2)
    discount_type: str
    discount_amount: Decimal = Field(max_digits=10, decimal_places=2)


class ProductDetail(ProductItem):
    ordering_windows: list[OrderingWindow]
    #: Registration pricing (membership products; ``None`` / empty / defaults for everything else).
    season_id: uuid.UUID | None = None
    season_name: str | None = None
    early_bird_discount_type: str = ""
    early_bird_discount_amount: Money = Decimal("0.00")
    early_bird_deadline: datetime.date | None = None
    registrant_discount_scope: str = "per_person"
    registrant_tiers: list[RegistrantTier] = []
    #: The team registrants for this product are placed in (membership products, not Volunteer).
    team_id: uuid.UUID | None = None
    team_name: str | None = None
    variants: list[VariantOut]
    order_line_count: int
    #: The gallery in display order (exactly one ``is_main`` when there is a picture) and the product's files (e.g. a size guide).
    images: list[ProductImageOut]
    attachments: list[ProductAttachmentOut]


class ImageAltIn(Schema):
    alt: str = Field("", max_length=255)


class ImageOrderIn(Schema):
    ids: list[uuid.UUID]


class AttachmentTitleIn(Schema):
    title: str = Field(max_length=255)


class ProductListOut(Schema):
    count: int
    results: list[ProductItem]


class ProductIn(Schema):
    name: str = Field(max_length=255)
    category_id: uuid.UUID | None = None
    description: str = ""
    price: Decimal = Field(max_digits=10, decimal_places=2)
    #: VAT contained in the price, in percent. Only used while the shop's VAT setting is on.
    vat_rate: Decimal = Field(Decimal("0"), ge=0, le=100, max_digits=5, decimal_places=2)
    product_type: str = "merchandise"
    is_active: bool = True
    is_public: bool = True
    personalization_enabled: bool = False
    inventory_managed: bool = False
    ordering_windows: list[OrderingWindow] = Field(default_factory=list, max_length=100)
    season_id: uuid.UUID | None = None
    early_bird_discount_type: str = ""
    early_bird_discount_amount: Decimal = Field(Decimal("0"), max_digits=10, decimal_places=2)
    early_bird_deadline: datetime.date | None = None
    registrant_discount_scope: str = "per_person"
    registrant_tiers: list[RegistrantTier] = []
    team_id: uuid.UUID | None = None
    variants: list[VariantRowIn] = []


class ProductPatch(Schema):
    name: str | None = Field(None, max_length=255)
    category_id: uuid.UUID | None = None
    description: str | None = None
    price: Decimal | None = Field(None, max_digits=10, decimal_places=2)
    vat_rate: Decimal | None = Field(None, ge=0, le=100, max_digits=5, decimal_places=2)
    product_type: str | None = None
    is_active: bool | None = None
    ordering_windows: list[OrderingWindow] | None = Field(None, max_length=100)
    is_public: bool | None = None
    personalization_enabled: bool | None = None
    inventory_managed: bool | None = None
    season_id: uuid.UUID | None = None
    early_bird_discount_type: str | None = None
    early_bird_discount_amount: Decimal | None = Field(None, max_digits=10, decimal_places=2)
    early_bird_deadline: datetime.date | None = None
    registrant_discount_scope: str | None = None
    registrant_tiers: list[RegistrantTier] | None = None
    #: ``null`` removes the team.
    team_id: uuid.UUID | None = None
