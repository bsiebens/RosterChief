"""Management API: what hangs off an event beyond the event itself (API.md §17.3, phase A).

The JSON counterpart of the /manage event screens for tasks, attachments, referee / official fees
and payment-form PDFs, the competition fetch and the fixture imports. Who may do what is the web's
rule (club.mixins), decided by club.services.access:

* tasks, attachments and the competition fetch: whoever may manage the event (an admin, a manager of
  one of its teams, a member of one of its groups; ``403`` otherwise, an unknown or other club's
  event is ``404``). Task reads only need to see the event;
* fees: club admins; the referee form PDF: ``can_manage_referees``; the official form PDF the same
  once the club has the "officials" feature;
* fixture imports (RBIHF, DFEL, IJNL) and the re-check: club admins, and the importer's feature
  switched on for the club (``404`` otherwise, like the web section). The JSON play-day import is
  admin-only and always available.

The logic is the web's: forms from management.forms for validation, services for the work
(events.services.{tasks,attachments,fees,fixture_import,json_import,competitions}); nothing is
re-implemented here.

Differences from the web:

* the imports have no session between preview and apply. ``…/preview`` returns the diff; ``…/apply``
  fetches the source again and applies it with the chosen locations and opponents (so it can apply a
  newer list than the one previewed, which the web's re-diff step does too). Fixture applies and the
  re-check apply are Celery jobs (202 + ``GET /manage/jobs/{id}``); the JSON import only touches the
  database and answers directly;
* ``PUT …/fees`` is all-or-nothing: if one row is invalid nothing is saved (422), where the web saves
  the valid rows and reports the rest;
* the competition fetch is synchronous (one source, one request) and answers the refreshed event.
"""

import json
import uuid

from django.core.exceptions import ValidationError
from django.db.models import Count
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404
from django.utils.translation import gettext_lazy as _
from ninja import File, Form, Router, UploadedFile

from api.core.errors import ApiError
from api.core.forms import form_error, post_data
from api.core.idempotency import idempotent
from api.core.jobs import start_job
from api.core.schemas import ErrorOut, JobOut
from api.core.uploads import validate_upload
from api.panel.events_access import NOT_FOUND, _events, _fresh_event_payload, _get_event, _get_manageable_event
from api.panel.guards import panel_club, require
from api.panel.schemas import event_extras as s
from club.models import Season
from club.services.access import can_manage_referees, is_club_admin
from events.models import EventAttachment, EventTask
from events.services import import_sources, json_import
from events.services.attachments import ALLOWED_EXTENSIONS, MAX_ATTACHMENT_BYTES, add_attachments, attachment_response, delete_attachment
from events.services.competitions import CompetitionFetchError, fetch_game_info
from events.services.fees import update_fees
from events.services.fixture_import import default_recheck_season, prepare_import, recheck_sections
from events.services.officials import officials_enabled_for
from events.services.tasks import create_task
from events.tasks import api_import_fixtures, api_recheck_fixtures
from management.forms import DFELImportForm, EventTaskForm, IJNLImportForm, RBIHFImportForm
from management.pdf import PDFExportError, event_official_form_document, event_referee_form_document
from members.models import Member
from teams.models import Team

router = Router(tags=["panel: event extras"])

ERRORS = {401: ErrorOut, 403: ErrorOut, 404: ErrorOut}
INVALID = {422: ErrorOut}
UPLOAD_ERRORS = {413: ErrorOut, 415: ErrorOut, 422: ErrorOut}

EVENT = "/manage/events/{uuid:event_id}"


# --- helpers ---------------------------------------------------------------------------------


def _member(request) -> Member | None:
    return Member.objects.filter(user=request.user).first()


def _require_admin(request, club) -> None:
    require(request, club, is_club_admin)


# --- tasks -----------------------------------------------------------------------------------


def _task_out(task: EventTask) -> dict:
    return {
        "id": task.pk,
        "title": task.title,
        "description": task.description,
        "needed_quantity": task.needed_quantity,
        "claimed_count": task.claimed_count,
        "created_at": task.created_at,
    }


def _task(event, task_id) -> EventTask:
    return get_object_or_404(EventTask.objects.filter(event=event).annotate(claimed_count=Count("claims")), pk=task_id)


