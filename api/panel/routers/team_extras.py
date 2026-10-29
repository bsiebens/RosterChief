"""Management API: team extras -- ordering, bulk-add, photos, analytics, fixture import sources (API.md §17).

The web screens are management.views (TeamReorderView, TeamBulkAddView, TeamPhotoSetView / DeleteView,
TeamAnalyticsView / StrengthView, FixtureRecheckView); the rules are the same predicates:

* reorder: ``can_manage_members`` (club admin or member admin);
* bulk-add, photo: a club admin or a manager of *this* team;
* analytics: the "analytics" feature and an ice hockey club (404 otherwise), then the team page's visibility;
* fixture sources: club admins, with at least one fixture-import feature on.

The work is done by the services the web views call (teams.services.ordering / bulk_add / photos / analytics,
events.services.import_sources). The "tournament only" switch is a field of ``PATCH /manage/teams/{id}``.

These routes sit before ``panel_teams_router`` in ``api.v1.PROTECTED`` so the static ``/manage/teams/reorder``
isn't taken by that router's ``/manage/teams/{team_id}``.
"""

import uuid
from typing import Annotated

from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext_lazy as _
from ninja import File, Form, Query, Router, UploadedFile

from api.core.errors import ApiError
from api.core.forms import form_error
from api.core.idempotency import idempotent
from api.core.jobs import start_job
from api.core.schemas import ErrorOut, JobOut
from api.core.uploads import IMAGE_TYPES, validate_upload
from api.panel.guards import panel_club, require
from api.panel.schemas import team_extras as s
from club.models import Season
from club.services.access import can_manage_members, current_season, is_club_admin, teams_managed_by, teams_staffed_by
from events.models import Event, GameTimeline
from events.services import import_sources
from events.services.game_stats import SCOPES, analytics_supported
from events.services.strength_check import STRENGTH_LABELS
from management.forms import TeamBulkAddFormSet, TeamPhotoForm
from members.models import Member
from teams.models import Position, Team, TeamPhoto
from teams.services.analytics import analytics_enabled, analytics_for, set_goal_strength
from teams.services.bulk_add import add_assignments, assigned_member_ids
from teams.services.ordering import InvalidTeamOrder, reorder_teams
from teams.services.photos import remove_team_photo, set_team_photo
from teams.tasks import sync_fixture_sources_job

router = Router(tags=["panel: team extras"])

ERRORS = {401: ErrorOut, 403: ErrorOut, 404: ErrorOut}
INVALID = {422: ErrorOut}
PHOTO_MAX_BYTES = 10 * 1024 * 1024


# --- scoping and authority ----------------------------------------------------------------------


def _managed_team(request, club, team_id) -> Team:
    """A team of this club (404 otherwise) the caller is a club admin or manager of (403 otherwise)."""
    team = get_object_or_404(Team.objects.filter(club=club), pk=team_id)
    require(request, club, lambda user, c: is_club_admin(user, c) or teams_managed_by(user, c).filter(pk=team.pk).exists())
    return team


def _season(club, season_id) -> Season:
    """The named season of this club, else the current one (409 when there is none)."""
    if season_id is not None:
        return get_object_or_404(Season.objects.filter(club=club), pk=season_id)
    season = current_season(club)
    if season is None:
        raise ApiError(409, "no_current_season", _("The club has no current season."))
    return season


# --- reorder ------------------------------------------------------------------------------------


@router.post("/manage/teams/reorder", response={200: s.ReorderOut, **ERRORS, **INVALID}, summary="Reorder the team list")
def reorder(request, payload: s.ReorderIn):
    """Club admins and member admins. Send the ids of the teams as now shown (the whole list, or one
    filtered page of it): they take the slots they held, in the new sequence, and every team is
    renumbered 1..N. A repeated id or a team of another club is a 422 on `ids`."""
    club = panel_club(request)
    require(request, club, can_manage_members)
    try:
        teams = reorder_teams(club, payload.ids)
    except InvalidTeamOrder:
        raise ApiError(422, "validation_error", fields={"ids": [str(_("These teams can't be ordered: an id is repeated or isn't one of the club's teams."))]}) from None
    return {"ids": [team.pk for team in teams]}


# --- bulk add -----------------------------------------------------------------------------------


def _formset_error(formset) -> ApiError:
    fields = {}
    for index, errors in enumerate(formset.errors):
        for name, messages in errors.items():
            key = {"member": "member_id", "position": "position_id", "__all__": "non_field_errors"}.get(name, name)
            fields[f"rows.{index}.{key}"] = [str(message) for message in messages]
    if formset.non_form_errors():
        fields["rows"] = [str(message) for message in formset.non_form_errors()]
    return ApiError(422, "validation_error", fields=fields)


