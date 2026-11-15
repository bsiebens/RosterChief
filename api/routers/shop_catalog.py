"""Shop categories, products, variants, product pictures (a gallery with one main picture) and product files such as a size guide
(``/shop/categories``, ``/shop/products``)."""

import uuid

from django.db.models import Q
from ninja import File, Form, Query, Router, UploadedFile
from ninja.errors import HttpError

from api.auth import AUTHENTICATORS
from api.routers.shop_common import access, gate
from api.schemas_shop_catalog import AttachmentTitleIn, CategoryIn, CategoryListOut, CategoryOut, ImageAltIn, ImageOrderIn, ProductDetail, ProductIn, ProductListOut, ProductPatch, VariantIn, VariantOut, VariantPatch
from shop.models import Product, ProductCategory, ProductVariant
from shop.services import catalog as svc
from shop.services import inventory

router = Router(auth=AUTHENTICATORS)


def _read(request):
    gate(request, access.require_view_shop_catalog)


def _write(request):
    gate(request, access.require_manage_shop_products)


def _levels(products):
    return inventory.stock_levels([p for p in products if p.inventory_managed])


def _variant(v, levels=None) -> dict:
    p = v.product
    if levels is None:
        levels = inventory.stock_levels([p]) if p.inventory_managed else {}
    stock = levels.get((p.pk, v.pk), 0) if p.inventory_managed else None
    open_for_orders = not p.ordering_windows or svc.next_ordering_window(p) is not None
    return {
        "id": v.pk, "product_id": p.pk, "name": v.name, "price": v.price, "effective_price": v.effective_price, "is_active": v.is_active,
        "ordering": v.ordering, "stock": stock, "in_stock": open_for_orders and (stock is None or stock > 0),
        "team_id": v.team_id, "team_name": v.team.name if v.team_id else None,
    }  # fmt: skip


def _item(p, levels) -> dict:
    window = svc.next_ordering_window(p)
    ordering_closed = bool(p.ordering_windows) and window is None
    variants = list(p.variants.all())
    active = [v for v in variants if v.is_active]
    if not p.inventory_managed:
        in_stock = True
    elif active:
        in_stock = any(levels.get((p.pk, v.pk), 0) > 0 for v in active)
    else:
        in_stock = levels.get((p.pk, None), 0) > 0
    return {
        "id": p.pk, "name": p.name, "slug": p.slug, "category": {"id": p.category.pk, "name": p.category.name, "system_key": p.category.system_key} if p.category else None,
        "description": p.description, "image_url": p.main_image.image.url if p.main_image else None, "price": p.price, "product_type": p.product_type,
        "is_active": p.is_active, "is_public": p.is_public, "personalization_enabled": p.personalization_enabled,
        "inventory_managed": p.inventory_managed, "variant_count": len(variants), "in_stock": in_stock and not ordering_closed, "created": p.created,
        "ordering_closed": ordering_closed, "next_ordering_window": window,
    }  # fmt: skip


def _detail(product_id) -> dict:
    p = svc.products_queryset().get(pk=product_id)
    levels = _levels([p])
    return {
        **_item(p, levels), "variants": [_variant(v, levels) for v in p.variants.all()], "order_line_count": p.order_lines.count(), "ordering_windows": p.ordering_windows,
        "team_id": p.team_id, "team_name": p.team.name if p.team_id else None,
        "season_id": p.season_id, "season_name": p.season.name if p.season else None, "early_bird_discount_type": p.early_bird_discount_type,
        "early_bird_discount_amount": p.early_bird_discount_amount, "early_bird_deadline": p.early_bird_deadline, "registrant_discount_scope": p.registrant_discount_scope,
        "registrant_tiers": [{"min_registrants": t.min_registrants, "discount_type": t.discount_type, "discount_amount": t.discount_amount} for t in p.registrant_tiers.all()],
        "images": [{"id": i.pk, "url": i.image.url, "is_main": i.is_main, "ordering": i.ordering, "alt": i.alt} for i in p.images.all()],
        "attachments": [{"id": a.pk, "title": a.title, "name": a.name, "url": a.file.url, "size": a.size, "content_type": a.content_type, "created": a.created} for a in p.attachments.all()],
    }  # fmt: skip


def _get_product(product_id) -> Product:
    p = Product.objects.filter(pk=product_id).first()
    if p is None:
        raise HttpError(404, "Not found.")
    return p


def _get_variant(product_id, variant_id) -> ProductVariant:
    v = ProductVariant.objects.select_related("product", "team").filter(pk=variant_id, product_id=product_id).first()
    if v is None:
        raise HttpError(404, "Not found.")
    return v


def _season(season_id):
    if season_id is None:
        return None
    from events.models import Season
    from members.services.errors import Invalid

    season = Season.objects.filter(pk=season_id).first()
    if season is None:
        raise Invalid("Unknown season.", {"season_id": ["Unknown season."]})
    return season


