class BillingError(Exception):
    """A billing operation that cannot be done (no price, already subscribed, nothing to pay, ...)."""
