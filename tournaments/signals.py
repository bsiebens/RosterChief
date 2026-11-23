"""Signal wiring for the tournaments app, registered from TournamentsConfig.ready.

Keeps a club team's roster in step with the line-up of its own calendar event (tournaments.services.registration.ensure_team_event
creates that event, scoped to the team). The coach picks the players on the event's line-up screen and the roster follows, so nothing
is typed twice. The logic lives here, not in the events app, so tournament-specific code stays out of it.
"""

from django.db.models.signals import post_save
from django.dispatch import receiver

from events.models import Attendance, Lineup
from events.services.lineup import lineup_changed

from .models import TournamentTeam
from .services.registration import sync_team_roster


def _sync_for_event(event) -> None:
    """Cheaply a no-op for the overwhelming majority of events: only a tournament team's own event gets past the lookup."""
    try:
        team = event.tournament_team
    except TournamentTeam.DoesNotExist:
        return
    sync_team_roster(team)


@receiver(lineup_changed, sender=Lineup)
def sync_roster_on_lineup_change(sender, lineup, **kwargs):
    """The picks changed: add the newly picked players to the roster and drop the un-picked ones."""
    _sync_for_event(lineup.event)


@receiver(post_save, sender=Attendance)
def sync_roster_on_attendance_change(sender, instance, **kwargs):
    """A picked player declined (or changed their mind again): the roster follows. Everything else about an answer leaves it as it was."""
    _sync_for_event(instance.event)