def _team(team_id):
    if team_id is None:
        return None
    from members.services.errors import Invalid
    from teams.models import Team

    team = Team.objects.filter(pk=team_id).first()
    if team is None:
        raise Invalid("Unknown team.", {"team_id": ["Unknown team."]})
    return team


def _category(category_id):
    if category_id is None:
        return None
    c = ProductCategory.objects.filter(pk=category_id).first()
    if c is None:
        from members.services.errors import Invalid

        raise Invalid("Unknown category.", {"category_id": ["Unknown category."]})
    return c


def _cat_out(c) -> dict:
    return {"id": c.pk, "name": c.name, "product_count": c.product_count, "system_key": c.system_key, "is_system": c.is_system}


# --- categories ---------------------------------------------------------------------------------------------------


@router.get("/categories", response=CategoryListOut, summary="List product categories", operation_id="list_shop_categories")
def list_categories(request):
    _read(request)
    qs = svc.categories()
    return {"count": qs.count(), "results": [_cat_out(c) for c in qs]}


@router.post("/categories", response={201: CategoryOut}, summary="Create a category", operation_id="create_shop_category")
def create_category(request, payload: CategoryIn):
    _write(request)
    c = svc.create_category(request.user, payload.name)
    return 201, _cat_out(svc.categories().get(pk=c.pk))


def _get_category(category_id):
    c = svc.categories().filter(pk=category_id).first()
    if c is None:
        raise HttpError(404, "Not found.")
    return c


@router.patch("/categories/{category_id}", response=CategoryOut, summary="Rename a category", operation_id="update_shop_category")
def update_category(request, category_id: uuid.UUID, payload: CategoryIn):
    _write(request)
    c = svc.update_category(request.user, _get_category(category_id), payload.name)
    return _cat_out(svc.categories().get(pk=c.pk))


@router.delete("/categories/{category_id}", response={204: None}, summary="Delete a category", operation_id="delete_shop_category")
def delete_category(request, category_id: uuid.UUID):
    _write(request)
    svc.delete_category(request.user, _get_category(category_id))
    return 204, None


# --- products -----------------------------------------------------------------------------------------------------


@router.get("/products", response=ProductListOut, summary="List products", operation_id="list_shop_products")
def list_products(
    request, q: str | None = None, category_id: uuid.UUID | None = None, product_type: str | None = None, active: bool | None = None,
    public: bool | None = None, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
):  # fmt: skip
    _read(request)
    qs = svc.products_queryset()
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(description__icontains=q))
    if category_id:
        qs = qs.filter(category_id=category_id)
    if product_type:
        qs = qs.filter(product_type=product_type)
    if active is not None:
        qs = qs.filter(is_active=active)
    if public is not None:
        qs = qs.filter(is_public=public)
    count = qs.count()
    rows = list(qs[offset : offset + limit])
    levels = _levels(rows)
    return {"count": count, "results": [_item(p, levels) for p in rows]}


@router.post("/products", response={201: ProductDetail}, summary="Create a product", operation_id="create_shop_product")
def create_product(request, payload: ProductIn):
    _write(request)
    data = payload.dict()
    category = _category(data.pop("category_id"))
    data["season"] = _season(data.pop("season_id"))
    data["team"] = _team(data.pop("team_id"))
    data["variants"] = [{**{k: x for k, x in row.items() if k != "team_id"}, "team": _team(row.get("team_id"))} for row in data.get("variants", [])]
    product = svc.create_product(request.user, category=category, **data)
    return 201, _detail(product.pk)


@router.get("/products/{product_id}", response=ProductDetail, summary="One product with its variants", operation_id="get_shop_product")
def get_product(request, product_id: uuid.UUID):
    _read(request)
    return _detail(_get_product(product_id).pk)


@router.patch("/products/{product_id}", response=ProductDetail, summary="Edit a product", operation_id="update_shop_product")
def update_product(request, product_id: uuid.UUID, payload: ProductPatch):
    _write(request)
    data = payload.dict(exclude_unset=True)
    if "category_id" in data:
        data["category"] = _category(data.pop("category_id"))
    if "season_id" in data:
        data["season"] = _season(data.pop("season_id"))
    if "team_id" in data:
        data["team"] = _team(data.pop("team_id"))
    data = {k: v for k, v in data.items() if v is not None or k in ("category", "season", "early_bird_deadline", "team")}
    svc.update_product(request.user, _get_product(product_id), **data)
    return _detail(product_id)


@router.delete("/products/{product_id}", response={204: None}, summary="Delete a product", operation_id="delete_shop_product")
def delete_product(request, product_id: uuid.UUID):
    _write(request)
    svc.delete_product(request.user, _get_product(product_id))
    return 204, None


