"""The fixture-list URLs a team's games are imported from, remembered per season and competition so they can be
re-checked (and re-imported) without pasting them again, plus the entry points that run an import.

Stored on ``Team.import_sources`` (a JSON list, not editable through any form). A confirmed import (``import_fixtures``)
calls ``record_source``, which **replaces** an existing entry for the same (source, season, competition label):
federations hand out a new URL each season, and a corrected URL for the same competition supersedes the old one; and
**appends** anything else (a second competition, a new season). Each entry is a plain dict::

    {
        "source": "RBIHF" | "DFEL" | "IJNL",   # Event.competition of the imported games (a club.Competition name)
        "url": "https://...",                   # the team page staff pasted (IJNL: the canonical one, with a season)
        "season_id": "<Season pk as str>",
        "season_name": "Season '25 - '26",      # display fallback if the season is ever deleted
        "competition_label": "Division 1",      # "" when the team has only one competition
        "recorded_at": "<ISO 8601>",            # last confirmed import from this URL
        "last_checked_at": "<ISO 8601>",        # optional: set by the weekly check
        "last_check": {...},                    # optional: see CheckResult.as_dict
        "standings": {...},                     # optional, RBIHF only: see events.services.rbihf_standings
    }

Whether a club may use a source is the feature of the club.Competition row with that name
(events.services.competitions.available_competitions): a club that lost the feature keeps its stored URLs, but they are
skipped by re-checks and imports are refused.
"""

from dataclasses import dataclass, field

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from events.services import dfel_import, ijnl_import, rbihf_import

SOURCE_RBIHF = "RBIHF"
SOURCE_DFEL = "DFEL"
SOURCE_IJNL = "IJNL"
SOURCES = (SOURCE_RBIHF, SOURCE_DFEL, SOURCE_IJNL)

#: The catalog feature behind each source (also set on the club.Competition row of the same name).
FEATURE_CODES = {SOURCE_RBIHF: "competition_rbihf", SOURCE_DFEL: "competition_dfel", SOURCE_IJNL: "competition_ijnl"}

_IMPORT_ERRORS = (rbihf_import.RBIHFImportError, dfel_import.DFELImportError, ijnl_import.IJNLImportError)


class ImportSourceError(Exception):
    """A stored or pasted source couldn't be fetched or parsed (wraps the per-source import errors)."""


def _key(source: str, season_id, competition_label: str) -> tuple:
    return (source, str(season_id), (competition_label or "").strip().casefold())


def entry_key(entry: dict) -> tuple:
    return _key(entry.get("source", ""), entry.get("season_id", ""), entry.get("competition_label", ""))


def record_source(team, *, source: str, url: str, season, competition_label: str = "", include_past: bool = False) -> dict:
    """Upserts ``team``'s entry for (source, season, competition label) and saves just that field. Returns the stored
    entry. Computed standings survive a re-import from the same URL (they are about the league, not the import)."""
    if source not in SOURCES:
        raise ValueError(f"Unknown import source {source!r}")

    entry = {
        "source": source,
        "url": url.strip(),
        "season_id": str(season.pk),
        "season_name": season.name,
        "competition_label": (competition_label or "").strip(),
        "recorded_at": timezone.now().isoformat(),
        "include_past": bool(include_past),
    }
    key = entry_key(entry)
    replaced = next((existing for existing in (team.import_sources or []) if entry_key(existing) == key), None)
    if replaced and replaced.get("url") == entry["url"] and replaced.get("standings"):
        entry["standings"] = replaced["standings"]
    team.import_sources = [existing for existing in (team.import_sources or []) if entry_key(existing) != key] + [entry]
    team.save(update_fields=["import_sources", "modified"])
    return entry


def update_entry(team, entry: dict, **changes) -> None:
    """Merges ``changes`` into ``team``'s stored entry matching ``entry`` (by key) and saves. A no-op if the entry was
    replaced/removed in the meantime."""
    key = entry_key(entry)
    sources = list(team.import_sources or [])
    for index, existing in enumerate(sources):
        if entry_key(existing) == key:
            sources[index] = {**existing, **changes}
            team.import_sources = sources
            team.save(update_fields=["import_sources", "modified"])
            return


