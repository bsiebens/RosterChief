"""The member shop (API.md §7.2, §12), club host: catalogue, cart, checkout (pay at the
club -- no in-app payment), orders and invoices. Rules come from shop.services.cart and
shop.services.checkout, the same ones the member web app uses.

Gates (API.md §5): without the club's `shop` licence everything is `404
feature_disabled`; while the club has the shop closed, the catalogue, cart and checkout
answer `409 shop_closed`, but the family's own orders and invoices stay readable.
"""

import uuid

from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils.translation import gettext_lazy as _
from ninja import Router

from rosterchief.client_ip import client_ip
from shop.models import CartItem, Order, ProductCategory
from shop.services.cart import MAX_CART_QUANTITY, add_to_cart, cart_summary, catalog, open_cart, orders_for, set_quantity
from shop.services.checkout import CheckoutError, place_order
from shop.services.inventory import annotate_availability
from shop.services.invoices import ShopInvoicePDFError, render_invoice_pdf

from ..errors import ApiError
from ..idempotency import idempotent
from ..pagination import paginate
from ..presenters import money
from ..schemas import CartItemIn, CartItemPatchIn, CartOut, CatalogOut, CheckoutIn, ErrorOut, OrderListOut, OrderOut, ProductOut
from ..scope import club_scope

router = Router(tags=["shop"])

ERRORS = {401: ErrorOut, 403: ErrorOut, 404: ErrorOut}
OPEN_ERRORS = {**ERRORS, 409: ErrorOut}


def _shop_scope(request, *, must_be_open=True):
    scope = club_scope(request)
    scope.require_feature("shop")
    if must_be_open and not scope.club.shop_open:
        raise ApiError(409, "shop_closed", _("The shop isn't taking orders right now."))
    return scope


def _image(request, image) -> str | None:
    return request.build_absolute_uri(image.url) if image else None


def _variant(variant) -> dict:
    return {"id": variant.pk, "name": variant.name, "price": money(variant.effective_price), "in_stock": getattr(variant, "in_stock", True)}


def _product(request, product) -> dict:
    return {
        "id": product.pk,
        "slug": product.slug,
        "name": product.name,
        "description": product.description,
        "image_url": _image(request, product.image),
        "price": money(product.price),
        "category_id": product.category_id,
        "personalization_enabled": product.personalization_enabled,
        "in_stock": getattr(product, "in_stock", True),
        "variants": [_variant(variant) for variant in product.variants.all() if variant.is_active],
    }


@router.get("/shop/products", response={200: CatalogOut, **OPEN_ERRORS}, summary="Catalogue")
def products(request):
    scope = _shop_scope(request)
    items = annotate_availability(catalog(scope.club).prefetch_related("variants").order_by("name"))
    used = {product.category_id for product in items}
    categories = [{"id": category.pk, "name": category.name} for category in ProductCategory.objects.filter(club=scope.club, pk__in=used).order_by("name")]
    return {"categories": categories, "items": [_product(request, product) for product in items]}


@router.get("/shop/products/{slug}", response={200: ProductOut, **OPEN_ERRORS}, summary="One product")
def product_detail(request, slug: str):
    scope = _shop_scope(request)
    return _product(request, annotate_availability([get_object_or_404(catalog(scope.club).prefetch_related("variants"), slug=slug)])[0])


def _cart(request, scope, code: str = "") -> dict:
    summary = cart_summary(scope.club, request.user, code=code, member=scope.me, ip=client_ip(request))
    totals = summary["totals"]
    discount = summary["discount"]
    return {
        "items": [
            {
                "id": item.pk,
                "product": {"id": item.product_id, "slug": item.product.slug, "name": item.product.name, "image_url": _image(request, item.product.image)},
                "variant": _variant(item.variant) if item.variant_id else None,
                "quantity": item.quantity,
                "unit_price": money(item.unit_price),
                "line_total": money(item.line_total),
                "beneficiary_id": item.beneficiary_id,
                "personalization_number": item.personalization_number,
                "personalization_name": item.personalization_name,
                "unavailable": item.unavailable,
            }
            for item in summary["items"]
        ],
        "subtotal": money(totals["subtotal"]),
        "discount": {"code": discount.code, "name": discount.name, "amount": money(totals["discount_amount"])} if discount else None,
        "discount_error": summary["discount_error"],
        "total": money(totals["total"]),
        "has_unavailable": summary["has_unavailable"],
        "prices_changed": summary["prices_changed"],
        "max_quantity": MAX_CART_QUANTITY,
    }


@router.get("/shop/cart", response={200: CartOut, **OPEN_ERRORS}, summary="The cart")
def cart(request, code: str = ""):
    """Re-priced to what checkout would charge now. Pass `code` to preview a discount
    code (`discount` or `discount_error`); only checkout actually redeems it. Wrong codes
    count towards a throttle."""
    scope = _shop_scope(request)
    return _cart(request, scope, code)


