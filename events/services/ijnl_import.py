"""Importing a team's fixture list from IJshockey Nederland (IJNL, the Dutch
ice hockey federation) at ijshockey.nl.

The public team page (``https://www.ijshockey.nl/competities/team/<team_id>?seizoen=2026%2F27``)
renders its schedule client-side from a JSON endpoint next to it
(``.../team/<team_id>/data?seizoen=...``), which is what's fetched here. One
response covers *every* division (``onderdeel``: regular season, playoffs, cup,
...) the team plays in that season, so one import brings them all in.

Each game is keyed by its ``gameGuid`` (a UUID), and has its own JSON endpoint
(``/competities/wedstrijd/<uuid>/data``) that the site polls for live scores --
that backs the single-game score refresh, events.competition.hockey.IJNL.

Same two-step preview/confirm flow as events.services.rbihf_import, sharing its
diff/apply core (``plan_from_fixtures``/``apply_plan``): ``fetch_team_data``
returns plain JSON text a view can stash in the session, and ``build_plan`` is
pure with respect to the network once it has that text.
"""

import json
import re
from datetime import datetime
from urllib.parse import parse_qs, quote, urlsplit
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from events.services.rbihf_import import ImportPlan, ScrapedFixture, apply_plan, plan_from_fixtures

__all__ = ["IJNLImportError", "ImportPlan", "apply_plan", "build_plan", "extract_ids", "fetch_game", "fetch_team_data", "parse_fixtures", "parse_score", "season_of", "source_id", "team_page_url"]

COMPETITION = "IJNL"

HOSTS = {"ijshockey.nl", "www.ijshockey.nl"}
TEAM_PATH_RE = re.compile(r"^/competities/team/(?P<team_id>\d+)/?$")
SEASON_RE = re.compile(r"^\d{4}/\d{2}$")
TEAM_PAGE_URL = "https://www.ijshockey.nl/competities/team/{team_id}?seizoen={season}"
TEAM_DATA_URL = "https://www.ijshockey.nl/competities/team/{team_id}/data"
GAME_DATA_URL = "https://www.ijshockey.nl/competities/wedstrijd/{game_id}/data"

#: The schedule's dates/times are Dutch wall-clock time.
SOURCE_TIMEZONE = ZoneInfo("Europe/Amsterdam")
#: What the site itself shows for a game whose date is set but hour isn't yet.
UNKNOWN_TIME = "12:00"

LIVE_STATE = "live"

REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; RosterChief/1.0)",
    "Accept": "application/json",
}
REQUEST_TIMEOUT_SECONDS = 30


class IJNLImportError(Exception):
    """The team data couldn't be fetched, or didn't look like an IJNL team schedule."""


def extract_ids(url: str) -> tuple[str, str]:
    """Returns (team_id, season) from an ijshockey.nl team page URL -- season
    (e.g. "2026/27") is "" when the URL has no ``seizoen``, meaning "whatever
    season the site currently shows" (see ``season_of``)."""
    parts = urlsplit(url.strip())
    match = TEAM_PATH_RE.match(parts.path)
    if parts.scheme not in ("http", "https") or parts.hostname not in HOSTS or not match:
        raise IJNLImportError(_("That doesn't look like an ijshockey.nl team page -- expected something like https://www.ijshockey.nl/competities/team/64414?seizoen=2026%2F27."))

    season = (parse_qs(parts.query).get("seizoen") or [""])[0].strip()
    if season and not SEASON_RE.match(season):
        raise IJNLImportError(_("“%(season)s” isn't a season ijshockey.nl knows -- expected something like 2026/27.") % {"season": season})
    return match.group("team_id"), season


def source_id(team_id, season) -> str:
    """What's stored as Event.external_source_id for an IJNL import -- the team
    *and* season ("64414/2026/27"), since the team id carries over between
    seasons while its schedule doesn't."""
    return f"{team_id}/{season}"


def team_page_url(team_id, season) -> str:
    """The canonical team page URL for (team, season) -- what's remembered on
    Team.import_sources, so a re-check keeps asking for the same season even if
    staff pasted a URL without one."""
    return TEAM_PAGE_URL.format(team_id=team_id, season=quote(season, safe=""))


def _get(url: str, params: dict | None = None) -> str:
    try:
        response = requests.get(url, params=params, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT_SECONDS)
    except requests.RequestException as error:
        raise IJNLImportError(_("Could not reach %(url)s: %(error)s") % {"url": url, "error": error}) from error

    if response.status_code != 200:
        raise IJNLImportError(_("%(url)s returned HTTP %(status)s.") % {"url": url, "status": response.status_code})

    return response.text


def _load_team_data(raw_json: str) -> dict:
    """Parses and sanity-checks a team ``/data`` response."""
    try:
        payload = json.loads(raw_json)
    except (TypeError, ValueError) as error:
        raise IJNLImportError(_("The ijshockey.nl team data wasn't valid JSON.")) from error

    if not isinstance(payload, dict) or not isinstance(payload.get("team"), dict) or not isinstance(payload.get("programma"), list):
        raise IJNLImportError(_("The ijshockey.nl team data had no games list -- the site's data format may have changed."))

    return payload


