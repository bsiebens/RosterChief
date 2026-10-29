"""Management API: the shop catalogue and inventory (API.md §17, phase D).

The JSON counterpart of the ``/manage/shop/`` product, category and inventory screens
(management.views: ShopToggleView, Product*View, ProductCategory*View, Inventory*View):

* ``/manage/shop`` and ``/manage/shop/toggle``: the open/closed switch;
* ``/manage/shop/products``: list, create, detail, edit, toggle-active, and a product's variants;
* ``/manage/shop/categories``: list, create, edit, delete;
* ``/manage/shop/inventory``: stock overview, receive a delivery, recount a product or variant, history.

Who may do what is the web's rule (``ShopManagerRequiredMixin``): ``can_manage_shop`` (a club admin or
a shop-manager grant), and the club needs the ``shop`` module (``404 feature_disabled`` without it).
Unlike the other panel routers a shop-manager grant alone is enough: the caller only has to belong to
the club. Validation is management.forms.ProductForm / ProductVariantForm / ProductCategoryForm and the
variant / tier formsets; writing is shop.services.catalog and shop.services.inventory, which the web
views call as well.

Product images are multipart on the product itself (``image`` part; ``clear_image`` removes it), so a
product update is a ``POST`` on the item: Django does not parse multipart ``PUT``/``PATCH``. The first
variants and the registrant-discount tiers travel as JSON strings in the same form. Where a web form
omits the club or the product, a duplicate name would be an IntegrityError; the API answers 422 on `name`.
"""

import json
import uuid
from decimal import Decimal

from django.db import transaction
from django.db.models import Count
from django.utils.translation import gettext_lazy as _
from ninja import File, Form, Router, UploadedFile
from pydantic import TypeAdapter
from pydantic import ValidationError as PydanticValidationError

from api.core.auth import require_club_member
from api.core.errors import ApiError
from api.core.forms import form_error, post_data
from api.core.idempotency import idempotent
from api.core.pagination import paginate, paginate_keyset
from api.core.schemas import ErrorOut
from api.core.uploads import IMAGE_TYPES, validate_upload
from api.panel.guards import require
from api.panel.schemas import shop_catalog as s
from api.protected.services import club_features
from club.services.access import can_manage_shop
from management.forms import ProductCategoryForm, ProductForm, ProductRegistrantDiscountTierFormSet, ProductVariantForm, ProductVariantFormSet
from shop.models import InventoryMovement, Product, ProductCategory, ProductVariant
from shop.services.catalog import CategoryProtected, annotate_sold_and_stock, create_product, delete_category, toggle_active, toggle_shop_open, update_product
from shop.services.inventory import InventoryError, inventory_overview, receive_stock, set_stock, stock_for
from shop.services.stats import quantity_sold_by_variant

router = Router(tags=["panel: shop catalog"])

PREFIX = "/manage/shop"
ERRORS = {401: ErrorOut, 403: ErrorOut, 404: ErrorOut}
INVALID = {422: ErrorOut}
UPLOAD_ERRORS = {413: ErrorOut, 415: ErrorOut, 422: ErrorOut}
CONFLICT = {409: ErrorOut}

PRODUCT_CHECKBOXES = ("is_active", "is_public", "personalization_enabled", "inventory_managed", "early_bird_discount_enabled")
PRODUCT_ALIASES = {"category": "category_id", "season": "season_id", "image": "image"}


# --- helpers -----------------------------------------------------------------------------------


def _club(request):
    """The club on this host for a shop manager, with the ``shop`` module on."""
    club = require_club_member(request)
    require(request, club, can_manage_shop)
    if not club_features(club)["shop"]:
        raise ApiError(404, "feature_disabled", _("This feature isn't available for this club."))
    return club


def _not_found() -> ApiError:
    return ApiError(404, "not_found", _("Not found."))


def _product(club, product_id) -> Product:
    product = Product.objects.filter(club=club, pk=product_id).select_related("category").prefetch_related("variants", "registrant_discount_tiers").first()
    if product is None:
        raise _not_found()
    return product


