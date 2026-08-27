"""Pricing for a registration entry.

Reuses shop.models.Product/ProductVariant as the price catalog --
Product.ProductType.MEMBERSHIP already exists in that enum for exactly this
purpose (see the module-level note on shop.models.Product) -- rather than
inventing a parallel one, and shop.services.pricing.discount_amount_for for
the actual currency math so the two never disagree about what a
percentage/fixed discount is worth.

Two independent discount conditions live on Product itself, mirroring its
own pre-existing (previously unwired) early_bird_discount_* fields:

- min_registrants_discount_* -- how many entries in the same batch chose a
  variant of this same product. Known immediately at submission time,
  applied straight onto the resulting ClubMembership.fee_amount.
- early_bird_discount_* -- whether the fee is paid by a deadline. NOT known
  at registration time (payment happens later, see club.services.fees) --
  never baked into fee_amount. Stored instead as ClubMembership.
  early_payment_deadline/early_payment_discount and re-evaluated by
  club.services.fees.effective_fee_amount only once the member actually
  pays.

Each entry prices independently against its own chosen Product -- there is
no cross-entry splitting of a single discount the way a whole-cart Discount
would need, since both conditions here are evaluated per (product, entry).
"""

from collections import Counter
from decimal import Decimal
from types import SimpleNamespace

from shop.models import Product
from shop.services.pricing import discount_amount_for


def available_registration_products(club):
    """Active MEMBERSHIP-type products a registration can be priced
    against -- what staff configures via the ordinary product management UI
    (ProductForm), just with product_type=MEMBERSHIP."""
    return Product.objects.filter(club=club, product_type=Product.ProductType.MEMBERSHIP, is_active=True).prefetch_related("variants").order_by("name")


class PricingError(Exception):
    """A batch's chosen products can't be priced/scoped as submitted."""


def resolve_registration_season(variants):
    """The single season every chosen variant's product agrees on. Raises
    PricingError if the list is empty or they disagree -- a club typically
    opens registration for next season while club.services.access.
    current_season still resolves to the current one (date ranges don't
    overlap), so season is read off the chosen Products' own ``season`` FK
    rather than derived from "today"."""
    products = {variant.product for variant in variants if variant is not None}
    seasons = {product.season for product in products if product.season_id is not None}
    if len(seasons) != 1:
        raise PricingError("Every chosen product must belong to the same season.")
    return seasons.pop()


def price_entries(variants):
    """``variants`` -- one ProductVariant per entry, in submission order
    (``None`` for a free/no-charge entry, e.g. many volunteer roles).
    Returns a parallel list of per-entry dicts: ``{"variant", "price",
    "min_registrants_discount", "deadline", "deadline_discount"}``.

    ``min_registrants_discount`` is immediate (known now, from how many of
    ``variants`` share a product). ``deadline``/``deadline_discount``
    describe a *conditional* early-payment discount the caller stores on
    the resulting ClubMembership rather than applying now -- see this
    module's own docstring."""
    counts = Counter(variant.product_id for variant in variants if variant is not None)

    rows = []
    for variant in variants:
        if variant is None:
            rows.append({"variant": None, "price": Decimal("0"), "min_registrants_discount": Decimal("0"), "deadline": None, "deadline_discount": Decimal("0")})
            continue

        product = variant.product
        price = variant.effective_price

        min_registrants_discount = Decimal("0")
        if product.min_registrants_discount_enabled and product.min_registrants and counts[product.pk] >= product.min_registrants:
            min_registrants_discount = discount_amount_for(price, SimpleNamespace(discount_type=product.min_registrants_discount_type, discount_amount=product.min_registrants_discount_amount))

        deadline = None
        deadline_discount = Decimal("0")
        if product.early_bird_discount_enabled and product.early_bird_discount_deadline:
            deadline = product.early_bird_discount_deadline
            deadline_discount = discount_amount_for(price - min_registrants_discount, SimpleNamespace(discount_type=product.early_bird_discount_type, discount_amount=product.early_bird_discount_amount))

        rows.append({"variant": variant, "price": price, "min_registrants_discount": min_registrants_discount, "deadline": deadline, "deadline_discount": deadline_discount})
    return rows
