"""The HTTP side of the fixture imports (``api.routers.fixture_imports``): turning an import plan into something a UI
can render (one row per game plus the opponent/location spellings that need a human decision), stashing the fetched
payload server-side between preview and import (Django cache, a random token, 15 minutes), and resolving the choices
posted back into the per-game picks ``rbihf_import.apply_plan`` takes (creating the opponents/locations asked for).

The import itself stays in ``events.services.import_sources`` (``preview_import`` / ``import_fixtures``) and
``events.services.json_import``; nothing here writes Event rows.
"""

import difflib
import hashlib
import secrets

from django.core.cache import cache
from django.db import connection, transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from events.models import Location, Opponent
from events.services import import_sources, json_import, rbihf_import
from members.services.errors import Invalid

TOKEN_SECONDS = 15 * 60
EXPIRED = "The preview expired, run it again."

#: What each source's URL looks like, for the "add a source" form.
SOURCE_HELP = {
    import_sources.SOURCE_RBIHF: {
        "name": "RBIHF (rbihf.be)",
        "help": "The team page on rbihf.be. A team in several competitions (Division 1, Cup) has a page per competition.",
        "example": "https://www.rbihf.be/league/team/4460",
    },
    import_sources.SOURCE_DFEL: {
        "name": "DFEL (ehv-nrw.de)",
        "help": "The team page on the EHV-NRW league site: team id and division id.",
        "example": "https://ehv-nrw.de/leagues/team/70779/21691/",
    },
    import_sources.SOURCE_IJNL: {
        "name": "IJNL (ijshockey.nl)",
        "help": "The team page on ijshockey.nl. Without ?seizoen= the season the site currently shows is used (and remembered).",
        "example": "https://www.ijshockey.nl/competities/team/64414?seizoen=2026%2F27",
    },
}


# -- stored sources ------------------------------------------------------------------------------------------------


def entry_id(entry: dict) -> str:
    """A short, URL-safe id for a stored entry, stable as long as its (source, season, competition label) is."""
    source, season_id, label = import_sources.entry_key(entry)
    return hashlib.sha1(f"{source}|{season_id}|{label}".encode()).hexdigest()[:12]


def find_entry(team, wanted_id: str) -> dict | None:
    return next((entry for entry in import_sources.sources_for(team) if entry_id(entry) == wanted_id), None)


def feature_name(source: str) -> str:
    from features.catalog import CATALOG

    code = import_sources.FEATURE_CODES.get(source, "")
    feature = CATALOG.get(code)
    return feature.name if feature else code


def feature_message(source: str) -> str:
    return _("Importing from %(source)s needs the “%(feature)s” feature (%(code)s), which this club doesn't have.") % {
        "source": source,
        "feature": feature_name(source),
        "code": import_sources.FEATURE_CODES.get(source, ""),
    }


def require_import(actor, source: str, features=None) -> None:
    """Settings holders only (403), and the club needs the source's feature (403 naming it)."""
    from members.services.access import require_settings
    from members.services.errors import NotAllowed

    require_settings(actor)
    if source not in import_sources.SOURCES:
        raise Invalid(_("Unknown fixture source."), {"source": [_("Unknown fixture source.")]})
    if not import_sources.source_enabled(source, features):
        raise NotAllowed(feature_message(source))


def source_out(team, row: dict, enabled: set) -> dict:
    """One row of ``sources_for_display``."""
    return {
        "id": entry_id(row),
        "source": row.get("source", ""),
        "url": row.get("url", ""),
        "season_id": row.get("season_id") or None,
        "season_name": row.get("season_name", ""),
        "season_exists": row.get("season_exists", False),
        "competition_label": row.get("competition_label", ""),
        "include_past": bool(row.get("include_past")),
        "last_imported": row.get("recorded_at_dt"),
        "last_checked": row.get("last_checked_at_dt"),
        "last_check": row.get("last_check") or None,
        "enabled": row.get("source") in enabled,
        "has_standings": bool((row.get("standings") or {}).get("competitions")),
    }


def sources_out(team, features) -> list[dict]:
    enabled = set(import_sources.enabled_sources(features))
    return [source_out(team, row, enabled) for row in import_sources.sources_for_display(team)]