def _variant(club, product_id, variant_id) -> ProductVariant:
    variant = ProductVariant.objects.filter(product__club=club, product__pk=product_id, pk=variant_id).select_related("product").first()
    if variant is None:
        raise _not_found()
    return variant


def _category(club, category_id) -> ProductCategory:
    category = ProductCategory.objects.filter(club=club, pk=category_id).first()
    if category is None:
        raise _not_found()
    return category


def _variant_out(variant: ProductVariant, sold: dict | None = None) -> dict:
    return {"id": variant.pk, "name": variant.name, "price": variant.price, "is_active": variant.is_active, "sold_count": sold.get(variant.pk, 0) if sold is not None else None}


def _product_out(request, product: Product, sold_variants: dict | None = None) -> dict:
    return {
        "id": product.pk,
        "name": product.name,
        "slug": product.slug,
        "category_id": product.category_id,
        "category_name": product.category.name if product.category_id else None,
        "product_type": product.product_type,
        "season_id": product.season_id,
        "description": product.description,
        "image_url": request.build_absolute_uri(product.image.url) if product.image else None,
        "price": product.price,
        "is_active": product.is_active,
        "is_public": product.is_public,
        "personalization_enabled": product.personalization_enabled,
        "inventory_managed": product.inventory_managed,
        "early_bird_discount_enabled": product.early_bird_discount_enabled,
        "early_bird_discount_deadline": product.early_bird_discount_deadline,
        "early_bird_discount_type": product.early_bird_discount_type,
        "early_bird_discount_amount": product.early_bird_discount_amount,
        "registrant_discount_scope": product.registrant_discount_scope,
        "sold_count": product.sold_count,
        "stock_count": product.stock_count,
        "variants": [_variant_out(variant, sold_variants) for variant in product.variants.all()],
        "tiers": [{"min_registrants": tier.min_registrants, "discount_type": tier.discount_type, "discount_amount": tier.discount_amount} for tier in product.registrant_discount_tiers.all()],
    }


def _fresh_product_out(request, club, product_id, *, detail=False) -> dict:
    product = _product(club, product_id)
    annotate_sold_and_stock(club, [product])
    return _product_out(request, product, quantity_sold_by_variant(product) if detail else None)


def _category_out(category: ProductCategory, count: int | None = None) -> dict:
    return {"id": category.pk, "name": category.name, "is_system": bool(category.registration_kind), "product_count": category.products.count() if count is None else count}


def _formset_data(prefix: str, rows: list[dict]) -> dict:
    data = {f"{prefix}-TOTAL_FORMS": len(rows), f"{prefix}-INITIAL_FORMS": 0, f"{prefix}-MIN_NUM_FORMS": 0, f"{prefix}-MAX_NUM_FORMS": 1000}
    for index, row in enumerate(rows):
        for key, value in row.items():
            if value is not None:
                data[f"{prefix}-{index}-{key}"] = str(value)
    return data


def _json_rows(raw: str | None, field: str) -> list[dict] | None:
    """A JSON string form value -> a list of row dicts (None when absent), 422 on ``field`` when malformed."""
    if raw is None or not raw.strip():
        return None
    try:
        rows = TypeAdapter(list[dict]).validate_python(json.loads(raw))
    except ValueError, TypeError, PydanticValidationError:
        raise ApiError(422, "validation_error", fields={field: [str(_("Send a JSON list of objects."))]}) from None
    return rows


def _formset_errors(formset, field: str) -> dict:
    errors = {}
    for index, row_errors in enumerate(formset.errors):
        for name, messages in row_errors.items():
            errors[f"{field}.{index}.{'non_field_errors' if name == '__all__' else name}"] = [str(message) for message in messages]
    if non_form := formset.non_form_errors():
        errors[field] = [str(message) for message in non_form]
    return errors


