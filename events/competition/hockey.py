from urllib.parse import urljoin

import requests

from ..models import Event
from ..services.attendance import resolve_season
from .base import CompetitionBaseClass


class RBIHF(CompetitionBaseClass):
    def __init__(self):
        super().__init__()
        self.url = "https://rbihf.be/modules/league/ajax/time.php"

    def update_game_information(self, event: Event) -> None:
        # event.season is blank whenever it was never set explicitly (see
        # Event.season's own help_text) -- resolve_season falls back to
        # whichever season actually covers the game's own start date, same
        # as every other event-scoped lookup in this codebase (roster/
        # attendance audience, referee/official eligibility).
        game_season = resolve_season(event)
        season = "{start}{end}".format(start=game_season.start_date.strftime("%y"), end=game_season.end_date.strftime("%y"))

        payload = {"gameNr": event.external_game_id, "season": season}
        headers = {
            "Cookie": "language=en",
            "Postman-Token": "rosterchief",
            "Host": "www.rbihf.be",
            "User-Agent": "PostmanRuntime/7.37.0",
            "Accept": "application/json",
            "Accept-Encoding": "gzip,deflate,br",
            "Connection": "keep-alive",
            "Referer": f"https://rbihf.be/game/{event.external_game_id}",
            "X-Requested-With": "XMLHttpRequest",
        }

        req = requests.get(self.url, params=payload, headers=headers)

        if req.status_code == 200:
            game_data = req.json()

            event.is_live = game_data["live"]

            if event.is_home_game:
                event.score_for = game_data["scoreA"]
                event.score_against = game_data["scoreB"]
            else:
                event.score_for = game_data["scoreB"]
                event.score_against = game_data["scoreA"]

            event.save(update_fields=["is_live", "score_for", "score_against"])


class CEHL(CompetitionBaseClass):
    def __init__(self):
        super().__init__()
        self.url = "https://www.cehl.eu/ajax/"

    def update_game_information(self, event: Event) -> None:
        game_season = resolve_season(event)
        season = "{start}{end}".format(start=game_season.start_date.strftime("%y"), end=game_season.end_date.strftime("%y"))

        referer_url = urljoin("https://www.cehl.eu", f"game/{season}/{event.external_game_id}")
        timeline_url = urljoin(self.url, "timeline.php")
        score_url = urljoin(self.url, "score.php")

        payload = {"nr": event.external_game_id, "season": season}
        headers = {
            "Cookie": "language=en",
            "Postman-Token": "rosterchief",
            "Host": "www.cehl.eu",
            "User-Agent": "PostmanRuntime/7.37.0",
            "Accept": "*/*",
            "Accept-Encoding": "gzip,deflate,br",
            "Connection": "close",
            "Referer": referer_url,
            "X-Requested-With": "XMLHttpRequest",
        }

        timeline_req = requests.get(timeline_url, params=payload, headers=headers)
        score_req = requests.get(score_url, params=payload, headers=headers)

        if timeline_req.status_code == 200:
            event.is_live = timeline_req.json()["live"] == 1

        if score_req.status_code == 200:
            game_data = score_req.json()

            if event.is_home_game:
                event.score_for = game_data["scoreA"]
                event.score_against = game_data["scoreB"]

            else:
                event.score_for = game_data["scoreB"]
                event.score_against = game_data["scoreA"]

        if timeline_req.status_code == 200 or score_req.status_code == 200:
            event.save(update_fields=["is_live", "score_for", "score_against"])
