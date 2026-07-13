class BillingError(Exception):
    """A billing action that must not silently half-happen.

    Lives here rather than in dues.py so invoices.py can raise it without the two modules
    importing each other in a circle.
    """