def _task_form(values: dict, *, instance=None) -> EventTaskForm:
    form = EventTaskForm(post_data(values), instance=instance)
    if not form.is_valid():
        raise form_error(form)
    return form


@router.get(f"{EVENT}/tasks", response={200: s.EventTaskListOut, **ERRORS}, summary="An event's tasks")
def list_tasks(request, event_id: uuid.UUID):
    club = panel_club(request)
    event = _get_event(request, club, event_id)
    return {"items": [_task_out(task) for task in EventTask.objects.filter(event=event).annotate(claimed_count=Count("claims"))]}


@router.post(f"{EVENT}/tasks", response={201: s.EventTaskOut, **ERRORS, **INVALID}, summary="Add a task to an event")
def add_task(request, event_id: uuid.UUID, payload: s.EventTaskIn):
    club = panel_club(request)
    event = _get_manageable_event(request, club, event_id)
    form = _task_form(payload.model_dump())
    return idempotent(request, lambda: (201, _task_out(_task(event, create_task(form, event, _member(request)).pk))))


@router.patch(f"{EVENT}/tasks/{{uuid:task_id}}", response={200: s.EventTaskOut, **ERRORS, **INVALID}, summary="Change a task")
def change_task(request, event_id: uuid.UUID, task_id: uuid.UUID, payload: s.EventTaskPatch):
    club = panel_club(request)
    event = _get_manageable_event(request, club, event_id)
    task = _task(event, task_id)
    values = {"title": task.title, "description": task.description, "needed_quantity": task.needed_quantity, **payload.model_dump(exclude_unset=True)}
    _task_form(values, instance=task).save()
    return _task_out(_task(event, task_id))


@router.delete(f"{EVENT}/tasks/{{uuid:task_id}}", response={204: None, **ERRORS}, summary="Delete a task (and its claims)")
def delete_task(request, event_id: uuid.UUID, task_id: uuid.UUID):
    club = panel_club(request)
    event = _get_manageable_event(request, club, event_id)
    _task(event, task_id).delete()
    return 204, None


# --- attachments -----------------------------------------------------------------------------


def _attachment_out(attachment: EventAttachment) -> dict:
    return {"id": attachment.pk, "name": attachment.name, "size": attachment.size, "uploaded_by": str(attachment.uploaded_by) if attachment.uploaded_by_id else None, "created": attachment.created}


def _attachment(event, attachment_id) -> EventAttachment:
    return get_object_or_404(EventAttachment.objects.filter(event=event).select_related("uploaded_by"), pk=attachment_id)


@router.get(f"{EVENT}/attachments", response={200: s.EventAttachmentListOut, **ERRORS}, summary="An event's attachments")
def list_attachments(request, event_id: uuid.UUID):
    club = panel_club(request)
    event = _get_manageable_event(request, club, event_id)
    return {"items": [_attachment_out(a) for a in event.attachments.select_related("uploaded_by")]}


@router.post(f"{EVENT}/attachments", response={201: s.EventAttachmentListOut, **ERRORS, **UPLOAD_ERRORS}, summary="Attach one or more files (multipart)")
def upload_attachments(request, event_id: uuid.UUID, files: File[list[UploadedFile]]):
    """`multipart/form-data` with one or more `files` parts: documents and pictures up to 10 MB each, at
    most 20 attachments per event. Answers the files that were added."""
    club = panel_club(request)
    event = _get_manageable_event(request, club, event_id)
    uploads = [validate_upload(upload, field="files", max_bytes=MAX_ATTACHMENT_BYTES, content_types=(), extensions=sorted(ALLOWED_EXTENSIONS)) for upload in files]

    def work():
        try:
            added = add_attachments(event, uploads, _member(request))
        except ValidationError as error:
            raise ApiError(422, "validation_error", fields={"files": error.messages}) from error
        return 201, {"items": [_attachment_out(_attachment(event, a.pk)) for a in added]}

    return idempotent(request, work)


@router.get(f"{EVENT}/attachments/{{uuid:attachment_id}}", response={200: None, **ERRORS}, summary="Download an attachment")
def download_attachment(request, event_id: uuid.UUID, attachment_id: uuid.UUID):
    """The file itself (`Content-Disposition: attachment`), authenticated by the bearer token."""
    club = panel_club(request)
    event = _get_manageable_event(request, club, event_id)
    try:
        return attachment_response(_attachment(event, attachment_id))
    except Http404 as error:
        raise ApiError(404, "not_found", NOT_FOUND) from error


