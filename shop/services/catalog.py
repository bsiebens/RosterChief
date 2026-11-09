"""Categories, products, variants and their images; and discount codes. Writes need the matching shop permission."""

import os
import uuid

from django.db import transaction
from django.db.models import Count, F

from formbuilder.services.management import Conflict
from members.services import access
from members.services.errors import Invalid
from shop.models import Discount, DiscountType, InventoryMovement, Product, ProductAttachment, ProductCategory, ProductImage, ProductVariant

MAX_IMAGE_BYTES = 10 * 1024 * 1024
IMAGE_FORMATS = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}
PRODUCT_FIELDS = (
    "name", "category", "description", "price", "product_type", "is_active", "is_public", "personalization_enabled", "inventory_managed",
    "season", "early_bird_discount_type", "early_bird_discount_amount", "early_bird_deadline", "registrant_discount_scope",
)  # fmt: skip


def _check_price(price, field="price"):
    if price is not None and price < 0:
        raise Invalid("The price can't be negative.", {field: ["Can't be negative."]})


# --- categories ---------------------------------------------------------------------------------------------------


def categories():
    """The fixed Player and Volunteer categories first, then the club's own by name."""
    return ProductCategory.objects.annotate(product_count=Count("products")).order_by(F("system_key").asc(nulls_last=True), "name")


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
    if category.is_system:
        raise Invalid("The Player and Volunteer categories are fixed and cannot be renamed.", {"name": ["This is a system category and cannot be renamed."]}, code="system_category")
    category.name = _category_name(name, category)
    category.save()
    return category


def delete_category(actor, category) -> None:
    access.require_manage_shop_products(actor)
    if category.is_system:
        raise Conflict("The Player and Volunteer categories are fixed and cannot be deleted.", "system_category")
    category.delete()


# --- products -----------------------------------------------------------------------------------------------------


def products_queryset():
    return Product.objects.select_related("category", "season").prefetch_related("variants", "images", "attachments", "registrant_tiers")


def _validate_product(product):
    _check_price(product.price)
    if product.inventory_managed and product.product_type != Product.ProductType.MERCHANDISE:
        raise Invalid("Only merchandise can be inventory managed.", {"inventory_managed": ["Only merchandise can be inventory managed."]})
    if product.inventory_managed is False and product.pk and InventoryMovement.objects.filter(product=product).exists():
        pass  # switching stock keeping off keeps the history
    if product.product_type != Product.ProductType.MEMBERSHIP and (product.season_id or product.early_bird_discount_type):
        raise Invalid("Season and early-bird discount are only for membership products.", {"season_id": ["Only for membership products."]})
    if product.early_bird_discount_type:
        if product.early_bird_discount_amount is None or product.early_bird_discount_amount <= 0:
            raise Invalid("The early-bird discount needs an amount.", {"early_bird_discount_amount": ["Must be more than zero."]})
        if product.early_bird_deadline is None:
            raise Invalid("The early-bird discount needs a deadline.", {"early_bird_deadline": ["Required."]})
        if product.early_bird_discount_type == "percentage" and product.early_bird_discount_amount > 100:
            raise Invalid("A percentage cannot be above 100.", {"early_bird_discount_amount": ["At most 100."]})
    if not product.early_bird_discount_type:
        product.early_bird_discount_amount, product.early_bird_deadline = 0, None


def set_registrant_tiers(product, tiers) -> None:
    """Replace the product's registrant discount tiers (``[{min_registrants, discount_type, discount_amount}]``); membership products only."""
    from shop.models import ProductRegistrantDiscountTier

    tiers = list(tiers or [])
    if tiers and product.product_type != Product.ProductType.MEMBERSHIP:
        raise Invalid("Registrant discounts are only for membership products.", {"registrant_tiers": ["Only for membership products."]})
    seen = set()
    for i, tier in enumerate(tiers):
        n, kind, amount = tier["min_registrants"], tier["discount_type"], tier["discount_amount"]
        errors = {}
        if n < 2 or n in seen:
            errors[f"registrant_tiers.{i}.min_registrants"] = ["Must be 2 or more and unique."]
        if kind not in ("percentage", "fixed_amount"):
            errors[f"registrant_tiers.{i}.discount_type"] = ["Invalid choice."]
        if amount <= 0 or (kind == "percentage" and amount > 100):
            errors[f"registrant_tiers.{i}.discount_amount"] = ["Invalid amount."]
        if errors:
            raise Invalid("Check the registrant discounts.", errors)
        seen.add(n)
    product.registrant_tiers.all().delete()
    ProductRegistrantDiscountTier.objects.bulk_create([ProductRegistrantDiscountTier(product=product, **t) for t in tiers])


