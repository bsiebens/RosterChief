"""Categories, products, variants and their images; and discount codes. Writes need the matching shop permission."""

import uuid

from django.db import transaction
from django.db.models import Count

from formbuilder.services.management import Conflict
from members.services import access
from members.services.errors import Invalid
from shop.models import Discount, DiscountType, InventoryMovement, Product, ProductCategory, ProductVariant

MAX_IMAGE_BYTES = 10 * 1024 * 1024
IMAGE_FORMATS = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}
PRODUCT_FIELDS = ("name", "category", "description", "price", "product_type", "is_active", "is_public", "personalization_enabled", "inventory_managed")


def _check_price(price, field="price"):
    if price is not None and price < 0:
        raise Invalid("The price can't be negative.", {field: ["Can't be negative."]})


# --- categories ---------------------------------------------------------------------------------------------------


def categories():
    return ProductCategory.objects.annotate(product_count=Count("products"))


def _category_name(name, exclude=None):
    name = (name or "").strip()
    if not name:
        raise Invalid("A name is required.", {"name": ["Required."]})
    if ProductCategory.objects.filter(name__iexact=name).exclude(pk=getattr(exclude, "pk", None)).exists():
        raise Invalid("That category already exists.", {"name": ["Already exists."]})
    return name


def create_category(actor, name) -> ProductCategory:
    access.require_manage_shop_products(actor)
    return ProductCategory.objects.create(name=_category_name(name))


def update_category(actor, category, name) -> ProductCategory:
    access.require_manage_shop_products(actor)
    category.name = _category_name(name, category)
    category.save()
    return category


def delete_category(actor, category) -> None:
    access.require_manage_shop_products(actor)
    category.delete()


# --- products -----------------------------------------------------------------------------------------------------


def products_queryset():
    return Product.objects.select_related("category").prefetch_related("variants")


def _validate_product(product):
    _check_price(product.price)
    if product.inventory_managed and product.product_type != Product.ProductType.MERCHANDISE:
        raise Invalid("Only merchandise can be inventory managed.", {"inventory_managed": ["Only merchandise can be inventory managed."]})
    if product.inventory_managed is False and product.pk and InventoryMovement.objects.filter(product=product).exists():
        pass  # switching stock keeping off keeps the history


@transaction.atomic
def create_product(actor, *, variants=(), **fields) -> Product:
    access.require_manage_shop_products(actor)
    product = Product(**{k: v for k, v in fields.items() if k in PRODUCT_FIELDS})
    if not product.name.strip():
        raise Invalid("A name is required.", {"name": ["Required."]})
    _validate_product(product)
    product.save()
    for i, v in enumerate(variants):
        _add_variant(product, ordering=i, **v)
    return product


@transaction.atomic
def update_product(actor, product, **fields) -> Product:
    access.require_manage_shop_products(actor)
    for k, v in fields.items():
        if k in PRODUCT_FIELDS:
            setattr(product, k, v)
    if not product.name.strip():
        raise Invalid("A name is required.", {"name": ["Required."]})
    _validate_product(product)
    product.save()
    return product


def delete_product(actor, product) -> None:
    access.require_manage_shop_products(actor)
    if product.order_lines.exists():
        raise Conflict("This product has orders; deactivate it instead.", "has_orders")
    if product.inventory_movements.exists():
        raise Conflict("This product has stock history; deactivate it instead.", "has_stock_history")
    product.delete()


def _verify_image(image) -> str:
    from PIL import Image, UnidentifiedImageError

    if image.size > MAX_IMAGE_BYTES:
        raise Invalid("The picture is larger than 10 MB.", {"image": ["The picture is larger than 10 MB."]})
    try:
        with Image.open(image) as img:
            fmt = img.format
            img.verify()
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as e:
        raise Invalid("This is not a valid image.", {"image": ["This is not a valid image."]}) from e
    if fmt not in IMAGE_FORMATS:
        raise Invalid("Use a JPEG, PNG or WebP picture.", {"image": ["Use a JPEG, PNG or WebP picture."]})
    image.seek(0)
    return IMAGE_FORMATS[fmt]


