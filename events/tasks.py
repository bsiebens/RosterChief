import datetime
import logging

from celery import shared_task
from django.db import connection
from django.utils import timezone
from django_tenants.utils import get_public_schema_name, get_tenant_model, schema_context

from .seasons import ensure_seasons

logger = logging.getLogger(__name__)


@shared_task(name="events.create_missing_seasons")
def create_missing_seasons(today: datetime.date | None = None) -> str:
    """Give every active club a season covering today plus the next one, using the club's season start/duration.

    Scheduled every Monday at 02:00. Idempotent: clubs that already have their seasons are left alone.
    """
    today = today or timezone.localdate()
    created = 0
    failed = []
    for club in get_tenant_model().objects.filter(archived_at__isnull=True).exclude(schema_name=get_public_schema_name()):
        try:
            with schema_context(club.schema_name):
                created += len(ensure_seasons(today, start_month=club.season_start.month, start_day=club.season_start.day, duration_months=club.season_duration_months))
        except Exception:  # one broken club must not stop the others
            logger.exception("Creating seasons failed for club %s", club.slug)
            failed.append(club.slug)
    return f"created {created} season(s)" + (f"; failed: {', '.join(failed)}" if failed else "")


@shared_task(name="events.publish_scheduled_lineups")
def publish_scheduled_lineups() -> str:
    """Publish every line-up whose scheduled publish time has come, in every active club (the system publishes: no actor,
    no permission check; only the players whose situation changed are told). Scheduled every minute."""
    from .services.lineup import publish_due_lineups

    now = timezone.now()
    published = 0
    failed = []
    for club in get_tenant_model().objects.filter(archived_at__isnull=True).exclude(schema_name=get_public_schema_name()):
        try:
            with schema_context(club.schema_name):
                published += publish_due_lineups(now=now, club=club)
        except Exception:  # one broken club must not stop the others
            logger.exception("Publishing scheduled line-ups failed for club %s", club.slug)
            failed.append(club.slug)
    return f"published {published} line-up(s)" + (f"; failed: {', '.join(failed)}" if failed else "")


@shared_task(name="events.extend_event_series")
def extend_event_series() -> str:
    """Generate recurring-series occurrences up to the rolling horizon in every active club. Scheduled daily at 01:30."""
    from .services.recurrence import extend_all_series, horizon

    until = horizon()
    created = 0
    failed = []
    for club in get_tenant_model().objects.filter(archived_at__isnull=True).exclude(schema_name=get_public_schema_name()):
        try:
            with schema_context(club.schema_name):
                created += extend_all_series(until)[1]
        except Exception:  # one broken club must not stop the others
            logger.exception("Extending event series failed for club %s", club.slug)
            failed.append(club.slug)
    return f"generated {created} occurrence(s)" + (f"; failed: {', '.join(failed)}" if failed else "")


def _each_club(label, work) -> tuple[dict, list]:
    """Runs ``work()`` (returning a counts dict) in every active club schema; sums the counts, collects failed slugs."""
    totals: dict = {}
    failed = []
    for club in get_tenant_model().objects.filter(archived_at__isnull=True).exclude(schema_name=get_public_schema_name()):
        try:
            with schema_context(club.schema_name):
                for key, value in work().items():
                    totals[key] = totals.get(key, 0) + value
        except Exception:  # one broken club must not stop the others
            logger.exception("%s failed for club %s", label, club.slug)
            failed.append(club.slug)
    return totals, failed


@shared_task(name="events.poll_live_games")
def poll_live_games() -> str:
    """Fetch score, live flag and timeline of every game about to start (20 min ahead), live, or within an hour of its
    planned end, from its competition's data source, in every active club; a game seen finishing stops being polled.
    Changed timelines record moments and push them to followers. Scheduled every minute."""
    from .services.game_timeline import poll_live_games as poll

    now = timezone.now()
    counts, failed = _each_club("Polling live games", lambda: poll(now))
    summary = f"checked {counts.get('checked', 0)} game(s); fetched {counts.get('fetched', 0)}, {counts.get('failed', 0)} failed, {counts.get('finished', 0)} finished"
    return summary + (f"; clubs failed: {', '.join(failed)}" if failed else "")


@shared_task(name="events.settle_game_timelines")
def settle_game_timelines() -> str:
    """Re-fetch the timelines of games that finished 3 h - 7 days ago until they stop changing (federations correct them
    after the final whistle), in every active club. Scheduled daily at 01:45."""
    from .services.game_timeline import settle_timelines

    now = timezone.now()
    counts, failed = _each_club("Settling game timelines", lambda: settle_timelines(now))
    summary = f"{counts.get('finalized', 0)} finalized, {counts.get('changed', 0)} still changing, {counts.get('failed', 0)} failed, {counts.get('skipped', 0)} skipped"
    return summary + (f"; clubs failed: {', '.join(failed)}" if failed else "")


