"""The member-facing shop: catalogue, one open cart per account and club, and the family's
orders. Shared by the member web app (mobile.views.Shop*) and the app API's /shop
endpoints, so both offer the same products and build carts the same way. Checkout itself
is shop.services.checkout.place_order.
"""

from decimal import Decimal

from django.db.models import Sum
from django.utils.translation import gettext_lazy as _

from shop.models import Cart, CartItem, Order, Product

from .checkout import CheckoutError, find_discount, reprice_cart_items
from .inventory import stock_for
from .pricing import cart_totals

#: Upper bound on one cart line's quantity: more clamps to it rather than erroring.
MAX_CART_QUANTITY = 99


def catalog(club):
    """Active, public products. MEMBERSHIP products are never sold here -- registration
    prices them -- whatever their is_public says."""
    return Product.objects.filter(club=club, is_active=True, is_public=True).exclude(product_type=Product.ProductType.MEMBERSHIP).select_related("category")


def open_cart(club, user) -> Cart | None:
    return Cart.objects.filter(club=club, user=user, status=Cart.CartStatus.OPEN).first()


def add_to_cart(club, user, product, *, quantity=1, variant=None, beneficiary=None, personalization_number="", personalization_name="") -> CartItem:
    """Add ``quantity`` to the account's open cart (created on first use). The same
    product/option/person/personalisation adds to the existing line. Personalisation only
    counts on a product that offers it. The caller has checked that ``variant`` belongs
    to ``product`` and that ``beneficiary`` is someone the account acts for.

    An inventory-managed product can only go in the cart as far as its stock
    reaches (counting what's already in the cart for it): ``quantity`` is
    lowered to what's left, and CheckoutError is raised when nothing is."""
    quantity = min(max(1, int(quantity)), MAX_CART_QUANTITY)
    if product.inventory_managed:
        in_cart = CartItem.objects.filter(cart__club=club, cart__user=user, cart__status=Cart.CartStatus.OPEN, product=product, variant=variant).aggregate(total=Sum("quantity"))["total"] or 0
        left = stock_for(product, variant) - in_cart
        if left < 1:
            name = f"{product.name} ({variant.name})" if variant is not None else product.name
            message = _("“%(item)s” is out of stock.") if in_cart == 0 else _("There's no more “%(item)s” in stock than what's already in your cart.")
            raise CheckoutError(message % {"item": name})
        quantity = min(quantity, left)
    if not product.personalization_enabled:
        personalization_number = personalization_name = ""
    unit_price = variant.effective_price if variant is not None else product.price
    cart, _created = Cart.objects.get_or_create(club=club, user=user, status=Cart.CartStatus.OPEN)
    item, created = CartItem.objects.get_or_create(
        cart=cart,
        product=product,
        variant=variant,
        beneficiary=beneficiary,
        personalization_number=(personalization_number or "").strip()[:20],
        personalization_name=(personalization_name or "").strip()[:100],
        defaults={"quantity": quantity, "unit_price": unit_price},
    )
    if not created:
        item.quantity = min(item.quantity + quantity, MAX_CART_QUANTITY)
        item.save(update_fields=["quantity"])
    return item


def set_quantity(item: CartItem, quantity: int) -> None:
    """Below 1 removes the line; above the cap clamps."""
    if quantity < 1:
        item.delete()
        return
    item.quantity = min(quantity, MAX_CART_QUANTITY)
    item.save(update_fields=["quantity"])


def cart_summary(club, user, *, code: str = "", member=None, ip: str | None = None) -> dict:
    """The open cart re-priced to what checkout would charge now. Keys: ``cart`` (or
    None), ``items`` (each with ``line_total`` and ``unavailable``), ``code``,
    ``discount``, ``discount_error``, ``totals``, ``has_unavailable``, ``prices_changed``.
    A wrong ``code`` counts towards the same throttle as one at checkout."""
    cart = open_cart(club, user)
    items = list(cart.items.select_related("cart", "product", "variant", "beneficiary").order_by("created")) if cart is not None else []
    unavailable, repriced = reprice_cart_items(items)
    unavailable_ids = {item.pk for item in unavailable}
    for item in items:
        item.line_total = item.unit_price * item.quantity
        item.unavailable = item.pk in unavailable_ids

    code = (code or "").strip()
    discount = discount_error = None
    if code and cart is not None:
        try:
            discount = find_discount(club, code, member=member, ip=ip)
        except CheckoutError as error:
            discount_error = str(error)

    totals = cart_totals(cart, discount) if cart is not None else {"subtotal": Decimal("0"), "discount_amount": Decimal("0"), "total": Decimal("0")}
    return {"cart": cart, "items": items, "code": code, "discount": discount, "discount_error": discount_error, "totals": totals, "has_unavailable": bool(unavailable), "prices_changed": bool(repriced)}


def orders_for(club, people):
    """Orders placed by anyone the account acts for, newest first -- a parent sees an order a
    teenager placed with their own login."""
    people = list(people)
    if not people:
        return Order.objects.none()
    return Order.objects.filter(club=club, purchaser__in=people).select_related("purchaser").order_by("-created")
