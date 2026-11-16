"""Re-linking a team's game stats after a player joins its roster (``events.StatsRefresh``).

The game sheets name players by jersey number and name; ``game_stats.sync_game_stats`` matches them against the team's
roster when it writes the stat rows. A player added afterwards is therefore missing from the games already stored, so
adding someone to a team queues ``events.refresh_member_stats`` which re-syncs every stored game sheet of the team that is
out of sync with the roster. Until it finishes the member's Stats tab shows a banner (``in_progress``).
"""

import datetime
import logging

from django.db import transaction
from django.utils import timezone

from events.models import Event, GameTimeline, StatsRefresh

logger = logging.getLogger(__name__)

#: A job that is still "running" after this long is treated as lost (a worker died): it no longer shows the banner.
STALE_AFTER = datetime.timedelta(hours=1)


def in_progress(member) -> bool:
    return StatsRefresh.objects.filter(member=member, status=StatsRefresh.Status.RUNNING, created__gte=timezone.now() - STALE_AFTER).exists()


def queue_for_roster(membership) -> StatsRefresh | None:
    """Starts the background re-link for a new roster spot; None when the team has no stored game sheet to re-link."""
    if not GameTimeline.objects.filter(event__teams=membership.team_id).exclude(data={}).exists():
        return None
    job = StatsRefresh.objects.create(member=membership.member, team=membership.team)
    from events.tasks import refresh_member_stats

    transaction.on_commit(lambda: refresh_member_stats.delay(str(job.pk)))
    return job


def run(job: StatsRefresh) -> StatsRefresh:
    """Re-syncs the stat rows of the team's stored game sheets that no longer match its roster. Never raises: a failure
    is recorded on the job."""
    from events.services.game_stats import ensure_game_stats

    try:
        games = Event.objects.filter(teams=job.team_id, kind=Event.EventKind.GAME, cancelled=False, timeline__isnull=False).select_related("timeline", "opponent", "location")
        checked = updated = 0
        for event in games:
            if not event.timeline.data:
                continue
            checked += 1
            updated += bool(ensure_game_stats(event, event.timeline.data))
        job.games, job.updated, job.status = checked, updated, StatsRefresh.Status.DONE
    except Exception as error:  # the banner must go away either way
        logger.exception("stats_refresh.failed job=%s", job.pk)
        job.status, job.error = StatsRefresh.Status.FAILED, str(error)[:500]
    job.finished_at = timezone.now()
    job.save(update_fields=["games", "updated", "status", "error", "finished_at", "modified"])
    return job
