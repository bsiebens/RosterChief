"""Importing a team's fixture list from RBIHF's own website, plus the source-neutral diff/apply core every fixture
importer shares (``plan_from_fixtures`` / ``apply_plan``).

RBIHF publishes a team's schedule at ``https://www.rbihf.be/league/team/<id>``: an HTML page, not an API, scraped with
BeautifulSoup. Two-step flow: ``fetch_html`` is the only network call; ``build_plan`` is pure once it has the raw HTML,
so a caller can show a preview and re-run it unchanged at confirm time (or the weekly check can diff it), and
``apply_plan`` writes the Event rows.

Runs inside the club's schema: there is no club column, every query is the current club's.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime

import requests
from bs4 import BeautifulSoup
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext as _

from events.models import Event, Location, Opponent

COMPETITION = "RBIHF"

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
    match = TEAM_URL_RE.match((url or "").strip())
    if not match:
        raise RBIHFImportError(_("That doesn't look like an RBIHF team page -- expected something like https://www.rbihf.be/league/team/4460."))
    return match.group("team_id")


def fetch_html(url: str) -> str:
    try:
        response = requests.get(url, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT_SECONDS)
    except requests.RequestException as error:
        raise RBIHFImportError(_("Could not reach %(url)s: %(error)s") % {"url": url, "error": error}) from error

    if response.status_code != 200:
        raise RBIHFImportError(_("%(url)s returned HTTP %(status)s.") % {"url": url, "status": response.status_code})

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
        raise RBIHFImportError(_("Could not find a team name on this page -- is this really an RBIHF team page?"))
    team_name = team_heading.get_text(strip=True)

    games_heading = soup.find(id="games-upcoming")
    table = games_heading.find_next("table") if games_heading is not None else None
    if table is None:
        raise RBIHFImportError(_("Could not find an “Upcoming games” table on this page."))

    fixtures = []
    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 6:
            continue  # the header row (<th>s) and any stray rows

        game_link = cells[0].find("a")
        game_id = game_link.get_text(strip=True) if game_link else cells[0].get_text(strip=True)
        if not game_id:
            continue

        try:
            naive_start = datetime.strptime(f"{cells[1].get_text(strip=True)} {cells[2].get_text(strip=True)}", "%Y-%m-%d %H:%M")
        except ValueError:
            continue  # a row that doesn't match the expected shape: skip rather than blow up the whole import
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
            continue  # neither side is the team we asked about: inconsistent row, skip it

        fixtures.append(ScrapedFixture(external_game_id=game_id, start=start, is_home=is_home, opponent_name=opponent_name, venue_text=venue_text))

    return team_name, fixtures


def suggested_location(fixture: ScrapedFixture) -> Location | None:
    """The club's home location for a home game, else a location whose city or name is the fixture's venue."""
    if fixture.is_home:
        return Location.objects.filter(is_home=True).first()
    venue = fixture.venue_text.strip()
    if not venue:
        return None
    return Location.objects.filter(Q(city__iexact=venue) | Q(name__iexact=venue)).first()


def suggested_opponent(fixture: ScrapedFixture) -> Opponent | None:
    """A case-insensitive name match among the club's Opponents, or one that recorded this spelling before
    (Opponent.alternative_names): the federation's spelling doesn't always match what is on file."""
    name = fixture.opponent_name.strip()
    if not name:
        return None
    exact = Opponent.objects.filter(name__iexact=name).first()
    if exact is not None:
        return exact
    return next((opponent for opponent in Opponent.objects.all() if opponent.knows_name(name)), None)


@dataclass
class PlannedCreate:
    fixture: ScrapedFixture
    suggested_location: Location | None
    suggested_opponent: Opponent | None = None


@dataclass
class PlannedUpdate:
    fixture: ScrapedFixture
    event: Event
    changes: dict = field(default_factory=dict)  # field name -> (old, new)
    suggested_location: Location | None = None
    suggested_opponent: Opponent | None = None


@dataclass
class ImportPlan:
    team: object
    scraped_team_name: str
    location_choices: list = field(default_factory=list)
    opponent_choices: list = field(default_factory=list)
    source_id: str = ""  # stamped onto every create/update as Event.external_source_id
    competition_label: str = ""  # stamped onto every create/update as Event.competition_label
    to_create: list[PlannedCreate] = field(default_factory=list)
    to_update: list[PlannedUpdate] = field(default_factory=list)
    to_delete: list[Event] = field(default_factory=list)
    unchanged_count: int = 0
    competition: str = COMPETITION  # Event.competition for every created event, and which existing events are diffed

    @property
    def rbihf_team_id(self) -> str:
        """The old name of ``source_id`` (it predates the non-RBIHF importers)."""
        return self.source_id

    @property
    def has_changes(self) -> bool:
        return bool(self.to_create or self.to_update or self.to_delete)


def _describe_changes(event: Event, fixture: ScrapedFixture, competition_label: str) -> dict:
    changes = {}
    if event.start != fixture.start:
        changes["start"] = (event.start, fixture.start)
    existing_opponent = event.opponent.name if event.opponent_id else ""
    # A recorded alternative spelling of the opponent already on the game isn't a change.
    known = event.opponent.knows_name(fixture.opponent_name) if event.opponent_id else False
    if existing_opponent != fixture.opponent_name and not known:
        changes["opponent"] = (existing_opponent, fixture.opponent_name)
    if event.competition_label != competition_label:
        changes["competition label"] = (event.competition_label, competition_label)
    return changes


def build_plan(team, rbihf_team_id: str, html: str, competition_label: str = "") -> ImportPlan:
    """Parses ``html`` (from ``fetch_html``) and diffs it against ``team``'s existing RBIHF games. ``rbihf_team_id`` is
    RBIHF's id for the scraped page: a team in several competitions (Division 1, Cup) has a page, and an id, per
    competition, and every game is stamped with it so re-importing one never deletes another's games."""
    scraped_team_name, fixtures = parse_fixtures(html, rbihf_team_id)
    return plan_from_fixtures(team, competition=COMPETITION, source_id=rbihf_team_id, scraped_team_name=scraped_team_name, fixtures=fixtures, competition_label=competition_label)