def remove_entry(team, entry: dict) -> bool:
    """Forgets ``team``'s stored entry matching ``entry`` (its games stay). True if one was removed."""
    key = entry_key(entry)
    kept = [existing for existing in (team.import_sources or []) if entry_key(existing) != key]
    if len(kept) == len(team.import_sources or []):
        return False
    team.import_sources = kept
    team.save(update_fields=["import_sources", "modified"])
    return True


def sources_for(team, season=None) -> list[dict]:
    """``team``'s stored entries, optionally only ``season``'s, in a stable display order."""
    entries = [entry for entry in (team.import_sources or []) if entry.get("source") in SOURCES]
    if season is not None:
        entries = [entry for entry in entries if entry.get("season_id") == str(season.pk)]
    return sorted(entries, key=lambda entry: (entry.get("season_name", ""), entry.get("source", ""), entry.get("competition_label", "").casefold()))


def sources_for_display(team) -> list[dict]:
    """``team``'s stored entries for the team page: timestamps parsed, and whether the season still exists."""
    from events.models import Season

    season_ids = {str(pk) for pk in Season.objects.values_list("pk", flat=True)}
    return [
        {
            **entry,
            "season_exists": entry.get("season_id") in season_ids,
            "recorded_at_dt": parse_datetime(entry["recorded_at"]) if entry.get("recorded_at") else None,
            "last_checked_at_dt": parse_datetime(entry["last_checked_at"]) if entry.get("last_checked_at") else None,
            "last_check": entry.get("last_check") or {},
        }
        for entry in sources_for(team)
    ]


def source_enabled(source: str, club_or_features=None) -> bool:
    """Whether the club (a Club, an Entitlements, or None for the current schema's club) may use ``source``."""
    from events.services.competitions import competition_allowed

    return source in SOURCES and competition_allowed(source, club_or_features)


def enabled_sources(club_or_features=None) -> list[str]:
    from events.services.competitions import available_competitions, entitlements_for

    features = entitlements_for(club_or_features)
    names = set(available_competitions(features).values_list("name", flat=True)) if features is not None else set()
    return [source for source in SOURCES if source in names]


def source_id_for(entry: dict) -> str:
    """The ``Event.external_source_id`` this entry's games are stamped with."""
    try:
        if entry["source"] == SOURCE_RBIHF:
            return rbihf_import.extract_team_id(entry["url"])
        if entry["source"] == SOURCE_IJNL:
            team_id, season = ijnl_import.extract_ids(entry["url"])
            if not season:
                raise ijnl_import.IJNLImportError(_("This ijshockey.nl URL has no season -- re-import it."))
            return ijnl_import.source_id(team_id, season)
        if entry["source"] == SOURCE_DFEL:
            team_id, division_id = dfel_import.extract_ids(entry["url"])
            return dfel_import.source_id(team_id, division_id)
        raise ImportSourceError(_("Unknown fixture source “%(source)s”.") % {"source": entry["source"]})
    except (*_IMPORT_ERRORS, KeyError) as error:
        raise ImportSourceError(str(error)) from error


def fetch(entry: dict) -> str:
    """Fetches the raw payload (RBIHF: HTML, DFEL/IJNL: JSON) for ``entry``. The only network call of an import."""
    source_id = source_id_for(entry)
    try:
        if entry["source"] == SOURCE_RBIHF:
            return rbihf_import.fetch_html(entry["url"])
        if entry["source"] == SOURCE_IJNL:
            team_id, season = source_id.split("/", 1)
            return ijnl_import.fetch_team_data(team_id, season)
        team_id, division_id = source_id.split("/", 1)
        return dfel_import.fetch_schedule(team_id, division_id)
    except _IMPORT_ERRORS as error:
        raise ImportSourceError(str(error)) from error


def build_plan_for(team, entry: dict, raw: str) -> rbihf_import.ImportPlan:
    """Network-free: diffs ``raw`` (from ``fetch``) against the database, with the entry's own competition label."""
    source_id = source_id_for(entry)
    label = entry.get("competition_label", "")
    include_past = bool(entry.get("include_past"))
    try:
        if entry["source"] == SOURCE_RBIHF:
            return rbihf_import.build_plan(team, source_id, raw, label, include_past)
        if entry["source"] == SOURCE_IJNL:
            return ijnl_import.build_plan(team, source_id, raw, label, include_past)
        return dfel_import.build_plan(team, source_id, raw, label, include_past)
    except _IMPORT_ERRORS as error:
        raise ImportSourceError(str(error)) from error


