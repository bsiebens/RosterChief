"""Cart/order totals -- one place both checkout (shop.services.checkout) and any
future "preview my total before I commit" screen compute from, so the two can
never disagree about what a discount is actually worth.
"""

from decimal import Decimal

from shop.models import DiscountType


def cart_subtotal(cart) -> Decimal:
    return sum((item.unit_price * item.quantity for item in cart.items.all()), Decimal("0"))


def discount_amount_for(subtotal: Decimal, discount) -> Decimal:
    """The actual currency amount ``discount`` is worth against ``subtotal`` --
    never more than the subtotal itself, so a fixed-amount discount larger
    than the cart can't push the total negative."""
    if discount.discount_type == DiscountType.PERCENTAGE:
        amount = subtotal * discount.discount_amount / Decimal("100")
    else:
        amount = discount.discount_amount
    return min(amount, subtotal)


def cart_totals(cart, discount=None) -> dict:
    subtotal = cart_subtotal(cart)
    discount_amount = discount_amount_for(subtotal, discount) if discount is not None else Decimal("0")
    return {"subtotal": subtotal, "discount_amount": discount_amount, "total": subtotal - discount_amount}