@router.post("/manage/teams/{uuid:team_id}/roster/bulk-add", response={201: s.BulkAddOut, **ERRORS, **INVALID}, summary="Add many players and/or staff at once")
def bulk_add(request, team_id: uuid.UUID, payload: s.BulkAddIn):
    """A club admin or a manager of this team. One row per assignment; the position decides what it means
    (a staff position becomes a staff assignment, anything else a player with an optional jersey number).
    All-or-nothing: errors are keyed `rows.<index>.<field>` and nothing is saved. Rows get the web
    form's checks (eligible member, already assigned, jersey number taken, duplicates in the batch)."""
    club = panel_club(request)
    team = _managed_team(request, club, team_id)
    season = _season(club, payload.season_id)

    def work():
        rostered_ids, staffed_ids = assigned_member_ids(team, season)
        data = {"form-TOTAL_FORMS": str(len(payload.rows)), "form-INITIAL_FORMS": "0", "form-MIN_NUM_FORMS": "0", "form-MAX_NUM_FORMS": "1000"}
        for index, row in enumerate(payload.rows):
            data[f"form-{index}-member"] = str(row.member_id)
            data[f"form-{index}-position"] = str(row.position_id)
            if row.jersey_number is not None:
                data[f"form-{index}-jersey_number"] = str(row.jersey_number)
            if row.is_captain:
                data[f"form-{index}-is_captain"] = "on"
            if row.is_alternate_captain:
                data[f"form-{index}-is_alternate_captain"] = "on"
        formset = TeamBulkAddFormSet(
            data,
            form_kwargs={"club": club, "team": team, "season": season, "position_queryset": Position.objects.filter(club=club), "rostered_ids": rostered_ids, "staffed_ids": staffed_ids},
        )
        if not formset.is_valid():
            raise _formset_error(formset)
        rows = [form.cleaned_data for form in formset.forms if form.cleaned_data.get("member") and form.cleaned_data.get("position")]
        try:
            players, staff = add_assignments(team, season, rows)
        except IntegrityError:
            raise ApiError(422, "validation_error", fields={"rows": [str(_("Someone changed this team while you were adding. Nothing was saved -- check the rows and try again."))]}) from None
        return 201, {"players_added": players, "staff_added": staff}

    return idempotent(request, work)


# --- photo --------------------------------------------------------------------------------------


def _photo_out(team, season) -> dict:
    photo = TeamPhoto.objects.filter(team=team, season=season).first()
    return {"season_id": season.pk, "url": photo.image.url if photo and photo.image else None}


@router.get("/manage/teams/{uuid:team_id}/photo", response={200: s.TeamPhotoOut, **ERRORS}, summary="A team's photo for a season")
def get_photo(request, team_id: uuid.UUID, season_id: uuid.UUID | None = None):
    """Any staff who can see the team (club/member admins: every team; others: the teams they staff)."""
    club = panel_club(request)
    teams = Team.objects.filter(club=club) if can_manage_members(request.user, club) else teams_staffed_by(request.user, club)
    team = get_object_or_404(teams, pk=team_id)
    return _photo_out(team, _season(club, season_id))


@router.post("/manage/teams/{uuid:team_id}/photo", response={200: s.TeamPhotoOut, 413: ErrorOut, 415: ErrorOut, **ERRORS, **INVALID}, summary="Upload or replace a team's photo")
def set_photo(request, team_id: uuid.UUID, photo: File[UploadedFile], season_id: Form[uuid.UUID | None] = None):
    """`multipart/form-data` with the image in `photo` (jpeg, png, webp or gif, max 10 MB) and optionally
    `season_id` (default: the current season). Replaces the season's photo. A club admin or a manager of this team."""
    club = panel_club(request)
    team = _managed_team(request, club, team_id)
    season = _season(club, season_id)
    upload = validate_upload(photo, field="photo", content_types=IMAGE_TYPES, max_bytes=PHOTO_MAX_BYTES)
    form = TeamPhotoForm(files={"image": upload})
    if not form.is_valid():
        raise form_error(form, {"image": "photo"})
    set_team_photo(team, season, form.cleaned_data["image"])
    return _photo_out(team, season)


@router.delete("/manage/teams/{uuid:team_id}/photo", response={204: None, **ERRORS}, summary="Remove a team's photo")
def delete_photo(request, team_id: uuid.UUID, season_id: uuid.UUID | None = None):
    """Season defaults to the current one. Removing nothing is still 204. A club admin or a manager of this team."""
    club = panel_club(request)
    team = _managed_team(request, club, team_id)
    remove_team_photo(team, _season(club, season_id).pk)
    return 204, None