@transaction.atomic
def create_product(actor, *, variants=(), registrant_tiers=None, **fields) -> Product:
    access.require_manage_shop_products(actor)
    product = Product(**{k: v for k, v in fields.items() if k in PRODUCT_FIELDS})
    if not product.name.strip():
        raise Invalid("A name is required.", {"name": ["Required."]})
    _validate_product(product)
    product.save()
    for i, v in enumerate(variants):
        _add_variant(product, ordering=i, **v)
    if registrant_tiers:
        set_registrant_tiers(product, registrant_tiers)
    return product


@transaction.atomic
def update_product(actor, product, **fields) -> Product:
    access.require_manage_shop_products(actor)
    tiers = fields.pop("registrant_tiers", None)
    for k, v in fields.items():
        if k in PRODUCT_FIELDS:
            setattr(product, k, v)
    if not product.name.strip():
        raise Invalid("A name is required.", {"name": ["Required."]})
    _validate_product(product)
    product.save()
    if tiers is not None:
        set_registrant_tiers(product, tiers)
    elif product.product_type != Product.ProductType.MEMBERSHIP:
        product.registrant_tiers.all().delete()  # a product no longer a membership keeps no registration pricing
    return product


def delete_product(actor, product) -> None:
    access.require_manage_shop_products(actor)
    if product.order_lines.exists():
        raise Conflict("This product has orders; deactivate it instead.", "has_orders")
    if product.inventory_movements.exists():
        raise Conflict("This product has stock history; deactivate it instead.", "has_stock_history")
    product.delete()


MAX_IMAGES = 12
MAX_ATTACHMENTS = 10
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024


def _verify_image(image, field: str = "images") -> str:
    from PIL import Image, UnidentifiedImageError

    name = getattr(image, "name", "") or "picture"
    if image.size > MAX_IMAGE_BYTES:
        message = f"“{name}” is larger than {MAX_IMAGE_BYTES // (1024 * 1024)} MB."
        raise Invalid(message, {field: [message]})
    try:
        with Image.open(image) as img:
            fmt = img.format
            img.verify()
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as e:
        message = f"“{name}” is not a valid image."
        raise Invalid(message, {field: [message]}) from e
    if fmt not in IMAGE_FORMATS:
        message = f"“{name}” is not a JPEG, PNG or WebP picture."
        raise Invalid(message, {field: [message]})
    image.seek(0)
    return IMAGE_FORMATS[fmt]


# --- pictures (a gallery with one main picture) -----------------------------------------------------------------------------


def _image_or_404(product, image_id) -> ProductImage:
    image = product.images.filter(pk=image_id).first()
    if image is None:
        raise Conflict("That picture is not on this product.", "not_found")
    return image


@transaction.atomic
def add_images(actor, product, files) -> list[ProductImage]:
    """Add one or more pictures (all or nothing); the first picture of a product becomes its main one."""
    access.require_manage_shop_products(actor)
    files = list(files)
    if not files:
        raise Invalid("Choose at least one picture.", {"images": ["Choose at least one picture."]})
    existing = product.images.count()
    if existing + len(files) > MAX_IMAGES:
        message = f"A product can have at most {MAX_IMAGES} pictures (it has {existing})."
        raise Invalid(message, {"images": [message]})
    extensions = [_verify_image(f) for f in files]  # check everything before anything is written
    last = product.images.order_by("-ordering").values_list("ordering", flat=True).first()
    created = []
    for n, (upload, ext) in enumerate(zip(files, extensions, strict=True), start=1):
        image = ProductImage(product=product, ordering=(last + n) if last is not None else n - 1, is_main=existing == 0 and n == 1)
        image.image.save(f"photo-{uuid.uuid4().hex[:12]}.{ext}", upload, save=False)
        image.save()
        created.append(image)
    return created


