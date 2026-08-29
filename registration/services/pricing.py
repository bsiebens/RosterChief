"""Pricing for a registration entry.

Reuses shop.models.Product/ProductVariant as the price catalog -- Product.
ProductType.MEMBERSHIP and EVENT_FEE (REGISTRATION_PRODUCT_TYPES below)
already exist in that enum for exactly this purpose (see the module-level
note on shop.models.Product) -- rather than inventing a parallel one, and
shop.services.pricing.discount_amount_for for the actual currency math so
the two never disagree about what a percentage/fixed discount is worth.

Two independent discount conditions apply to a registration, on different
schedules:

- ProductRegistrantDiscountTier -- a staircase of "N+ people through this
  product get X% off" steps. How many entries in the same batch chose a
  variant of this same product is known immediately at submission time, so
  the *best* tier a batch qualifies for (the highest min_registrants still
  at or below the count) is applied straight onto the resulting
  ClubMembership.fee_amount. Product.registrant_discount_scope decides
  *how*: PER_PERSON (the default) applies the tier to every qualifying
  entry; PER_ORDER subtracts it once for the whole batch, from whichever
  qualifying entry price_entries reaches first.
- Product.early_bird_discount_* -- whether the fee is paid by a deadline.
  NOT known at registration time (payment happens later, see club.services.
  fees) -- never baked into fee_amount. Stored instead as ClubMembership.
  early_payment_deadline/early_payment_discount and re-evaluated by
  club.services.fees.effective_fee_amount only once the member actually
  pays.

Each entry prices independently against its own chosen Product -- there is
no cross-entry splitting of a single discount the way a whole-cart Discount
would need, since both conditions here are evaluated per (product, entry)
(registrant_discount_scope's PER_ORDER case is the one exception, and it's
handled entirely inside price_entries).
"""

from collections import Counter, defaultdict
from decimal import Decimal
from types import SimpleNamespace

from django.utils import timezone

from club.models import Season
from shop.models import Product, ProductRegistrantDiscountTier, ProductVariant
from shop.services.pricing import discount_amount_for
from teams.models import Team
from teams.services.numbers import available_numbers

#: Registration -- and the discounts that go with it (ProductRegistrantDiscountTier,
#: Product.early_bird_discount_*) -- applies to a membership fee or an event
#: fee (e.g. a camp/tournament a family registers kids for), never plain
#: merchandise.
REGISTRATION_PRODUCT_TYPES = (Product.ProductType.MEMBERSHIP, Product.ProductType.EVENT_FEE)


def available_registration_products(club, season=None):
    """Active MEMBERSHIP/EVENT_FEE products a registration can be priced
    against -- what staff configures via the ordinary product management UI
    (ProductForm). A product whose own season has already ended is excluded
    -- once a season is over, nobody should still be able to register
    against it (management.forms.ProductForm's own season queryset applies
    the same "current + upcoming only" rule when staff picks it in the
    first place). A season-less product is left alone -- resolve_registration_season
    already rejects it for a different reason (nothing to resolve).

    ``season``, when given, narrows this to just that season's own products
    -- what a registration entry's own product_variant choices are scoped
    to once resolve_chosen_season has settled on one, so a batch spanning
    two overlapping registration windows can't be built by accident."""
    today = timezone.localdate()
    qs = Product.objects.filter(club=club, product_type__in=REGISTRATION_PRODUCT_TYPES, is_active=True).exclude(season__end_date__lt=today)
    if season is not None:
        qs = qs.filter(season=season)
    return qs.prefetch_related("variants").order_by("name")


def variant_registration_kinds(club, season=None):
    """``{str(variant_id): registration_kind}`` for every variant a
    registration entry could currently choose (same scope as
    RegistrationEntryRowForm's own product_variant queryset) --
    registration_kind is "" for a variant whose product isn't tagged with
    one of the two system categories (shop.models.ProductCategory.
    RegistrationKind), meaning it's offered regardless of "Registering as".

    Consumed as a json_script blob by register.html/reregister.html's own
    extra_body script, which hides a row's non-matching product_variant
    options once "Registering as" is chosen -- a UX narrowing only,
    RegistrationEntryRowForm.clean is what actually enforces the match
    server-side."""
    variants = ProductVariant.objects.filter(product__in=available_registration_products(club, season=season), is_active=True).select_related("product__category")
    return {str(variant.pk): (variant.product.category.registration_kind if variant.product.category_id else "") for variant in variants}


