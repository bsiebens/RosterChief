"""League standings for teams imported from RBIHF, computed from the league's own
results rather than read off RBIHF's ranking picture.

RBIHF only publishes a ranking as a PNG (``/images/league/ranking/<type id>``,
hot-link protected), but the same league page (``/league/<slug>``) lists every
played game with its score, per competition tab (``#type-<id>``: "Regular
Competition", "Friendly Games", ...), and spells out the points rule under the
ranking ("Won: 3 points | Won after overtime or penaltyshots: 2 points | ...").
That's enough to recompute the table exactly. The one thing a plain score
doesn't say is whether a game was decided after regulation -- only matters in a
league that awards overtime/shoot-out points, and only for a one-goal game --
so those few games are checked against the game's own per-period overview.

A team's league is found from the RBIHF team page already remembered by the
fixture import (Team.import_sources, see events.services.import_sources), and
the result is stored back on that same entry under ``standings`` -- so the
public API (api/public/routers/teams.py) only ever reads the database. Refreshed when a game of
the team finishes (events.management.commands.poll_live_game_results) and once a
night (refresh_standings).
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime

import requests
from bs4 import BeautifulSoup
from django.core.cache import cache
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from events.services import import_sources
from events.services.rbihf_import import REQUEST_HEADERS, REQUEST_TIMEOUT_SECONDS, RBIHFImportError, extract_team_id, fetch_html

logger = logging.getLogger(__name__)

BASE_URL = "https://www.rbihf.be"
LEAGUE_URL = BASE_URL + "/league/{slug}"
OVERVIEW_URL = BASE_URL + "/modules/league/ajax/overview.php?gameNr={game_id}&season={season}"

LEAGUE_PATH_RE = re.compile(r"^/league/(?P<slug>[a-z0-9_-]+)/?$")
TEAM_PATH_RE = re.compile(r"/league/team/(?P<team_id>\d+)")
GAME_PATH_RE = re.compile(r"/game/(?P<game_id>\d+)")
SCORE_RE = re.compile(r"^(?P<home>\d+)\s*-\s*(?P<away>\d+)$")
RULE_RE = re.compile(r"(?P<label>[A-Za-z][A-Za-z ]*?)\s*:\s*(?P<points>\d+)\s*points?", re.IGNORECASE)

#: Several clubs' teams share one league page, and one finished game refreshes
#: every team in it -- a short cache keeps that to one fetch per league.
LEAGUE_CACHE_SECONDS = 10 * 60
#: A played game's period breakdown doesn't change any more.
OVERVIEW_CACHE_SECONDS = 30 * 24 * 60 * 60


@dataclass
class PointsRule:
    win: int = 2
    draw: int = 1
    loss: int = 0
    ot_win: int | None = None  # None: the league has no overtime/shoot-out points
    ot_loss: int | None = None
    text: str = ""

    @property
    def has_overtime(self) -> bool:
        return self.ot_win is not None


@dataclass
class PlayedGame:
    game_id: str
    date: datetime | None
    home_id: str
    away_id: str
    home_score: int
    away_score: int


@dataclass
class LeagueCompetition:
    """One ranked tab (``#type-<id>``) of a league page."""

    type_id: str
    name: str
    rule: PointsRule
    teams: dict = field(default_factory=dict)  # RBIHF team id -> display name
    games: list[PlayedGame] = field(default_factory=list)


def parse_points_rule(text: str) -> PointsRule:
    """From the line under the ranking, e.g. "Won: 3 points | Won after overtime
    or penaltyshots: 2 points | Lost after overtime or penaltyshots: 1 point |
    Lost: 0 points" (English, see rbihf_import.REQUEST_HEADERS)."""
    rule = PointsRule()
    parts = []
    for match in RULE_RE.finditer(text or ""):
        label, points = match.group("label").strip().lower(), int(match.group("points"))
        parts.append(f"{match.group('label').strip()}: {points}")
        if "overtime" in label or "penalty" in label:
            if label.startswith("won"):
                rule.ot_win = points
            else:
                rule.ot_loss = points
        elif label.startswith("won"):
            rule.win = points
        elif label.startswith("draw"):
            rule.draw = points
        elif label.startswith("lost"):
            rule.loss = points
    if rule.ot_win is not None and rule.ot_loss is None:
        rule.ot_loss = rule.loss
    rule.text = " | ".join(parts)
    return rule


def _team_link(cell):
    link = cell.find("a", href=TEAM_PATH_RE) if cell else None
    if link is None:
        return None, ""
    return TEAM_PATH_RE.search(link["href"]).group("team_id"), (link.get("title") or link.get_text(strip=True)).strip()


def _parse_date(text: str):
    try:
        return datetime.strptime(text.strip(), "%d %b %Y")
    except ValueError:
        return None


def league_name(html: str) -> str:
    """The league name from the page title, e.g. "Under 18" from "RBIHF :: Under 18"."""
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(strip=True) if soup.title else ""
    return title.split("::", 1)[-1].strip()


def parse_league(html: str) -> list[LeagueCompetition]:
    """Every ranked competition tab on a league page -- a tab counts as ranked
    when RBIHF shows a ranking for it (friendly games, for one, have none)."""
    soup = BeautifulSoup(html, "html.parser")
    competitions = []
    for tab in soup.select("div.tab[id^=type-]"):
        type_id = tab["id"].removeprefix("type-")
        ranking = tab.select_one("img[src*='/images/league/ranking/']")
        if type_id == "all" or ranking is None:
            continue

        anchor = soup.select_one(f'a[href="#type-{type_id}"]')
        meta = ranking.find_parent(class_="ranking")
        meta = meta.select_one(".meta") if meta else None
        competition = LeagueCompetition(type_id=type_id, name=anchor.get_text(strip=True) if anchor else "", rule=parse_points_rule(meta.get_text(" ", strip=True) if meta else ""))

        for table in tab.find_all("table"):
            rows = table.find_all("tr")
            if not rows:
                continue
            headers = [cell.get_text(strip=True).lower() for cell in rows[0].find_all(["th", "td"])]
            if "home" not in headers or "visit" not in headers:
                continue
            home_at, visit_at = headers.index("home"), headers.index("visit")
            score_at = headers.index("score") if "score" in headers else None
            date_at = headers.index("date") if "date" in headers else None

            for row in rows[1:]:
                cells = row.find_all("td")
                if len(cells) <= max(home_at, visit_at, score_at or 0):
                    continue
                home_id, home_name = _team_link(cells[home_at])
                away_id, away_name = _team_link(cells[visit_at])
                if not home_id or not away_id:
                    continue
                competition.teams.setdefault(home_id, home_name)
                competition.teams.setdefault(away_id, away_name)

                if score_at is None:
                    continue  # an upcoming game -- only tells us who's in the competition
                score = SCORE_RE.match(cells[score_at].get_text(strip=True))
                if score is None:
                    continue  # "Unfinished" and the like don't count (yet)
                game_link = row.find("a", href=GAME_PATH_RE)
                game_id = GAME_PATH_RE.search(game_link["href"]).group("game_id") if game_link else cells[0].get_text(strip=True)
                date = _parse_date(cells[date_at].get_text(strip=True)) if date_at is not None else None
                competition.games.append(PlayedGame(game_id=game_id, date=date, home_id=home_id, away_id=away_id, home_score=int(score.group("home")), away_score=int(score.group("away"))))

        competitions.append(competition)
    return competitions


def season_code(date) -> str:
    """RBIHF's season parameter for a game played on ``date``, e.g. "2627" --
    seasons run from summer to summer."""
    start = date.year if date.month >= 7 else date.year - 1
    return f"{start % 100:02d}{(start + 1) % 100:02d}"


def parse_overview(payload) -> bool | None:
    """Whether the regulation periods (the first three) ended level -- i.e. the
    game was decided in overtime or a shoot-out -- from overview.php's JSON.
    None when the breakdown can't be read."""
    if not isinstance(payload, dict) or not payload.get("success"):
        return None
    soup = BeautifulSoup(payload.get("data") or "", "html.parser")
    goals = soup.select_one("td.goals")
    if goals is None:
        return None
    first_row = goals.find_parent("tr")
    second_row = first_row.find_next_sibling("tr")

    def periods(row, skip):
        cells = row.find_all("td")[skip:-1]  # drop the row label(s) and the total
        values = [cell.get_text(strip=True) for cell in cells]
        return [int(value) if value.isdigit() else 0 for value in values]

    home, away = periods(first_row, 2), periods(second_row, 1) if second_row else []
    if len(home) < 3 or len(away) < 3:
        return None
    return sum(home[:3]) == sum(away[:3])


def decided_after_regulation(game: PlayedGame) -> bool:
    """Asks RBIHF for ``game``'s per-period overview (cached for good once known)."""
    if game.date is None:
        return False
    cache_key = f"rbihf:overtime:{season_code(game.date)}:{game.game_id}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    url = OVERVIEW_URL.format(game_id=game.game_id, season=season_code(game.date))
    headers = {**REQUEST_HEADERS, "Accept": "application/json", "X-Requested-With": "XMLHttpRequest", "Referer": f"{BASE_URL}/game/{game.game_id}"}
    try:
        response = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)
        payload = response.json() if response.status_code == 200 else None
    except (requests.RequestException, ValueError) as error:
        raise RBIHFImportError(_("Could not read the period overview of game %(game)s: %(error)s") % {"game": game.game_id, "error": error}) from error

    result = parse_overview(payload)
    if result is None:
        raise RBIHFImportError(_("Could not read the period overview of game %(game)s.") % {"game": game.game_id})
    cache.set(cache_key, result, OVERVIEW_CACHE_SECONDS)
    return result


