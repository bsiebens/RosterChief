import logging

from django.utils import timezone

from club.models import Club, Season
from events.services.import_sources import SOURCE_RBIHF, source_enabled
from events.services.rbihf_standings import refresh_team
from features.commands import ScheduledJobCommand
from teams.models import Team

logger = logging.getLogger(__name__)


class Command(ScheduledJobCommand):
    help = "Recompute the league standings of every team imported from RBIHF for the current season."
    job_name = "events.tasks.refresh_standings"

    def handle(self, *args, **options):
        """Runs nightly (see features.jobs.JOB_REGISTRY) as the catch-all next to
        the refresh poll_live_game_results does the moment a game finishes --
        picks up results entered later, games the poll never saw going live, and
        other teams' games in the same league. Clubs without the RBIHF feature
        are skipped. One failing team never stops the sweep."""
        today = timezone.localdate()
        refreshed = failed = 0

        for club in Club.objects.active().order_by("name"):
            season = Season.covering(club, today)
            if season is None or not source_enabled(club, SOURCE_RBIHF):
                continue
            for team in Team.objects.filter(club=club).order_by("name"):
                try:
                    ok, errors = refresh_team(club, team, season)
                except Exception:
                    logger.exception("refresh_standings.team_failed club=%s team=%s", club.pk, team.pk)
                    failed += 1
                    continue
                refreshed += ok
                failed += errors

        summary = f"Refreshed standings for {refreshed} competition source(s); {failed} failed."
        self.stdout.write(self.style.SUCCESS(summary))
        return summary
