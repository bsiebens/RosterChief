"""Fixture imports API. Two routers:

- ``teams_router`` (mounted at ``/teams``): a team's stored fixture sources (``/{team_id}/fixture-sources``), the
  preview/import of a federation fixture list (RBIHF / DFEL / IJNL), re-checks and the RBIHF league standings.
- ``json_router`` (mounted at ``/fixture-imports``): the JSON play-day file import.

Everyone in the management app reads the sources; previewing, importing, re-checking and forgetting a source need the
settings permission (403 first), and a federation source also the club's feature for it (403 naming the feature).
A fetch/parse problem is a 422 on ``url``; an expired or unknown preview token a 422 on ``token``. The fetched payload
is kept server-side between preview and import (``events.services.fixture_previews``), so the import never fetches.
"""

import uuid

from django.db import transaction
from ninja import File, Router, UploadedFile
from ninja.errors import HttpError

from api.schemas_fixture_imports import (
    FixtureGamesDeletedOut,
    FixtureGamesOut,
    FixtureImportIn,
    FixtureImportOut,
    FixturePreviewIn,
    FixturePreviewOut,
    FixtureSourcesOut,
    PlayDayImportIn,
    PlayDayImportOut,
    PlayDayPreviewOut,
    RecheckAllOut,
    RecheckOut,
    RunIn,
    StandingsCompetitionOut,
    TaskOut,
)
from events.services import fixture_previews as svc
from events.services import import_sources, json_import, rbihf_standings
from members.services import access, audit
from members.services.errors import Invalid
from teams.services import teams as team_svc

teams_router = Router()
json_router = Router()


def _team(team_id):
    team = team_svc.get_team(team_id)
    if team is None:
        raise HttpError(404, "Not found.")
    return team


def _season(season_id, field="season_id"):
    season = team_svc.current_season(season_id)
    if season is None:
        message = "Unknown season." if season_id is not None else "There is no current season."
        raise Invalid(message, {field: [message]})
    return season


def _entry(team, source_id: str) -> dict:
    entry = svc.find_entry(team, source_id)
    if entry is None:
        raise HttpError(404, "Not found.")
    return entry


def _recheck(request, team, entry: dict) -> dict:
    """Fetches the stored entry once, records the outcome as its ``last_check``, and returns the summary plus a full
    preview (with a token for ``/import``) when the fetch worked."""
    from django.utils import timezone

    preview = None
    try:
        raw = import_sources.fetch(entry)
        plan = import_sources.build_plan_for(team, entry, raw)
    except import_sources.ImportSourceError as error:
        result = import_sources.CheckResult(entry=entry, error=str(error) or "Could not fetch this fixture list.")
    else:
        result = import_sources.result_from_plan(entry, plan)
        token = svc.stash(request.user, kind="fixtures", team_id=str(team.pk), entry=entry, raw=raw)
        preview = svc.plan_out(plan, entry, token)
    import_sources.update_entry(team, entry, last_checked_at=timezone.now().isoformat(), last_check=result.as_dict())
    team.refresh_from_db(fields=["import_sources"])
    source = next(row for row in svc.sources_out(team, request.features) if row["id"] == svc.entry_id(entry))
    return {"source": source, "check": svc.check_out(result), "preview": preview}


# -- stored sources --


@teams_router.get("/{team_id}/fixture-sources", response=FixtureSourcesOut, summary="A team's fixture import sources", operation_id="team_fixture_sources")
def fixture_sources(request, team_id: uuid.UUID):
    """``sources``: the fixture-list URLs remembered per season and competition (``id`` identifies one in the paths
    below; ``enabled`` is false when the club lost the source's feature). ``available``: every source with what its
    URL looks like and whether the club has its feature. ``can_import``: whether you may preview/import/re-check."""
    team = _team(team_id)
    return {"sources": svc.sources_out(team, request.features), "available": svc.available_out(request.features), "can_import": access.can_manage_settings(request.user)}


@teams_router.post("/{team_id}/fixture-sources/preview", response=FixturePreviewOut, summary="Fetch a fixture list and show what an import would change (settings permission)", operation_id="preview_fixture_import")
def preview(request, team_id: uuid.UUID, payload: FixturePreviewIn):
    """Fetches ``url`` and diffs it against the team's games; nothing is saved. ``season_id`` defaults to the current
    season. Returns every game (create / update / remove / unchanged) with the opponent and location the import will
    use unless told otherwise, the opponent spellings and venue texts that need a decision, warnings, and a
    ``token`` (valid 15 minutes) for ``/import``."""
    team = _team(team_id)
    svc.require_import(request.user, payload.source, request.features)
    season = _season(payload.season_id)
    entry, raw, plan = import_sources.preview_import(request.user, team, source=payload.source, url=payload.url, season=season, competition_label=payload.competition_label, include_past=payload.include_past)
    token = svc.stash(request.user, kind="fixtures", team_id=str(team.pk), entry=entry, raw=raw)
    return svc.plan_out(plan, entry, token)