def _bound_forms(club, instance, payload: s.ProductFormIn, image: UploadedFile | None, *, variants_raw=None, tiers_raw=None):
    """The ProductForm and the variant / tier formsets for this payload, as the web builds them."""
    values = payload.model_dump(exclude={"clear_image", "variants", "tiers"})
    data = post_data(values, single={"category_id": "category", "season_id": "season"}, checkboxes=PRODUCT_CHECKBOXES)
    files = {}
    if image is not None:
        files["image"] = validate_upload(image, field="image", content_types=IMAGE_TYPES)
    elif payload.clear_image:
        data["image-clear"] = "on"
    form = ProductForm(data, files or None, club=club, instance=instance)
    variants = ProductVariantFormSet(_formset_data("variants", variants_raw or []), prefix="variants")
    tiers = ProductRegistrantDiscountTierFormSet(_formset_data("tiers", tiers_raw or []), prefix="tiers")
    return form, variants, tiers


def _check(form, *formsets) -> None:
    fields = {}
    valid = form.is_valid()
    if not valid:
        fields.update(form_error(form, PRODUCT_ALIASES).fields)
    for name, formset in formsets:
        if not formset.is_valid():
            fields.update(_formset_errors(formset, name))
    if fields:
        raise ApiError(422, "validation_error", fields=fields)


# --- shop switch --------------------------------------------------------------------------------


@router.get(PREFIX, response={200: s.ShopStateOut, **ERRORS}, summary="Is the shop open")
def shop_state(request):
    """The open/closed switch members' ordering depends on. Shop managers."""
    return {"shop_open": _club(request).shop_open}


@router.post(PREFIX + "/toggle", response={200: s.ShopStateOut, **ERRORS}, summary="Open or close the shop")
def shop_toggle(request):
    """Flips the switch and answers the new state. Honours `Idempotency-Key`. Shop managers."""
    club = _club(request)
    return idempotent(request, lambda: (200, {"shop_open": toggle_shop_open(club)}))


# --- categories ---------------------------------------------------------------------------------


def _save_category(club, instance: ProductCategory, name: str) -> ProductCategory:
    form = ProductCategoryForm({"name": name}, instance=instance)
    if not form.is_valid():
        raise form_error(form)
    clean = form.cleaned_data["name"]
    if ProductCategory.objects.filter(club=club, name=clean).exclude(pk=instance.pk).exists():
        raise ApiError(422, "validation_error", fields={"name": [str(_("A category with this name already exists."))]})
    instance.club = club
    return form.save()


@router.get(PREFIX + "/categories", response={200: s.CategoryListOut, **ERRORS}, summary="Product categories")
def list_categories(request):
    """All of the club's categories by name, with how many products each holds. Shop managers."""
    club = _club(request)
    categories = ProductCategory.objects.filter(club=club).annotate(n=Count("products"))
    return {"items": [_category_out(category, category.n) for category in categories]}


@router.post(PREFIX + "/categories", response={201: s.CategoryOut, **ERRORS, **INVALID}, summary="Add a category")
def create_category(request, payload: s.CategoryIn):
    """422 on `name` when blank or already taken. Honours `Idempotency-Key`. Shop managers."""
    club = _club(request)

    def work():
        category = _save_category(club, ProductCategory(club=club), payload.name)
        return 201, _category_out(category, 0)

    return idempotent(request, work)


@router.patch(PREFIX + "/categories/{uuid:category_id}", response={200: s.CategoryOut, **ERRORS, **INVALID}, summary="Rename a category")
def update_category(request, category_id: uuid.UUID, payload: s.CategoryPatch):
    """System categories (Player / Volunteer) can be renamed too, as on the web. Shop managers."""
    club = _club(request)
    category = _category(club, category_id)
    _save_category(club, category, payload.name if payload.name is not None else category.name)
    return _category_out(category)


@router.delete(PREFIX + "/categories/{uuid:category_id}", response={204: None, **ERRORS, **CONFLICT}, summary="Delete a category")
def remove_category(request, category_id: uuid.UUID):
    """Its products just lose the category. 409 `category_protected` for the system categories used by registration. Shop managers."""
    club = _club(request)
    category = _category(club, category_id)
    try:
        delete_category(category)
    except CategoryProtected as error:
        raise ApiError(409, "category_protected", str(error)) from None
    return 204, None