def plan_from_fixtures(team, *, competition: str, source_id: str, scraped_team_name: str, fixtures: list[ScrapedFixture], competition_label: str = "", keep_game_ids=()) -> ImportPlan:
    """The source-agnostic half of every importer: diffs parsed ``fixtures`` against ``team``'s existing
    ``competition`` games. ``keep_game_ids`` are games the source still lists but couldn't be parsed into a fixture
    (e.g. IJNL's "date TBD"): left alone rather than deleted."""
    # Matched by external_game_id alone across every source of this competition, so a game imported before
    # external_source_id existed (blank) is found and updated (which heals its source id) instead of duplicated.
    existing_by_game_id = {event.external_game_id: event for event in Event.objects.filter(teams=team, competition=competition).exclude(external_game_id="").select_related("opponent", "location")}

    plan = ImportPlan(
        team=team,
        scraped_team_name=scraped_team_name,
        location_choices=list(Location.objects.order_by("name")),
        opponent_choices=list(Opponent.objects.order_by("name")),
        source_id=source_id,
        competition_label=competition_label,
        competition=competition,
    )

    seen_game_ids = set(keep_game_ids)
    for fixture in fixtures:
        seen_game_ids.add(fixture.external_game_id)
        existing = existing_by_game_id.get(fixture.external_game_id)
        if existing is None:
            plan.to_create.append(PlannedCreate(fixture=fixture, suggested_location=suggested_location(fixture), suggested_opponent=suggested_opponent(fixture)))
            continue

        changes = _describe_changes(existing, fixture, competition_label)
        if changes:
            # Prefer what is already on the event (a previous run's manual pick) over a fresh guess.
            default_location = existing.location if existing.location_id else suggested_location(fixture)
            default_opponent = existing.opponent if existing.opponent_id else suggested_opponent(fixture)
            plan.to_update.append(PlannedUpdate(fixture=fixture, event=existing, changes=changes, suggested_location=default_location, suggested_opponent=default_opponent))
        else:
            plan.unchanged_count += 1

    now = timezone.now()
    for game_id, event in existing_by_game_id.items():
        if game_id in seen_game_ids or event.start < now:
            continue  # still listed, or already played ("upcoming" naturally stops listing a played game)
        # A blank source id (imported before the field existed) counts as this import's own; a known, different
        # one belongs to another competition's page and is never deleted from here.
        if event.external_source_id and event.external_source_id != source_id:
            continue
        plan.to_delete.append(event)

    return plan


def apply_plan(plan: ImportPlan, locations_by_game_id: dict | None = None, opponents_by_game_id: dict | None = None) -> dict:
    """Applies ``plan``, taking each create/update row's location from ``locations_by_game_id`` and opponent from
    ``opponents_by_game_id`` (external_game_id -> pk string, or blank). A pk that is not one of the plan's choices is
    ignored (it is user input). No location is a valid final state; a blank opponent falls back to find-or-create by
    the source's spelling. Returns the counts."""
    locations_by_game_id = locations_by_game_id or {}
    opponents_by_game_id = opponents_by_game_id or {}
    locations_by_id = {str(location.pk): location for location in plan.location_choices}
    opponents_by_id = {str(opponent.pk): opponent for opponent in plan.opponent_choices}

    def resolve_location(game_id):
        location_id = locations_by_game_id.get(game_id)
        return locations_by_id.get(str(location_id)) if location_id else None

    def resolve_opponent(fixture):
        opponent_id = opponents_by_game_id.get(fixture.external_game_id)
        if opponent_id:
            opponent = opponents_by_id.get(str(opponent_id))
            if opponent is not None:
                # Remember the federation's spelling so the next check/import doesn't report it as a change again.
                opponent.remember_name(fixture.opponent_name)
                return opponent
        opponent, _created = Opponent.objects.get_or_create(name=fixture.opponent_name)
        return opponent

    created = updated = deleted = 0
    with transaction.atomic():
        for planned in plan.to_create:
            opponent = resolve_opponent(planned.fixture)
            # Home side first: the source's own is_home flag is authoritative (it also picked the home Location).
            us, them = plan.team.short_name or plan.team.name, opponent.name
            home_side, away_side = (us, them) if planned.fixture.is_home else (them, us)
            event = Event.objects.create(
                title=f"{home_side} {_('vs')} {away_side}",
                kind=Event.EventKind.GAME,
                start=planned.fixture.start,
                competition=plan.competition,
                competition_label=plan.competition_label,
                external_game_id=planned.fixture.external_game_id,
                external_source_id=plan.source_id,
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
            # Heals a legacy (pre-external_source_id) or mislabelled row onto this import's own competition: safe,
            # since it only runs for a game this scrape re-saw by external_game_id.
            event.competition_label = plan.competition_label
            event.external_source_id = plan.source_id
            event.save(update_fields=["start", "opponent", "location", "competition_label", "external_source_id", "modified"])
            updated += 1

        for event in plan.to_delete:
            event.delete()
            deleted += 1

    return {"created": created, "updated": updated, "deleted": deleted}
