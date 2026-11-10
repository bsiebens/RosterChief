"""What registering costs: membership products (a season, variants, an early-bird discount and registrant tiers) -> a price per person.

Ported from the old ``registration.services.pricing``. Nothing here writes: ``price_entries`` is also the preview of the join page.

* The season comes from the products (every chosen product must be for the same season), never from today's date.
* A registrant tier (``ProductRegistrantDiscountTier``) applies when at least ``min_registrants`` people are registered for **that product** at once
  (across all its variants; people on other products don't count): the highest tier reached wins. ``per_person`` takes it off every line of that
  product (``Line.discount``, shown on the line). ``per_order`` takes it off once for the product: shown as ONE separate "multi-registration discount"
  line per product (``Pricing.order_discounts``); each person's fee still carries its share (``Line.order_discount``) so no fee goes below 0.
* A person registers for an *option*: one active variant of a membership product, or the product itself ("as is") when it has no variants.
* A negative price (membership products only) is a **credit**: a line with a negative base is not charged itself (its person's fee is 0) and takes
  nothing from tiers or early-bird. The credits of the whole registration are added up and spread in proportion over its positive lines (the cents
  that rounding leaves go to the first positive line), capped at what those lines cost: a person's fee never goes below 0 and neither does the total.
* The early-bird discount is *conditional*: it is stored on the membership as ``early_payment_deadline`` / ``early_payment_discount`` and only
  counts when the fee is paid on or before that deadline (``members.services.memberships.discount_applies``). The totals show both.
"""

import datetime
import uuid
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


@dataclass(frozen=True)
class Option:
    """Something a person can register for: a variant of a membership product, or the product itself when it has no variants."""

    product: Product
    variant: ProductVariant | None = None

    @property
    def key(self) -> str:
        return f"variant:{self.variant.pk}" if self.variant is not None else f"product:{self.product.pk}"

    @property
    def effective_price(self) -> Decimal:
        return self.variant.effective_price if self.variant is not None else self.product.price

    @property
    def full_name(self) -> str:
        return self.variant.full_name if self.variant is not None else self.product.name

    @property
    def label(self) -> str:
        """What the join page shows: the variant's name, or the product name for a product without variants."""
        return self.variant.name if self.variant is not None else self.product.name

    @property
    def team(self):
        """The team a registrant for this option is placed in: the variant's own team, else the product's, else ``None`` (they may ask for one)."""
        if self.variant is not None and self.variant.team_id:
            return self.variant.team
        return self.product.team if self.product.team_id else None


def as_option(value) -> Option:
    return value if isinstance(value, Option) else Option(product=value.product, variant=value)


@dataclass
class Line:
    option: Option
    base: Decimal
    #: The registrant discount of this line alone (a per-person tier): shown on the line.
    discount: Decimal = Decimal("0.00")
    #: This line's share of the product's per-registration discount (shown as a separate line, not on this one).
    order_discount: Decimal = Decimal("0.00")
    #: This line's share of the registration's credits (positive lines only).
    credit_share: Decimal = Decimal("0.00")
    early_bird_discount: Decimal = Decimal("0.00")
    early_bird_deadline: datetime.date | None = None

    @property
    def product(self) -> Product:
        return self.option.product

    @property
    def variant(self) -> ProductVariant | None:
        return self.option.variant

    @property
    def is_credit(self) -> bool:
        return self.base < 0

    @property
    def before_credit(self) -> Decimal:
        """What the line costs after its discounts and before the credits (a credit line: its negative amount)."""
        return self.base if self.is_credit else max(self.base - self.discount - self.order_discount, Decimal("0.00"))

    @property
    def own_price(self) -> Decimal:
        """What the line shows as its price: the base less its own discount; a credit line: its negative amount. The per-registration discount and the
        credits are separate lines."""
        return self.base if self.is_credit else max(self.base - self.discount, Decimal("0.00"))

    @property
    def price(self) -> Decimal:
        """What this person's membership costs (their fee): never below 0, and 0 for a credit line."""
        return Decimal("0.00") if self.is_credit else max(self.before_credit - self.credit_share, Decimal("0.00"))


