"""Signal wiring for the tournaments app, registered from TournamentsConfig.ready.

Keeps a tournament's home-team roster in step with the line-up of its own calendar event (tournaments.services.registration.
ensure_home_team creates that event scoped to the home team). The coach picks the players on the event's line-up screen and the
roster follows, so nothing is typed twice. The logic lives here, not in the events app, so tournament-specific code stays out of it.
"""

from django.db.models.signals import post_save
from django.dispatch import receiver

from events.models import Attendance, Lineup
from events.services.lineup import lineup_changed

from .models import Tournament
from .services.registration import sync_home_roster


def _sync_for_event(event) -> None:
    """Cheaply a no-op for the overwhelming majority of events: only a tournament's own linked event gets past the lookup."""
    try:
        tournament = event.tournament
    except Tournament.DoesNotExist:
        return
    sync_home_roster(tournament)


@receiver(lineup_changed, sender=Lineup)
def sync_roster_on_lineup_change(sender, lineup, **kwargs):
    """The picks changed: add the newly picked players to the home roster and drop the un-picked ones."""
    _sync_for_event(lineup.event)


@receiver(post_save, sender=Attendance)
def sync_roster_on_attendance_change(sender, instance, **kwargs):
    """A picked player declined (or changed their mind again): the roster follows. Everything else about an answer leaves it as it was."""
    _sync_for_event(instance.event)
