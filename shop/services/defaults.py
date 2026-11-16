"""The shop's default texts: pickup instructions come from the shop settings, payment instructions from the club settings
(one text for everything the club asks people to pay, so the shop never keeps its own copy)."""

from shop.settings_models import ShopSettings


def pickup_instructions() -> str:
    """Used when an order is marked ready without instructions of its own."""
    return ShopSettings.current().pickup_instructions


def payment_instructions() -> str:
    """How to pay, as set in the club settings (``Club.payment_instructions``); empty when the club left it blank."""
    from events.services.attendance import current_club

    club = current_club()
    return (club.payment_instructions if club is not None else "") or ""