@teams_router.post("/{team_id}/fixture-sources/import", response=FixtureImportOut, summary="Apply a previewed fixture list (settings permission)", operation_id="import_fixtures")
def import_previewed(request, team_id: uuid.UUID, payload: FixtureImportIn):
    """Re-diffs the payload kept by ``/preview`` (or a re-check) against the current database and applies it:
    creates, updates and deletes games, remembers the source on the team and writes an audit entry.

    - ``opponents``: {source spelling: {"opponent_id"} | {"create": true, "name"?}}. Picking an existing opponent
      remembers the spelling as one of its alternative names. Not listed: the suggested/current opponent, else a new
      opponent named as the source spells it.
    - ``locations``: {venue text: {"location_id"} | {"create": {name, address, city, zip_code, country}} | null}, for
      the games that had no suggested location. Not listed: no location (or the suggestion when there is one).
    - ``game_locations``: {game id: location id | null}, a per-game override.

    Safe to repeat: a second call with the same token changes nothing."""
    team = _team(team_id)
    access.require_settings(request.user)
    data = svc.unstash(request.user, payload.token, "fixtures", team_id=str(team.pk))
    svc.require_import(request.user, data["entry"].get("source", ""), request.features)
    body = payload.dict()
    result = svc.import_with_choices(request.user, team, data["entry"], data["raw"], opponents=body["opponents"], locations=body["locations"], game_locations=body["game_locations"])
    team.refresh_from_db(fields=["import_sources"])
    return {**result, "sources": svc.sources_out(team, request.features)}


@teams_router.post("/{team_id}/fixture-sources/run", response={202: TaskOut}, summary="Check or import a team's stored fixture sources in the background (settings permission)", operation_id="run_team_fixtures")
def run_sources(request, team_id: uuid.UUID, payload: RunIn):
    """Queues ``events.run_team_fixtures`` for this team's stored sources of the current season: ``apply`` false only
    checks them (records what changed on each source), true imports them using the suggested locations and opponents.
    Poll ``/fixture-imports/tasks/{id}`` for the result."""
    from events.tasks import run_team_fixtures

    team = _team(team_id)
    access.require_settings(request.user)
    if not import_sources.sources_for(team, _season(None)):
        raise Invalid("This team has no stored fixture sources.", {"team": ["This team has no stored fixture sources."]})
    async_result = run_team_fixtures.delay(str(team.pk), payload.apply)
    return 202, _task_out(async_result)


@teams_router.post("/{team_id}/fixture-sources/update-past", response={202: TaskOut}, summary="Update the score and game sheet of all played games in the background (settings permission)", operation_id="update_past_games")
def update_past(request, team_id: uuid.UUID):
    """Queues ``events.update_past_games`` for this team's played, imported games; poll ``/fixture-imports/tasks/{id}``."""
    from events.tasks import update_past_games

    team = _team(team_id)
    access.require_settings(request.user)
    return 202, _task_out(update_past_games.delay(str(team.pk)))


@json_router.get("/tasks/{task_id}", response=TaskOut, summary="State and result of a queued fixture task (settings permission)", operation_id="fixture_task")
def task_state(request, task_id: str):
    from celery.result import AsyncResult

    access.require_settings(request.user)
    return _task_out(AsyncResult(task_id))


def _task_out(result) -> dict:
    state = result.state
    done = state in ("SUCCESS", "FAILURE", "REVOKED")
    value = result.result if state == "SUCCESS" and isinstance(result.result, dict) else None
    return {"id": str(result.id), "state": state, "done": done, "ok": state == "SUCCESS", "result": value}


@teams_router.post("/{team_id}/fixture-sources/recheck-all", response=RecheckAllOut, summary="Re-check every stored fixture source of a season (settings permission)", operation_id="recheck_all_fixture_sources")
def recheck_all(request, team_id: uuid.UUID, season: uuid.UUID | None = None):
    """Every stored source of ``?season=`` (default: the current one) whose feature the club has is fetched and diffed
    (never applied); each result carries a preview + token to apply it with ``/import``. Sources without the feature
    are listed under ``skipped``."""
    team = _team(team_id)
    access.require_settings(request.user)
    season = _season(season, "season")
    enabled = set(import_sources.enabled_sources(request.features))
    results, skipped = [], []
    for entry in import_sources.sources_for(team, season):
        if entry["source"] not in enabled:
            skipped.append({"id": svc.entry_id(entry), "source": entry["source"], "reason": svc.feature_message(entry["source"])})
            continue
        results.append(_recheck(request, team, entry))
    return {"season_id": season.pk, "results": results, "skipped": skipped}