@router.delete(f"{EVENT}/attachments/{{uuid:attachment_id}}", response={204: None, **ERRORS}, summary="Delete an attachment")
def remove_attachment(request, event_id: uuid.UUID, attachment_id: uuid.UUID):
    club = panel_club(request)
    event = _get_manageable_event(request, club, event_id)
    delete_attachment(_attachment(event, attachment_id))
    return 204, None


# --- fees ------------------------------------------------------------------------------------


def _fee_event(request, club, event_id):
    """Admin only, like the web screen; unscoped to the caller's teams (an unknown id is a 404)."""
    event = _events(club).filter(pk=event_id).first()
    if event is None:
        raise ApiError(404, "not_found", NOT_FOUND)
    _require_admin(request, club)
    return event


def _fee_row(kind: str, assignment) -> dict:
    return {
        "kind": kind,
        "id": assignment.pk,
        "name": assignment.display_name,
        "fee": assignment.fee,
        "km": assignment.km,
        "km_rate": assignment.km_rate,
        "no_fee": assignment.no_fee,
        "total_payable": assignment.total_payable,
    }


def _fee_rows(event, club) -> list[dict]:
    rows = [_fee_row("referee", a) for a in event.referees.select_related("member")]
    if officials_enabled_for(club):
        rows += [_fee_row("official", a) for a in event.officials.select_related("member")]
    return rows


@router.get(f"{EVENT}/fees", response={200: s.FeeListOut, **ERRORS}, summary="The fee line of every referee (and official) on a game")
def list_fees(request, event_id: uuid.UUID):
    club = panel_club(request)
    event = _fee_event(request, club, event_id)
    return {"items": _fee_rows(event, club)}


@router.put(f"{EVENT}/fees", response={200: s.FeesOut, **ERRORS, **INVALID}, summary="Save the fee line of referees and officials")
def save_fees(request, event_id: uuid.UUID, payload: s.FeesIn):
    """Each row: the assignment's `kind` and `id` with `fee`, `km`, `km_rate`, `no_fee`. All-or-nothing: a
    row that doesn't validate answers 422 (`fields["fees.<id>.<field>"]`) and nothing is saved."""
    club = panel_club(request)
    event = _fee_event(request, club, event_id)
    officials_on = officials_enabled_for(club)
    known = {("referee", a.pk) for a in event.referees.all()} | ({("official", a.pk) for a in event.officials.all()} if officials_on else set())
    unknown = [str(row.id) for row in payload.fees if (row.kind, row.id) not in known]
    if unknown:
        raise ApiError(422, "validation_error", fields={"fees": [str(_("“%(id)s” is not a referee or official on this event.") % {"id": pk}) for pk in unknown]})

    data = {}
    for row in payload.fees:
        prefix = f"{row.kind}_{row.id}"
        data[f"{prefix}-fee"] = "" if row.fee is None else str(row.fee)
        data[f"{prefix}-km"] = "" if row.km is None else str(row.km)
        data[f"{prefix}-km_rate"] = "" if row.km_rate is None else str(row.km_rate)
        if row.no_fee:
            data[f"{prefix}-no_fee"] = "on"

    update = update_fees(event, data, include_officials=officials_on, all_or_nothing=True)
    if update.failed:
        fields = {f"fees.{failure.assignment.pk}.{name}": messages for failure in update.failed for name, messages in failure.errors.items()}
        raise ApiError(422, "validation_error", fields=fields)
    return {"saved": update.saved, "items": _fee_rows(event, club)}


# --- payment-form PDFs -----------------------------------------------------------------------


def _pdf_response(builder, club, event, filename: str) -> HttpResponse:
    try:
        pdf = builder(club, event)
    except PDFExportError as error:
        raise ApiError(503, "pdf_unavailable", str(error)) from error
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response["Cache-Control"] = "private, no-store"
    return response


@router.get(f"{EVENT}/referees/form-pdf", response={200: None, **ERRORS, 503: ErrorOut}, summary="Referee payment form (PDF)")
def referee_form_pdf(request, event_id: uuid.UUID):
    club = panel_club(request)
    event = get_object_or_404(_events(club).prefetch_related("referees__member"), pk=event_id)
    require(request, club, can_manage_referees)
    return _pdf_response(event_referee_form_document, club, event, f"referee-form-{event.pk}.pdf")


