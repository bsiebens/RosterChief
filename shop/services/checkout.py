"""Cart -> Order. The only way an Order should ever come into existence outside
the Django admin -- so every rule about what makes a checkout valid (shop
open, cart non-empty, a real discount code) lives in exactly one place, not
duplicated between the mobile checkout view and any future admin-side
"place an order for a member" screen.
"""

from django.db import transaction
from django.utils.translation import gettext_lazy as _

from rosterchief import throttle

from ..models import AppliedDiscount, Cart, Discount, Order, OrderLine, Product
from .inventory import InventoryError, check_available, is_in_stock, lock_products, take_for_order_lines
from .invoices import create_invoice_for_order
from .notifications import dispatch_order_placed_notification
from .pricing import cart_totals
from .production import sync_production_status

#: Wrong discount codes allowed per member / per client IP before every code
#: (even a right one) is refused for the rest of the window -- keeps anyone
#: from guessing their way through a club's codes.
WRONG_CODE_MEMBER_RATE = throttle.Rate("shop:discount-code:member", limit=10, window=15 * 60)
WRONG_CODE_IP_RATE = throttle.Rate("shop:discount-code:ip", limit=10, window=15 * 60)

#: One message for an unknown, inactive, not-yet-valid, expired or used-up
#: code alike, so trying codes never confirms that one exists.
INVALID_CODE_MESSAGE = _("This code isn't valid.")


class CheckoutError(Exception):
    """Anything that stops a checkout from completing -- shown to the member
    as-is (already a translated, user-facing message), not logged as a bug."""


def discount_uses(discount, *, member=None):
    """Orders that used ``discount`` and weren't cancelled -- what
    Discount.max_uses and Discount.once_per_member count against. A
    cancelled order gives its use back."""
    uses = AppliedDiscount.objects.filter(discount=discount).exclude(order__fulfillment_status=Order.FulfillmentStatus.CANCELLED)
    if member is not None:
        uses = uses.filter(order__purchaser=member)
    return uses


def find_discount(club, code: str, *, member=None, ip: str | None = None, lock: bool = False):
    """None for an empty code (no discount requested -- not an error), raises
    CheckoutError for a code that can't be used right now: unknown,
    inactive, outside its validity window or used up all give the same
    INVALID_CODE_MESSAGE and count as a wrong attempt against ``member`` and
    ``ip`` (throttled -- see WRONG_CODE_*_RATE); a code ``member`` already
    used gets its own message and isn't counted. ``lock`` row-locks the
    discount (inside the caller's transaction) before its uses are counted,
    so two orders racing for a code's last use can't both get it."""
    code = (code or "").strip()
    if not code:
        return None

    checks = ((WRONG_CODE_MEMBER_RATE, str(member.pk) if member is not None else None), (WRONG_CODE_IP_RATE, ip))
    if any(throttle.is_limited(rate, identifier) for rate, identifier in checks if identifier):
        raise CheckoutError(_("Too many wrong codes. Try again in a few minutes."))

    discounts = Discount.objects.filter(club=club, code=code.upper(), is_active=True)
    if lock:
        discounts = discounts.select_for_update()
    discount = discounts.first()

    if discount is None or not discount.is_within_validity() or (discount.max_uses is not None and discount_uses(discount).count() >= discount.max_uses):
        for rate, identifier in checks:
            if identifier:
                throttle.record(rate, identifier)
        raise CheckoutError(INVALID_CODE_MESSAGE)

    if member is not None and discount.once_per_member and discount_uses(discount, member=member).exists():
        raise CheckoutError(_("You've already used this code."))

    return discount


def is_orderable(item) -> bool:
    """Whether a cart line can still be bought: its product is active, in the
    member-facing shop (public, not a membership product) and in the cart's
    own club, its variant (if any) is still active and, for an inventory-managed
    product, enough of it is in stock."""
    product = item.product
    if product.club_id != item.cart.club_id or not product.is_active or not product.is_public or product.product_type == Product.ProductType.MEMBERSHIP:
        return False
    if item.variant is not None and not (item.variant.is_active and item.variant.product_id == product.pk):
        return False
    return is_in_stock(product, item.variant, item.quantity)


def current_unit_price(item):
    return item.variant.effective_price if item.variant is not None else item.product.price