@teams_router.post("/{team_id}/fixture-sources/{source_id}/recheck", response=RecheckOut, summary="Re-check one stored fixture source (settings permission)", operation_id="recheck_fixture_source")
def recheck(request, team_id: uuid.UUID, source_id: str):
    """Fetches and diffs the stored source (never applies), records the summary as its ``last_check`` and returns it
    with a full preview + token (``preview`` is null when the fetch failed; ``check.error`` says why)."""
    team = _team(team_id)
    entry = _entry(team, source_id)
    svc.require_import(request.user, entry["source"], request.features)
    return _recheck(request, team, entry)


@teams_router.get("/{team_id}/fixture-sources/{source_id}/games", response=FixtureGamesOut, summary="Count the games of a fixture source that deleting it with its games would remove or keep (settings permission)", operation_id="fixture_source_games")
def source_games(request, team_id: uuid.UUID, source_id: str):
    team = _team(team_id)
    access.require_settings(request.user)
    entry = _entry(team, source_id)
    return import_sources.game_counts(import_sources.linked_games(team, entry))


@teams_router.get("/{team_id}/fixture-games", response=FixtureGamesOut, summary="Count the games of a team's fixture sources that deleting the team with them would remove or keep (settings permission)", operation_id="team_fixture_games")
def team_games(request, team_id: uuid.UUID):
    team = _team(team_id)
    access.require_settings(request.user)
    return import_sources.game_counts(import_sources.games_of_entries(team, import_sources.sources_for(team)))


@teams_router.delete(
    "/{team_id}/fixture-sources/{source_id}",
    response={200: FixtureGamesDeletedOut, 204: None},
    summary="Forget a stored fixture source; its games stay, or with delete_games its unplayed games go too (settings permission)",
    operation_id="remove_fixture_source",
)
def remove_source(request, team_id: uuid.UUID, source_id: str, delete_games: bool = False):
    """Without ``delete_games``: 204, every game stays (detached from the source). With it: 200 and, in one transaction,
    every linked game that is still upcoming with nothing recorded on it is deleted (as the normal event delete does,
    audited); played, live, scored and games with data stay. The counts come back."""
    team = _team(team_id)
    access.require_settings(request.user)
    entry = _entry(team, source_id)
    with transaction.atomic():
        result = import_sources.delete_unplayed_games(request.user, list(import_sources.linked_games(team, entry))) if delete_games else None
        import_sources.remove_entry(team, entry)
        changes = {"source": entry["source"], "url": entry.get("url", ""), "season": entry.get("season_name", ""), "competition_label": entry.get("competition_label", "")}
        if result is not None:
            changes["games_deleted"] = result["deleted"]
        audit.record(request.user, "events.fixture_source_removed", target=team, changes=changes)
    return (200, result) if result is not None else (204, None)


@teams_router.get("/{team_id}/standings", response=list[StandingsCompetitionOut], summary="A team's RBIHF league standings for a season (competition_rbihf feature)", operation_id="team_standings")
def standings(request, team_id: uuid.UUID, season: uuid.UUID | None = None):
    """Read from what the nightly refresh stored on the team's RBIHF sources; never fetched here. One item per ranked
    competition; ``rows`` are the table rows (team_id, team, played, won, ..., points, is_own_team)."""
    if not request.features.has("competition_rbihf"):
        raise HttpError(404, "Not found.")
    team = _team(team_id)
    return rbihf_standings.standings_for(team, team_svc.current_season(season))


# -- JSON play days --


@json_router.post("/json/preview", response=PlayDayPreviewOut, summary="Read a play-day JSON file and show what it would import (settings permission)", operation_id="preview_play_day_import")
def json_preview(request, file: File[UploadedFile]):
    """Multipart, field ``file`` (UTF-8 JSON, see events.services.json_import for the format). Nothing is saved;
    returns every play day (create / update) with its suggested location and home/away warning, and a ``token``."""
    access.require_settings(request.user)
    try:
        raw = file.read().decode("utf-8-sig")
        plan = json_import.build_plan(raw)
    except UnicodeDecodeError as error:
        raise Invalid("The file isn't UTF-8 text.", {"file": ["The file isn't UTF-8 text."]}) from error
    except json_import.JSONImportError as error:
        raise Invalid(str(error), {"file": [str(error)]}) from error
    token = svc.stash(request.user, kind="json", raw=raw)
    return svc.json_plan_out(plan, token)


@json_router.post("/json/import", response=PlayDayImportOut, summary="Apply a previewed play-day file (settings permission)", operation_id="import_play_days")
def json_import_confirm(request, payload: PlayDayImportIn):
    """``locations``: {game id: location id | null}; a game not listed gets its suggested location. A location that
    contradicts who hosts the day is a 422 (``errors["locations.<game id>"]``) unless ``acknowledge_warnings``."""
    access.require_settings(request.user)
    data = svc.unstash(request.user, payload.token, "json")
    return svc.json_import_with_choices(request.user, data["raw"], locations={k: (str(v) if v else None) for k, v in payload.locations.items()}, acknowledge_warnings=payload.acknowledge_warnings)
