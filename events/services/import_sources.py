"""The fixture-list URLs a team's games are imported from, remembered per season
and competition so they can be re-checked without pasting them again.

Stored on ``Team.import_sources`` (a JSON list, not editable through any form):
a confirmed one-off import (management.views.RBIHFImportConfirmView /
DFELImportConfirmView / IJNLImportConfirmView) calls ``record_source``, which **replaces** an existing
entry for the same (source, season, competition label) -- federations hand out a
new URL each season, and a corrected URL for the same competition supersedes the
old one -- and **appends** anything else (a second competition, a new season).

Each entry is a plain dict::

    {
        "source": "RBIHF" | "DFEL" | "IJNL",  # Event.competition of the imported games
        "url": "https://...",                  # the team page staff pasted
        "season_id": "<Season pk as str>",
        "season_name": "25-26",                # display fallback if the season is ever deleted
        "competition_label": "Division 1",     # "" when the team has only one competition
        "recorded_at": "<ISO 8601>",           # last confirmed import from this URL
        "last_checked_at": "<ISO 8601>",       # optional -- set by the weekly check
        "last_check": {...},                   # optional -- see CheckResult.as_dict
        "standings": {...},                    # optional, RBIHF only -- see events.services.rbihf_standings
    }

``fetch`` / ``build_plan_for`` turn an entry back into the same ``ImportPlan`` the
one-off import previews, so a "re-check all" run and the weekly check share the
exact diff logic of events.services.rbihf_import.plan_from_fixtures.
"""

from dataclasses import dataclass, field

from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from events.services import dfel_import, ijnl_import, rbihf_import

SOURCE_RBIHF = "RBIHF"
SOURCE_DFEL = "DFEL"
SOURCE_IJNL = "IJNL"
SOURCES = (SOURCE_RBIHF, SOURCE_DFEL, SOURCE_IJNL)

#: The waffle flag gating each source's import section (management.views'
#: RBIHFImportView/DFELImportView/IJNLImportView ``feature_flag``) -- a club whose flag was turned
#: off keeps its stored URLs but they're skipped by re-checks.
FEATURE_FLAGS = {SOURCE_RBIHF: "RBIHF", SOURCE_DFEL: "DFEL", SOURCE_IJNL: "IJNL"}

_IMPORT_ERRORS = (rbihf_import.RBIHFImportError, dfel_import.DFELImportError, ijnl_import.IJNLImportError)


class ImportSourceError(Exception):
    """A stored source couldn't be fetched or parsed (wraps the per-source import errors)."""


def _key(source: str, season_id, competition_label: str) -> tuple:
    return (source, str(season_id), (competition_label or "").strip().casefold())


def entry_key(entry: dict) -> tuple:
    return _key(entry.get("source", ""), entry.get("season_id", ""), entry.get("competition_label", ""))


def record_source(team, *, source: str, url: str, season, competition_label: str = "") -> dict:
    """Upserts ``team``'s entry for (source, season, competition label) and saves
    just that field. Returns the stored entry. Computed standings survive a
    re-import from the same URL -- they're about the league, not the import."""
    if source not in SOURCES:
        raise ValueError(f"Unknown import source {source!r}")

    entry = {
        "source": source,
        "url": url.strip(),
        "season_id": str(season.pk),
        "season_name": season.name,
        "competition_label": (competition_label or "").strip(),
        "recorded_at": timezone.now().isoformat(),
    }
    key = entry_key(entry)
    replaced = next((existing for existing in (team.import_sources or []) if entry_key(existing) == key), None)
    if replaced and replaced.get("url") == entry["url"] and replaced.get("standings"):
        entry["standings"] = replaced["standings"]
    sources = [existing for existing in (team.import_sources or []) if entry_key(existing) != key]
    sources.append(entry)
    team.import_sources = sources
    team.save(update_fields=["import_sources"])
    return entry


def update_entry(team, entry: dict, **changes) -> None:
    """Merges ``changes`` into ``team``'s stored entry matching ``entry`` (by key)
    and saves -- used by the weekly check's bookkeeping. A no-op if the entry was
    replaced/removed in the meantime."""
    key = entry_key(entry)
    sources = list(team.import_sources or [])
    for index, existing in enumerate(sources):
        if entry_key(existing) == key:
            sources[index] = {**existing, **changes}
            team.import_sources = sources
            team.save(update_fields=["import_sources"])
            return


def sources_for(team, season=None) -> list[dict]:
    """``team``'s stored entries, optionally only ``season``'s, in a stable display order."""
    entries = [entry for entry in (team.import_sources or []) if entry.get("source") in SOURCES]
    if season is not None:
        entries = [entry for entry in entries if entry.get("season_id") == str(season.pk)]
    return sorted(entries, key=lambda entry: (entry.get("season_name", ""), entry.get("source", ""), entry.get("competition_label", "").casefold()))


def source_enabled(club, source: str) -> bool:
    """Whether ``club`` still has ``source``'s import feature turned on."""
    from features.models import Flag  # deferred: features imports back into club/events models

    flag = Flag.objects.filter(name=FEATURE_FLAGS[source]).first()
    return flag is not None and flag.is_active_for_club(club)


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
        team_id, division_id = dfel_import.extract_ids(entry["url"])
        return dfel_import.source_id(team_id, division_id)
    except (*_IMPORT_ERRORS, KeyError) as error:
        raise ImportSourceError(str(error)) from error


def fetch(entry: dict) -> str:
    """Fetches the raw payload (RBIHF: HTML, DFEL/IJNL: JSON) for ``entry`` --
    the same plain string the one-off import stashes in the session."""
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


def build_plan_for(club, team, entry: dict, raw: str) -> rbihf_import.ImportPlan:
    """Network-free: diffs ``raw`` (from ``fetch``) against the DB exactly like the
    one-off import's preview, using the entry's own competition label."""
    source_id = source_id_for(entry)
    label = entry.get("competition_label", "")
    try:
        if entry["source"] == SOURCE_RBIHF:
            return rbihf_import.build_plan(club, team, source_id, raw, label)
        if entry["source"] == SOURCE_IJNL:
            return ijnl_import.build_plan(club, team, source_id, raw, label)
        return dfel_import.build_plan(club, team, source_id, raw, label)
    except _IMPORT_ERRORS as error:
        raise ImportSourceError(str(error)) from error


@dataclass
class CheckResult:
    """What the weekly check cares about in one entry's plan -- deliberately
    narrower than the full plan: only a moved date/hour, a new game, a removed
    game, or a fetch/parse problem is worth a human's attention. Opponent
    spelling or competition-label drift is left for the next manual import."""

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
        return {
            "rescheduled": len(self.rescheduled),
            "new_games": len(self.new_games),
            "removed_games": len(self.removed_games),
            "error": self.error,
        }


def check_entry(club, team, entry: dict) -> CheckResult:
    """Fetches ``entry`` and summarises what changed, never raising for a fetch/parse
    problem (that's reported on the result instead)."""
    result = CheckResult(entry=entry)
    try:
        plan = build_plan_for(club, team, entry, fetch(entry))
    except ImportSourceError as error:
        result.error = str(error) or str(_("Could not fetch this fixture list."))
        return result

    for planned in plan.to_update:
        if "start" in planned.changes:
            old, new = planned.changes["start"]
            result.rescheduled.append((planned.fixture.external_game_id, old, new))
    result.new_games = [(planned.fixture.external_game_id, planned.fixture.start, planned.fixture.opponent_name) for planned in plan.to_create]
    result.removed_games = [(event.external_game_id, event.start, event.title) for event in plan.to_delete]
    return result
