import logging

from celery import shared_task
from django.utils import timezone
from django_tenants.utils import get_public_schema_name, get_tenant_model, schema_context

logger = logging.getLogger(__name__)


@shared_task(name="formbuilder.notify_send")
def notify_send(send_id: str) -> str:
    """Tell one send's audience (runs in the club schema it was queued from). A send that is not open yet is left for the sweep."""
    from .services import notifications

    return f"told {notifications.notify_now(send_id)} account(s)"


@shared_task(name="formbuilder.notify_due_sends")
def notify_due_sends() -> str:
    """Announce every on, open, not yet announced send in every active club (this delivers scheduled sends when they open)."""
    from .services import notifications

    now = timezone.now()
    sends = told = 0
    failed = []
    for club in get_tenant_model().objects.filter(archived_at__isnull=True).exclude(schema_name=get_public_schema_name()):
        try:
            with schema_context(club.schema_name):
                counts = notifications.notify_due(now)
                sends += counts["sends"]
                told += counts["told"]
        except Exception:
            logger.exception("Notifying form sends failed for club %s", club.slug)
            failed.append(club.slug)
    return f"told {told} account(s) for {sends} form send(s)" + (f"; failed: {', '.join(failed)}" if failed else "")


@shared_task(name="formbuilder.send_form_reminders")
def send_form_reminders() -> str:
    """Nudge, once per send, whoever has not responded to a send closing within 24-48 hours. Scheduled hourly."""
    from .services import notifications

    result = notifications.send_form_reminders()
    return f"reminded {result['told']} account(s) for {result['sends']} form send(s)" + (f"; failed: {', '.join(result['failed'])}" if result["failed"] else "")
