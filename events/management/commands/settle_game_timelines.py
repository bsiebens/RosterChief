import logging

from django.utils import timezone

from events.services.competitions import CompetitionFetchError, competition_for
from events.services.game_timeline import FAILED, FINALIZED, settle, settle_candidates, timeline_enabled
from features.commands import ScheduledJobCommand

logger = logging.getLogger(__name__)


class Command(ScheduledJobCommand):
    help = "Re-fetch the timelines of recently finished games until they stop changing (federations correct them after the final whistle)."
    job_name = "events.tasks.settle_game_timelines"

    def handle(self, *args, **options):
        """Runs nightly (see features.jobs.JOB_REGISTRY). poll_live_game_results
        stops polling a game as soon as it's seen finishing, which would freeze
        a timeline at its first, often incomplete, final version -- this keeps
        re-fetching games that finished in the last week (events.services.
        game_timeline.settle_candidates) until a fetch comes back unchanged,
        then marks them finalized. Clubs whose "game_timeline" flag (or
        competition flag) was turned off are skipped. One failing game never
        stops the sweep."""
        counts = {}
        enabled = {}
        for timeline in settle_candidates(timezone.now()):
            event = timeline.event
            if event.club_id not in enabled:
                enabled[event.club_id] = timeline_enabled(event.club)
            if not enabled[event.club_id]:
                continue
            try:
                source = competition_for(event)
            except CompetitionFetchError:
                logger.warning("settle_game_timelines.misconfigured event_id=%s competition=%s", event.pk, event.competition)
                source = None
            if source is None:
                counts["skipped"] = counts.get("skipped", 0) + 1
                continue
            outcome = settle(timeline, source)
            counts[outcome] = counts.get(outcome, 0) + 1

        summary = f"Settled game timelines: {counts.get(FINALIZED, 0)} finalized, {counts.get('changed', 0)} still changing, {counts.get(FAILED, 0)} failed, {counts.get('skipped', 0)} skipped."
        self.stdout.write(self.style.SUCCESS(summary))
        return summary
