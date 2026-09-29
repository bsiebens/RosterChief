"""Importing a team's fixture list for the DFEL (German women's ice hockey
league, run by EHV-NRW) from the hockeydata.net API.

The public team page (``https://ehv-nrw.de/leagues/team/<team_id>/<division_id>/``)
carries no game data of its own -- it embeds a hockeydata widget whose config
(an inline script) holds the public hockeydata API key. That key is read from the
page on every fetch rather than hard-coded, since it may rotate. The schedule
itself comes from hockeydata's JSONP ``Schedule`` endpoint for the whole
division, filtered locally by team id (server-side team filtering times out).

Same two-step preview/confirm flow as events.services.rbihf_import, sharing its
diff/apply core (``plan_from_fixtures``/``apply_plan``): ``fetch_schedule``
returns plain JSON text a view can stash in the session, and ``build_plan`` is
pure with respect to the network once it has that text. The same fetch also
backs the single-game score refresh, events.competition.hockey.DFEL.
"""

import json
import re
from datetime import UTC, datetime

import requests
from django.utils.translation import gettext_lazy as _

from events.services.rbihf_import import ImportPlan, ScrapedFixture, apply_plan, plan_from_fixtures

__all__ = ["DFELImportError", "ImportPlan", "apply_plan", "build_plan", "extract_api_key", "extract_ids", "fetch_schedule", "find_game", "parse_fixtures", "source_id"]

COMPETITION = "DFEL"

TEAM_URL_RE = re.compile(r"^https?://(?:www\.)?ehv-nrw\.de/leagues/team/(?P<team_id>\d+)/(?P<division_id>\d+)/?$")
TEAM_PAGE_URL = "https://ehv-nrw.de/leagues/team/{team_id}/{division_id}/"
SCHEDULE_URL = "https://api.hockeydata.net/data/ebel/Schedule"
JSONP_CALLBACK = "rosterchief"

# The widget config is JSON inside a JS string literal, so its quotes show up
# "-escaped (or backslash-escaped, or plain) depending on how the page was
# rendered -- accept any of them.
_QUOTE = r"""(?:\\u0022|\\?["'])"""
API_KEY_RE = re.compile(rf"apiKey{_QUOTE}\s*:\s*{_QUOTE}(?P<key>[A-Za-z0-9]+){_QUOTE}")
JSONP_RE = re.compile(r"^\s*[\w$.]+\s*\((?P<body>.*)\)\s*;?\s*$", re.DOTALL)

REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; RosterChief/1.0)",
    "Referer": "https://ehv-nrw.de/",
}
REQUEST_TIMEOUT_SECONDS = 30

LIVE_GAME_STATUS = 1


class DFELImportError(Exception):
    """The schedule couldn't be fetched, or didn't look like a DFEL/hockeydata schedule."""


def extract_ids(url: str) -> tuple[str, str]:
    """Returns (team_id, division_id) from an EHV-NRW team page URL."""
    match = TEAM_URL_RE.match(url.strip())
    if not match:
        raise DFELImportError(_("That doesn't look like an EHV-NRW team page -- expected something like https://ehv-nrw.de/leagues/team/70779/21691/."))
    return match.group("team_id"), match.group("division_id")


def source_id(team_id, division_id) -> str:
    """What's stored as Event.external_source_id for a DFEL import -- the team
    *and* division, since the same team has a separate schedule per division."""
    return f"{team_id}/{division_id}"


def _get(url: str, params: dict | None = None) -> str:
    try:
        response = requests.get(url, params=params, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT_SECONDS)
    except requests.RequestException as error:
        raise DFELImportError(_("Could not reach %(url)s: %(error)s") % {"url": url, "error": error}) from error

    if response.status_code != 200:
        raise DFELImportError(_("%(url)s returned HTTP %(status)s.") % {"url": url, "status": response.status_code})

    return response.text


def extract_api_key(html: str) -> str:
    match = API_KEY_RE.search(html)
    if not match:
        raise DFELImportError(_("Could not find the hockeydata API key on the EHV-NRW team page -- the page layout may have changed."))
    return match.group("key")


