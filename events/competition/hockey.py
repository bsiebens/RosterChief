import json
from urllib.parse import urljoin

import requests
from django.core.cache import cache
from django.utils.translation import gettext_lazy as _

from ..models import Event
from ..services import dfel_import, ijnl_import, timeline_parsers
from ..services.attendance import resolve_season
from ..services.game_timeline import TimelineFetch
from .base import CompetitionBaseClass

#: RBIHF's per-game roster/penalty-code object (posted back to timeline.php) --
#: doesn't change during a game, so one game-page fetch serves a while of polls.
RBIHF_GAME_DATA_CACHE_SECONDS = 10 * 60
#: The hockeydata API key scraped from the EHV-NRW team page.
HOCKEYDATA_KEY_CACHE_SECONDS = 24 * 60 * 60

RBIHF_PAGE_HEADERS = {"Cookie": "language=en", "User-Agent": "Mozilla/5.0 (compatible; RosterChief/1.0)"}


def season_code(event) -> str:
    """RBIHF/CEHL's season parameter ("2627") for the season covering ``event``."""
    game_season = resolve_season(event)
    return "{start}{end}".format(start=game_season.start_date.strftime("%y"), end=game_season.end_date.strftime("%y"))


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

        req = requests.get(self.url, params=payload, headers=headers, timeout=dfel_import.REQUEST_TIMEOUT_SECONDS)

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

    def fetch_timeline(self, event: Event):
        """timeline.php wants the object the game page hands to its own
        updateTimeline(...) posted back (rosters, penalty codes) -- without it
        the rows come back with empty names. So: game page (cached per game,
        the rosters don't change mid-game), then the POST."""
        if not event.external_game_id:
            return None
        season = season_code(event)
        game_url = f"https://www.rbihf.be/game/{season}/{event.external_game_id}"
        cache_key = f"rbihf:game-data:{season}:{event.external_game_id}"
        game_data = cache.get(cache_key)
        if game_data is None:
            page = requests.get(game_url, headers=RBIHF_PAGE_HEADERS, timeout=dfel_import.REQUEST_TIMEOUT_SECONDS)
            page.raise_for_status()
            game_data = timeline_parsers.rbihf_extract_gd(page.text)
            cache.set(cache_key, game_data, RBIHF_GAME_DATA_CACHE_SECONDS)

        headers = {**RBIHF_PAGE_HEADERS, "X-Requested-With": "XMLHttpRequest", "Referer": game_url}
        response = requests.post("https://www.rbihf.be/modules/league/ajax/timeline.php", data=timeline_parsers.rbihf_form_fields(game_data), headers=headers, timeout=dfel_import.REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
        return TimelineFetch(document=timeline_parsers.parse_rbihf(response.text, game_data), raw=response.text)


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

        timeline_req = requests.get(timeline_url, params=payload, headers=headers, timeout=dfel_import.REQUEST_TIMEOUT_SECONDS)
        score_req = requests.get(score_url, params=payload, headers=headers, timeout=dfel_import.REQUEST_TIMEOUT_SECONDS)
        # timeline.php already carries the whole timeline -- kept for fetch_timeline.
        self.timeline_payload = (event.pk, timeline_req.text) if timeline_req.status_code == 200 else None

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

    def fetch_timeline(self, event: Event):
        """Reuses the timeline.php response update_game_information just
        fetched; fetches it on its own otherwise (the settle/backfill jobs)."""
        if not event.external_game_id:
            return None
        stashed = getattr(self, "timeline_payload", None)
        if stashed and stashed[0] == event.pk:
            raw = stashed[1]
        else:
            season = season_code(event)
            headers = {"Cookie": "language=en", "User-Agent": "Mozilla/5.0 (compatible; RosterChief/1.0)", "X-Requested-With": "XMLHttpRequest", "Referer": urljoin("https://www.cehl.eu", f"game/{season}/{event.external_game_id}")}
            response = requests.get(urljoin(self.url, "timeline.php"), params={"nr": event.external_game_id, "season": season}, headers=headers, timeout=dfel_import.REQUEST_TIMEOUT_SECONDS)
            response.raise_for_status()
            raw = response.text
        return TimelineFetch(document=timeline_parsers.parse_cehl(raw), raw=raw)


class DFEL(CompetitionBaseClass):
    """German women's ice hockey league (EHV-NRW), via the hockeydata.net
    schedule -- see events.services.dfel_import. There's no single-game
    endpoint, so this re-fetches the game's division schedule (from
    event.external_source_id, "team/division") and picks the game out by
    its hockeydata uuid (event.external_game_id)."""

    def update_game_information(self, event: Event) -> None:
        team_id, _sep, division_id = (event.external_source_id or "").partition("/")
        if not team_id or not division_id:
            raise ValueError(_("This game has no DFEL team/division source id -- re-import it from the team's EHV-NRW page."))
        if not event.external_game_id:
            raise ValueError(_("This game has no DFEL game id."))

        raw_json = dfel_import.fetch_schedule(team_id, division_id)
        game = dfel_import.find_game(raw_json, event.external_game_id)
        if game is None:
            raise ValueError(_("Game %(game_id)s is not in the DFEL schedule any more.") % {"game_id": event.external_game_id})

        event.is_live = game.get("gameStatus") == dfel_import.LIVE_GAME_STATUS and not game.get("gameHasEnded")

        if event.is_home_game:
            event.score_for = game.get("homeTeamScore")
            event.score_against = game.get("awayTeamScore")
        else:
            event.score_for = game.get("awayTeamScore")
            event.score_against = game.get("homeTeamScore")

        event.save(update_fields=["is_live", "score_for", "score_against"])

    def fetch_timeline(self, event: Event):
        """hockeydata's GetGameReport for the game -- goals with stable guids,
        penalties, goalie changes, team stats. The API key comes from the
        team's EHV-NRW page (cached a day rather than scraped every poll)."""
        team_id, _sep, division_id = (event.external_source_id or "").partition("/")
        if not team_id or not division_id or not event.external_game_id:
            return None
        cache_key = f"dfel:api-key:{team_id}/{division_id}"
        api_key = cache.get(cache_key)
        if api_key is None:
            api_key = dfel_import.fetch_api_key(team_id, division_id)
            cache.set(cache_key, api_key, HOCKEYDATA_KEY_CACHE_SECONDS)
        raw = dfel_import.fetch_game_report(api_key, event.external_game_id)
        return TimelineFetch(document=timeline_parsers.parse_hockeydata(raw), raw=raw)


class IJNL(CompetitionBaseClass):
    """IJshockey Nederland (ijshockey.nl), see events.services.ijnl_import. Each
    game has its own live-data endpoint keyed by its uuid
    (event.external_game_id), which the site's own game page polls."""

    def update_game_information(self, event: Event) -> None:
        if not event.external_game_id:
            raise ValueError(_("This game has no ijshockey.nl game id."))

        game = ijnl_import.fetch_game(event.external_game_id)
        # The same response carries the timeline -- kept for fetch_timeline.
        self.game_payload = (event.pk, game)
        home_score, away_score = ijnl_import.parse_score(game)

        event.is_live = game.get("state") == ijnl_import.LIVE_STATE

        if event.is_home_game:
            event.score_for, event.score_against = home_score, away_score
        else:
            event.score_for, event.score_against = away_score, home_score

        event.save(update_fields=["is_live", "score_for", "score_against"])

    def fetch_timeline(self, event: Event):
        """Parses the game response update_game_information just fetched (its
        ``sections.timeline``); fetches it on its own otherwise."""
        if not event.external_game_id:
            return None
        stashed = getattr(self, "game_payload", None)
        game = stashed[1] if stashed and stashed[0] == event.pk else ijnl_import.fetch_game(event.external_game_id)
        raw = json.dumps(game, ensure_ascii=False)
        return TimelineFetch(document=timeline_parsers.parse_ijnl(raw), raw=raw)
