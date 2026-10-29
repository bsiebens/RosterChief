"""The fixture imports (RBIHF, DFEL, IJNL) and the "re-check all imported fixtures" run, as one set of
steps shared by the /manage screens (management.views) and the management API (api.panel).

Both surfaces work in two steps: ``prepare_import`` fetches the source and diffs it against the DB
(the preview), ``rebuild_plan`` + ``apply_import`` re-diff the raw payload against the *current* DB
and write it. The web keeps the raw payload in the session between the steps; the API is stateless
and fetches again when it applies (see api.panel.routers.event_extras).

Every source-specific failure surfaces as ``events.services.import_sources.ImportSourceError``.
"""

from dataclasses import dataclass

from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from club.models import Season
from events.services import dfel_import, ijnl_import, import_sources, rbihf_import
from events.services.import_sources import SOURCE_DFEL, SOURCE_IJNL, SOURCE_RBIHF, ImportSourceError
from teams.models import Team

_ERRORS = (rbihf_import.RBIHFImportError, dfel_import.DFELImportError, ijnl_import.IJNLImportError)


@dataclass
class PreparedImport:
    """A fetched and diffed source: what the preview shows, and what the apply step needs again."""

    source: str
    raw: str  # RBIHF: HTML, DFEL/IJNL: JSON
    source_id: str  # Event.external_source_id of the games
    url: str  # what is remembered on Team.import_sources
    plan: rbihf_import.ImportPlan


def prepare_import(club, source: str, *, team, url: str, competition_label: str = "") -> PreparedImport:
    """Step 1: fetch ``url`` for ``team`` and diff it against the club's events."""
    try:
        if source == SOURCE_RBIHF:
            source_id = rbihf_import.extract_team_id(url)
            raw = rbihf_import.fetch_html(url)
            plan = rbihf_import.build_plan(club, team, source_id, raw, competition_label)
            remembered_url = url
        elif source == SOURCE_DFEL:
            team_id, division_id = dfel_import.extract_ids(url)
            source_id = dfel_import.source_id(team_id, division_id)
            raw = dfel_import.fetch_schedule(team_id, division_id)
            plan = dfel_import.build_plan(club, team, source_id, raw, competition_label)
            remembered_url = url
        elif source == SOURCE_IJNL:
            team_id, source_season = ijnl_import.extract_ids(url)
            raw = ijnl_import.fetch_team_data(team_id, source_season)
            source_season = source_season or ijnl_import.season_of(raw)
            source_id = ijnl_import.source_id(team_id, source_season)
            plan = ijnl_import.build_plan(club, team, source_id, raw, competition_label)
            remembered_url = ijnl_import.team_page_url(team_id, source_season)
        else:
            raise ValueError(f"Unknown import source {source!r}")
    except _ERRORS as error:
        raise ImportSourceError(str(error)) from error
    return PreparedImport(source=source, raw=raw, source_id=source_id, url=remembered_url, plan=plan)


def rebuild_plan(club, source: str, *, team, source_id: str, raw: str, competition_label: str = ""):
    """Step 2a: network-free re-diff of ``raw`` against the current DB."""
    try:
        if source == SOURCE_RBIHF:
            return rbihf_import.build_plan(club, team, source_id, raw, competition_label)
        if source == SOURCE_DFEL:
            return dfel_import.build_plan(club, team, source_id, raw, competition_label)
        if source == SOURCE_IJNL:
            return ijnl_import.build_plan(club, team, source_id, raw, competition_label)
    except _ERRORS as error:
        raise ImportSourceError(str(error)) from error
    raise ValueError(f"Unknown import source {source!r}")


def apply_import(plan, source: str, *, team, season, url: str, competition_label: str = "", locations_by_game_id: dict | None = None, opponents_by_game_id: dict | None = None) -> dict:
    """Step 2b: write ``plan`` and remember the URL on the team for the season. Returns ``{created, updated, deleted}``."""
    result = rbihf_import.apply_plan(plan, locations_by_game_id or {}, opponents_by_game_id or {})
    import_sources.record_source(team, source=source, url=url, season=season, competition_label=competition_label)
    return result


