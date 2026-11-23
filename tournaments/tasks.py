from celery import shared_task


@shared_task(name="tournaments.notify_shifts_open")
def notify_shifts_open(tournament_id) -> str:
    """Tell the home team's squad that a freshly published tournament needs volunteers. Runs in the club schema it was queued from."""
    from .services.notifications import notify_tournament_shifts_open

    return notify_tournament_shifts_open(tournament_id)


@shared_task(name="tournaments.send_alert")
def send_alert(function: str, *args) -> None:
    """One event alert (game started / ended, goal, penalty), queued from the staff's save; runs in the club schema it was queued from."""
    from .services import alerts

    if function in ("game_started", "game_ended", "event_recorded"):
        getattr(alerts, function)(*args)


@shared_task(name="tournaments.send_due_alerts")
def send_due_alerts() -> str:
    """Tell followers that a game or block starts in 5 minutes, in every active club with the tournaments feature. Scheduled every minute."""
    import logging

    from django_tenants.utils import get_public_schema_name, get_tenant_model, schema_context

    from .services import alerts

    log = logging.getLogger(__name__)
    sent = 0
    for club in get_tenant_model().objects.filter(archived_at__isnull=True).exclude(schema_name=get_public_schema_name()):
        try:
            with schema_context(club.schema_name):
                sent += alerts.send_due_alerts()
        except Exception:  # one club's problem (or a club without the tournaments tables) must not stop the others
            log.exception("tournaments.due_alerts_failed", extra={"club": club.slug})
    return f"sent {sent} alert(s)"


@shared_task(name="tournaments.invoice_due_balances")
def invoice_due_balances() -> str:
    """Send the balance invoice (entry fee minus deposit, plus lunches) of guest teams of tournaments that start within a week, in every
    active club with the tournaments tables. Scheduled daily."""
    import logging

    from django_tenants.utils import get_public_schema_name, get_tenant_model, schema_context

    from .services import billing

    log = logging.getLogger(__name__)
    sent = 0
    for club in get_tenant_model().objects.filter(archived_at__isnull=True).exclude(schema_name=get_public_schema_name()):
        try:
            with schema_context(club.schema_name):
                sent += billing.invoice_due_balances()
        except Exception:
            log.exception("tournaments.balance_invoices_failed", extra={"club": club.slug})
    return f"invoiced {sent} balance(s)"