def available_out(features) -> list[dict]:
    enabled = set(import_sources.enabled_sources(features))
    return [{"source": source, "feature": import_sources.FEATURE_CODES[source], "feature_name": feature_name(source), "enabled": source in enabled, **SOURCE_HELP[source]} for source in import_sources.SOURCES]


# -- the token stash -----------------------------------------------------------------------------------------------


def _cache_key(token: str) -> str:
    return f"fixture-import:{connection.schema_name}:{token}"


def stash(actor, **data) -> str:
    token = secrets.token_urlsafe(24)
    cache.set(_cache_key(token), {**data, "user_id": str(actor.pk)}, TOKEN_SECONDS)
    return token


def unstash(actor, token: str, kind: str, **expected) -> dict:
    """The stashed payload for ``token`` (same user, same kind and e.g. team), else ``Invalid`` "run it again". Kept
    after use: confirming twice re-diffs against the database and changes nothing the second time."""
    data = cache.get(_cache_key(token or "")) if token else None
    if not data or data.get("user_id") != str(actor.pk) or data.get("kind") != kind or any(data.get(k) != v for k, v in expected.items()):
        raise Invalid(_(EXPIRED), {"token": [_(EXPIRED)]})
    return data


# -- serialising a plan --------------------------------------------------------------------------------------------


def _value(value):
    if hasattr(value, "isoformat"):
        return timezone.localtime(value).isoformat() if getattr(value, "tzinfo", None) else value.isoformat()
    return None if value in (None, "") else str(value)


def _ref(obj):
    return {"id": obj.pk, "name": obj.name} if obj is not None else None


def _similar(text: str, candidates: list[tuple[object, list[str]]], limit: int = 3) -> list:
    """Candidates whose names look like ``text`` (difflib ratio, or one contains the other), best first."""
    wanted = text.strip().casefold()
    if not wanted:
        return []
    scored = []
    for obj, names in candidates:
        best = 0.0
        for name in names:
            name = (name or "").strip().casefold()
            if not name:
                continue
            ratio = difflib.SequenceMatcher(None, wanted, name).ratio()
            if len(name) >= 3 and (name in wanted or wanted in name):
                ratio = max(ratio, 0.8)
            best = max(best, ratio)
        if best >= 0.6:
            scored.append((best, obj))
    scored.sort(key=lambda pair: -pair[0])
    return [obj for _score, obj in scored[:limit]]


def opponent_suggestions(name: str) -> list[Opponent]:
    return _similar(name, [(opponent, [opponent.name, *opponent.alternative_names]) for opponent in Opponent.objects.all()])


def location_suggestions(text: str) -> list[Location]:
    return _similar(text, [(location, [location.name, location.city]) for location in Location.objects.all()])


def _sides(plan, fixture, opponent_name: str) -> tuple[str, str]:
    us = plan.team.short_name or plan.team.name
    return (us, opponent_name) if fixture.is_home else (opponent_name, us)


def _game_row(plan, action: str, fixture, event=None, *, changes=None, location=None, opponent=None) -> dict:
    home, away = _sides(plan, fixture, opponent.name if opponent is not None else fixture.opponent_name)
    return {
        "action": action,
        "game_id": fixture.external_game_id,
        "kickoff": fixture.start,
        "is_home": fixture.is_home,
        "our_side": "home" if fixture.is_home else "away",
        "home": home,
        "away": away,
        "opponent_name": fixture.opponent_name,
        "venue": fixture.venue_text,
        "opponent": _ref(opponent),
        "location": _ref(location),
        "title": event.title if event is not None else "",
        "event_id": event.pk if event is not None else None,
        "score_for": event.score_for if event is not None else None,
        "score_against": event.score_against if event is not None else None,
        "changes": {name: {"old": _value(old), "new": _value(new)} for name, (old, new) in (changes or {}).items()},
    }


def _removed_row(event) -> dict:
    opponent = event.opponent.name if event.opponent_id else ""
    return {
        "action": "remove",
        "game_id": event.external_game_id,
        "kickoff": event.start,
        "is_home": event.is_home_game,
        "our_side": "home" if event.is_home_game else "away",
        "home": "",
        "away": "",
        "title": event.title,
        "opponent_name": opponent,
        "venue": "",
        "opponent": _ref(event.opponent) if event.opponent_id else None,
        "location": _ref(event.location) if event.location_id else None,
        "event_id": event.pk,
        "score_for": event.score_for,
        "score_against": event.score_against,
        "changes": {},
    }