def recheck_sections(club, season, teams) -> tuple[list[dict], list[dict]]:
    """Fetch every stored fixture-list URL of ``teams`` for ``season`` (skipping sources the club has switched off) and diff each.

    Returns ``(sections, stash)``: one section per source entry (``team``, ``entry``, ``plan`` or
    ``error``, the form-field ``prefix``, ``show_game_ids``), and the raw payloads in the same order
    as the sections that fetched (``team_id``, ``entry``, ``raw``) for ``apply_recheck``."""
    sections, stash = [], []
    enabled = {source: import_sources.source_enabled(club, source) for source in import_sources.SOURCES}
    for team in teams:
        for entry in import_sources.sources_for(team, season):
            if not enabled.get(entry["source"]):
                continue
            # DFEL/IJNL game ids are UUIDs -- meaningless to staff.
            section = {"team": team, "entry": entry, "plan": None, "error": "", "prefix": "", "show_game_ids": entry["source"] == import_sources.SOURCE_RBIHF}
            try:
                raw = import_sources.fetch(entry)
                section["plan"] = import_sources.build_plan_for(club, team, entry, raw)
            except import_sources.ImportSourceError as error:
                section["error"] = str(error) or _("Could not fetch this fixture list.")
            else:
                section["prefix"] = f"{len(stash)}_"
                stash.append({"team_id": str(team.pk), "entry": entry, "raw": raw})
            sections.append(section)
    return sections, stash


def apply_recheck(club, season, entries: list[dict], choices_for) -> tuple[dict, int, dict]:
    """Re-diff and apply every stashed entry (``recheck_sections``' stash) against the current DB.

    ``choices_for(index, plan, item)`` returns that entry's ``(locations_by_game_id, opponents_by_game_id)``.
    Returns ``(totals, failed, teams_by_id)``; a source that can't be re-read counts as failed and is skipped."""
    teams_by_id = {str(team.pk): team for team in Team.objects.filter(club=club, pk__in=[item["team_id"] for item in entries])}
    enabled = {source: import_sources.source_enabled(club, source) for source in import_sources.SOURCES}
    totals = {"created": 0, "updated": 0, "deleted": 0}
    failed = 0
    for index, item in enumerate(entries):
        team = teams_by_id.get(item["team_id"])
        entry = item["entry"]
        if team is None or not enabled.get(entry.get("source")):
            continue
        try:
            plan = import_sources.build_plan_for(club, team, entry, item["raw"])
        except import_sources.ImportSourceError:
            failed += 1
            continue
        locations_by_game_id, opponents_by_game_id = choices_for(index, plan, item)
        result = rbihf_import.apply_plan(plan, locations_by_game_id, opponents_by_game_id)
        for key in totals:
            totals[key] += result.get(key, 0)
        import_sources.record_source(team, source=entry["source"], url=entry["url"], season=season, competition_label=entry.get("competition_label", ""))
    return totals, failed, teams_by_id


def default_recheck_season(club):
    """The season covering today, else the newest one (None when the club has none)."""
    return Season.covering(club, timezone.localdate()) or Season.objects.filter(club=club).order_by("-start_date").first()


def import_from_source(club, source: str, *, team, season, url: str, competition_label: str = "", locations_by_game_id: dict | None = None, opponents_by_game_id: dict | None = None) -> dict:
    """Both steps in one go (the API has no session to keep the preview in): fetch, diff against the
    current DB, apply and remember the URL. Raises ``ImportSourceError``."""
    prepared = prepare_import(club, source, team=team, url=url, competition_label=competition_label)
    return apply_import(prepared.plan, source, team=team, season=season, url=prepared.url, competition_label=competition_label, locations_by_game_id=locations_by_game_id, opponents_by_game_id=opponents_by_game_id)


def recheck_key(team_id, entry: dict) -> tuple:
    """Identifies one stored source across requests: (team, source, competition label)."""
    return (str(team_id), entry.get("source", ""), (entry.get("competition_label", "") or "").strip().casefold())


def recheck_and_apply(club, season, teams, choices_by_key: dict) -> dict:
    """The whole "re-check all imported fixtures" run without a preview session: fetch every stored
    source of ``teams`` for ``season`` and apply it, taking each source's location/opponent choices
    from ``choices_by_key`` (``recheck_key`` -> ``(locations_by_game_id, opponents_by_game_id)``).
    Returns ``{created, updated, deleted, failed}`` (``failed`` also counts sources that could not be fetched)."""
    sections, stash = recheck_sections(club, season, teams)
    fetch_failed = sum(1 for section in sections if section["error"])
    totals, failed, _teams = apply_recheck(club, season, stash, lambda index, plan, item: choices_by_key.get(recheck_key(item["team_id"], item["entry"]), ({}, {})))
    return {**totals, "failed": failed + fetch_failed}