def team_number_pools(club, season=None):
    """``{str(team_id): [available number, ...]}`` for every team that has a
    jersey-number pool assigned (RegistrationEntryRowForm's own
    requested_team queryset) -- a team with no pool simply has no entry,
    which the client-side script reads as "no number step for this team".

    Ignores age-gap exemptions (teams.services.numbers.is_number_available's
    ``for_member``): at this point in the flow the entry might be a
    brand-new person with no Member row yet, so there's no real identity to
    check an age gap against -- same "UX narrowing only" caveat as
    variant_registration_kinds above; RegistrationEntryRowForm.clean is what
    actually enforces the pick server-side, against the submitted date of
    birth."""
    if season is None:
        return {}
    teams = Team.objects.filter(club=club, pool__isnull=False).select_related("pool")
    return {str(team.pk): available_numbers(team.pool, season) for team in teams}


def available_registration_seasons(club):
    """Every distinct season available_registration_products(club) currently
    spans, soonest first -- usually exactly one. More than one means two
    registration windows are open at once (e.g. late sign-ups for the
    outgoing season alongside the new one already open); resolve_chosen_season
    is what makes the registrant pick between them rather than letting a
    batch silently mix products from two different seasons."""
    season_ids = available_registration_products(club).values_list("season_id", flat=True)
    return Season.objects.filter(pk__in=season_ids).order_by("start_date")


def resolve_chosen_season(club, requested_season_id=None):
    """``(season, available_seasons)`` for a registration in progress.
    ``season`` is the one actually in effect: ``requested_season_id`` if
    it's a genuine choice, else the sole option if there's only one, else
    ``None`` -- the caller (registration.views.RegistrationView/mobile.
    views.ReRegisterView) must then ask the registrant to pick one before
    showing the rest of the form, rather than silently guessing."""
    available = list(available_registration_seasons(club))
    if requested_season_id:
        season = next((season for season in available if str(season.pk) == str(requested_season_id)), None)
        if season is not None:
            return season, available
    if len(available) == 1:
        return available[0], available
    return None, available


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


def _best_tier(tiers, count):
    """The highest-threshold tier ``count`` still qualifies for -- ``tiers``
    must already be sorted by ``min_registrants`` ascending. ``None`` if
    none apply yet."""
    best = None
    for tier in tiers:
        if count >= tier.min_registrants:
            best = tier
        else:
            break
    return best


def price_entries(variants):
    """``variants`` -- one ProductVariant per entry, in submission order
    (``None`` for a free/no-charge entry, e.g. many volunteer roles).
    Returns a parallel list of per-entry dicts: ``{"variant", "price",
    "min_registrants_discount", "deadline", "deadline_discount"}``.

    ``min_registrants_discount`` is immediate (known now, from how many of
    ``variants`` share a product, matched against that product's own
    ProductRegistrantDiscountTier staircase). ``deadline``/
    ``deadline_discount`` describe a *conditional* early-payment discount
    the caller stores on the resulting ClubMembership rather than applying
    now -- see this module's own docstring."""
    counts = Counter(variant.product_id for variant in variants if variant is not None)

    tiers_by_product = defaultdict(list)
    for tier in ProductRegistrantDiscountTier.objects.filter(product_id__in=counts.keys()).order_by("min_registrants"):
        tiers_by_product[tier.product_id].append(tier)

    # PER_ORDER: the best qualifying tier is subtracted once for the whole
    # batch, not once per entry -- tracks which products' one-time discount
    # hasn't been handed out yet as entries are walked below. PER_PERSON
    # (the default) needs no such tracking; every qualifying entry gets it.
    per_order_pending = {product_id for product_id, tiers in tiers_by_product.items() if tiers and tiers[0].product.registrant_discount_scope == Product.RegistrantDiscountScope.PER_ORDER}

    rows = []
    for variant in variants:
        if variant is None:
            rows.append({"variant": None, "price": Decimal("0"), "min_registrants_discount": Decimal("0"), "deadline": None, "deadline_discount": Decimal("0")})
            continue

        product = variant.product
        price = variant.effective_price

        min_registrants_discount = Decimal("0")
        tier = _best_tier(tiers_by_product.get(product.pk, []), counts[product.pk])
        if tier is not None:
            if product.pk in per_order_pending:
                min_registrants_discount = discount_amount_for(price, tier)
                per_order_pending.discard(product.pk)
            elif product.registrant_discount_scope == Product.RegistrantDiscountScope.PER_PERSON:
                min_registrants_discount = discount_amount_for(price, tier)

        deadline = None
        deadline_discount = Decimal("0")
        if product.early_bird_discount_enabled and product.early_bird_discount_deadline:
            deadline = product.early_bird_discount_deadline
            deadline_discount = discount_amount_for(price - min_registrants_discount, SimpleNamespace(discount_type=product.early_bird_discount_type, discount_amount=product.early_bird_discount_amount))

        rows.append({"variant": variant, "price": price, "min_registrants_discount": min_registrants_discount, "deadline": deadline, "deadline_discount": deadline_discount})
    return rows