# --- products -----------------------------------------------------------------------------------


@router.get(PREFIX + "/products", response={200: s.ProductListOut, **ERRORS}, summary="The club's products")
def list_products(request, category_id: uuid.UUID | None = None, product_type: str | None = None, is_active: bool | None = None, q: str | None = None, cursor: str | None = None, limit: int | None = None):
    """By name, keyset paged, with variants, tiers, units sold and stock. Filter by category, type, active flag or a name search. Shop managers."""
    club = _club(request)
    products = Product.objects.filter(club=club).select_related("category").prefetch_related("variants", "registrant_discount_tiers")
    if category_id:
        products = products.filter(category_id=category_id)
    if product_type:
        products = products.filter(product_type=product_type)
    if is_active is not None:
        products = products.filter(is_active=is_active)
    if q:
        products = products.filter(name__icontains=q)
    page = paginate_keyset(products, fields=["name", "pk"], cursor=cursor, limit=limit)
    annotate_sold_and_stock(club, page.items)
    return {"items": [_product_out(request, product) for product in page.items], "next_cursor": page.next_cursor}


@router.post(PREFIX + "/products", response={201: s.ProductOut, **ERRORS, **UPLOAD_ERRORS}, summary="Add a product")
def create_product_endpoint(request, payload: Form[s.ProductFormIn], image: File[UploadedFile] = None):
    """Multipart: the product fields, an optional `image`, `variants` (JSON list of `{name, price?}`) and `tiers`
    (JSON list of registrant-discount tiers). A membership or event-fee product without variants gets one named
    after itself. Honours `Idempotency-Key`. Shop managers."""
    club = _club(request)

    def work():
        form, variants, tiers = _bound_forms(club, Product(club=club), payload, image, variants_raw=_json_rows(payload.variants, "variants"), tiers_raw=_json_rows(payload.tiers, "tiers"))
        _check(form, ("variants", variants), ("tiers", tiers))
        product = create_product(form, club, variant_rows=variants.cleaned_data, tier_rows=tiers.cleaned_data)
        return 201, _fresh_product_out(request, club, product.pk, detail=True)

    return idempotent(request, work)


@router.get(PREFIX + "/products/{uuid:product_id}", response={200: s.ProductOut, **ERRORS}, summary="One product")
def get_product(request, product_id: uuid.UUID):
    """With units sold per variant. Shop managers."""
    club = _club(request)
    _product(club, product_id)
    return _fresh_product_out(request, club, product_id, detail=True)


@router.post(PREFIX + "/products/{uuid:product_id}", response={200: s.ProductOut, **ERRORS, **UPLOAD_ERRORS}, summary="Edit a product")
def update_product_endpoint(request, product_id: uuid.UUID, payload: Form[s.ProductFormIn], image: File[UploadedFile] = None):
    """Multipart POST (all product fields are replaced, as on the web form): a new `image` replaces the old one,
    `clear_image=true` removes it, no image part keeps it. `tiers`, when sent, replaces all registrant-discount tiers;
    omitted keeps them. Variants have their own endpoints. Shop managers."""
    club = _club(request)
    product = _product(club, product_id)
    tiers_raw = _json_rows(payload.tiers, "tiers")
    form, _variants, tiers = _bound_forms(club, product, payload, image, tiers_raw=tiers_raw)
    _check(form, ("tiers", tiers))
    update_product(form, tier_rows=tiers.cleaned_data if tiers_raw is not None else None)
    return _fresh_product_out(request, club, product_id, detail=True)


@router.post(PREFIX + "/products/{uuid:product_id}/toggle-active", response={200: s.ProductOut, **ERRORS}, summary="Activate or deactivate a product")
def toggle_product(request, product_id: uuid.UUID):
    """Products are never deleted (orders reference them); deactivating hides one from members. Honours `Idempotency-Key`. Shop managers."""
    club = _club(request)

    def work():
        product = _product(club, product_id)
        toggle_active(product)
        return 200, _fresh_product_out(request, club, product_id, detail=True)

    return idempotent(request, work)


