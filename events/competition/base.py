from typing import TypedDict

from ..models import Event


class GameInformation(TypedDict):
    live: bool
    scoreA: int
    scoreB: int


class CompetitionBaseClass:
    url: str

    def __init__(self):
        self.url = "http://localhost"

    def update_game_information(self, event: Event) -> None:
        raise NotImplementedError

    def fetch_timeline(self, event: Event):
        """What happened during ``event`` (goals, penalties, ...) as an
        events.services.game_timeline.TimelineFetch, or None when this source
        offers no timeline. Called right after update_game_information on the
        same instance, so a source may reuse what that call downloaded. May raise
        -- the caller records the error. See GAME_TIMELINE.md."""
        return None
