import datetime
import logging

from django.utils import timezone

from events.models import Event
from events.services.competitions import CompetitionFetchError, fetch_game_info
from features.commands import ScheduledJobCommand

logger = logging.getLogger(__name__)

#: How long before kickoff / after the planned end a game is worth polling --
#: matches the window the federation actually has fresh data for. Outside it,
#: fetch_game_info would either find nothing to report yet or nothing new.
POLL_LEAD_TIME = datetime.timedelta(minutes=20)
POLL_TRAIL_TIME = datetime.timedelta(hours=1)


class Command(ScheduledJobCommand):
    help = "Poll each game due to start soon or still within its post-game window for a fresh score/live status from its competition's data source."
    job_name = "events.tasks.poll_live_game_results"

    def handle(self, *args, **options):
        """Runs every minute (see features.jobs.JOB_REGISTRY). A game only stays a
        candidate from 20 minutes before its scheduled start through 1 hour after
        its planned end -- outside that window there's nothing to poll for yet, or
        nothing left worth checking. Once a game is seen going live and then coming
        back off live, it's finished; live_score_polling_done_at is stamped so the
        rest of that trailing hour doesn't keep calling out to the data source for
        no further benefit.

        fetch_game_info itself already gates on the competition's flag being active
        for the event's club and no-ops (returns False) when it isn't configured --
        that's not a failure, just nothing to do here. A genuine fetch failure
        (network error, bad response, ...) is logged and skipped so one bad game
        doesn't stop the rest of the sweep.
        """
        now = timezone.now()
        candidates = list(
            Event.objects.filter(
                kind=Event.EventKind.GAME,
                cancelled=False,
                live_score_polling_done_at__isnull=True,
                start__lte=now + POLL_LEAD_TIME,
                end__gte=now - POLL_TRAIL_TIME,
            )
            .exclude(competition="")
            .exclude(external_game_id="")
            .select_related("club")
        )

        fetched = 0
        failed = 0
        for event in candidates:
            was_live = event.is_live
            try:
                if not fetch_game_info(event):
                    continue
            except CompetitionFetchError:
                failed += 1
                logger.warning("poll_live_game_results.fetch_failed event_id=%s", event.pk, exc_info=True)
                continue

            fetched += 1
            if was_live and not event.is_live:
                event.live_score_polling_done_at = now
                event.save(update_fields=["live_score_polling_done_at"])

        return f"Checked {len(candidates)} game(s); fetched {fetched}, {failed} failed."