@dataclass
class Pricing:
    season: object
    lines: list[Line] = field(default_factory=list)

    @property
    def total(self) -> Decimal:
        return sum((line.price for line in self.lines), Decimal("0.00"))

    @property
    def order_discounts(self) -> list[dict]:
        """The per-registration discounts, one per product that has one: ``{"product", "amount", "people"}`` (amount positive)."""
        found: dict = {}
        for line in self.lines:
            if line.order_discount > 0:
                row = found.setdefault(line.product.pk, {"product": line.product, "amount": Decimal("0.00"), "people": 0})
                row["amount"] += line.order_discount
                row["people"] += 1
        for pk, row in found.items():
            row["percent"] = tier_percent(row["product"], sum(1 for line in self.lines if line.product.pk == pk))
        return list(found.values())

    @property
    def order_discount_total(self) -> Decimal:
        return sum((row["amount"] for row in self.order_discounts), Decimal("0.00"))

    @property
    def credit_total(self) -> Decimal:
        """The credits of the registration (a positive amount)."""
        return sum((-line.base for line in self.lines if line.is_credit), Decimal("0.00"))

    @property
    def credit_applied(self) -> Decimal:
        return sum((line.credit_share for line in self.lines), Decimal("0.00"))

    @property
    def early_bird_total_discount(self) -> Decimal:
        return sum((line.early_bird_discount for line in self.lines), Decimal("0.00"))


