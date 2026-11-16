"""Per-request entitlements, so the auth backend (which has no request) can see them."""

from contextvars import ContextVar

current_entitlements: ContextVar = ContextVar("current_entitlements", default=None)


def get_current_entitlements():
    """The request's entitlements, or a fresh resolution for the active tenant outside a request (shell, commands)."""
    value = current_entitlements.get()
    if value is not None:
        return value
    from django.db import connection

    from club.models import Club

    from .entitlements import resolve_entitlements

    tenant = getattr(connection, "tenant", None)
    if tenant is not None and not isinstance(tenant, Club):
        # ``schema_context(...)`` (Celery sweeps, commands, tests) sets django-tenants' FakeTenant, which only knows its schema
        # name: resolve the real club, or fall back to the platform's bypass when there is none (the public schema).
        tenant = Club.objects.filter(schema_name=tenant.schema_name).first()
    return resolve_entitlements(tenant)