# --- variants -----------------------------------------------------------------------------------


def _save_variant(product: Product, instance: ProductVariant, *, name: str, price: Decimal | None, is_active: bool) -> ProductVariant:
    data = post_data({"name": name, "price": price, "is_active": is_active}, checkboxes=("is_active",))
    form = ProductVariantForm(data, instance=instance)
    if not form.is_valid():
        raise form_error(form)
    if ProductVariant.objects.filter(product=product, name=form.cleaned_data["name"]).exclude(pk=instance.pk).exists():
        raise ApiError(422, "validation_error", fields={"name": [str(_("This product already has an option with this name."))]})
    instance.product = product
    return form.save()


@router.post(PREFIX + "/products/{uuid:product_id}/variants", response={201: s.VariantOut, **ERRORS, **INVALID}, summary="Add a variant")
def create_variant(request, product_id: uuid.UUID, payload: s.VariantIn):
    """422 on `name` when it repeats another option of the product. Honours `Idempotency-Key`. Shop managers."""
    club = _club(request)

    def work():
        product = _product(club, product_id)
        variant = _save_variant(product, ProductVariant(product=product), name=payload.name, price=payload.price, is_active=payload.is_active)
        return 201, _variant_out(variant)

    return idempotent(request, work)


@router.patch(PREFIX + "/products/{uuid:product_id}/variants/{uuid:variant_id}", response={200: s.VariantOut, **ERRORS, **INVALID}, summary="Edit a variant")
def update_variant(request, product_id: uuid.UUID, variant_id: uuid.UUID, payload: s.VariantPatch):
    """Send only the fields to change (`price: null` falls back to the product's price). Shop managers."""
    club = _club(request)
    variant = _variant(club, product_id, variant_id)
    values = {"name": variant.name, "price": variant.price, "is_active": variant.is_active, **payload.model_dump(exclude_unset=True)}
    if values["name"] is None or values["is_active"] is None:
        raise ApiError(422, "validation_error", fields={"name" if values["name"] is None else "is_active": [str(_("This field is required."))]})
    return _variant_out(_save_variant(variant.product, variant, **values))


@router.post(PREFIX + "/products/{uuid:product_id}/variants/{uuid:variant_id}/toggle-active", response={200: s.VariantOut, **ERRORS}, summary="Activate or deactivate a variant")
def toggle_variant(request, product_id: uuid.UUID, variant_id: uuid.UUID):
    """Honours `Idempotency-Key`. Shop managers."""
    club = _club(request)

    def work():
        variant = _variant(club, product_id, variant_id)
        toggle_active(variant)
        return 200, _variant_out(variant)

    return idempotent(request, work)


# --- inventory ----------------------------------------------------------------------------------


def _row_out(row) -> dict:
    return {
        "product_id": row.product.pk,
        "product_name": row.product.name,
        "variant_id": row.variant.pk if row.variant else None,
        "variant_name": row.variant.name if row.variant else None,
        "is_active": row.product.is_active and (row.variant.is_active if row.variant else True),
        "on_hand": row.on_hand,
        "committed": row.committed,
    }


@router.get(PREFIX + "/inventory", response={200: s.InventoryOut, **ERRORS}, summary="Stock per product and variant")
def inventory(request):
    """One row per stock-keeping combination of the inventory-managed products: what is on hand and how much is
    on open orders. Not paged (the web screen is not either). Shop managers."""
    rows = inventory_overview(_club(request))
    return {"items": [_row_out(row) for row in rows], "out_of_stock_count": sum(1 for row in rows if row.on_hand <= 0)}