@router.get(f"{EVENT}/officials/form-pdf", response={200: None, **ERRORS, 503: ErrorOut}, summary="Official payment form (PDF)")
def official_form_pdf(request, event_id: uuid.UUID):
    """404 while the club doesn't use the officials module."""
    club = panel_club(request)
    if not officials_enabled_for(club):
        raise ApiError(404, "not_found", NOT_FOUND)
    event = get_object_or_404(_events(club).prefetch_related("officials__member"), pk=event_id)
    require(request, club, can_manage_referees)
    return _pdf_response(event_official_form_document, club, event, f"official-form-{event.pk}.pdf")


# --- competition fetch -----------------------------------------------------------------------


@router.post(f"{EVENT}/fetch-game-info", response={200: s.FetchGameInfoOut, **ERRORS, 502: ErrorOut}, summary="Refresh a game's score and status from its competition")
def fetch_game(request, event_id: uuid.UUID):
    """`fetched: false` when the event's competition isn't enabled for this club. A failing source is 502 `competition_fetch_failed`."""
    club = panel_club(request)
    event = _get_manageable_event(request, club, event_id)
    try:
        fetched = fetch_game_info(event)
    except CompetitionFetchError as error:
        raise ApiError(502, "competition_fetch_failed", str(error)) from error
    return {"fetched": fetched, "event": _fresh_event_payload(request, club, event.pk)}


# --- fixture imports -------------------------------------------------------------------------

SOURCES = {
    "rbihf": (import_sources.SOURCE_RBIHF, "RBIHF", RBIHFImportForm),
    "dfel": (import_sources.SOURCE_DFEL, "DFEL", DFELImportForm),
    "ijnl": (import_sources.SOURCE_IJNL, "IJNL", IJNLImportForm),
}
FORM_ALIASES = {"team": "team_id", "season": "season_id"}


def _admin(request):
    club = panel_club(request)
    _require_admin(request, club)
    return club


def _importer(request, source: str):
    """(club, the source's constant, its form class) for an admin whose club has the importer on."""
    club = panel_club(request)
    constant, _name, form_class = SOURCES[source]
    if not import_sources.source_enabled(club, constant):
        raise ApiError(404, "not_found", NOT_FOUND)
    _require_admin(request, club)
    return club, constant, form_class


def _import_form(club, form_class, payload):
    form = form_class(post_data(payload.model_dump(include={"url", "team_id", "season_id", "competition_label"}), single={"team_id": "team", "season_id": "season"}), club=club)
    if not form.is_valid():
        raise form_error(form, FORM_ALIASES)
    return form


def _text(value) -> str | None:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _fixture_out(fixture) -> dict:
    return {"external_game_id": fixture.external_game_id, "start": fixture.start, "is_home": fixture.is_home, "opponent_name": fixture.opponent_name, "venue_text": fixture.venue_text}


def _plan_out(plan) -> dict:
    def create(planned):
        return {"fixture": _fixture_out(planned.fixture), "suggested_location_id": planned.suggested_location.pk if planned.suggested_location else None, "suggested_opponent_id": planned.suggested_opponent.pk if planned.suggested_opponent else None}

    return {
        "source": plan.competition,
        "scraped_team_name": plan.scraped_team_name,
        "source_id": plan.source_id,
        "to_create": [create(planned) for planned in plan.to_create],
        "to_update": [{**create(planned), "event_id": planned.event.pk, "changes": {str(field): [_text(old), _text(new)] for field, (old, new) in planned.changes.items()}} for planned in plan.to_update],
        "to_delete": [{"event_id": event.pk, "title": event.title, "start": event.start, "external_game_id": event.external_game_id} for event in plan.to_delete],
        "unchanged_count": plan.unchanged_count,
        "location_choices": [{"id": location.pk, "name": str(location)} for location in plan.location_choices],
        "opponent_choices": [{"id": opponent.pk, "name": str(opponent)} for opponent in (plan.opponent_choices or [])],
    }


@router.get("/manage/events/import/sources", response={200: s.ImportSourcesOut, **ERRORS}, summary="Which fixture importers the club has")
def import_sources_list(request):
    club = _admin(request)
    sources = [{"source": key, "name": name, "enabled": import_sources.source_enabled(club, constant)} for key, (constant, name, _form) in SOURCES.items()]
    return {"sources": sources, "json_import": True, "recheck": any(item["enabled"] for item in sources)}


