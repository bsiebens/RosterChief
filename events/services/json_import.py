"""Importing a club's own list of multi-team play days ("development games")
from a JSON file -- e.g. the federation's promotional calendar, boiled down to
the days the club plays.

Unlike the RBIHF/DFEL/IJNL importers there is no opponent: a play day is one
GAME event with one or several of the club's teams on it, no opponent, and the
other clubs taking part only listed on the competition label. Same two-step
preview/confirm flow though: ``build_plan`` is pure with respect to the
request, and the location of every game is picked by staff in the preview.

File format::

    {
      "source": "Kalender Promo 2026-2027",   # stamped on every event, scopes re-imports
      "competition": "Development",           # optional, default "Development"
      "title": "Development game",            # optional, default "Development game"
      "our_code": "COP",                      # optional, how the file names the club; left out of the label
      "games": [
        {
          "id": "2026-10-18-mechelen-u10",    # unique and stable: a re-import updates by it
          "date": "2026-10-18", "start": "11:45", "end": "13:45",   # end optional
          "city": "Mechelen",                 # matched against the club's Locations
          "teams": ["U10"],                   # the club's own teams, matched by name
          "hosted_by_us": true,               # the club is the organiser -> the home location
          "participants": ["COP", "IHC"],     # optional, shown on the competition label
          "ice": "cross-ice"                  # optional, same
        }
      ]
    }
"""

import json
import re
from dataclasses import dataclass, field
from datetime import datetime

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from events.models import Event, Location
from teams.models import Team

DEFAULT_COMPETITION = "Development"
DEFAULT_TITLE = "Development game"
LABEL_MAX_LENGTH = 100


class JSONImportError(Exception):
    """The file couldn't be read, or didn't have the expected shape."""


@dataclass
class PlannedGame:
    external_game_id: str
    start: datetime
    end: datetime | None
    city: str
    teams: list[Team]
    hosted_by_us: bool
    label: str
    suggested_location: Location | None
    event: Event | None = None
    changes: dict = field(default_factory=dict)

    @property
    def team_names(self) -> str:
        return ", ".join(team.name for team in self.teams)


@dataclass
class JSONImportPlan:
    club: object
    source: str
    competition: str
    title: str
    location_choices: list[Location]
    to_create: list[PlannedGame] = field(default_factory=list)
    to_update: list[PlannedGame] = field(default_factory=list)
    unchanged_count: int = 0

    @property
    def games(self) -> list[PlannedGame]:
        return [*self.to_create, *self.to_update]


def _text(value, key: str, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise JSONImportError(_("%(where)s: “%(key)s” is missing.") % {"where": where, "key": key})
    return value.strip()


def _moment(date_text: str, time_text: str, where: str) -> datetime:
    try:
        return timezone.make_aware(datetime.strptime(f"{date_text} {time_text}", "%Y-%m-%d %H:%M"))
    except ValueError as error:
        raise JSONImportError(_("%(where)s: “%(date)s %(time)s” isn't a valid date and time (expected 2026-10-18 and 11:45).") % {"where": where, "date": date_text, "time": time_text}) from error


def load(raw: str | bytes) -> dict:
    """Parses and shape-checks the uploaded file, returning the payload."""
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError) as error:
        raise JSONImportError(_("The file isn't valid JSON.")) from error

    if not isinstance(payload, dict) or not isinstance(payload.get("games"), list):
        raise JSONImportError(_("The file has no “games” list."))
    _text(payload.get("source"), "source", _("File"))
    return payload


def resolve_team(club, name: str) -> Team | None:
    """The club's team for ``name``: exact name or short name first, then the one team whose name
    contains it as a whole word (so "U10" finds "Sharks U10" but never "U1")."""
    teams = list(Team.objects.filter(club=club))
    wanted = name.strip().casefold()
    for team in teams:
        if wanted in (team.name.casefold(), team.short_name.casefold()):
            return team
    pattern = re.compile(rf"(?<!\w){re.escape(wanted)}(?!\w)")
    containing = [team for team in teams if pattern.search(team.name.casefold())]
    return containing[0] if len(containing) == 1 else None


def suggested_location(club, city: str, hosted_by_us: bool) -> Location | None:
    if hosted_by_us:
        return Location.objects.filter(club=club, is_home=True).first()
    return Location.objects.filter(club=club).filter(Q(city__iexact=city) | Q(name__iexact=city)).first()


def _label(game: dict, our_code: str) -> str:
    others = [name for name in game.get("participants") or [] if isinstance(name, str) and name.casefold() != our_code.casefold()]
    return " · ".join(part for part in [str(game.get("ice") or "").strip(), ", ".join(others)] if part)[:LABEL_MAX_LENGTH]


