"""Member catalogue, checkout, and order receipts on the club app."""

import datetime
import uuid
from decimal import Decimal

from django.core.cache import cache
from django.db import transaction
from django.db.models import Prefetch
from django.http import HttpResponse
from ninja import Field, Query, Router, Schema
from ninja.errors import HttpError

from api.auth import CookieAuth, TokenAuth
from api.routers.portal import _people, _viewer
from api.routers.shop_catalog import _item, _levels, _variant
from api.schemas_shop_catalog import OrderingWindow
from api.schemas_shop_orders import Money, MoneyIn
from formbuilder.services.management import Conflict
from members.models import Member
from members.services.errors import Invalid
from shop.models import Order, OrderLine, Product, ProductVariant
from shop.services import catalog, inventory, invoices, orders, payments
from shop.settings_models import ShopSettings

router = Router(auth=[TokenAuth(door=False), CookieAuth(door=False)])
MAX_SUBTOTAL = Decimal("99999999.99")
WRONG_CODE_LIMIT = 10
WRONG_CODE_WINDOW = 900


class ProductOption(Schema):
    id: uuid.UUID
    name: str
    effective_price: Money
    in_stock: bool


class ProductFile(Schema):
    title: str
    url: str


class ShopProduct(Schema):
    id: uuid.UUID
    name: str
    category: str
    description: str
    image_url: str | None
    price: Money
    in_stock: bool
    next_ordering_window: OrderingWindow | None
    personalization_enabled: bool
    variants: list[ProductOption]
    files: list[ProductFile]


class Person(Schema):
    id: uuid.UUID
    name: str


class Catalog(Schema):
    viewer_id: uuid.UUID
    people: list[Person]
    open: bool
    closed_message: str
    payment_instructions: str
    products: list[ShopProduct]


class CheckoutLine(Schema):
    product_id: uuid.UUID
    variant_id: uuid.UUID | None = None
    beneficiary_id: uuid.UUID | None = None
    quantity: int = Field(ge=1, le=orders.MAX_QUANTITY)
    unit_price: MoneyIn
    ordering_deadline: datetime.date | None = None
    estimated_delivery: datetime.date | None = None
    personalization_name: str = Field("", max_length=100)
    personalization_number: str = Field("", max_length=20)


class Checkout(Schema):
    checkout_id: uuid.UUID
    lines: list[CheckoutLine] = Field(min_length=1, max_length=100)
    discount_code: str = Field("", max_length=255)


class ReceiptLine(Schema):
    id: uuid.UUID
    name: str
    variant: str
    quantity: int
    amount: Money


class ReceiptDiscount(Schema):
    description: str
    amount: Money


class Receipt(Schema):
    id: uuid.UUID
    number: str
    created: datetime.datetime
    fulfillment_status: str
    payment_status: str
    pickup_instructions: str
    invoice_number: str | None
    lines: list[ReceiptLine]
    discounts: list[ReceiptDiscount]
    total: Money
    due: Money


class OrderHistory(Schema):
    results: list[Receipt]
    more: bool


def _buyer(request):
    buyer = _viewer(request)
    if not request.features.has("shop"):
        raise HttpError(404, "Not found.")
    return buyer


def _shop_product(product) -> bool:
    return product.is_active and product.is_public and product.product_type != Product.ProductType.MEMBERSHIP


@router.get("", response=Catalog, operation_id="portal_shop_catalog")
def shop_catalog(request):
    buyer = _buyer(request)
    settings = ShopSettings.current()
    products = []
    if settings.shop_open:
        items = list(catalog.products_queryset().filter(is_active=True, is_public=True).exclude(product_type=Product.ProductType.MEMBERSHIP))
        levels = _levels(items)
        for product in items:
            row = _item(product, levels)
            products.append(
                {
                    **row, "category": product.category.name if product.category else "",
                    "variants": [_variant(variant, levels) for variant in product.variants.all() if variant.is_active],
                    "files": [{"title": file.title or file.name, "url": file.file.url} for file in product.attachments.all()],
                }
            )  # fmt: skip
    return {
        "viewer_id": buyer.pk,
        "people": [{"id": person.pk, "name": str(person)} for person in _people(buyer).values()],
        "open": settings.shop_open,
        "closed_message": settings.closed_message,
        "payment_instructions": request.tenant.payment_instructions,
        "products": products,
    }


def _orders(buyer):
    return Order.objects.filter(purchaser=buyer).select_related("invoice").prefetch_related(
        Prefetch("lines", queryset=OrderLine.objects.select_related("product", "variant")), "applied_discounts"
    ).order_by("-created", "-id")


