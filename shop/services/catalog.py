"""Catalogue maintenance shared by the /manage views and the management API.

The product screens used to hold this inline in management.views: the shop open/closed switch, the
product + variants + registrant-discount-tier write, the active toggles, the sold/stock figures on the
product list and the category delete rule. The views and api.panel.routers.shop_catalog both call it.
"""

from django.db import transaction
from django.utils.translation import gettext_lazy as _

from shop.models import Product, ProductCategory, ProductRegistrantDiscountTier, ProductVariant

from .inventory import stock_levels
from .stats import quantity_sold_by_product
from .variants import ensure_default_variant


class CategoryProtected(Exception):
    """A system category (registration_kind set) can't be deleted; the message is user-facing."""


def toggle_shop_open(club) -> bool:
    """Flip the club's shop open/closed switch (Club.shop_open); returns the new state."""
    club.shop_open = not club.shop_open
    club.save(update_fields=["shop_open"])
    return club.shop_open


def toggle_active(obj) -> bool:
    """Flip ``is_active`` of a product or variant (neither can be deleted once ordered); returns the new state."""
    obj.is_active = not obj.is_active
    obj.save(update_fields=["is_active"])
    return obj.is_active


def _create_tiers(product, tier_rows) -> None:
    ProductRegistrantDiscountTier.objects.bulk_create(
        [ProductRegistrantDiscountTier(product=product, min_registrants=row["min_registrants"], discount_type=row["discount_type"], discount_amount=row["discount_amount"]) for row in tier_rows if row.get("min_registrants")]
    )


@transaction.atomic
def create_product(form, club, *, variant_rows, tier_rows) -> Product:
    """Save a validated ProductForm plus its first variants and discount tiers in one step.
    The rows are the formsets' cleaned_data (blank rows are skipped)."""
    product = form.save(commit=False)
    product.club = club
    product.save()
    ProductVariant.objects.bulk_create([ProductVariant(product=product, name=row["name"], price=row.get("price")) for row in variant_rows if row.get("name")])
    ensure_default_variant(product)
    _create_tiers(product, tier_rows)
    return product


@transaction.atomic
def update_product(form, *, tier_rows=None) -> Product:
    """Save a validated ProductForm; ``tier_rows`` (cleaned formset rows) replace the product's whole
    tier set, ``None`` leaves the tiers alone."""
    product = form.save()
    ensure_default_variant(product)
    if tier_rows is not None:
        product.registrant_discount_tiers.all().delete()
        _create_tiers(product, tier_rows)
    return product


def annotate_sold_and_stock(club, products) -> None:
    """Sets ``sold_count`` (paid units) and ``stock_count`` (on hand, None unless inventory managed) on each product."""
    sold = quantity_sold_by_product(club)
    stock = stock_levels([product for product in products if product.inventory_managed])
    for product in products:
        product.sold_count = sold.get(product.pk, 0)
        product.stock_count = sum(count for (product_pk, _variant), count in stock.items() if product_pk == product.pk) if product.inventory_managed else None


def delete_category(category: ProductCategory) -> str:
    """Delete an ordinary category (its products just lose it); returns its name. A system category raises CategoryProtected."""
    if category.registration_kind:
        raise CategoryProtected(_("“%(category)s” is a system category used by registration and cannot be deleted.") % {"category": category.name})
    name = category.name
    category.delete()
    return name