def compute_table(competition: LeagueCompetition, overtime_check=decided_after_regulation) -> list[dict]:
    """The ranking as a list of plain dicts (JSON-safe, stored as-is): points,
    then goal difference, then goals scored, then name -- the order RBIHF's own
    ranking shows."""
    rule = competition.rule
    table = {team_id: {"team_id": team_id, "team_name": name, "played": 0, "wins": 0, "ot_wins": 0, "ot_losses": 0, "draws": 0, "losses": 0, "goals_for": 0, "goals_against": 0, "points": 0} for team_id, name in competition.teams.items()}

    def record(team_id, scored, conceded, outcome):
        row = table.setdefault(team_id, {"team_id": team_id, "team_name": team_id, "played": 0, "wins": 0, "ot_wins": 0, "ot_losses": 0, "draws": 0, "losses": 0, "goals_for": 0, "goals_against": 0, "points": 0})
        row["played"] += 1
        row["goals_for"] += scored
        row["goals_against"] += conceded
        row[outcome] += 1
        row["points"] += {"wins": rule.win, "ot_wins": rule.ot_win or 0, "ot_losses": rule.ot_loss or 0, "draws": rule.draw, "losses": rule.loss}[outcome]

    for game in competition.games:
        home, away = game.home_score, game.away_score
        if home == away:
            record(game.home_id, home, away, "draws")
            record(game.away_id, away, home, "draws")
            continue
        overtime = rule.has_overtime and abs(home - away) == 1 and overtime_check(game)
        winner, loser = (game.home_id, game.away_id) if home > away else (game.away_id, game.home_id)
        high, low = max(home, away), min(home, away)
        record(winner, high, low, "ot_wins" if overtime else "wins")
        record(loser, low, high, "ot_losses" if overtime else "losses")

    rows = sorted(table.values(), key=lambda row: (-row["points"], -(row["goals_for"] - row["goals_against"]), -row["goals_for"], row["team_name"].casefold()))
    for position, row in enumerate(rows, start=1):
        row["position"] = position
        row["goal_difference"] = row["goals_for"] - row["goals_against"]
    return rows