@shared_task(name="events.sync_event_attendances")
def sync_event_attendances(event_id) -> str:
    """Reconcile one event's attendance rows with its audience (queued for club-wide events, whose audience is the
    whole club: see events/signals.py). Runs in the club schema it was queued from."""
    from .models import Event
    from .services.attendance import sync_attendances

    event = Event.objects.filter(pk=event_id).first()
    if event is None:
        return "event gone"
    return f"added {len(sync_attendances([event]))} attendance row(s)"


@shared_task(name="events.sync_club_wide_events")
def sync_club_wide_events() -> str:
    """Re-sync every upcoming club-wide event (queued when a club membership is created, deleted or changes status).
    Runs in the club schema it was queued from."""
    from .services.attendance import future_events, sync_attendances

    events = future_events(club_wide=True)
    return f"synced {len(events)} event(s); added {len(sync_attendances(events))} attendance row(s)"


def _each_club_with(label, work) -> tuple[dict, list]:
    """Like ``_each_club`` but ``work(club)`` gets the club (for its features and admins)."""
    totals: dict = {}
    failed = []
    for club in get_tenant_model().objects.filter(archived_at__isnull=True).exclude(schema_name=get_public_schema_name()):
        try:
            with schema_context(club.schema_name):
                for key, value in work(club).items():
                    totals[key] = totals.get(key, 0) + value
        except Exception:  # one broken club must not stop the others
            logger.exception("%s failed for club %s", label, club.slug)
            failed.append(club.slug)
    return totals, failed


@shared_task(name="events.refresh_fixtures")
def refresh_fixtures(today: datetime.date | None = None) -> str:
    """Re-check every team's stored fixture import sources (Team.import_sources) for the current season in every active
    club whose features still allow the source: diff against the federation's schedule (never applied automatically,
    as before: locations/opponents need a human), record the outcome on the entry and send the club's admins one inbox
    notification when games moved, appeared or disappeared. Scheduled Mondays 06:15."""
    from .services.import_sources import check_club

    counts, failed = _each_club_with("Checking fixture sources", lambda club: check_club(club, today=today))
    summary = f"checked {counts.get('checked', 0)} fixture source(s): {counts.get('changed', 0)} with changes ({counts.get('errors', 0)} could not be fetched); notified {counts.get('notified', 0)} admin(s)"
    return summary + (f"; clubs failed: {', '.join(failed)}" if failed else "")


@shared_task(name="events.refresh_standings")
def refresh_standings(today: datetime.date | None = None) -> str:
    """Recompute the league standings of every team imported from RBIHF for the current season, in every active club
    with the RBIHF feature (the catch-all next to the refresh when a polled game finishes). Scheduled daily at 04:30."""
    from .services.rbihf_standings import refresh_club

    def work(club):
        refreshed, errors = refresh_club(club, today=today)
        return {"refreshed": refreshed, "failed": errors}

    counts, failed = _each_club_with("Refreshing standings", work)
    summary = f"refreshed standings for {counts.get('refreshed', 0)} competition source(s); {counts.get('failed', 0)} failed"
    return summary + (f"; clubs failed: {', '.join(failed)}" if failed else "")


@shared_task(name="events.run_team_fixtures")
def run_team_fixtures(team_id: str, apply: bool = False) -> dict:
    """Check (or, with ``apply``, import) one team's stored fixture sources for the current season. Runs in the club
    schema it was queued from; started from the calendar's import page."""
    from teams.models import Team

    from .services.import_sources import run_team

    club = get_tenant_model().objects.get(schema_name=connection.schema_name)
    return run_team(club, Team.objects.get(pk=team_id), apply=apply)


@shared_task(name="events.update_past_games")
def update_past_games(team_id: str) -> dict:
    """Fetch score and game sheet of every played, imported game of one team (queued from the calendar's import page).
    Runs in the club schema it was queued from."""
    from teams.models import Team

    from .services.game_timeline import update_past_games as update

    return update(Team.objects.get(pk=team_id))


@shared_task(name="events.refresh_member_stats")
def refresh_member_stats(job_id: str) -> dict:
    """Re-link a team's stored game sheets to its roster after someone joined it (the member's Stats tab shows a banner
    meanwhile). Runs in the club schema it was queued from."""
    from .models import StatsRefresh
    from .services.stats_refresh import run

    job = run(StatsRefresh.objects.get(pk=job_id))
    return {"games": job.games, "updated": job.updated, "status": job.status}
