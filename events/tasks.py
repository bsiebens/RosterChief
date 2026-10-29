from celery import shared_task
from django.utils.translation import gettext as _

from api.core.errors import ApiError
from api.core.jobs import JobContext, job_task
from features.celery_support import run_job


@shared_task(name="events.extend-event-series")
def extend_event_series():
    return run_job("extend_event_series")


@shared_task(name="events.send-deadline-reminders")
def send_deadline_reminders():
    return run_job("send_deadline_reminders")


@shared_task(name="events.publish-scheduled-lineups")
def publish_scheduled_lineups():
    return run_job("publish_scheduled_lineups")


@shared_task(name="events.sync-crew-invites")
def sync_crew_invites():
    return run_job("sync_crew_invites")


@shared_task(name="events.poll-live-game-results")
def poll_live_game_results():
    return run_job("poll_live_game_results")


@shared_task(name="events.check-fixture-sources")
def check_fixture_sources():
    return run_job("check_fixture_sources")


@shared_task(name="events.refresh-standings")
def refresh_standings():
    return run_job("refresh_standings")


@shared_task(name="events.settle-game-timelines")
def settle_game_timelines():
    return run_job("settle_game_timelines")


# --- management API jobs (api.panel.routers.event_extras): started with api.core.jobs.start_job ---------
# Defined here, not in the router module, so the Celery worker (which autodiscovers ``tasks``) registers them.


@job_task(name="events.api-import-fixtures")
def api_import_fixtures(ctx: JobContext, source, team_id, season_id, url, competition_label, locations, opponents):
    from club.models import Season
    from events.services.fixture_import import import_from_source
    from events.services.import_sources import ImportSourceError
    from teams.models import Team

    club = ctx.club
    team = Team.objects.filter(club=club, pk=team_id).first()
    season = Season.objects.filter(club=club, pk=season_id).first()
    if team is None or season is None:
        raise ApiError(404, "not_found", _("Team or season not found."))
    try:
        return import_from_source(club, source, team=team, season=season, url=url, competition_label=competition_label, locations_by_game_id=locations, opponents_by_game_id=opponents)
    except ImportSourceError as error:
        raise ApiError(422, "import_failed", str(error)) from error


@job_task(name="events.api-recheck-fixtures")
def api_recheck_fixtures(ctx: JobContext, season_id, team_id, choices):
    from club.models import Season
    from events.services.fixture_import import recheck_and_apply, recheck_key
    from teams.models import Team

    club = ctx.club
    season = Season.objects.filter(club=club, pk=season_id).first()
    if season is None:
        raise ApiError(404, "not_found", _("Season not found."))
    teams = Team.objects.filter(club=club).order_by("name")
    if team_id:
        teams = teams.filter(pk=team_id)
    by_key = {recheck_key(choice["team_id"], choice): (choice.get("locations") or {}, choice.get("opponents") or {}) for choice in choices}
    return recheck_and_apply(club, season, teams, by_key)
