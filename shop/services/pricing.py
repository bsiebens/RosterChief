"""Order totals and discount validation; framework-free so checkout and the order API compute the same numbers."""

from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Sum
from django.utils import timezone

from shop.models import AppliedDiscount, DiscountType, Order

_CENTS = Decimal("0.01")


def discount_amount_for(subtotal: Decimal, discount) -> Decimal:
    """What ``discount`` (a Discount or AppliedDiscount snapshot) is worth against ``subtotal``: rounded half up, never above
    the subtotal, never negative."""
    if discount.discount_type == DiscountType.PERCENTAGE:
        amount = (subtotal * discount.discount_amount / Decimal("100")).quantize(_CENTS, rounding=ROUND_HALF_UP)
    else:
        amount = discount.discount_amount
    return max(Decimal("0"), min(amount, subtotal))


def order_total(order) -> Decimal:
    """Recomputed from the order's lines and applied discounts; refreshes each AppliedDiscount.amount."""
    lines_total = order.lines.aggregate(total=Sum("line_total"))["total"] or Decimal("0")
    discount_total = Decimal("0")
    for applied in order.applied_discounts.all():
        amount = discount_amount_for(lines_total, applied)
        if applied.amount != amount:
            applied.amount = amount
            applied.save(update_fields=["amount", "modified"])
        discount_total += amount
    return max(Decimal("0"), lines_total - discount_total)


def discount_uses(discount) -> int:
    """Non-cancelled orders that used the discount."""
    return AppliedDiscount.objects.filter(discount=discount).exclude(order__fulfillment_status=Order.FulfillmentStatus.CANCELLED).count()


def discount_status(discount, today=None) -> str:
    today = today or timezone.localdate()
    if not discount.is_active:
        return "inactive"
    if discount.valid_from and discount.valid_from > today:
        return "scheduled"
    if discount.valid_until and discount.valid_until < today:
        return "expired"
    return "active"


def validate_discount(discount, member=None, today=None) -> str | None:
    """None when ``discount`` can be used by ``member`` now, else a user-facing reason."""
    if not discount.is_active:
        return "This discount code is not active."
    if not discount.is_within_validity(today):
        return "This discount code is not valid today."
    if discount.max_uses is not None and discount_uses(discount) >= discount.max_uses:
        return "This discount code has been used the maximum number of times."
    if discount.once_per_member and member is not None:
        used = AppliedDiscount.objects.filter(discount=discount, order__purchaser=member).exclude(order__fulfillment_status=Order.FulfillmentStatus.CANCELLED).exists()
        if used:
            return "This member already used this discount code."
    return None