def build_plan(club, raw: str | bytes) -> JSONImportPlan:
    payload = load(raw)
    source = payload["source"].strip()
    plan = JSONImportPlan(
        club=club,
        source=source,
        competition=str(payload.get("competition") or DEFAULT_COMPETITION).strip(),
        title=str(payload.get("title") or DEFAULT_TITLE).strip(),
        location_choices=list(Location.objects.filter(club=club).order_by("name")),
    )
    existing_by_id = {event.external_game_id: event for event in Event.objects.filter(club=club, competition=plan.competition, external_source_id=source).exclude(external_game_id="").prefetch_related("teams").select_related("location")}

    seen_ids, missing_teams = set(), []
    for index, game in enumerate(payload["games"], 1):
        where = _("Game %(number)s") % {"number": index}
        if not isinstance(game, dict):
            raise JSONImportError(_("%(where)s isn't an object.") % {"where": where})
        game_id = _text(game.get("id"), "id", where)
        if game_id in seen_ids:
            raise JSONImportError(_("%(where)s: id “%(id)s” appears twice.") % {"where": where, "id": game_id})
        seen_ids.add(game_id)

        start = _moment(_text(game.get("date"), "date", where), _text(game.get("start"), "start", where), where)
        end = _moment(game["date"], game["end"], where) if game.get("end") else None
        team_names = game.get("teams")
        if not isinstance(team_names, list) or not team_names:
            raise JSONImportError(_("%(where)s: “teams” must list at least one team.") % {"where": where})
        teams = []
        for name in team_names:
            team = resolve_team(club, str(name))
            if team is None:
                missing_teams.append(str(name))
            elif team not in teams:
                teams.append(team)

        hosted = bool(game.get("hosted_by_us"))
        city = _text(game.get("city"), "city", where)
        label = _label(game, str(payload.get("our_code") or ""))
        existing = existing_by_id.get(game_id)
        planned = PlannedGame(external_game_id=game_id, start=start, end=end, city=city, teams=teams, hosted_by_us=hosted, label=label, suggested_location=suggested_location(club, city, hosted))

        if existing is None:
            plan.to_create.append(planned)
            continue

        # Whatever location is already on the event is a previous manual pick -- keep it as the default.
        planned.event = existing
        planned.suggested_location = existing.location or planned.suggested_location
        planned.changes = _describe_changes(existing, planned)
        if planned.changes:
            plan.to_update.append(planned)
        else:
            plan.unchanged_count += 1

    if missing_teams:
        available = ", ".join(Team.objects.filter(club=club).order_by("name").values_list("name", flat=True)) or "—"
        raise JSONImportError(_("No team of this club matches: %(missing)s. The club's teams are: %(available)s.") % {"missing": ", ".join(sorted(set(missing_teams))), "available": available})
    return plan


def _describe_changes(event: Event, planned: PlannedGame) -> dict:
    changes = {}
    if event.start != planned.start:
        changes[_("Start")] = (timezone.localtime(event.start).strftime("%Y-%m-%d %H:%M"), planned.start.strftime("%Y-%m-%d %H:%M"))
    if planned.end and event.end != planned.end:
        changes[_("End")] = (timezone.localtime(event.end).strftime("%H:%M") if event.end else "", planned.end.strftime("%H:%M"))
    if {team.pk for team in event.teams.all()} != {team.pk for team in planned.teams}:
        changes[_("Teams")] = (", ".join(team.name for team in event.teams.all()), planned.team_names)
    if event.competition_label != planned.label:
        changes[_("Label")] = (event.competition_label, planned.label)
    return changes


def home_warning(planned: PlannedGame, location: Location | None) -> str | None:
    """Whether ``location`` contradicts who organises the game: a game the club hosts belongs on the
    club's home location, and the home location is by definition only ever the club's own days."""
    if planned.hosted_by_us and (location is None or not location.is_home):
        return _("The club hosts this day, but the location isn't the home location.")
    if not planned.hosted_by_us and location is not None and location.is_home:
        return _("The club doesn't host this day, but the location is the home location.")
    return None


def apply_plan(plan: JSONImportPlan, locations_by_game_id: dict) -> dict:
    """Creates/updates every game in ``plan``, taking each one's location from ``locations_by_game_id``
    (external_game_id -> pk string, or blank for none). A pk that isn't one of the club's locations is
    ignored -- it's straight from the confirm POST."""
    locations_by_id = {str(location.pk): location for location in plan.location_choices}
    created = updated = 0

    with transaction.atomic():
        for planned in plan.to_create:
            event = Event.objects.create(
                club=plan.club,
                title=plan.title,
                kind=Event.EventKind.GAME,
                start=planned.start,
                end=planned.end,
                competition=plan.competition,
                competition_label=planned.label,
                external_game_id=planned.external_game_id,
                external_source_id=plan.source,
                location=locations_by_id.get(str(locations_by_game_id.get(planned.external_game_id) or "")),
            )
            event.teams.set(planned.teams)
            created += 1

        for planned in plan.to_update:
            event = planned.event
            event.start, event.competition_label = planned.start, planned.label
            if planned.end:
                event.end = planned.end
            event.location = locations_by_id.get(str(locations_by_game_id.get(planned.external_game_id) or ""))
            event.save(update_fields=["start", "end", "competition_label", "location"])
            event.teams.set(planned.teams)
            updated += 1

    return {"created": created, "updated": updated}
