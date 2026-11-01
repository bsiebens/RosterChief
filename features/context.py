"""Per-request entitlements, so the auth backend (which has no request) can see them."""

from contextvars import ContextVar

current_entitlements: ContextVar = ContextVar("current_entitlements", default=None)


def get_current_entitlements():
    """The request's entitlements, or a fresh resolution for the active tenant outside a request (shell, commands)."""
    value = current_entitlements.get()
    if value is not None:
        return value
    from django.db import connection

    from .entitlements import resolve_entitlements

    return resolve_entitlements(getattr(connection, "tenant", None))