def reprice_cart_items(items):
    """Brings every orderable line's unit_price up to its product/variant's
    current price (saving the change) -- a cart line otherwise keeps the
    price it was added at forever. Returns ``(unavailable, repriced)``, the
    lines that can no longer be ordered and the lines whose price changed."""
    unavailable, repriced = [], []
    for item in items:
        if not is_orderable(item):
            unavailable.append(item)
            continue
        price = current_unit_price(item)
        if item.unit_price != price:
            item.unit_price = price
            item.save(update_fields=["unit_price"])
            repriced.append(item)
    return unavailable, repriced


def _item_label(item):
    return f"{item.product.name} ({item.variant.name})" if item.variant is not None else item.product.name


def place_order(cart: Cart, *, purchaser, discount_code: str = "", ip: str | None = None) -> Order:
    """Converts every item in ``cart`` into an OrderLine, applies ``discount_code``
    if given, creates the order's Invoice, notifies ``purchaser`` that it's
    placed and payable on pickup, and marks the cart checked out. Nothing here
    touches Payment -- that only exists once a shop admin actually marks the
    order paid (there's no online payment to record automatically).

    Every line is re-priced from its current product/variant first: a line
    that can't be ordered any more refuses the checkout, and a changed price
    is saved onto the cart and refuses it too, so the member sees the new
    total before committing to it. The cart and the discount code are
    row-locked for the rest of the checkout, so a double submit can't order
    one cart twice and a code's last use can't be taken twice."""
    if not cart.club.shop_open:
        raise CheckoutError(_("The shop is closed right now."))

    problem = None
    with transaction.atomic():
        if not Cart.objects.select_for_update().filter(pk=cart.pk, status=Cart.CartStatus.OPEN).exists():
            raise CheckoutError(_("This cart has already been ordered."))

        items = list(cart.items.select_related("cart", "product", "variant", "beneficiary", "team"))
        if not items:
            raise CheckoutError(_("Your cart is empty."))

        # Stock is only trustworthy to read with its products locked: a
        # concurrent order for the last items waits here, then sees them gone.
        lock_products({item.product_id for item in items if item.product.inventory_managed})

        unavailable, repriced = reprice_cart_items(items)
        try:
            check_available(items)
        except InventoryError as error:
            # Each line fits on its own (else it'd be "unavailable" above) but
            # together -- the same item for two people -- they don't.
            if not unavailable:
                raise CheckoutError(str(error)) from error
        if unavailable:
            problem = _("These items are no longer available: %(items)s. Remove them from your cart to place your order.") % {"items": ", ".join(_item_label(item) for item in unavailable)}
        elif repriced:
            # Leaves the atomic block normally so the new prices are saved.
            problem = _("Some prices have changed since you added them to your cart: %(items)s. Check the updated total and place your order again.") % {"items": ", ".join(_item_label(item) for item in repriced)}
        else:
            order = _create_order(cart, items, purchaser=purchaser, discount_code=discount_code, ip=ip)

    if problem is not None:
        raise CheckoutError(problem)

    dispatch_order_placed_notification(order.pk)
    return order


def _create_order(cart, items, *, purchaser, discount_code, ip):
    discount = find_discount(cart.club, discount_code, member=purchaser, ip=ip, lock=True)
    totals = cart_totals(cart, discount)

    order = Order.objects.create(club=cart.club, purchaser=purchaser, total=totals["total"])
    lines = []
    for item in items:
        line = OrderLine.objects.create(
            order=order,
            product=item.product,
            variant=item.variant,
            quantity=item.quantity,
            unit_price=item.unit_price,
            beneficiary=item.beneficiary,
            team=item.team,
            line_total=item.unit_price * item.quantity,
            personalization_number=item.personalization_number,
            personalization_name=item.personalization_name,
        )
        lines.append(line)

    # Inventory-managed lines come out of stock and are received on the spot
    # -- they never go through production.
    take_for_order_lines(lines)
    sync_production_status(order)

    if discount is not None:
        AppliedDiscount.objects.create(
            order=order,
            discount=discount,
            discount_type=discount.discount_type,
            discount_amount=discount.discount_amount,
            amount=totals["discount_amount"],
            description=discount.name,
            applied_by=purchaser,
        )

    cart.status = Cart.CartStatus.CHECKED_OUT
    cart.save(update_fields=["status", "modified"])

    create_invoice_for_order(order)
    return order