def normalize_entry(*, source: str, url: str, season, competition_label: str = "", include_past: bool = False, raw: str | None = None) -> tuple[dict, str]:
    """A not-yet-stored entry for a pasted URL, and its raw payload (fetched unless ``raw`` is given). An IJNL URL
    without a season is pinned to the season the fetched data is for, so the stored URL always names one."""
    if source not in SOURCES:
        raise ImportSourceError(_("Unknown fixture source “%(source)s”.") % {"source": source})
    entry = {"source": source, "url": (url or "").strip(), "season_id": str(season.pk), "season_name": season.name, "competition_label": (competition_label or "").strip(), "include_past": bool(include_past)}
    if source == SOURCE_IJNL:
        try:
            team_id, source_season = ijnl_import.extract_ids(entry["url"])
            if not source_season:
                raw = raw if raw is not None else ijnl_import.fetch_team_data(team_id)
                source_season = ijnl_import.season_of(raw)
        except ijnl_import.IJNLImportError as error:
            raise ImportSourceError(str(error)) from error
        entry["url"] = ijnl_import.team_page_url(team_id, source_season)
    if raw is None:
        raw = fetch(entry)
    else:
        source_id_for(entry)  # validates the URL
    return entry, raw


@transaction.atomic
def apply_entry(team, entry: dict, raw: str, *, locations_by_game_id: dict | None = None, opponents_by_game_id: dict | None = None, use_suggestions: bool = False) -> dict:
    """Re-diffs ``raw`` against the current database and applies it, then records the entry on the team. With
    ``use_suggestions`` the games without an explicit pick get the planner's suggested location/opponent (what the
    preview pre-selects). Returns the counts."""
    plan = build_plan_for(team, entry, raw)
    locations = dict(locations_by_game_id or {})
    opponents = dict(opponents_by_game_id or {})
    if use_suggestions:
        for planned in [*plan.to_create, *plan.to_update]:
            game_id = planned.fixture.external_game_id
            if game_id not in locations and planned.suggested_location is not None:
                locations[game_id] = str(planned.suggested_location.pk)
            if game_id not in opponents and planned.suggested_opponent is not None:
                opponents[game_id] = str(planned.suggested_opponent.pk)
    result = rbihf_import.apply_plan(plan, locations, opponents)
    season = _season(entry)
    if season is not None:
        record_source(team, source=entry["source"], url=entry["url"], season=season, competition_label=entry.get("competition_label", ""), include_past=bool(entry.get("include_past")))
    return result


def _season(entry: dict):
    from events.models import Season

    return Season.objects.filter(pk=entry.get("season_id")).first() if entry.get("season_id") else None


# -- permission-checked entry points (management app) -----------------------------------------------------------------


def _require_import(actor, source: str) -> None:
    from members.services.access import require_settings
    from members.services.errors import NotAllowed

    require_settings(actor)
    if not source_enabled(source):
        raise NotAllowed(_("This club can't import fixtures from %(source)s.") % {"source": source})


def preview_import(actor, team, *, source: str, url: str, season, competition_label: str = "", include_past: bool = False) -> tuple[dict, str, rbihf_import.ImportPlan]:
    """Step 1 of a one-off import: fetch the pasted URL and diff it. Returns (entry, raw, plan); the caller keeps
    ``entry`` and ``raw`` for the confirm step (never the parsed plan). Settings holders only, and the club needs the
    source's feature. A fetch/parse problem raises ``Invalid`` on ``url``."""
    from members.services.errors import Invalid

    _require_import(actor, source)
    try:
        entry, raw = normalize_entry(source=source, url=url, season=season, competition_label=competition_label, include_past=include_past)
        plan = build_plan_for(team, entry, raw)
    except ImportSourceError as error:
        raise Invalid(str(error), {"url": [str(error)]}) from error
    return entry, raw, plan


