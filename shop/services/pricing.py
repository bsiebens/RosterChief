"""Cart/order totals -- one place both checkout (shop.services.checkout) and any
future "preview my total before I commit" screen compute from, so the two can
never disagree about what a discount is actually worth.
"""

from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Sum

from shop.models import DiscountType

#: Every currency amount in this app is 2 decimal places (every DecimalField
#: storing one is decimal_places=2) -- a percentage discount is the one place
#: that math can produce more (33.33% of €80.00 is €26.6640), so this is the
#: one place that needs rounding back down to what a euro amount actually is.
_CENTS = Decimal("0.01")


def cart_subtotal(cart) -> Decimal:
    return sum((item.unit_price * item.quantity for item in cart.items.all()), Decimal("0"))


def order_total(order) -> Decimal:
    """Recomputed from the order's own line items and applied discounts --
    the same subtotal-minus-discount shape cart_totals uses at checkout, but
    read back from what's actually on the order rather than a live cart.
    Used after a staff edit to an existing OrderLine (management.views.
    OrderLineUpdateView) changes what the order is actually worth."""
    lines_total = order.order_items.aggregate(total=Sum("line_total"))["total"] or Decimal("0")
    discount_total = order.applied_discounts.aggregate(total=Sum("discount_amount"))["total"] or Decimal("0")
    return max(Decimal("0"), lines_total - discount_total)


def discount_amount_for(subtotal: Decimal, discount) -> Decimal:
    """The actual currency amount ``discount`` is worth against ``subtotal`` --
    never more than the subtotal itself, so a fixed-amount discount larger
    than the cart can't push the total negative. Rounded to the nearest cent
    (half up) -- a percentage of an odd subtotal otherwise carries however
    many decimal places Decimal division happens to produce."""
    if discount.discount_type == DiscountType.PERCENTAGE:
        amount = (subtotal * discount.discount_amount / Decimal("100")).quantize(_CENTS, rounding=ROUND_HALF_UP)
    else:
        amount = discount.discount_amount
    return min(amount, subtotal)


def cart_totals(cart, discount=None) -> dict:
    subtotal = cart_subtotal(cart)
    discount_amount = discount_amount_for(subtotal, discount) if discount is not None else Decimal("0")
    return {"subtotal": subtotal, "discount_amount": discount_amount, "total": subtotal - discount_amount}