def offerable_products():
    """Membership products people may register for: active, public, with a season that has not ended, and either at least one active variant or no
    variants at all (then the product itself is the one option)."""
    today = timezone.localdate()
    has_active = ProductVariant.objects.filter(product=OuterRef("pk"), is_active=True)
    has_any = ProductVariant.objects.filter(product=OuterRef("pk"))
    return (
        Product.objects.filter(product_type=Product.ProductType.MEMBERSHIP, is_active=True, is_public=True, season__isnull=False, season__end_date__gte=today)
        .filter(Exists(has_active) | ~Exists(has_any))
        .select_related("season", "category", "team")
        .prefetch_related("variants__team", "registrant_tiers")
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


def options_of(product: Product) -> list[Option]:
    """The options of a product: its active variants, or the product itself when it has no variants (at all)."""
    variants = [v for v in product.variants.all() if v.is_active]
    if variants:
        return [Option(product=product, variant=v) for v in variants]
    return [] if list(product.variants.all()) else [Option(product=product)]


def parse_key(key) -> tuple[str, str] | None:
    """``"variant:<uuid>"`` / ``"product:<uuid>"`` -> (kind, normalised uuid); a bare uuid (the old ``variant_id``) is a variant. ``None`` when malformed."""
    text = str(key or "").strip()
    kind, _sep, ident = text.rpartition(":")
    kind = kind or "variant"
    try:
        return (kind, str(uuid.UUID(ident))) if kind in ("variant", "product") else None
    except ValueError:
        return None


def options_by_key(keys) -> dict:
    """The chosen options (only offerable ones), keyed by the key exactly as the caller gave it (``Option.key`` form, or a bare variant uuid)."""
    wanted = {k: parse_key(k) for k in keys if k}
    parsed = [p for p in wanted.values() if p is not None]
    product_ids = [i for kind, i in parsed if kind == "product"]
    product_ids += ProductVariant.objects.filter(pk__in=[i for kind, i in parsed if kind == "variant"]).values_list("product_id", flat=True)
    found = {option.key: option for product in offerable_products().filter(pk__in=product_ids) for option in options_of(product)}
    return {original: found[f"{p[0]}:{p[1]}"] for original, p in wanted.items() if p is not None and f"{p[0]}:{p[1]}" in found}


def variants_by_id(ids) -> dict:
    """The chosen variants (only offerable ones), keyed by id."""
    found = ProductVariant.objects.filter(pk__in=list(ids), is_active=True, product__in=offerable_products()).select_related("product", "product__season", "product__category", "product__team", "team").prefetch_related("product__registrant_tiers")
    return {v.pk: v for v in found}


def resolve_season(options):
    seasons = {v.product.season_id: v.product.season for v in options}
    if not seasons:
        raise Invalid("Choose what to register for.", {"entries": ["Choose what to register for."]})
    if len(seasons) > 1:
        raise Invalid("Everything in one registration must be for the same season.", {"entries": ["Choose products for one season."]})
    return next(iter(seasons.values()))


def tier_percent(product: Product, registrants: int):
    """The percentage of the registrant tier that applies for ``registrants`` people on ``product`` (``None`` when there is none or it is a fixed amount)."""
    tier = _tier_for(product, registrants)
    if tier is not None and tier.discount_type == DiscountType.PERCENTAGE:
        return Decimal(format(tier.discount_amount.normalize(), "f"))  # 5.00 -> 5, 12.50 -> 12.5
    return None


def _tier_for(product: Product, registrants: int):
    tiers = [t for t in product.registrant_tiers.all() if t.min_registrants <= registrants]
    return max(tiers, key=lambda t: t.min_registrants) if tiers else None


def price_entries(options: list, *, today: datetime.date | None = None) -> Pricing:
    """Price one registration: ``options`` is one option (or, for convenience, a variant) per person, in order."""
    today = today or timezone.localdate()
    options = [as_option(o) for o in options]
    pricing = Pricing(season=resolve_season(options), lines=[Line(option=o, base=_q(o.effective_price)) for o in options])
    by_product: dict = {}
    for line in pricing.lines:
        by_product.setdefault(line.product.pk, []).append(line)
    for all_lines in by_product.values():
        product = all_lines[0].product
        lines = [ln for ln in all_lines if not ln.is_credit]  # credits get no tier and no early-bird
        if not lines:
            continue
        tier = _tier_for(product, len(lines))  # the people registered on THIS product (all its variants), not the whole form
        if tier is not None:
            per_order = product.registrant_discount_scope == Product.RegistrantDiscountScope.PER_ORDER
            if per_order and tier.discount_type == DiscountType.FIXED_AMOUNT:
                total = min(_q(tier.discount_amount), sum((ln.base for ln in lines), Decimal("0.00")))
                share = (total / len(lines)).quantize(CENT, rounding=ROUND_HALF_UP)
                for ln in lines:
                    ln.order_discount = min(share, ln.base)
                lines[0].order_discount = min(lines[0].base, lines[0].order_discount + (total - sum((ln.order_discount for ln in lines), Decimal("0.00"))))  # the cents that rounding left over
            else:
                for ln in lines:
                    amount = _q(discount_amount_for(ln.base, SimpleNamespace(discount_type=tier.discount_type, discount_amount=tier.discount_amount)))
                    if per_order:
                        ln.order_discount = amount  # taken off once for the product: shown as its own line
                    else:
                        ln.discount = amount
    spread_credits(pricing.lines)
    for ln in pricing.lines:
        product = ln.product
        if not ln.is_credit and product.early_bird_discount_type and product.early_bird_deadline and product.early_bird_deadline >= today:
            ln.early_bird_discount = min(_q(discount_amount_for(ln.before_credit, SimpleNamespace(discount_type=product.early_bird_discount_type, discount_amount=product.early_bird_discount_amount))), ln.price)
            ln.early_bird_deadline = product.early_bird_deadline
    return pricing


def spread_credits(lines) -> None:
    """Spread the credits (negative lines) over the positive lines in proportion to what those cost (after the registrant discount), capped at their
    total; the cents rounding leaves over go to the first positive line. Sets ``credit_share`` on every line; ``lines`` are ``Line`` objects."""
    positive = [ln for ln in lines if not ln.is_credit]
    for ln in lines:
        ln.credit_share = Decimal("0.00")
    credit = sum((-ln.base for ln in lines if ln.is_credit), Decimal("0.00"))
    cost = sum((ln.before_credit for ln in positive), Decimal("0.00"))
    applied = min(credit, cost)
    if applied <= 0 or cost <= 0:
        return
    given = Decimal("0.00")
    for ln in positive:
        ln.credit_share = (applied * ln.before_credit / cost).quantize(CENT, rounding=ROUND_HALF_UP)
        given += ln.credit_share
    first = next(ln for ln in positive if ln.before_credit > 0)
    first.credit_share = min(first.before_credit, first.credit_share + (applied - given))
