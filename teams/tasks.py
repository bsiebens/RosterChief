# Management API jobs (api.core.jobs): started by api/panel/routers/team_extras.py, polled at GET /manage/jobs/{id}.
# They live here so the Celery worker's autodiscovery registers them without importing the API.

from django.utils.translation import gettext as _

from api.core.errors import ApiError
from api.core.jobs import JobContext, job_task


@job_task(name="teams.sync-fixture-sources")
def sync_fixture_sources_job(ctx: JobContext, team_id: str, season_id: str, apply: bool):
    from club.models import Season
    from events.services import import_sources
    from teams.models import Team

    team = Team.objects.filter(club=ctx.club, pk=team_id).first()
    season = Season.objects.filter(club=ctx.club, pk=season_id).first()
    if team is None or season is None:
        raise ApiError(404, "not_found", _("Not found."))
    return import_sources.sync_team_sources(ctx.club, team, season, apply=apply)