@transaction.atomic
def set_main_image(actor, product, image_id) -> None:
    access.require_manage_shop_products(actor)
    image = _image_or_404(product, image_id)
    product.images.exclude(pk=image.pk).filter(is_main=True).update(is_main=False)  # one at a time: the unique constraint allows a single main
    if not image.is_main:
        image.is_main = True
        image.save(update_fields=["is_main", "modified"])


def update_image(actor, product, image_id, *, alt: str) -> None:
    access.require_manage_shop_products(actor)
    image = _image_or_404(product, image_id)
    image.alt = alt.strip()[:255]
    image.save(update_fields=["alt", "modified"])


@transaction.atomic
def reorder_images(actor, product, ids) -> None:
    """``ids`` lists every picture of the product in the wanted order."""
    access.require_manage_shop_products(actor)
    current = {img.pk: img for img in product.images.all()}
    if len(ids) != len(set(ids)) or set(ids) != set(current):
        message = "Give every picture of the product exactly once."
        raise Invalid(message, {"ids": [message]})
    for position, pk in enumerate(ids):
        if current[pk].ordering != position:
            current[pk].ordering = position
            current[pk].save(update_fields=["ordering", "modified"])


@transaction.atomic
def delete_image(actor, product, image_id) -> None:
    """Delete a picture; when it was the main one the next picture takes over."""
    access.require_manage_shop_products(actor)
    image = _image_or_404(product, image_id)
    was_main = image.is_main
    image.delete()
    if was_main:
        nxt = product.images.order_by("ordering", "created").first()
        if nxt is not None:
            nxt.is_main = True
            nxt.save(update_fields=["is_main", "modified"])


# --- attachments (size guides and other files) -------------------------------------------------------------------------------


def _attachment_or_404(product, attachment_id) -> ProductAttachment:
    attachment = product.attachments.filter(pk=attachment_id).first()
    if attachment is None:
        raise Conflict("That file is not on this product.", "not_found")
    return attachment


def _validate_attachment(uploaded) -> str:
    """The same file rules as event attachments (documents and pictures only, never HTML or scripts); returns the display name."""
    from events.services import attachments as rules

    name = rules.display_name(getattr(uploaded, "name", ""))
    stem, extension = os.path.splitext(name)
    if not stem or not name:
        raise Invalid("The file has no usable name.", {"files": ["The file has no usable name."]})
    if extension.lower() not in rules.ALLOWED_EXTENSIONS:
        message = f"“{name}” is not an allowed file type."
        raise Invalid(message, {"files": [message]})
    size = uploaded.size or 0
    if size <= 0:
        raise Invalid(f"“{name}” is empty.", {"files": [f"“{name}” is empty."]})
    if size > MAX_ATTACHMENT_BYTES:
        message = f"“{name}” is larger than {MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB."
        raise Invalid(message, {"files": [message]})
    return name


@transaction.atomic
def add_attachments(actor, product, files, *, title: str = "") -> list[ProductAttachment]:
    """Add one or more files (all or nothing). ``title`` names a single file; otherwise each is titled by its file name."""
    access.require_manage_shop_products(actor)
    files = list(files)
    if not files:
        raise Invalid("Choose at least one file.", {"files": ["Choose at least one file."]})
    existing = product.attachments.count()
    if existing + len(files) > MAX_ATTACHMENTS:
        message = f"A product can have at most {MAX_ATTACHMENTS} files (it has {existing})."
        raise Invalid(message, {"files": [message]})
    names = [_validate_attachment(f) for f in files]
    created = []
    for upload, name in zip(files, names, strict=True):
        label = title.strip() if len(files) == 1 and title.strip() else os.path.splitext(name)[0]
        attachment = ProductAttachment(product=product, title=label[:255], name=name, size=upload.size or 0, content_type=(getattr(upload, "content_type", "") or "")[:100])
        attachment.file.save(name, upload, save=False)
        attachment.save()
        created.append(attachment)
    return created


def rename_attachment(actor, product, attachment_id, *, title: str) -> None:
    access.require_manage_shop_products(actor)
    attachment = _attachment_or_404(product, attachment_id)
    title = title.strip()
    if not title:
        raise Invalid("Enter a title.", {"title": ["Enter a title."]})
    attachment.title = title[:255]
    attachment.save(update_fields=["title", "modified"])


def delete_attachment(actor, product, attachment_id) -> None:
    access.require_manage_shop_products(actor)
    _attachment_or_404(product, attachment_id).delete()


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
