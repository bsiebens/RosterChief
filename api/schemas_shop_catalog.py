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


class CategoryOut(Schema):
    id: uuid.UUID
    name: str
    product_count: int


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


class VariantIn(Schema):
    name: str = Field(max_length=255)
    price: Decimal | None = Field(None, max_digits=10, decimal_places=2)
    is_active: bool = True
    ordering: int | None = Field(None, ge=0)


class VariantPatch(Schema):
    name: str | None = Field(None, max_length=255)
    price: Decimal | None = Field(None, max_digits=10, decimal_places=2)
    is_active: bool | None = None
    ordering: int | None = Field(None, ge=0)


class VariantRowIn(Schema):
    name: str = Field(max_length=255)
    price: Decimal | None = Field(None, max_digits=10, decimal_places=2)


class ProductItem(Schema):
    id: uuid.UUID
    name: str
    slug: str
    category: CategoryRef | None
    description: str
    image_url: str | None
    price: Money
    product_type: str
    is_active: bool
    is_public: bool
    personalization_enabled: bool
    inventory_managed: bool
    variant_count: int
    in_stock: bool
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


class ProductDetail(ProductItem):
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
    product_type: str = "merchandise"
    is_active: bool = True
    is_public: bool = True
    personalization_enabled: bool = False
    inventory_managed: bool = False
    variants: list[VariantRowIn] = []


class ProductPatch(Schema):
    name: str | None = Field(None, max_length=255)
    category_id: uuid.UUID | None = None
    description: str | None = None
    price: Decimal | None = Field(None, max_digits=10, decimal_places=2)
    product_type: str | None = None
    is_active: bool | None = None
    is_public: bool | None = None
    personalization_enabled: bool | None = None
    inventory_managed: bool | None = None