# --- analytics ----------------------------------------------------------------------------------


def _plain(value):
    """team_analytics output -> JSON: games become ``{id, title, start}``, members their id."""
    if isinstance(value, Event):
        return {"id": value.pk, "title": value.title, "start": value.start}
    if isinstance(value, Member):
        return value.pk
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items() if key != "member"}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _analytics_team(request, club, team_id) -> Team:
    """The web TeamAnalyticsView.get_queryset: the feature on, an ice hockey club, then the team page's visibility."""
    if not analytics_enabled(club) or not analytics_supported(club):
        raise ApiError(404, "not_found", _("Not found."))
    teams = Team.objects.filter(club=club) if can_manage_members(request.user, club) else teams_staffed_by(request.user, club)
    return get_object_or_404(teams, pk=team_id)


@router.get("/manage/teams/{uuid:team_id}/analytics", response={200: s.AnalyticsOut, **ERRORS}, summary="A team's season in numbers")
def get_analytics(
    request,
    team_id: uuid.UUID,
    season_id: uuid.UUID | None = None,
    games: Annotated[str, Query(description="`all` (default), `home`, `away` or `last5`.")] = "all",
    friendlies: bool = True,
    competition: str = "",
):
    """Results, goals, penalty minutes and leaderboards from the games' timelines, as the web analytics
    screen shows them. 404 where the "analytics" feature is off or the club's sport has none. Unknown
    `games` falls back to `all`; a `competition` not among the games is dropped (see `competition` in the answer)."""
    club = panel_club(request)
    team = _analytics_team(request, club, team_id)
    season = get_object_or_404(Season.objects.filter(club=club), pk=season_id) if season_id else current_season(club)
    scope = games if games in SCOPES else "all"
    analytics, competition = analytics_for(team, season, scope, friendlies, competition)
    return {"season_id": season.pk if season else None, "scope": scope, "friendlies": friendlies, "competition": competition, "analytics": _plain(analytics) if analytics is not None else None}


@router.post("/manage/teams/{uuid:team_id}/analytics/strength", response={200: s.StrengthOut, **ERRORS, **INVALID}, summary="Fix the strength of a goal the analytics flagged")
def set_strength(request, team_id: uuid.UUID, payload: s.StrengthIn):
    """Stores the strength on the game's timeline (a re-fetch leaves it alone) and re-syncs the game's stats.
    Same visibility as the analytics screen. 422 on `strength` / `goal` when unknown."""
    club = panel_club(request)
    team = _analytics_team(request, club, team_id)
    game = get_object_or_404(Event.objects.filter(club=club, teams=team, kind=Event.EventKind.GAME), pk=payload.event_id)
    timeline = get_object_or_404(GameTimeline, event=game)
    if payload.strength not in STRENGTH_LABELS:
        raise ApiError(422, "validation_error", fields={"strength": [str(_("Unknown strength."))]})
    if not set_goal_strength(game, timeline, payload.goal, payload.strength):
        raise ApiError(422, "validation_error", fields={"goal": [str(_("This game has no such goal."))]})
    return {"ok": True}


# --- fixture import sources ---------------------------------------------------------------------


def _sources_team(request, club, team_id) -> Team:
    """Club admins only, and only where at least one fixture-import feature is on (the web FixtureRecheckAccessMixin)."""
    require(request, club, is_club_admin)
    if not any(import_sources.source_enabled(club, source) for source in import_sources.SOURCES):
        raise ApiError(404, "not_found", _("Not found."))
    return get_object_or_404(Team.objects.filter(club=club), pk=team_id)


def _source_out(entry: dict, season_ids: set) -> dict:
    return {
        **entry,
        "id": import_sources.entry_id(entry),
        "season_id": entry.get("season_id"),
        "season_name": entry.get("season_name", ""),
        "season_exists": entry.get("season_id") in season_ids,
        "competition_label": entry.get("competition_label", ""),
        "recorded_at": parse_datetime(entry["recorded_at"]) if entry.get("recorded_at") else None,
        "last_checked_at": parse_datetime(entry["last_checked_at"]) if entry.get("last_checked_at") else None,
        "last_check": entry.get("last_check") or None,
    }


def _season_ids(club) -> set:
    return {str(pk) for pk in Season.objects.filter(club=club).values_list("pk", flat=True)}


def _checked_url(source: str, url: str) -> str:
    """The URL, trimmed, if it is a team page of ``source`` (else 422 on `url`); no network."""
    url = url.strip()
    try:
        import_sources.validate_url(source, url)
    except import_sources.ImportSourceError as error:
        raise ApiError(422, "validation_error", fields={"url": [str(error) or str(_("This isn't a valid fixture list URL."))]}) from None
    return url