def _planned_rows(plan):
    return [*plan.to_create, *plan.to_update]


def plan_out(plan, entry: dict, token: str) -> dict:
    """The preview of one fixture plan: every game (create/update/remove/unchanged) with the opponent and location the
    import will use unless told otherwise, the spellings that need a decision, and warnings."""
    games = []
    for planned in plan.to_create:
        games.append(_game_row(plan, "create", planned.fixture, location=planned.suggested_location, opponent=planned.suggested_opponent))
    for planned in plan.to_update:
        games.append(_game_row(plan, "update", planned.fixture, planned.event, changes=planned.changes, location=planned.suggested_location, opponent=planned.suggested_opponent))
    for fixture, event in plan.unchanged:
        games.append(_game_row(plan, "unchanged", fixture, event, location=event.location if event.location_id else None, opponent=event.opponent if event.opponent_id else None))
    games.extend(_removed_row(event) for event in plan.to_delete)
    games.sort(key=lambda row: (row["kickoff"], row["game_id"]))

    opponents: dict[str, dict] = {}
    locations: dict[str, dict] = {}
    for planned in _planned_rows(plan):
        fixture = planned.fixture
        name = fixture.opponent_name.strip()
        if name and rbihf_import.suggested_opponent(fixture) is None:
            current = _ref(planned.suggested_opponent)  # an update keeps the game's own opponent unless told otherwise
            problem = opponents.setdefault(name, {"source_name": name, "game_ids": [], "current": current, "default": "keep" if current else "create", "suggestions": [_ref(o) for o in opponent_suggestions(name)]})
            problem["game_ids"].append(fixture.external_game_id)
        venue = fixture.venue_text.strip()
        if venue and planned.suggested_location is None:
            problem = locations.setdefault(venue, {"source_text": venue, "game_ids": [], "suggestions": [{**_ref(loc), "city": loc.city} for loc in location_suggestions(venue)]})
            problem["game_ids"].append(fixture.external_game_id)

    warnings = []
    if any(planned.fixture.is_home for planned in _planned_rows(plan)) and not Location.objects.filter(is_home=True).exists():
        warnings.append(_("The club has no home location yet, so home games get no location unless you pick one."))
    if plan.to_delete:
        warnings.append(_("%(count)s upcoming game(s) are no longer listed and will be deleted.") % {"count": len(plan.to_delete)})

    return {
        "token": token,
        "source": entry["source"],
        "url": entry["url"],
        "season_id": entry.get("season_id") or None,
        "season_name": entry.get("season_name", ""),
        "competition_label": entry.get("competition_label", ""),
        "include_past": bool(entry.get("include_past")),
        "scraped_team_name": plan.scraped_team_name,
        "team": {"id": plan.team.pk, "name": plan.team.name},
        "counts": {"create": len(plan.to_create), "update": len(plan.to_update), "remove": len(plan.to_delete), "unchanged": plan.unchanged_count},
        "has_changes": plan.has_changes,
        "games": games,
        "unknown_opponents": list(opponents.values()),
        "unknown_locations": list(locations.values()),
        "warnings": warnings,
    }


def check_out(result: import_sources.CheckResult) -> dict:
    return {
        "rescheduled": [{"game_id": game_id, "old_start": old, "new_start": new} for game_id, old, new in result.rescheduled],
        "new_games": [{"game_id": game_id, "start": start, "opponent_name": name} for game_id, start, name in result.new_games],
        "removed_games": [{"game_id": game_id, "start": start, "title": title} for game_id, start, title in result.removed_games],
        "error": result.error,
        "has_changes": result.has_changes,
    }


# -- resolving the posted choices ----------------------------------------------------------------------------------


