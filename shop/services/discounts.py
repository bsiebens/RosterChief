"""Discount codes as the management screens (web and API) list them."""

from django.db.models import Count, Q

from ..models import Discount, Order


def discounts_with_use_count(club):
    """The club's discounts, each with ``use_count``: orders that used the code and weren't cancelled --
    the same count Discount.max_uses is enforced against (shop.services.checkout.discount_uses)."""
    uses = Count("applied_discounts", filter=~Q(applied_discounts__order__fulfillment_status=Order.FulfillmentStatus.CANCELLED))
    return Discount.objects.filter(club=club).annotate(use_count=uses)


def code_taken(club, code: str, *, exclude_pk=None) -> bool:
    """Whether another discount of the club already uses ``code`` (compared as Discount.save stores it: stripped, uppercased)."""
    return Discount.objects.filter(club=club, code=(code or "").upper().strip()).exclude(pk=exclude_pk).exists()