def _entry(team, source_id) -> dict:
    entry = import_sources.find_entry(team, source_id)
    if entry is None:
        raise ApiError(404, "not_found", _("Not found."))
    return entry


@router.get("/manage/teams/{uuid:team_id}/fixture-sources", response={200: s.FixtureSourceListOut, **ERRORS}, summary="A team's stored fixture import sources")
def list_sources(request, team_id: uuid.UUID, season_id: uuid.UUID | None = None):
    """The federation team-page URLs the team's games were imported from, remembered per season and
    competition (all seasons, or one with `season_id`). Club admins; 404 where no fixture-import feature is on."""
    club = panel_club(request)
    team = _sources_team(request, club, team_id)
    season = get_object_or_404(Season.objects.filter(club=club), pk=season_id) if season_id else None
    ids = _season_ids(club)
    return {"items": [_source_out(entry, ids) for entry in import_sources.sources_for(team, season)]}


@router.post("/manage/teams/{uuid:team_id}/fixture-sources", response={201: s.FixtureSourceOut, **ERRORS, **INVALID}, summary="Remember a fixture import source")
def create_source(request, team_id: uuid.UUID, payload: s.FixtureSourceIn):
    """Stores (or, for the same source + season + competition label, replaces) a source without importing
    anything. The URL must be a team page of that federation; the source's feature must be on (422 on
    `source` otherwise). Club admins."""
    club = panel_club(request)
    team = _sources_team(request, club, team_id)
    season = get_object_or_404(Season.objects.filter(club=club), pk=payload.season_id)
    if not import_sources.source_enabled(club, payload.source):
        raise ApiError(422, "validation_error", fields={"source": [str(_("This fixture source isn't enabled for your club."))]})
    url = _checked_url(payload.source, payload.url)

    def work():
        entry = import_sources.record_source(team, source=payload.source, url=url, season=season, competition_label=payload.competition_label)
        return 201, _source_out(entry, _season_ids(club))

    return idempotent(request, work)


@router.post("/manage/teams/{uuid:team_id}/fixture-sources/sync", response={202: JobOut, **ERRORS, **INVALID}, summary="Re-read a team's fixture sources (job)")
def sync_sources(request, team_id: uuid.UUID, payload: s.FixtureSyncIn):
    """Starts a background job (poll `GET /manage/jobs/{id}`) that fetches the season's stored sources of the
    team. Without `apply` it only records what differs (`result`: counts of rescheduled, new and removed
    games and failed sources); with `apply` it also creates, updates and deletes the games like the web
    re-check's confirm step, without location choices. Sources whose feature is off are skipped. Club admins."""
    club = panel_club(request)
    team = _sources_team(request, club, team_id)
    season = _season(club, payload.season_id)

    def work():
        return 202, start_job(request, sync_fixture_sources_job, str(team.pk), str(season.pk), payload.apply)

    return idempotent(request, work)


@router.patch("/manage/teams/{uuid:team_id}/fixture-sources/{source_id}", response={200: s.FixtureSourceOut, **ERRORS, **INVALID}, summary="Change a stored fixture source")
def update_source(request, team_id: uuid.UUID, source_id: str, payload: s.FixtureSourcePatch):
    """Change the URL and/or the competition label. A new label gives the source a new `id`; if another
    source already has that (source, season, label), it is replaced. Club admins."""
    club = panel_club(request)
    team = _sources_team(request, club, team_id)
    entry = _entry(team, source_id)
    season = get_object_or_404(Season.objects.filter(club=club), pk=entry.get("season_id"))
    url = _checked_url(entry["source"], payload.url) if payload.url is not None else entry["url"]
    label = entry.get("competition_label", "") if payload.competition_label is None else payload.competition_label
    with transaction.atomic():
        import_sources.remove_source(team, source_id)
        stored = import_sources.record_source(team, source=entry["source"], url=url, season=season, competition_label=label)
        if url == entry["url"] and label.strip().casefold() == entry.get("competition_label", "").strip().casefold():
            import_sources.update_entry(team, stored, **{k: entry[k] for k in ("last_checked_at", "last_check") if k in entry})
            stored = import_sources.find_entry(team, import_sources.entry_id(stored)) or stored
    return _source_out(stored, _season_ids(club))


@router.delete("/manage/teams/{uuid:team_id}/fixture-sources/{source_id}", response={204: None, **ERRORS}, summary="Forget a stored fixture source")
def delete_source(request, team_id: uuid.UUID, source_id: str):
    """Only forgets the URL; imported games stay. Club admins."""
    club = panel_club(request)
    team = _sources_team(request, club, team_id)
    _entry(team, source_id)
    import_sources.remove_source(team, source_id)
    return 204, None