def _location_from_choice(actor, choice, created: list, field: str):
    """A choice is None (no location), {"location_id": ...} or {"create": {...}}. Returns a pk string or ""."""
    from events import services_calendar

    if choice is None:
        return ""
    if choice.get("location_id"):
        location = Location.objects.filter(pk=choice["location_id"]).first()
        if location is None:
            raise Invalid(_("Unknown location."), {field: [_("Unknown location.")]})
        return str(location.pk)
    if choice.get("create"):
        values = dict(choice["create"])
        existing = Location.objects.filter(name__iexact=(values.get("name") or "").strip()).first()
        if existing is not None:
            return str(existing.pk)
        try:
            location = services_calendar.create_location(actor, **values)
        except Invalid as error:
            raise Invalid(str(error), {f"{field}.{key}": messages for key, messages in (error.errors or {}).items()}) from error
        created.append(location)
        return str(location.pk)
    return ""


def _opponent_from_choice(actor, source_name: str, choice: dict, created: list, field: str) -> str:
    """{"opponent_id": ...} or {"create": true, "name": optional}. Returns a pk string ("" = the importer's own
    find-or-create by the source spelling)."""
    from events import services_calendar

    if choice.get("opponent_id"):
        opponent = Opponent.objects.filter(pk=choice["opponent_id"]).first()
        if opponent is None:
            raise Invalid(_("Unknown opponent."), {field: [_("Unknown opponent.")]})
        return str(opponent.pk)
    if choice.get("create"):
        name = (choice.get("name") or source_name).strip()
        existing = next((opponent for opponent in Opponent.objects.all() if opponent.knows_name(name)), None)
        if existing is not None:
            return str(existing.pk)
        alternatives = [source_name] if source_name.strip().casefold() != name.casefold() else []
        try:
            opponent = services_calendar.create_opponent(actor, name=name, alternative_names=alternatives)
        except Invalid as error:
            raise Invalid(str(error), {f"{field}.{key}": messages for key, messages in (error.errors or {}).items()}) from error
        created.append(opponent)
        return str(opponent.pk)
    return ""


def resolve_choices(actor, plan, *, opponents: dict, locations: dict, game_locations: dict) -> tuple[dict, dict, dict]:
    """Per-game picks for ``apply_plan`` from the UI's decisions. Only spellings that still occur in the plan's
    create/update rows are acted on (so confirming twice never creates a second location or opponent).

    - opponents: {source name: {"opponent_id"} | {"create": true, "name"?}}; applies to every game with that name.
      Not mentioned: the planner's suggestion (exact name / remembered spelling / the game's current opponent),
      else a new opponent with the source's spelling.
    - locations: {venue text: {"location_id"} | {"create": {...}} | null}; applies to the games with that venue text
      that had no suggested location. Not mentioned: the suggestion (home location for home games, a matching
      city/name, the game's current location), else none.
    - game_locations: {game id: location id | null}: per-game override, wins over both.
    """
    rows = _planned_rows(plan)
    names_in_plan = {planned.fixture.opponent_name.strip() for planned in rows}
    venues_needing_choice = {planned.fixture.venue_text.strip() for planned in rows if planned.suggested_location is None}
    game_ids = {planned.fixture.external_game_id for planned in rows}
    created_opponents, created_locations = [], []

    opponent_pk = {}
    for name, choice in (opponents or {}).items():
        if name.strip() in names_in_plan and choice:
            opponent_pk[name.strip()] = _opponent_from_choice(actor, name.strip(), choice, created_opponents, f"opponents.{name}")
    location_pk = {}
    for text, choice in (locations or {}).items():
        if text.strip() in venues_needing_choice:
            location_pk[text.strip()] = _location_from_choice(actor, choice, created_locations, f"locations.{text}")
    override_pk = {}
    for game_id, location_id in (game_locations or {}).items():
        if game_id in game_ids:
            override_pk[game_id] = _location_from_choice(actor, {"location_id": location_id} if location_id else None, created_locations, f"game_locations.{game_id}")

    locations_by_game_id, opponents_by_game_id = {}, {}
    for planned in rows:
        fixture = planned.fixture
        game_id = fixture.external_game_id
        venue = fixture.venue_text.strip()
        if game_id in override_pk:
            locations_by_game_id[game_id] = override_pk[game_id]
        elif planned.suggested_location is None and venue in location_pk:
            locations_by_game_id[game_id] = location_pk[venue]
        else:
            locations_by_game_id[game_id] = str(planned.suggested_location.pk) if planned.suggested_location else ""
        name = fixture.opponent_name.strip()
        if name in opponent_pk:
            opponents_by_game_id[game_id] = opponent_pk[name]
        else:
            opponents_by_game_id[game_id] = str(planned.suggested_opponent.pk) if planned.suggested_opponent else ""
    return locations_by_game_id, opponents_by_game_id, {"opponents_created": len(created_opponents), "locations_created": len(created_locations)}