def import_fixtures(actor, team, entry: dict, raw: str, *, locations_by_game_id: dict | None = None, opponents_by_game_id: dict | None = None) -> dict:
    """Step 2: re-diff the kept payload against the current database, apply it with the picked locations/opponents,
    remember the source on the team and write an audit entry. Returns the counts."""
    from members.services import audit
    from members.services.errors import Invalid

    _require_import(actor, entry.get("source", ""))
    try:
        result = apply_entry(team, entry, raw, locations_by_game_id=locations_by_game_id, opponents_by_game_id=opponents_by_game_id)
    except ImportSourceError as error:
        raise Invalid(str(error), {"url": [str(error)]}) from error
    audit.record(actor, "events.fixtures_imported", target=team, changes={"source": entry["source"], "url": entry["url"], "competition_label": entry.get("competition_label", ""), **result})
    return result


# -- the weekly check ------------------------------------------------------------------------------------------------


@dataclass
class CheckResult:
    """What the weekly check cares about in one entry's plan: only a moved date/hour, a new game, a removed game, or a
    fetch/parse problem is worth a human's attention. Opponent spelling or label drift waits for the next import."""

    entry: dict
    rescheduled: list = field(default_factory=list)  # [(external_game_id, old_start, new_start)]
    new_games: list = field(default_factory=list)  # [(external_game_id, start, opponent_name)]
    removed_games: list = field(default_factory=list)  # [(external_game_id, start, title)]
    error: str = ""

    @property
    def has_changes(self) -> bool:
        return bool(self.rescheduled or self.new_games or self.removed_games or self.error)

    def as_dict(self) -> dict:
        """JSON-safe summary stored on the entry as ``last_check``."""
        return {"rescheduled": len(self.rescheduled), "new_games": len(self.new_games), "removed_games": len(self.removed_games), "error": self.error}


def check_entry(team, entry: dict) -> CheckResult:
    """Fetches ``entry`` and summarises what changed, never raising for a fetch/parse problem (reported on the result)."""
    try:
        plan = build_plan_for(team, entry, fetch(entry))
    except ImportSourceError as error:
        return CheckResult(entry=entry, error=str(error) or _("Could not fetch this fixture list."))
    return result_from_plan(entry, plan)


def result_from_plan(entry: dict, plan) -> CheckResult:
    """The CheckResult of an already built plan (so a caller that keeps the raw payload fetches only once)."""
    result = CheckResult(entry=entry)
    for planned in plan.to_update:
        if "start" in planned.changes:
            old, new = planned.changes["start"]
            result.rescheduled.append((planned.fixture.external_game_id, old, new))
    result.new_games = [(planned.fixture.external_game_id, planned.fixture.start, planned.fixture.opponent_name) for planned in plan.to_create]
    result.removed_games = [(event.external_game_id, event.start, event.title) for event in plan.to_delete]
    return result


def describe_result(team, result: CheckResult) -> str:
    """One summary line, e.g. "First Team · RBIHF · Division 1: 2 games moved to another date or time, 1 new game."."""
    entry = result.entry
    label = (entry.get("competition_label") or "").strip()
    if label:
        heading = _("%(team)s · %(source)s · %(competition)s") % {"team": team.name, "source": entry.get("source", ""), "competition": label}
    else:
        heading = _("%(team)s · %(source)s") % {"team": team.name, "source": entry.get("source", "")}

    if result.error:
        return _("%(heading)s: the fixture list could not be fetched (%(error)s).") % {"heading": heading, "error": result.error}

    parts = []
    if result.rescheduled:
        count = len(result.rescheduled)
        parts.append(ngettext("%(count)d game moved to another date or time", "%(count)d games moved to another date or time", count) % {"count": count})
    if result.new_games:
        count = len(result.new_games)
        parts.append(ngettext("%(count)d new game", "%(count)d new games", count) % {"count": count})
    if result.removed_games:
        count = len(result.removed_games)
        parts.append(ngettext("%(count)d game removed", "%(count)d games removed", count) % {"count": count})
    return _("%(heading)s: %(changes)s.") % {"heading": heading, "changes": ", ".join(parts)}


def build_body(lines: list[str]) -> str:
    intro = _("This week's check of your imported fixture lists found differences with the federation's current schedule:")
    outro = _("Open the team's fixture sources in the management app and re-run the import to review and apply them.")
    bullets = "\n".join(f"- {line}" for line in lines)
    return f"{intro}\n\n{bullets}\n\n{outro}"


