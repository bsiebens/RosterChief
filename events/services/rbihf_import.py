"""Importing a team's fixture list from RBIHF's own website.

RBIHF publishes a team's schedule at ``https://www.rbihf.be/league/team/<id>`` --
an HTML page, not an API, scraped with BeautifulSoup. This is the create/update/
delete counterpart to ``events.services.competitions.fetch_game_info``, which
only ever refreshes a single *existing* game's score: this is how those `Event`
rows get created in the first place.

Two-step flow, mirroring ``management.bulk_import`` (the member Excel import):
``build_plan`` is pure with respect to the outside world once it has the raw
HTML (no further network calls), so a view can render a preview from it and
then re-run it unchanged at confirm time -- see management/views.py.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime

import requests
from bs4 import BeautifulSoup
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from events.models import Event, Location, Opponent

TEAM_URL_RE = re.compile(r"^https://(?:www\.)?rbihf\.be/league/team/(?P<team_id>\d+)/?$")

REQUEST_HEADERS = {
    "Cookie": "language=en",
    "User-Agent": "Mozilla/5.0 (compatible; RosterChief/1.0)",
    "Accept": "text/html",
}
REQUEST_TIMEOUT_SECONDS = 15


class RBIHFImportError(Exception):
    """The page couldn't be fetched, or didn't look like an RBIHF team schedule."""


def extract_team_id(url: str) -> str:
    match = TEAM_URL_RE.match(url.strip())
    if not match:
        raise RBIHFImportError("That doesn't look like an RBIHF team page -- expected something like https://www.rbihf.be/league/team/4460.")
    return match.group("team_id")


def fetch_html(url: str) -> str:
    try:
        response = requests.get(url, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT_SECONDS)
    except requests.RequestException as error:
        raise RBIHFImportError(f"Could not reach {url}: {error}") from error

    if response.status_code != 200:
        raise RBIHFImportError(f"{url} returned HTTP {response.status_code}.")

    return response.text


@dataclass
class ScrapedFixture:
    external_game_id: str
    start: datetime
    is_home: bool
    opponent_name: str
    venue_text: str


def _team_id_from_href(href: str) -> str | None:
    match = re.search(r"/league/team/(\d+)", href or "")
    return match.group(1) if match else None


def parse_fixtures(html: str, team_id: str) -> tuple[str, list[ScrapedFixture]]:
    """Returns (the scraped team's own display name, its upcoming fixtures)."""
    soup = BeautifulSoup(html, "html.parser")

    team_heading = soup.find("h2")
    if team_heading is None:
        raise RBIHFImportError("Could not find a team name on this page -- is this really an RBIHF team page?")
    team_name = team_heading.get_text(strip=True)

    games_heading = soup.find(id="games-upcoming")
    if games_heading is None:
        raise RBIHFImportError("Could not find an “Upcoming games” table on this page.")
    table = games_heading.find_next("table")
    if table is None:
        raise RBIHFImportError("Could not find an “Upcoming games” table on this page.")

    fixtures = []
    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 6:
            continue  # the header row (<th>s) and any stray rows

        game_link = cells[0].find("a")
        game_id = game_link.get_text(strip=True) if game_link else cells[0].get_text(strip=True)
        if not game_id:
            continue

        date_text = cells[1].get_text(strip=True)
        hour_text = cells[2].get_text(strip=True)
        try:
            naive_start = datetime.strptime(f"{date_text} {hour_text}", "%Y-%m-%d %H:%M")
        except ValueError:
            continue  # a row that doesn't match the expected shape -- skip rather than blow up the whole import
        start = timezone.make_aware(naive_start)

        venue_text = cells[3].get_text(strip=True)

        home_link, visit_link = cells[4].find("a"), cells[5].find("a")
        if home_link is None or visit_link is None:
            continue

        home_id = _team_id_from_href(home_link.get("href", ""))
        visit_id = _team_id_from_href(visit_link.get("href", ""))

        if home_id == team_id:
            is_home, opponent_name = True, visit_link.get("title") or visit_link.get_text(strip=True)
        elif visit_id == team_id:
            is_home, opponent_name = False, home_link.get("title") or home_link.get_text(strip=True)
        else:
            continue  # neither side is the team we asked about -- inconsistent row, skip it

        fixtures.append(ScrapedFixture(external_game_id=game_id, start=start, is_home=is_home, opponent_name=opponent_name, venue_text=venue_text))

    return team_name, fixtures


def suggested_location(club, fixture: ScrapedFixture):
    if fixture.is_home:
        return Location.objects.filter(club=club, is_home=True).first()

    venue = fixture.venue_text.strip()
    if not venue:
        return None
    return Location.objects.filter(club=club).filter(Q(city__iexact=venue) | Q(name__iexact=venue)).first()


def suggested_opponent(club, fixture: ScrapedFixture):
    """A case-insensitive name match among the club's existing Opponents, if
    any -- RBIHF's own spelling doesn't always match punctuation/casing
    already on file (e.g. "Rivals FC" vs "Rivals F.C."), and get_or_create's
    exact-string match alone would just create a near-duplicate instead of
    reusing it."""
    name = fixture.opponent_name.strip()
    if not name:
        return None
    return Opponent.objects.filter(club=club, name__iexact=name).first()


@dataclass
class PlannedCreate:
    fixture: ScrapedFixture
    suggested_location: Location | None
    suggested_opponent: Opponent | None = None


@dataclass
class PlannedUpdate:
    fixture: ScrapedFixture
    event: Event
    changes: dict = field(default_factory=dict)  # field name -> (old, new), display-ready strings
    suggested_location: Location | None = None
    suggested_opponent: Opponent | None = None


@dataclass
class ImportPlan:
    club: object
    team: object
    scraped_team_name: str
    location_choices: object  # QuerySet[Location], evaluated once for the template
    opponent_choices: object = None  # QuerySet[Opponent], evaluated once for the template
    to_create: list[PlannedCreate] = field(default_factory=list)
    to_update: list[PlannedUpdate] = field(default_factory=list)
    to_delete: list[Event] = field(default_factory=list)
    unchanged_count: int = 0


def _describe_changes(event, fixture: ScrapedFixture) -> dict:
    changes = {}
    if event.start != fixture.start:
        changes["start"] = (event.start, fixture.start)
    existing_opponent = event.opponent.name if event.opponent_id else ""
    if existing_opponent != fixture.opponent_name:
        changes["opponent"] = (existing_opponent, fixture.opponent_name)
    return changes


def build_plan(club, team, rbihf_team_id: str, html: str) -> ImportPlan:
    """Parses ``html`` (already fetched -- see ``fetch_html``) and diffs it
    against this club/team's existing RBIHF events. Pure with respect to the
    network: safe to call twice against the same stashed HTML (preview, then
    confirm) and get an identical result modulo whatever changed in the DB
    between the two calls."""
    scraped_team_name, fixtures = parse_fixtures(html, rbihf_team_id)
    location_choices = list(Location.objects.filter(club=club).order_by("name"))
    opponent_choices = list(Opponent.objects.filter(club=club).order_by("name"))

    existing_by_game_id = {event.external_game_id: event for event in Event.objects.filter(club=club, teams=team, competition="RBIHF").exclude(external_game_id="").select_related("opponent", "location")}

    plan = ImportPlan(club=club, team=team, scraped_team_name=scraped_team_name, location_choices=location_choices, opponent_choices=opponent_choices)

    seen_game_ids = set()
    for fixture in fixtures:
        seen_game_ids.add(fixture.external_game_id)
        existing = existing_by_game_id.get(fixture.external_game_id)

        if existing is None:
            plan.to_create.append(PlannedCreate(fixture=fixture, suggested_location=suggested_location(club, fixture), suggested_opponent=suggested_opponent(club, fixture)))
            continue

        changes = _describe_changes(existing, fixture)
        if changes:
            # Prefer whatever location/opponent is already on the event -- a
            # previous run's manual pick -- over re-guessing, so a re-import
            # doesn't silently discard it. Only fall back to a fresh guess
            # when nothing's set yet.
            default_location = existing.location if existing.location_id else suggested_location(club, fixture)
            default_opponent = existing.opponent if existing.opponent_id else suggested_opponent(club, fixture)
            plan.to_update.append(PlannedUpdate(fixture=fixture, event=existing, changes=changes, suggested_location=default_location, suggested_opponent=default_opponent))
        else:
            plan.unchanged_count += 1

    now = timezone.now()
    for game_id, event in existing_by_game_id.items():
        if game_id not in seen_game_ids and event.start >= now:
            plan.to_delete.append(event)

    return plan


def apply_plan(plan: ImportPlan, locations_by_game_id: dict, opponents_by_game_id: dict | None = None) -> dict:
    """Applies everything in ``plan``, resolving each create/update row's
    location from ``locations_by_game_id`` and opponent from
    ``opponents_by_game_id`` (both external_game_id -> pk as a plain string,
    or None/""). A pk that doesn't belong to ``plan.club`` -- or isn't a real
    pk at all -- is silently ignored rather than trusted: this is user input
    straight from the confirm POST, not derived data. Compared as strings
    since the input is whatever a <select> submitted, not already a UUID.

    Blank/missing means different things for the two: no location is a valid,
    final state (an away game with no venue on file yet), but a game always
    needs *an* opponent -- so blank there falls back to the same
    find-or-create-by-scraped-name this always did, rather than leaving it
    unset."""
    opponents_by_game_id = opponents_by_game_id or {}
    locations_by_id = {str(location.pk): location for location in plan.location_choices}
    opponents_by_id = {str(opponent.pk): opponent for opponent in plan.opponent_choices}

    def resolve_location(game_id):
        location_id = locations_by_game_id.get(game_id)
        if not location_id:
            return None
        return locations_by_id.get(str(location_id))

    def resolve_opponent(fixture):
        opponent_id = opponents_by_game_id.get(fixture.external_game_id)
        if opponent_id:
            opponent = opponents_by_id.get(str(opponent_id))
            if opponent is not None:
                return opponent
        opponent, _created = Opponent.objects.get_or_create(club=plan.club, name=fixture.opponent_name)
        return opponent

    created = updated = deleted = 0

    with transaction.atomic():
        for planned in plan.to_create:
            opponent = resolve_opponent(planned.fixture)
            event = Event.objects.create(
                club=plan.club,
                title=f"vs {opponent.name}",
                kind=Event.EventKind.GAME,
                start=planned.fixture.start,
                competition="RBIHF",
                external_game_id=planned.fixture.external_game_id,
                opponent=opponent,
                location=resolve_location(planned.fixture.external_game_id),
            )
            event.teams.add(plan.team)
            created += 1

        for planned in plan.to_update:
            event = planned.event
            event.start = planned.fixture.start
            event.opponent = resolve_opponent(planned.fixture)
            event.location = resolve_location(planned.fixture.external_game_id)
            event.save(update_fields=["start", "opponent", "location"])
            updated += 1

        for event in plan.to_delete:
            event.delete()
            deleted += 1

    return {"created": created, "updated": updated, "deleted": deleted}
