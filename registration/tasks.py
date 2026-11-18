import logging

from celery import shared_task

log = logging.getLogger(__name__)


@shared_task(name="registration.send_payment_reminders")
def send_payment_reminders() -> str:
    """Tell the people who handle payments, in every active club, 10 and 5 days before a registration payment falls due, to check that
    everything received so far has been registered correctly. Scheduled daily (see ``registration.services.payment_reminders``)."""
    from django_tenants.utils import get_public_schema_name, get_tenant_model, schema_context

    from registration.services import payment_reminders

    notices = told = 0
    failed = []
    for club in get_tenant_model().objects.filter(archived_at__isnull=True).exclude(schema_name=get_public_schema_name()):
        try:
            with schema_context(club.schema_name):
                counts = payment_reminders.send_due()
                notices += counts["notices"]
                told += counts["told"]
        except Exception:
            log.exception("Payment reminders failed for club %s", club.slug)
            failed.append(club.slug)
    return f"{notices} notice(s), {told} account(s) told" + (f"; failed: {', '.join(failed)}" if failed else "")