def check_club(club, *, today=None, notify: bool = True) -> dict:
    """The weekly check for one club (run inside its schema): every team's entries for the season covering ``today``
    whose source the club may still use are fetched and diffed (never applied), the outcome is recorded on the entry,
    and when anything changed the club's admins get ONE inbox notification summarising it. Returns counts."""
    import logging

    from events.models import Season
    from teams.models import Team

    logger = logging.getLogger(__name__)
    today = today or timezone.localdate()
    stats = {"checked": 0, "changed": 0, "errors": 0, "notified": 0}
    season = Season.covering(club, today)
    if season is None:
        return stats

    enabled = set(enabled_sources(club))
    changes = []
    for team in Team.objects.order_by("name"):
        for entry in sources_for(team, season):
            if entry["source"] not in enabled:
                continue
            try:
                result = check_entry(team, entry)
            except Exception:  # one bad entry never stops the sweep
                logger.exception("fixture_check.entry_failed club=%s team=%s source=%s", club.slug, team.pk, entry.get("source"))
                result = CheckResult(entry=entry, error=_("Unexpected error while checking this fixture list."))
            stats["checked"] += 1
            stats["errors"] += bool(result.error)
            update_entry(team, entry, last_checked_at=timezone.now().isoformat(), last_check=result.as_dict())
            if result.has_changes:
                stats["changed"] += 1
                changes.append((team, result))

    if changes and notify:
        stats["notified"] = notify_admins(club, changes)
    return stats


def notify_admins(club, changes: list) -> int:
    """One inbox notification (pushed, category "admin") to every admin (Owner role) of ``club``."""
    from club.services import club_admins
    from notifications.inbox import notify

    body = build_body([describe_result(team, result) for team, result in changes])
    teams = {team.pk for team, _result in changes}
    href = f"/teams/{next(iter(teams))}" if len(teams) == 1 else "/teams"
    told = 0
    for user in club_admins(club):
        notify(user, club, title=_("Fixture changes found for %(club)s") % {"club": club.name}, body=body, kind="warning", href=href, push=True, category="admin")
        told += 1
    return told


def run_team(club, team, *, apply: bool, today=None) -> dict:
    """Checks (``apply`` false: diff and record the outcome on each entry) or imports (``apply`` true: diff and apply, the
    planner's suggested location/opponent for every game) every stored source of ``team`` for the season covering
    ``today`` that the club may still use. Run inside the club's schema (``events.run_team_fixtures``). Returns counts
    and a line per source."""
    from events.models import Season

    today = today or timezone.localdate()
    out = {"mode": "import" if apply else "check", "sources": 0, "errors": 0, "created": 0, "updated": 0, "deleted": 0, "new": 0, "rescheduled": 0, "removed": 0, "lines": []}
    season = Season.covering(club, today)
    if season is None:
        out["lines"].append(str(_("There is no current season.")))
        return out
    enabled = set(enabled_sources(club))
    for entry in sources_for(team, season):
        if entry["source"] not in enabled:
            continue
        out["sources"] += 1
        label = " · ".join(x for x in (entry["source"], entry.get("competition_label", "")) if x)
        try:
            raw = fetch(entry)
            if apply:
                counts = apply_entry(team, entry, raw, use_suggestions=True)
                for key in ("created", "updated", "deleted"):
                    out[key] += counts.get(key, 0)
                update_entry(team, entry, last_checked_at=timezone.now().isoformat(), last_check={"rescheduled": 0, "new_games": 0, "removed_games": 0, "error": ""})
                out["lines"].append(f"{label}: {counts.get('created', 0)} / {counts.get('updated', 0)} / {counts.get('deleted', 0)}")
            else:
                result = result_from_plan(entry, build_plan_for(team, entry, raw))
                update_entry(team, entry, last_checked_at=timezone.now().isoformat(), last_check=result.as_dict())
                out["new"] += len(result.new_games)
                out["rescheduled"] += len(result.rescheduled)
                out["removed"] += len(result.removed_games)
                out["lines"].append(f"{label}: {describe_result(team, result)}" if result.has_changes else f"{label}: ok")
        except ImportSourceError as error:
            out["errors"] += 1
            out["lines"].append(f"{label}: {error}")
    return out