@router.post("/shop/cart/items", response={201: CartOut, **OPEN_ERRORS, 422: ErrorOut}, summary="Add to cart")
def add_item(request, payload: CartItemIn):
    """Adds to an existing line for the same product, option, person and
    personalisation. Returns the whole cart. Send an `Idempotency-Key` so a retried tap
    doesn't add twice."""
    scope = _shop_scope(request)
    if scope.me is None:
        raise ApiError(403, "forbidden")
    product = get_object_or_404(catalog(scope.club), pk=payload.product_id)
    variant = None
    if payload.variant_id:
        variant = product.variants.filter(pk=payload.variant_id, is_active=True).first()
        if variant is None:
            raise ApiError(422, "validation_error", fields={"variant_id": [_("That option isn't available.")]})
    beneficiary = scope.person(payload.beneficiary_id) if payload.beneficiary_id else None

    def work():
        try:
            add_to_cart(
                scope.club,
                request.user,
                product,
                quantity=payload.quantity,
                variant=variant,
                beneficiary=beneficiary,
                personalization_number=payload.personalization_number,
                personalization_name=payload.personalization_name,
            )
        except CheckoutError as error:
            raise ApiError(409, "out_of_stock", str(error)) from error
        return 201, _cart(request, scope)

    return idempotent(request, work)


def _own_item(request, scope, item_id) -> CartItem:
    cart = open_cart(scope.club, request.user)
    if cart is None:
        raise ApiError(404, "not_found")
    return get_object_or_404(CartItem, pk=item_id, cart=cart)


@router.patch("/shop/cart/items/{item_id}", response={200: CartOut, **OPEN_ERRORS, 422: ErrorOut}, summary="Change a quantity")
def update_item(request, item_id: uuid.UUID, payload: CartItemPatchIn):
    scope = _shop_scope(request)
    set_quantity(_own_item(request, scope, item_id), payload.quantity)
    return _cart(request, scope)


@router.delete("/shop/cart/items/{item_id}", response={200: CartOut, **OPEN_ERRORS}, summary="Remove a line")
def remove_item(request, item_id: uuid.UUID):
    scope = _shop_scope(request)
    set_quantity(_own_item(request, scope, item_id), 0)
    return _cart(request, scope)


def _order(order: Order, *, detail: bool = False) -> dict:
    data = {
        "id": order.pk,
        "number": order.number,
        "created": order.created,
        "total": money(order.total),
        "purchaser_id": order.purchaser_id,
        "payment_status": order.payment_status,
        "status": order.member_status,
    }
    if detail:
        data |= {
            "lines": [
                {
                    "product_name": line.product.name,
                    "variant_name": line.variant.name if line.variant_id else None,
                    "quantity": line.quantity,
                    "unit_price": money(line.unit_price),
                    "line_total": money(line.line_total),
                    "beneficiary_id": line.beneficiary_id,
                    "personalization_number": line.personalization_number,
                    "personalization_name": line.personalization_name,
                }
                for line in order.order_items.select_related("product", "variant").order_by("created")
            ],
            "discounts": [{"description": applied.description or applied.discount.name, "amount": money(applied.amount)} for applied in order.applied_discounts.select_related("discount")],
            "pickup_instructions": order.pickup_instructions,
            "has_invoice": hasattr(order, "invoice"),
        }
    return data


@router.post("/shop/orders", response={201: OrderOut, **OPEN_ERRORS}, summary="Place the order")
def checkout(request, payload: CheckoutIn):
    """Turns the cart into an order, paid at the club on pickup. Send an
    `Idempotency-Key`: a double tap must not order twice. `409 checkout_failed` carries
    the reason (empty cart, unavailable lines, a code that can't be used) in `message`."""
    scope = _shop_scope(request)
    if scope.me is None:
        raise ApiError(403, "forbidden")

    def work():
        cart = open_cart(scope.club, request.user)
        if cart is None:
            raise ApiError(409, "checkout_failed", _("Your cart is empty."))
        try:
            order = place_order(cart, purchaser=scope.me, discount_code=payload.discount_code, ip=client_ip(request))
        except CheckoutError as error:
            raise ApiError(409, "checkout_failed", str(error)) from error
        return 201, _order(order, detail=True)

    return idempotent(request, work)


@router.get("/shop/orders", response={200: OrderListOut, **ERRORS, 400: ErrorOut}, summary="The family's orders")
def orders(request, cursor: str | None = None, limit: int | None = None):
    """Newest first, also while the shop is closed."""
    scope = _shop_scope(request, must_be_open=False)
    page = paginate(orders_for(scope.club, scope.people), sort_field="-created", cursor=cursor, limit=limit)
    return {"items": [_order(order) for order in page.items], "next_cursor": page.next_cursor}


def _family_order(scope, order_id) -> Order:
    return get_object_or_404(orders_for(scope.club, scope.people), pk=order_id)


@router.get("/shop/orders/{order_id}", response={200: OrderOut, **ERRORS}, summary="One order")
def order_detail(request, order_id: uuid.UUID):
    scope = _shop_scope(request, must_be_open=False)
    return _order(_family_order(scope, order_id), detail=True)


@router.get(
    "/shop/orders/{order_id}/invoice.pdf",
    response={200: None, **ERRORS, 409: ErrorOut},
    summary="Order invoice (PDF)",
    openapi_extra={"responses": {200: {"description": "The invoice.", "content": {"application/pdf": {}}}}},
)
def order_invoice(request, order_id: uuid.UUID):
    scope = _shop_scope(request, must_be_open=False)
    order = _family_order(scope, order_id)
    invoice = getattr(order, "invoice", None)
    if invoice is None:
        raise ApiError(404, "not_found")
    try:
        pdf = render_invoice_pdf(invoice)
    except ShopInvoicePDFError as error:
        raise ApiError(409, "pdf_unavailable", str(error)) from error
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{invoice.number}.pdf"'
    return response
