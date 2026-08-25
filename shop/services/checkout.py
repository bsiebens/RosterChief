"""Cart -> Order. The only way an Order should ever come into existence outside
the Django admin -- so every rule about what makes a checkout valid (shop
open, cart non-empty, a real discount code) lives in exactly one place, not
duplicated between the mobile checkout view and any future admin-side
"place an order for a member" screen.
"""

from django.db import transaction
from django.utils.translation import gettext_lazy as _

from notifications.services import notify_members

from ..models import AppliedDiscount, Cart, Discount, Order, OrderLine
from .invoices import create_invoice_for_order
from .pricing import cart_totals


class CheckoutError(Exception):
    """Anything that stops a checkout from completing -- shown to the member
    as-is (already a translated, user-facing message), not logged as a bug."""


def find_discount(club, code: str):
    """None for an empty code (no discount requested -- not an error), raises
    for a non-empty code that doesn't match an active discount (the member
    typed something wrong and needs to know, not have it silently ignored)."""
    code = (code or "").strip()
    if not code:
        return None
    try:
        return Discount.objects.get(club=club, code=code.upper(), is_active=True)
    except Discount.DoesNotExist:
        raise CheckoutError(_("That discount code isn't valid.")) from None


@transaction.atomic
def place_order(cart: Cart, *, purchaser, discount_code: str = "") -> Order:
    """Converts every item in ``cart`` into an OrderLine, applies ``discount_code``
    if given, creates the order's Invoice, notifies ``purchaser`` that it's
    placed and payable on pickup, and marks the cart checked out. Nothing here
    touches Payment -- that only exists once a shop admin actually marks the
    order paid (there's no online payment to record automatically)."""
    if not cart.club.shop_open:
        raise CheckoutError(_("The shop is closed right now."))

    items = list(cart.items.select_related("product", "beneficiary", "team"))
    if not items:
        raise CheckoutError(_("Your cart is empty."))

    discount = find_discount(cart.club, discount_code)
    totals = cart_totals(cart, discount)

    order = Order.objects.create(club=cart.club, purchaser=purchaser, total=totals["total"])
    for item in items:
        OrderLine.objects.create(
            order=order,
            product=item.product,
            quantity=item.quantity,
            unit_price=item.unit_price,
            beneficiary=item.beneficiary,
            team=item.team,
            line_total=item.unit_price * item.quantity,
        )

    if discount is not None:
        AppliedDiscount.objects.create(
            order=order,
            discount=discount,
            discount_type=discount.discount_type,
            discount_amount=discount.discount_amount,
            description=discount.name,
            applied_by=purchaser,
        )

    cart.status = Cart.CartStatus.CHECKED_OUT
    cart.save(update_fields=["status", "modified"])

    create_invoice_for_order(order)
    dispatch_order_placed_notification(order)

    return order


def dispatch_order_placed_notification(order) -> None:
    """The one notification this feature asks for: the purchaser, and only the
    purchaser (not every shop admin), told their order is in and payable on
    pickup. Uses notifications.services.notify_members as-is -- it already
    accepts any iterable of Member, single-recipient calls are the normal
    case, not a special one."""
    title = _("Order %(number)s placed") % {"number": order.number}
    body = _("Your order is in — pay when you pick it up. Total: €%(total)s.") % {"total": order.total}
    notify_members([order.purchaser], club=order.club, title=title, body=body, source=order)
