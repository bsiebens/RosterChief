from celery import shared_task


@shared_task(name="tournaments.notify_shifts_open")
def notify_shifts_open(tournament_id) -> str:
    """Tell the home team's squad that a freshly published tournament needs volunteers. Runs in the club schema it was queued from."""
    from .services.notifications import notify_tournament_shifts_open

    return notify_tournament_shifts_open(tournament_id)