@router.post("/manage/events/import/json/preview", response={200: s.JSONImportPreviewOut, **ERRORS, **UPLOAD_ERRORS}, summary="Preview a JSON play-day import (multipart)")
def json_preview(request, file: File[UploadedFile]):
    """`multipart/form-data` with the `file` part: the play-day JSON file. Nothing is saved."""
    club = _admin(request)
    plan = _json_plan(club, file)[1]
    return _json_plan_out(plan, json_import.review_rows(plan))


@router.post("/manage/events/import/json/apply", response={200: s.JSONImportOut, **ERRORS, **UPLOAD_ERRORS}, summary="Apply a JSON play-day import (multipart)")
def json_apply(request, file: File[UploadedFile], locations: Form[str] = "", acknowledge_warnings: Form[bool] = False):
    """`multipart/form-data`: the same `file`, `locations` (a JSON object, `external_game_id` -> location id) and
    `acknowledge_warnings`. A location that contradicts who hosts the day is 422 (`fields["locations.<game id>"]`)
    until `acknowledge_warnings` is true."""
    club = _admin(request)
    try:
        chosen = json.loads(locations) if locations.strip() else {}
        if not isinstance(chosen, dict):
            raise ValueError
    except ValueError as error:
        raise ApiError(422, "validation_error", fields={"locations": [str(_("Send a JSON object of game id to location id."))]}) from error
    chosen = {str(game_id): str(location_id or "") for game_id, location_id in chosen.items()}

    def work():
        plan = _json_plan(club, file)[1]
        warnings = {f"locations.{row['planned'].external_game_id}": [row["warning"]] for row in json_import.review_rows(plan, chosen) if row["warning"]}
        if warnings and not acknowledge_warnings:
            raise ApiError(422, "validation_error", fields={**{key: [str(m) for m in messages] for key, messages in warnings.items()}, "acknowledge_warnings": [str(_("Acknowledge the location warnings to import anyway."))]})
        return 200, json_import.apply_plan(plan, chosen)

    return idempotent(request, work)


def _json_plan(club, upload):
    upload = validate_upload(upload, field="file", content_types=(), extensions=(".json",))
    try:
        raw = upload.read().decode("utf-8-sig")
        return raw, json_import.build_plan(club, raw)
    except UnicodeDecodeError as error:
        raise ApiError(422, "validation_error", fields={"file": [str(_("The file isn't UTF-8 text."))]}) from error
    except json_import.JSONImportError as error:
        raise ApiError(422, "validation_error", fields={"file": [str(error)]}) from error


def _json_plan_out(plan, rows) -> dict:
    return {
        "source": plan.source,
        "competition": plan.competition,
        "title": plan.title,
        "games": [
            {
                "external_game_id": row["planned"].external_game_id,
                "start": row["planned"].start,
                "end": row["planned"].end,
                "city": row["planned"].city,
                "team_ids": [team.pk for team in row["planned"].teams],
                "team_names": row["planned"].team_names,
                "hosted_by_us": row["planned"].hosted_by_us,
                "label": row["planned"].label,
                "is_update": row["is_update"],
                "changes": {str(field): [str(old), str(new)] for field, (old, new) in row["planned"].changes.items()},
                "suggested_location_id": row["planned"].suggested_location.pk if row["planned"].suggested_location else None,
                "warning": str(row["warning"]) if row["warning"] else None,
            }
            for row in rows
        ],
        "unchanged_count": plan.unchanged_count,
        "location_choices": [{"id": location.pk, "name": str(location)} for location in plan.location_choices],
    }