@router.post(PREFIX + "/inventory/receive", response={200: s.ReceiveOut, **ERRORS, **INVALID}, summary="Take a delivery into stock")
def receive(request, payload: s.ReceiveIn):
    """Adds each item's `quantity` to its stock. Every item must be a row of `GET /manage/shop/inventory`
    (`variant_id` null for a product without variants) and appear once, otherwise 422 on `items.<n>` and nothing
    is booked. Honours `Idempotency-Key`. Shop managers."""
    club = _club(request)

    def work():
        rows = {(row.product.pk, row.variant.pk if row.variant else None): row for row in inventory_overview(club)}
        receipts, errors = {}, {}
        for index, item in enumerate(payload.items):
            key = (item.product_id, item.variant_id)
            row = rows.get(key)
            if row is None:
                errors[f"items.{index}"] = [str(_("This is not an inventory-managed product or option of this club."))]
            elif key in receipts or (row.product, row.variant) in receipts:
                errors[f"items.{index}"] = [str(_("This item is listed twice."))]
            else:
                receipts[(row.product, row.variant)] = item.quantity
        if errors:
            raise ApiError(422, "validation_error", fields=errors)
        with transaction.atomic():
            movements = receive_stock(club, receipts, note=payload.note.strip(), user=request.user)
            if len(movements) != len(receipts):
                raise ApiError(422, "validation_error", fields={"items": [str(_("Some of these items can't be stocked (an option that is switched off)."))]})
        return 200, {"rows": len(movements), "units": sum(movement.quantity for movement in movements)}

    return idempotent(request, work)


def _adjust(request, product_id: uuid.UUID, variant_id: uuid.UUID | None, payload: s.AdjustIn):
    club = _club(request)

    def work():
        product = Product.objects.filter(club=club, inventory_managed=True, pk=product_id).first()
        if product is None:
            raise _not_found()
        variant = None
        if variant_id:
            variant = product.variants.filter(pk=variant_id).first()
            if variant is None:
                raise _not_found()
        try:
            movement = set_stock(club, product, variant, payload.counted, note=payload.note.strip(), user=request.user)
        except InventoryError as error:
            raise ApiError(422, "validation_error", fields={"counted": [str(error)]}) from None
        return 200, {"changed": movement is not None, "on_hand": stock_for(product, variant)}

    return idempotent(request, work)


@router.post(PREFIX + "/inventory/{uuid:product_id}/adjust", response={200: s.AdjustOut, **ERRORS, **INVALID}, summary="Recount a product's stock")
def adjust_product(request, product_id: uuid.UUID, payload: s.AdjustIn):
    """Sets the on-hand count to `counted` (a recount, breakage); the difference is booked as an adjustment.
    404 for a product that isn't inventory managed. Honours `Idempotency-Key`. Shop managers."""
    return _adjust(request, product_id, None, payload)


@router.post(PREFIX + "/inventory/{uuid:product_id}/{uuid:variant_id}/adjust", response={200: s.AdjustOut, **ERRORS, **INVALID}, summary="Recount one variant's stock")
def adjust_variant(request, product_id: uuid.UUID, variant_id: uuid.UUID, payload: s.AdjustIn):
    """As the product recount, for one option. Shop managers."""
    return _adjust(request, product_id, variant_id, payload)


@router.get(PREFIX + "/inventory/history", response={200: s.MovementListOut, **ERRORS}, summary="Every stock movement")
def inventory_history(request, product_id: uuid.UUID | None = None, cursor: str | None = None, limit: int | None = None):
    """Deliveries, recounts, what orders took and what cancelled orders gave back, newest first; optionally for one product. Shop managers."""
    club = _club(request)
    movements = InventoryMovement.objects.filter(product__club=club).select_related("product", "variant", "created_by")
    if product_id:
        movements = movements.filter(product_id=product_id)
    page = paginate(movements, sort_field="-created", cursor=cursor, limit=limit)
    return {
        "items": [
            {
                "id": movement.pk,
                "product_id": movement.product_id,
                "product_name": movement.product.name,
                "variant_id": movement.variant_id,
                "variant_name": movement.variant.name if movement.variant_id else None,
                "quantity": movement.quantity,
                "reason": movement.reason,
                "note": movement.note,
                "order_id": movement.order_id,
                "created_by": (movement.created_by.get_full_name() or movement.created_by.email) if movement.created_by_id else None,
                "created": movement.created,
            }
            for movement in page.items
        ],
        "next_cursor": page.next_cursor,
    }