def fetch_team_data(team_id: str, season: str = "") -> str:
    """Fetches the team's whole season (every division) and returns it as JSON
    text -- a plain str so a view can stash it in the session."""
    params = {"seizoen": season} if season else None
    raw_json = _get(TEAM_DATA_URL.format(team_id=team_id), params=params)
    _load_team_data(raw_json)  # fail here, at fetch time, rather than later at preview time
    return raw_json


def season_of(raw_json: str) -> str:
    """The season (e.g. "2026/27") a ``fetch_team_data`` response is for."""
    season = str(_load_team_data(raw_json).get("seizoen") or "").strip()
    if not SEASON_RE.match(season):
        raise IJNLImportError(_("The ijshockey.nl team data didn't say which season it's for."))
    return season


def _games(payload: dict) -> list[dict]:
    """Upcoming (``programma``) and played (``uitslagen``) games together -- a game
    moves from one list to the other once it's been played."""
    return [game for game in [*payload.get("programma", []), *(payload.get("uitslagen") or [])] if isinstance(game, dict)]


def _start(game: dict) -> datetime | None:
    date_text = game.get("datum")
    if game.get("datumOnbekend") or not date_text:
        return None
    time_text = UNKNOWN_TIME if game.get("tijdOnbekend") or not game.get("tijd") else game["tijd"]
    try:
        naive_start = datetime.strptime(f"{date_text} {time_text}", "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    return naive_start.replace(tzinfo=SOURCE_TIMEZONE)


def parse_fixtures(raw_json: str, team_id: str) -> tuple[str, list[ScrapedFixture], list[str]]:
    """Returns (the team's own display name, its upcoming fixtures, the ids of
    listed games that have no date yet).

    A game with no date yet ("TBD" on the site) can't become an Event, but it's
    still on the schedule -- typically a postponed game waiting for a new date --
    so its id is reported separately for ``build_plan`` to keep rather than
    delete an already-imported copy."""
    payload = _load_team_data(raw_json)
    team_id = str(team_id)
    team_name = (payload["team"].get("naam") or "").strip()
    fixtures = []
    undated_game_ids = []
    now = timezone.now()

    for game in _games(payload):
        home, away = game.get("thuis") or {}, game.get("uit") or {}
        if str(home.get("teamId")) == team_id:
            is_home, opponent_name = True, away.get("naam", "")
        elif str(away.get("teamId")) == team_id:
            is_home, opponent_name = False, home.get("naam", "")
        else:
            continue  # shouldn't happen on a team's own schedule -- skip rather than guess

        game_id = str(game.get("gameGuid") or "")
        if not game_id:
            continue
        start = _start(game)
        if start is None:
            undated_game_ids.append(game_id)
            continue
        if start <= now:
            continue  # already played (or under way) -- only upcoming games are imported, same as RBIHF

        fixtures.append(ScrapedFixture(external_game_id=game_id, start=start, is_home=is_home, opponent_name=(opponent_name or "").strip(), venue_text=(game.get("locatie") or "").strip()))

    if not team_name:
        raise IJNLImportError(_("Team %(team_id)s has no name in the ijshockey.nl data -- check the team page URL.") % {"team_id": team_id})

    return team_name, fixtures, undated_game_ids


def build_plan(club, team, source_id: str, raw_json: str, competition_label: str = "") -> ImportPlan:
    """IJNL counterpart to events.services.rbihf_import.build_plan -- ``source_id``
    is ``source_id(team_id, season)``, ``raw_json`` what fetch_team_data returned."""
    team_id = source_id.split("/", 1)[0]
    scraped_team_name, fixtures, undated_game_ids = parse_fixtures(raw_json, team_id)
    return plan_from_fixtures(club, team, competition=COMPETITION, source_id=source_id, scraped_team_name=scraped_team_name, fixtures=fixtures, competition_label=competition_label, keep_game_ids=undated_game_ids)


def fetch_game(game_id: str) -> dict:
    """One game's live ``/data`` response (``part=live``: just the scoreboard,
    what the site's own game page polls with)."""
    raw_json = _get(GAME_DATA_URL.format(game_id=quote(str(game_id), safe="")), params={"part": "live"})
    try:
        payload = json.loads(raw_json)
    except (TypeError, ValueError) as error:
        raise IJNLImportError(_("The ijshockey.nl game data wasn't valid JSON.")) from error
    if not isinstance(payload, dict) or "state" not in payload:
        raise IJNLImportError(_("The ijshockey.nl game data had no game state -- the site's data format may have changed."))
    return payload


def parse_score(game: dict) -> tuple[int | None, int | None]:
    """(home, away) from a ``fetch_game`` response's scoreboard markup -- both
    None before the game starts (the site shows "-")."""
    html = (game.get("sections") or {}).get("scoreboard") or game.get("html") or ""
    score = BeautifulSoup(html, "html.parser").select_one(".mp-score")
    spans = score.find_all("span", recursive=False) if score else []
    if len(spans) != 2:
        return None, None

    def as_int(span):
        text = span.get_text(strip=True)
        return int(text) if text.isdigit() else None

    return as_int(spans[0]), as_int(spans[1])
