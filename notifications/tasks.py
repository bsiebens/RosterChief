"""Celery tasks of the push engine. They work on shared tables, so they always run in the public schema."""

from celery import shared_task
from django_tenants.utils import get_public_schema_name, schema_context


@shared_task(name="notifications.process_outbox")
def process_outbox() -> str:
    from . import outbox

    with schema_context(get_public_schema_name()):
        counts = outbox.process_outbox()
    return f"outbox: {counts['done']} done, {counts['failed']} failed, {counts['recovered']} recovered"


@shared_task(name="notifications.retry_failed_deliveries")
def retry_failed_deliveries() -> str:
    from . import delivery

    with schema_context(get_public_schema_name()):
        attempted = delivery.retry_due()
    return f"retry: {attempted} attempted"


@shared_task(name="notifications.check_push_health")
def check_push_health() -> str:
    from . import alerts, sandbox

    with schema_context(get_public_schema_name()):
        sandbox.expire_if_due()
        counts = alerts.evaluate()
    return "alerts: " + ", ".join(f"{k} {v}" for k, v in counts.items())


@shared_task(name="notifications.cleanup")
def cleanup() -> str:
    from . import cleanup as cleanup_module

    with schema_context(get_public_schema_name()):
        counts = cleanup_module.run()
    return "cleanup: " + ", ".join(f"{k} {v}" for k, v in counts.items())