def _receipt(order) -> dict:
    invoice = getattr(order, "invoice", None)
    return {
        "id": order.pk, "number": order.number, "created": order.created, "fulfillment_status": orders.member_status(order), "payment_status": order.payment_status,
        "pickup_instructions": order.pickup_instructions, "invoice_number": invoice.number if invoice else None, "total": order.total, "due": payments.amount_due(order),
        "lines": [{"id": line.pk, "name": line.product.name, "variant": line.variant.name if line.variant else "", "quantity": line.quantity, "amount": line.line_total} for line in order.lines.all()],
        "discounts": [{"description": line.description, "amount": line.amount} for line in order.applied_discounts.all()],
    }  # fmt: skip


@router.get("/orders", response=OrderHistory, operation_id="portal_shop_orders")
def order_history(request, offset: int = Query(0, ge=0), limit: int = Query(15, ge=1, le=50)):
    rows = list(_orders(_buyer(request))[offset : offset + limit + 1])
    return {"results": [_receipt(row) for row in rows[:limit]], "more": len(rows) > limit}


@router.post("/orders", response={201: Receipt}, operation_id="portal_shop_checkout")
@transaction.atomic
def checkout(request, payload: Checkout):
    buyer = _buyer(request)
    # A retry uses the same order ID. The purchaser lock makes checking and placing it a single operation.
    order_id = uuid.uuid5(uuid.NAMESPACE_URL, f"rosterchief:shop-checkout:{buyer.pk}:{payload.checkout_id}")
    Member.objects.select_for_update(no_key=True).get(pk=buyer.pk)
    existing = _orders(buyer).filter(pk=order_id).first()
    if existing is not None:
        return 201, _receipt(existing)
    orders.ensure_shop_open()
    discount_code = payload.discount_code.strip()
    discount_attempts = f"portal-shop:discount:{request.tenant.pk}:{buyer.pk}"
    if discount_code and cache.get(discount_attempts, 0) >= WRONG_CODE_LIMIT:
        raise HttpError(429, "Too many invalid discount codes. Try again in a few minutes.")
    products = {product.pk: product for product in inventory.lock_products({line.product_id for line in payload.lines}) if _shop_product(product)}
    variants = ProductVariant.objects.select_for_update().filter(product_id__in=products, pk__in={line.variant_id for line in payload.lines if line.variant_id}).in_bulk()
    people = _people(buyer)
    specs = []
    for index, line in enumerate(payload.lines):
        product = products.get(line.product_id)
        variant = variants.get(line.variant_id)
        key = f"lines.{index}"
        if product is None or (line.variant_id and (variant is None or not variant.is_active or variant.product_id != product.pk)):
            raise Invalid("This product or option is no longer available.", {key: ["Remove this line or choose a different option."]})
        if line.beneficiary_id and line.beneficiary_id not in people:
            raise Invalid("Unknown recipient.", {key: ["Choose a recipient from your family."]})
        window = catalog.next_ordering_window(product)
        if (window or {}).get("deadline") != (line.ordering_deadline.isoformat() if line.ordering_deadline else None) or (
            (window or {}).get("estimated_delivery") != (line.estimated_delivery.isoformat() if line.estimated_delivery else None)
        ):
            raise Conflict("An ordering deadline or delivery estimate changed. Review the cart before ordering.", code="cart_changed")
        spec = orders.LineSpec(product, variant, line.quantity, people.get(line.beneficiary_id), None, line.personalization_number.strip(), line.personalization_name.strip())
        if orders._price(spec) != line.unit_price:
            raise Conflict("A price changed. Review the cart before ordering.", code="cart_changed")
        specs.append(spec)
    if sum((orders._price(spec) * spec.quantity for spec in specs), Decimal("0.00")) > MAX_SUBTOTAL:
        raise Invalid("This order is too large.", {"lines": ["Split these items into smaller orders."]})
    try:
        order = orders.create_order(request.user, purchaser=buyer, specs=specs, discount_code=discount_code, order_id=order_id)
    except Invalid as error:
        if "discount_code" not in error.errors:
            raise
        if not cache.add(discount_attempts, 1, WRONG_CODE_WINDOW):
            try:
                cache.incr(discount_attempts)
            except ValueError:
                cache.add(discount_attempts, 1, WRONG_CODE_WINDOW)
        raise Invalid("This discount code isn't valid.", {"discount_code": ["This discount code isn't valid."]}) from error
    return 201, _receipt(_orders(buyer).get(pk=order.pk))


@router.get("/orders/{order_id}/invoice.pdf", operation_id="portal_shop_invoice")
def invoice_pdf(request, order_id: uuid.UUID):
    order = _orders(_buyer(request)).filter(pk=order_id).first()
    invoice = getattr(order, "invoice", None)
    if invoice is None:
        raise HttpError(404, "Not found.")
    try:
        pdf = invoices.render_pdf(invoice)
    except invoices.PDFUnavailable:
        raise HttpError(503, "PDF rendering is not available.") from None
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{invoice.number}.pdf"'
    response["Cache-Control"] = "private, no-store"
    return response