def set_image(actor, product, image) -> Product:
    access.require_manage_shop_products(actor)
    ext = _verify_image(image)
    if product.image:
        product.image.delete(save=False)
    product.image.save(f"photo-{uuid.uuid4().hex[:12]}.{ext}", image, save=True)
    return product


def clear_image(actor, product) -> Product:
    access.require_manage_shop_products(actor)
    if product.image:
        product.image.delete(save=True)
    return product


# --- variants -----------------------------------------------------------------------------------------------------


def _add_variant(product, *, name, price=None, is_active=True, ordering=0) -> ProductVariant:
    name = (name or "").strip()
    if not name:
        raise Invalid("A name is required.", {"name": ["Required."]})
    _check_price(price)
    if product.variants.filter(name__iexact=name).exists():
        raise Invalid("That option already exists.", {"name": ["Already exists."]})
    return ProductVariant.objects.create(product=product, name=name, price=price, is_active=is_active, ordering=ordering)


def create_variant(actor, product, **fields) -> ProductVariant:
    access.require_manage_shop_products(actor)
    fields.setdefault("ordering", max((v.ordering for v in product.variants.all()), default=-1) + 1)
    return _add_variant(product, **fields)


def update_variant(actor, variant, **fields) -> ProductVariant:
    access.require_manage_shop_products(actor)
    if "name" in fields:
        name = (fields["name"] or "").strip()
        if not name:
            raise Invalid("A name is required.", {"name": ["Required."]})
        if variant.product.variants.filter(name__iexact=name).exclude(pk=variant.pk).exists():
            raise Invalid("That option already exists.", {"name": ["Already exists."]})
        fields["name"] = name
    if "price" in fields:
        _check_price(fields["price"])
    for k, v in fields.items():
        setattr(variant, k, v)
    variant.save()
    return variant


def delete_variant(actor, variant) -> None:
    access.require_manage_shop_products(actor)
    if variant.order_lines.exists():
        raise Conflict("This option has orders; deactivate it instead.", "has_orders")
    if variant.inventory_movements.exists():
        raise Conflict("This option has stock history; deactivate it instead.", "has_stock_history")
    variant.delete()


# --- discounts ----------------------------------------------------------------------------------------------------

DISCOUNT_FIELDS = ("name", "description", "code", "discount_type", "discount_amount", "is_active", "valid_from", "valid_until", "max_uses", "once_per_member")


def _validate_discount(d):
    errors = {}
    if not d.name.strip():
        errors["name"] = ["Required."]
    if not (d.code or "").strip():
        errors["code"] = ["Required."]
    elif Discount.objects.filter(code__iexact=d.code.strip()).exclude(pk=d.pk).exists():
        errors["code"] = ["Already in use."]
    if d.discount_type == DiscountType.PERCENTAGE and not (0 < d.discount_amount <= 100):
        errors["discount_amount"] = ["A percentage must be between 0 and 100."]
    elif d.discount_type == DiscountType.FIXED_AMOUNT and d.discount_amount <= 0:
        errors["discount_amount"] = ["Must be greater than zero."]
    if d.valid_from and d.valid_until and d.valid_until < d.valid_from:
        errors["valid_until"] = ["Must be on or after the first valid day."]
    if errors:
        raise Invalid("Please correct the highlighted fields.", errors)


def create_discount(actor, **fields) -> Discount:
    access.require_manage_shop_discounts(actor)
    d = Discount(**{k: v for k, v in fields.items() if k in DISCOUNT_FIELDS})
    _validate_discount(d)
    d.save()
    return d


def update_discount(actor, discount, **fields) -> Discount:
    access.require_manage_shop_discounts(actor)
    for k, v in fields.items():
        if k in DISCOUNT_FIELDS:
            setattr(discount, k, v)
    _validate_discount(discount)
    discount.save()
    return discount


def delete_discount(actor, discount) -> None:
    access.require_manage_shop_discounts(actor)
    if discount.applied_discounts.exists():
        raise Conflict("This discount has been used on orders; deactivate it instead.", "has_orders")
    discount.delete()