@transaction.atomic
def import_with_choices(actor, team, entry: dict, raw: str, *, opponents: dict, locations: dict, game_locations: dict) -> dict:
    """Resolves the choices against a fresh diff and runs ``import_sources.import_fixtures`` (which re-diffs once more
    inside the same transaction, records the source and audits). Returns the counts."""
    import_sources._require_import(actor, entry.get("source", ""))
    try:
        plan = import_sources.build_plan_for(team, entry, raw)
    except import_sources.ImportSourceError as error:
        raise Invalid(str(error), {"token": [str(error)]}) from error
    locations_by_game_id, opponents_by_game_id, created = resolve_choices(actor, plan, opponents=opponents, locations=locations, game_locations=game_locations)
    result = import_sources.import_fixtures(actor, team, entry, raw, locations_by_game_id=locations_by_game_id, opponents_by_game_id=opponents_by_game_id)
    return {**result, **created}


# -- JSON play days ------------------------------------------------------------------------------------------------


def json_plan_out(plan: json_import.JSONImportPlan, token: str) -> dict:
    games = []
    for action, planned in [*(("create", p) for p in plan.to_create), *(("update", p) for p in plan.to_update)]:
        location = planned.suggested_location
        games.append(
            {
                "action": action,
                "game_id": planned.external_game_id,
                "start": planned.start,
                "end": planned.end,
                "city": planned.city,
                "teams": [{"id": team.pk, "name": team.name} for team in planned.teams],
                "hosted_by_us": planned.hosted_by_us,
                "label": planned.label,
                "location": _ref(location),
                "warning": json_import.home_warning(planned, location),
                "event_id": planned.event.pk if planned.event is not None else None,
                "changes": {name: {"old": _value(old), "new": _value(new)} for name, (old, new) in planned.changes.items()},
            }
        )
    return {
        "token": token,
        "source": plan.source,
        "competition": plan.competition,
        "title": plan.title,
        "counts": {"create": len(plan.to_create), "update": len(plan.to_update), "unchanged": plan.unchanged_count},
        "has_changes": bool(plan.games),
        "games": games,
    }


@transaction.atomic
def json_import_with_choices(actor, raw: str, *, locations: dict, acknowledge_warnings: bool) -> dict:
    """Re-reads the stashed file against the database and applies it. ``locations``: {game id: location id | null};
    a game not mentioned gets its suggested location. A pick that contradicts who hosts the day is refused (422,
    ``errors.warnings``) unless ``acknowledge_warnings``."""
    from members.services import audit
    from members.services.access import require_settings

    require_settings(actor)
    try:
        plan = json_import.build_plan(raw)
    except json_import.JSONImportError as error:
        raise Invalid(str(error), {"token": [str(error)]}) from error
    by_id = {str(location.pk): location for location in plan.location_choices}
    picks, warnings = {}, {}
    for planned in plan.games:
        if planned.external_game_id in (locations or {}):
            wanted = locations[planned.external_game_id]
            if wanted and str(wanted) not in by_id:
                raise Invalid(_("Unknown location."), {f"locations.{planned.external_game_id}": [_("Unknown location.")]})
            location = by_id.get(str(wanted)) if wanted else None
        else:
            location = planned.suggested_location
        picks[planned.external_game_id] = str(location.pk) if location else ""
        warning = json_import.home_warning(planned, location)
        if warning:
            warnings[planned.external_game_id] = [warning]
    if warnings and not acknowledge_warnings:
        raise Invalid(_("Some locations contradict who hosts the day. Check them, or confirm with acknowledge_warnings."), {f"locations.{game_id}": messages for game_id, messages in warnings.items()})
    result = json_import.apply_plan(plan, picks)
    audit.record(actor, "events.play_days_imported", changes={"source": plan.source, **result})
    return result
