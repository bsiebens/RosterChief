from typing import TypedDict

from ..models import Event


class GameInformation(TypedDict):
    live: bool
    scoreA: int
    scoreB: int


class CompetitionBaseClass:
    """The interface every competition data source implements (stable: the live-score poller and the game timeline code
    call it). An instance is used for one game at a time: ``fetch_timeline`` is called right after
    ``update_game_information`` on the same instance, so a source may reuse what that call downloaded."""

    url: str

    def __init__(self):
        self.url = "http://localhost"

    def update_game_information(self, event: Event) -> None:
        """Fetch ``event``'s score and live state from the source and save them on the event
        (``is_live``, ``score_for``, ``score_against``). May raise: the caller reports the error."""
        raise NotImplementedError

    def fetch_timeline(self, event: Event):
        """What happened during ``event`` (goals, penalties, ...) as an ``events.services.game_timeline.TimelineFetch``,
        or None when this source offers no timeline. May raise: the caller records the error."""
        return None