def fetch_league_html(slug: str, *, fresh: bool = False) -> str:
    cache_key = f"rbihf:league:{slug}"
    if not fresh:
        html = cache.get(cache_key)
        if html is not None:
            return html
    html = fetch_html(LEAGUE_URL.format(slug=slug))
    cache.set(cache_key, html, LEAGUE_CACHE_SECONDS)
    return html


def league_slug_from_team_page(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for link in soup.find_all("a", href=True):
        match = LEAGUE_PATH_RE.match(link["href"])
        if match:
            return match.group("slug")
    raise RBIHFImportError(_("Could not find which league this RBIHF team plays in."))


def build_standings(entry: dict, *, fresh: bool = False) -> dict:
    """Fetches and computes the standings for one stored RBIHF import entry --
    every ranked competition of the team's league the team takes part in.
    Raises RBIHFImportError when RBIHF can't be read."""
    rbihf_team_id = extract_team_id(entry["url"])
    slug = (entry.get("standings") or {}).get("league_slug") or league_slug_from_team_page(fetch_html(entry["url"]))
    html = fetch_league_html(slug, fresh=fresh)

    competitions = []
    for competition in parse_league(html):
        if rbihf_team_id not in competition.teams:
            continue
        competitions.append(
            {
                "type_id": competition.type_id,
                "name": competition.name,
                "points_rule": competition.rule.text,
                "has_overtime": competition.rule.has_overtime,
                "rows": compute_table(competition),
            }
        )

    return {
        "league_slug": slug,
        "league": league_name(html),
        "league_url": LEAGUE_URL.format(slug=slug),
        "rbihf_team_id": rbihf_team_id,
        "competitions": competitions,
    }


def refresh_entry(team, entry: dict, *, fresh: bool = False) -> bool:
    """Recomputes ``entry``'s standings and stores them on the team. A failure
    keeps the last good standings and records the error next to them, so the
    public site keeps showing something. Returns whether it succeeded."""
    previous = entry.get("standings") or {}
    now = timezone.now().isoformat()
    try:
        standings = {**build_standings(entry, fresh=fresh), "updated_at": now, "checked_at": now, "error": ""}
        ok = True
    except RBIHFImportError as error:
        logger.warning("rbihf_standings.refresh_failed team=%s url=%s error=%s", team.pk, entry.get("url"), error)
        standings = {**previous, "checked_at": now, "error": str(error)}
        ok = False
    import_sources.update_entry(team, entry, standings=standings)
    return ok


def refresh_team(club, team, season, *, fresh: bool = False, source_id: str | None = None) -> tuple[int, int]:
    """Refreshes ``team``'s RBIHF entries for ``season`` (only the one whose RBIHF
    team id is ``source_id``, when given). Returns (refreshed, failed)."""
    refreshed = failed = 0
    for entry in import_sources.sources_for(team, season):
        if entry.get("source") != import_sources.SOURCE_RBIHF:
            continue
        if source_id is not None:
            try:
                if extract_team_id(entry["url"]) != source_id:
                    continue
            except RBIHFImportError:
                continue
        if refresh_entry(team, entry, fresh=fresh):
            refreshed += 1
        else:
            failed += 1
    return refreshed, failed


def refresh_for_finished_game(event) -> None:
    """Called once a polled RBIHF game is seen finishing: refreshes the standings
    of every team playing it, bypassing the league page cache so the new result
    is picked up straight away."""
    from club.models import Season  # deferred: club imports back into events

    if event.competition != import_sources.SOURCE_RBIHF or not import_sources.source_enabled(event.club, import_sources.SOURCE_RBIHF):
        return
    season = Season.covering(event.club, timezone.localdate(event.start))
    if season is None:
        return
    for team in event.teams.all():
        refresh_team(event.club, team, season, fresh=True, source_id=event.external_source_id or None)
