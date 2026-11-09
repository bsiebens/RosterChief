"""What registering costs: membership products (a season, variants, an early-bird discount and registrant tiers) -> a price per person.

Ported from the old ``registration.services.pricing``. Nothing here writes: ``price_entries`` is also the preview of the join page.

* The season comes from the products (every chosen product must be for the same season), never from today's date.
* A registrant tier (``ProductRegistrantDiscountTier``) applies when at least ``min_registrants`` people are registered at once: the highest tier
  reached wins; ``per_person`` takes it off every line of that product, ``per_order`` takes a fixed amount off once (spread over the product's lines).
* The early-bird discount is *conditional*: it is stored on the membership as ``early_payment_deadline`` / ``early_payment_discount`` and only
  counts when the fee is paid on or before that deadline (``members.services.memberships.discount_applies``). The totals show both.
"""

import datetime
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from types import SimpleNamespace

from django.db.models import Exists, OuterRef
from django.utils import timezone

from members.services.errors import Invalid
from shop.models import DiscountType, Product, ProductVariant
from shop.services.pricing import discount_amount_for

CENT = Decimal("0.01")


def _q(value) -> Decimal:
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass
class Line:
    variant: ProductVariant
    base: Decimal
    discount: Decimal = Decimal("0.00")
    early_bird_discount: Decimal = Decimal("0.00")
    early_bird_deadline: datetime.date | None = None

    @property
    def product(self) -> Product:
        return self.variant.product

    @property
    def price(self) -> Decimal:
        return max(self.base - self.discount, Decimal("0.00"))


@dataclass
class Pricing:
    season: object
    lines: list[Line] = field(default_factory=list)

    @property
    def total(self) -> Decimal:
        return sum((line.price for line in self.lines), Decimal("0.00"))

    @property
    def early_bird_total_discount(self) -> Decimal:
        return sum((line.early_bird_discount for line in self.lines), Decimal("0.00"))


def offerable_products():
    """Membership products people may register for: active, public, with a season that has not ended and at least one active variant."""
    today = timezone.localdate()
    has_variant = ProductVariant.objects.filter(product=OuterRef("pk"), is_active=True)
    return (
        Product.objects.filter(product_type=Product.ProductType.MEMBERSHIP, is_active=True, is_public=True, season__isnull=False, season__end_date__gte=today)
        .filter(Exists(has_variant))
        .select_related("season", "category")
        .prefetch_related("variants", "registrant_tiers")
        .order_by("season__start_date", "name")
    )


def entry_kind(product: Product) -> str | None:
    """The registration option a product is for: ``"player"`` / ``"volunteer"`` when it is in one of the fixed system categories, else ``None``
    (offered for both)."""
    return product.category.system_key if product.category_id and product.category is not None else None


def fits_kind(product: Product, kind: str) -> bool:
    """A product in the Player / Volunteer category is offered only for that kind; any other product is offered for both."""
    key = entry_kind(product)
    return key is None or key == kind


KIND_LABELS = {"player": "a player", "volunteer": "a volunteer"}


def kind_error(kind: str) -> str:
    return f"This option is not available for {KIND_LABELS.get(kind, 'this kind of registration')}."


def variants_by_id(ids) -> dict:
    """The chosen variants (only offerable ones), keyed by id."""
    found = ProductVariant.objects.filter(pk__in=list(ids), is_active=True, product__in=offerable_products()).select_related("product", "product__season", "product__category").prefetch_related("product__registrant_tiers")
    return {v.pk: v for v in found}


def resolve_season(variants):
    seasons = {v.product.season_id: v.product.season for v in variants}
    if not seasons:
        raise Invalid("Choose what to register for.", {"entries": ["Choose what to register for."]})
    if len(seasons) > 1:
        raise Invalid("Everything in one registration must be for the same season.", {"entries": ["Choose products for one season."]})
    return next(iter(seasons.values()))


def _tier_for(product: Product, registrants: int):
    tiers = [t for t in product.registrant_tiers.all() if t.min_registrants <= registrants]
    return max(tiers, key=lambda t: t.min_registrants) if tiers else None


def price_entries(variants: list[ProductVariant], *, today: datetime.date | None = None) -> Pricing:
    """Price one registration: ``variants`` is one variant per person, in order."""
    today = today or timezone.localdate()
    pricing = Pricing(season=resolve_season(variants), lines=[Line(variant=v, base=_q(v.effective_price)) for v in variants])
    registrants = len(variants)
    by_product: dict = {}
    for line in pricing.lines:
        by_product.setdefault(line.product.pk, []).append(line)
    for lines in by_product.values():
        product = lines[0].product
        tier = _tier_for(product, registrants)
        if tier is not None:
            if product.registrant_discount_scope == Product.RegistrantDiscountScope.PER_ORDER and tier.discount_type == DiscountType.FIXED_AMOUNT:
                total = min(_q(tier.discount_amount), sum((ln.base for ln in lines), Decimal("0.00")))
                share = (total / len(lines)).quantize(CENT, rounding=ROUND_HALF_UP)
                for ln in lines:
                    ln.discount = min(share, ln.base)
                lines[0].discount = min(lines[0].base, lines[0].discount + (total - sum((ln.discount for ln in lines), Decimal("0.00"))))  # the cents that rounding left over
            else:
                for ln in lines:
                    ln.discount = _q(discount_amount_for(ln.base, SimpleNamespace(discount_type=tier.discount_type, discount_amount=tier.discount_amount)))
        if product.early_bird_discount_type and product.early_bird_deadline and product.early_bird_deadline >= today:
            for ln in lines:
                ln.early_bird_discount = _q(discount_amount_for(ln.price, SimpleNamespace(discount_type=product.early_bird_discount_type, discount_amount=product.early_bird_discount_amount)))
                ln.early_bird_deadline = product.early_bird_deadline
    return pricing