def _load_schedule(raw_json: str) -> dict:
    """Parses and sanity-checks the (already unwrapped) Schedule response."""
    try:
        payload = json.loads(raw_json)
    except (TypeError, ValueError) as error:
        raise DFELImportError(_("The DFEL schedule response wasn't valid JSON.")) from error

    if not isinstance(payload, dict) or payload.get("statusId") != 1:
        message = payload.get("statusMsg", "") if isinstance(payload, dict) else ""
        raise DFELImportError(_("The DFEL schedule service returned an error: %(message)s") % {"message": message or _("unknown error")})

    rows = (payload.get("data") or {}).get("rows")
    if not isinstance(rows, list):
        raise DFELImportError(_("The DFEL schedule response had no games list."))

    return payload


def fetch_schedule(team_id: str, division_id: str) -> str:
    """Fetches the division's whole schedule and returns it as (unwrapped)
    JSON text -- a plain str so a view can stash it in the session."""
    html = _get(TEAM_PAGE_URL.format(team_id=team_id, division_id=division_id))
    api_key = extract_api_key(html)

    params = {"apiKey": api_key, "divisionId": division_id, "lang": "en", "referer": "ehv-nrw.de", "callback": JSONP_CALLBACK}
    body = _get(SCHEDULE_URL, params=params)

    match = JSONP_RE.match(body)
    raw_json = match.group("body") if match else body
    _load_schedule(raw_json)  # fail here, at fetch time, rather than later at preview time
    return raw_json


def _rows(raw_json: str) -> list[dict]:
    return _load_schedule(raw_json)["data"]["rows"]


def _venue_text(location) -> str:
    """The venue's city when the address has one (what suggested_location
    matches against Location.city), else its display name."""
    if not isinstance(location, dict):
        return ""
    address = location.get("address")
    if isinstance(address, str) and address.strip():
        try:
            address = json.loads(address)
        except ValueError:
            address = None
    if isinstance(address, dict):
        city = (address.get("city") or "").strip()
        if city:
            return city
    return (location.get("longname") or location.get("shortname") or "").strip()


def parse_fixtures(raw_json: str, team_id: str) -> tuple[str, list[ScrapedFixture]]:
    """Returns (the team's own display name, its upcoming fixtures)."""
    team_id = str(team_id)
    team_name = ""
    fixtures = []
    now = datetime.now(UTC)

    for row in _rows(raw_json):
        if str(row.get("homeTeamId")) == team_id:
            is_home, own_name, opponent_name = True, row.get("homeTeamLongName", ""), row.get("awayTeamLongName", "")
        elif str(row.get("awayTeamId")) == team_id:
            is_home, own_name, opponent_name = False, row.get("awayTeamLongName", ""), row.get("homeTeamLongName", "")
        else:
            continue  # another team's game in the same division

        team_name = team_name or own_name

        game_id, timestamp = row.get("id"), row.get("gameUtcTimestamp")
        if not game_id or not isinstance(timestamp, int | float):
            continue
        start = datetime.fromtimestamp(timestamp / 1000, tz=UTC)
        if start <= now:
            continue  # already played (or under way) -- only upcoming games are imported, same as RBIHF

        fixtures.append(ScrapedFixture(external_game_id=str(game_id), start=start, is_home=is_home, opponent_name=(opponent_name or "").strip(), venue_text=_venue_text(row.get("location"))))

    if not team_name:
        raise DFELImportError(_("Team %(team_id)s has no games in this division's schedule -- check the team page URL.") % {"team_id": team_id})

    return team_name, fixtures


def build_plan(club, team, source_id: str, raw_json: str, competition_label: str = "") -> ImportPlan:
    """DFEL counterpart to events.services.rbihf_import.build_plan -- ``source_id``
    is ``source_id(team_id, division_id)``, ``raw_json`` what fetch_schedule returned."""
    team_id = source_id.split("/", 1)[0]
    scraped_team_name, fixtures = parse_fixtures(raw_json, team_id)
    return plan_from_fixtures(club, team, competition=COMPETITION, source_id=source_id, scraped_team_name=scraped_team_name, fixtures=fixtures, competition_label=competition_label)


def find_game(raw_json: str, game_id: str) -> dict | None:
    """The schedule row for ``game_id`` (hockeydata's game uuid), if present."""
    game_id = str(game_id)
    return next((row for row in _rows(raw_json) if str(row.get("id")) == game_id), None)