@router.get("/manage/events/fixture-recheck", response={200: s.RecheckPreviewOut, **ERRORS}, summary="Re-check every stored fixture list")
def recheck_preview(request, season_id: uuid.UUID | None = None, team_id: uuid.UUID | None = None):
    """Fetches every stored fixture-list URL (`Team.import_sources`) of the season (default the current one),
    of every team or just `team_id`, skipping importers the club switched off, and diffs each against the
    calendar. Nothing is saved. 404 when no importer is on."""
    club = panel_club(request)
    if not any(import_sources.source_enabled(club, source) for source in import_sources.SOURCES):
        raise ApiError(404, "not_found", NOT_FOUND)
    _require_admin(request, club)
    season = get_object_or_404(Season.objects.filter(club=club), pk=season_id) if season_id else default_recheck_season(club)
    teams = Team.objects.filter(club=club).order_by("name")
    if team_id:
        get_object_or_404(teams, pk=team_id)
        teams = teams.filter(pk=team_id)
    sections = recheck_sections(club, season, teams)[0] if season else []
    out = [
        {
            "team_id": section["team"].pk,
            "team_name": section["team"].name,
            "source": section["entry"]["source"],
            "url": section["entry"].get("url", ""),
            "competition_label": section["entry"].get("competition_label", ""),
            "error": section["error"],
            "plan": _plan_out(section["plan"]) if section["plan"] else None,
        }
        for section in sections
    ]
    has_changes = any(section["plan"] and (section["plan"].to_create or section["plan"].to_update or section["plan"].to_delete) for section in sections)
    return {"season_id": season.pk if season else None, "team_id": team_id, "sections": out, "has_changes": has_changes}


@router.post("/manage/events/fixture-recheck/apply", response={202: JobOut, **ERRORS, **INVALID}, summary="Apply the re-check of every stored fixture list (job)")
def recheck_apply(request, payload: s.RecheckApplyIn):
    """Starts a job that fetches and applies every stored list again. `choices` carries the location / opponent
    picks per `(team_id, source, competition_label)` as in the preview. Result: `{created, updated, deleted, failed}`."""
    club = panel_club(request)
    if not any(import_sources.source_enabled(club, source) for source in import_sources.SOURCES):
        raise ApiError(404, "not_found", NOT_FOUND)
    _require_admin(request, club)
    season = get_object_or_404(Season.objects.filter(club=club), pk=payload.season_id) if payload.season_id else default_recheck_season(club)
    if season is None:
        raise ApiError(422, "validation_error", fields={"season_id": [str(_("The club has no season."))]})
    if payload.team_id:
        get_object_or_404(Team.objects.filter(club=club), pk=payload.team_id)
    choices = [choice.model_dump(mode="json") for choice in payload.choices]
    return idempotent(request, lambda: (202, start_job(request, api_recheck_fixtures, str(season.pk), str(payload.team_id) if payload.team_id else None, choices)))


@router.post("/manage/events/import/{source}/preview", response={200: s.ImportPreviewOut, **ERRORS, **INVALID}, summary="Preview a federation fixture import (RBIHF, DFEL or IJNL)")
def import_preview(request, source: str, payload: s.ImportPreviewIn):
    """Fetches the team page and diffs it against the club's calendar; nothing is saved. `source` is `rbihf`,
    `dfel` or `ijnl`. A URL that can't be read is 422 on `url`."""
    club, constant, form_class = _importer(request, _source(source))
    form = _import_form(club, form_class, payload)
    try:
        prepared = prepare_import(club, constant, team=form.cleaned_data["team"], url=form.cleaned_data["url"], competition_label=form.cleaned_data["competition_label"])
    except import_sources.ImportSourceError as error:
        raise ApiError(422, "validation_error", fields={"url": [str(error)]}) from error
    return {"plan": _plan_out(prepared.plan)}


@router.post("/manage/events/import/{source}/apply", response={202: JobOut, **ERRORS, **INVALID}, summary="Apply a federation fixture import (job)")
def import_apply(request, source: str, payload: s.ImportApplyIn):
    """Starts a job that fetches the team page again, applies it with the chosen `locations` / `opponents` and
    remembers the URL on the team for the season. Result: `{created, updated, deleted}`; a page that can't be
    read fails the job with `import_failed`."""
    club, constant, form_class = _importer(request, _source(source))
    form = _import_form(club, form_class, payload)
    team, season = form.cleaned_data["team"], form.cleaned_data["season"]
    args = (constant, str(team.pk), str(season.pk), form.cleaned_data["url"], form.cleaned_data["competition_label"], dict(payload.locations), dict(payload.opponents))
    return idempotent(request, lambda: (202, start_job(request, api_import_fixtures, *args)))


def _source(source: str) -> str:
    if source not in SOURCES:
        raise ApiError(404, "not_found", NOT_FOUND)
    return source