@router.post("/products/{product_id}/images", response=ProductDetail, summary="Add pictures to the product", operation_id="add_shop_product_images")
def add_images(request, product_id: uuid.UUID, images: File[list[UploadedFile]]):
    """One or more JPEG/PNG/WebP pictures (10 MB each, 12 per product); the first picture becomes the main one. All or nothing."""
    _write(request)
    svc.add_images(request.user, _get_product(product_id), images)
    return _detail(product_id)


@router.post("/products/{product_id}/images/{image_id}/set-main", response=ProductDetail, summary="Make a picture the main one", operation_id="set_shop_product_main_image")
def set_main_image(request, product_id: uuid.UUID, image_id: uuid.UUID):
    _write(request)
    svc.set_main_image(request.user, _get_product(product_id), image_id)
    return _detail(product_id)


@router.put("/products/{product_id}/images/order", response=ProductDetail, summary="Set the order of the pictures", operation_id="reorder_shop_product_images")
def reorder_images(request, product_id: uuid.UUID, payload: ImageOrderIn):
    """``ids`` lists every picture of the product, once, in the wanted order."""
    _write(request)
    svc.reorder_images(request.user, _get_product(product_id), payload.ids)
    return _detail(product_id)


@router.patch("/products/{product_id}/images/{image_id}", response=ProductDetail, summary="Describe a picture", operation_id="update_shop_product_image")
def update_image(request, product_id: uuid.UUID, image_id: uuid.UUID, payload: ImageAltIn):
    _write(request)
    svc.update_image(request.user, _get_product(product_id), image_id, alt=payload.alt)
    return _detail(product_id)


@router.delete("/products/{product_id}/images/{image_id}", response=ProductDetail, summary="Remove a picture", operation_id="delete_shop_product_image")
def delete_image(request, product_id: uuid.UUID, image_id: uuid.UUID):
    """When the main picture is removed the next one takes over."""
    _write(request)
    svc.delete_image(request.user, _get_product(product_id), image_id)
    return _detail(product_id)


@router.post("/products/{product_id}/attachments", response=ProductDetail, summary="Add files to the product", operation_id="add_shop_product_attachments")
def add_attachments(request, product_id: uuid.UUID, files: File[list[UploadedFile]], title: Form[str] = ""):
    """Documents and pictures (PDF, Office/OpenDocument, txt/csv, images; 10 MB each, 10 per product), e.g. a size guide. ``title`` names a
    single file; otherwise each file is titled by its name. All or nothing."""
    _write(request)
    svc.add_attachments(request.user, _get_product(product_id), files, title=title)
    return _detail(product_id)


@router.patch("/products/{product_id}/attachments/{attachment_id}", response=ProductDetail, summary="Rename a file", operation_id="update_shop_product_attachment")
def update_attachment(request, product_id: uuid.UUID, attachment_id: uuid.UUID, payload: AttachmentTitleIn):
    _write(request)
    svc.rename_attachment(request.user, _get_product(product_id), attachment_id, title=payload.title)
    return _detail(product_id)


@router.delete("/products/{product_id}/attachments/{attachment_id}", response=ProductDetail, summary="Remove a file", operation_id="delete_shop_product_attachment")
def delete_attachment(request, product_id: uuid.UUID, attachment_id: uuid.UUID):
    _write(request)
    svc.delete_attachment(request.user, _get_product(product_id), attachment_id)
    return _detail(product_id)


# --- variants -----------------------------------------------------------------------------------------------------


@router.post("/products/{product_id}/variants", response={201: VariantOut}, summary="Add a variant", operation_id="create_shop_variant")
def create_variant(request, product_id: uuid.UUID, payload: VariantIn):
    _write(request)
    data = {k: x for k, x in payload.dict().items() if not (k == "ordering" and x is None) and k != "team_id"}
    v = svc.create_variant(request.user, _get_product(product_id), team=_team(payload.team_id), **data)
    return 201, _variant(v)


@router.patch("/products/{product_id}/variants/{variant_id}", response=VariantOut, summary="Edit a variant", operation_id="update_shop_variant")
def update_variant(request, product_id: uuid.UUID, variant_id: uuid.UUID, payload: VariantPatch):
    _write(request)
    data = payload.dict(exclude_unset=True)
    if "team_id" in data:
        data["team"] = _team(data.pop("team_id"))
    data = {k: v for k, v in data.items() if v is not None or k in ("price", "team")}
    return _variant(svc.update_variant(request.user, _get_variant(product_id, variant_id), **data))


@router.delete("/products/{product_id}/variants/{variant_id}", response={204: None}, summary="Delete a variant", operation_id="delete_shop_variant")
def delete_variant(request, product_id: uuid.UUID, variant_id: uuid.UUID):
    _write(request)
    svc.delete_variant(request.user, _get_variant(product_id, variant_id))
    return 204, None
