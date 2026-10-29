"""Management API: player evaluations (API.md §17, phase D).

The JSON counterpart of ``/manage/evaluations/*`` (management.views.Evaluation*View): the club's
checklists (list, create, archive / reactivate), a checklist's rubric (read, replace), per-question
statistics, the latest-results matrix, the walkthrough (browse a player's history, run a meeting,
record outcome decisions and discussion notes), one evaluation, and the meeting outcomes as a PDF.

Filing an evaluation and listing a player's evaluations are the coach endpoints
(``api/protected/routers/coach_evaluations.py``, ``/evaluations/checklists``,
``/players/{id}/evaluations``); this router shares their entry serializer.

Who may do what is the web's rule. The club needs the ``evaluations`` module (``404
feature_disabled``). Creating or archiving a checklist and replacing a rubric are club-admin only
(``FeatureRequiredMixin``); everything else needs ``can_manage_evaluations`` (admin or an
EvaluationManager grant), including reading the rubric.

A checklist is addressed by ``{checklist_ref}``: its ``slug`` (as in the web URLs) or its ``id``.
Logic lives in evaluations.services, which the web views call as well.
"""

import datetime
import uuid

from django.db.models import Count
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from ninja import Router

from api.core.errors import ApiError
from api.core.forms import form_error, post_data
from api.core.idempotency import idempotent
from api.core.schemas import ErrorOut
from api.panel.guards import panel_club, require
from api.panel.schemas import evaluations as s
from api.protected.routers.coach_evaluations import _detail, _evaluations
from api.protected.services import club_features
from club.services.access import can_manage_evaluations, current_season, is_club_admin
from evaluations.models import EvaluationChecklist, EvaluationSession
from evaluations.services import (
    active_session,
    add_evaluation_note,
    checklist_players,
    end_session,
    evaluation_totals,
    latest_session,
    player_evaluation_history,
    player_notes,
    question_stats,
    record_outcome,
    results_matrix,
    rubric_criteria,
    save_rubric,
    session_outcome_rows,
    set_checklist_active,
    start_session,
)
from events.services.attendance import member_attendance_counts, member_attendance_sparkline
from formbuilder.services.options import field_choices
from management.forms import EvaluationChecklistForm, RubricCriterionFormSet
from management.pdf import PDFExportError, evaluation_outcomes_pdf
from members.models import Member
from teams.models import TeamMembership

router = Router(tags=["panel: evaluations"])

PREFIX = "/manage/evaluations"
ERRORS = {401: ErrorOut, 403: ErrorOut, 404: ErrorOut}
INVALID = {422: ErrorOut}
CONFLICT = {409: ErrorOut}
FILE = {503: ErrorOut}


# --- helpers -----------------------------------------------------------------------------------


def _club(request, *, admin: bool = False):
    """The club on this host with the ``evaluations`` module on, for an evaluation manager
    (``admin=True``: a club admin, the web's rubric / checklist tier)."""
    club = panel_club(request)
    if not club_features(club)["evaluations"]:
        raise ApiError(404, "feature_disabled", _("This feature isn't available for this club."))
    require(request, club, is_club_admin if admin else can_manage_evaluations)
    return club


def _checklists(club):
    return EvaluationChecklist.objects.filter(club=club).annotate(evaluation_count=Count("evaluations"))


def _checklist(club, ref: str) -> EvaluationChecklist:
    """By slug (the web's key) or by id; archived ones stay reachable, as on the web."""
    queryset = _checklists(club)
    try:
        return get_object_or_404(queryset, pk=uuid.UUID(ref))
    except ValueError:
        return get_object_or_404(queryset, slug=ref)


def _checklist_out(checklist, evaluation_count=None) -> dict:
    return {
        "id": checklist.pk,
        "slug": checklist.slug,
        "name": checklist.name,
        "description": checklist.description,
        "is_active": checklist.is_active,
        "has_rubric": checklist.form_id is not None,
        "evaluation_count": getattr(checklist, "evaluation_count", None) if evaluation_count is None else evaluation_count,
        "created": checklist.created,
    }


def _fresh(club, checklist) -> dict:
    return _checklist_out(_checklist(club, str(checklist.pk)))


def _player_out(player: Member) -> dict:
    return {"id": player.pk, "first_name": player.first_name, "last_name": player.last_name}


def _author(request) -> Member | None:
    return Member.objects.filter(user=request.user).first()


def _criterion_out(field) -> dict:
    return {
        "id": field.pk,
        "key": field.key,
        "label": field.label,
        "field_type": field.field_type,
        "required": field.required,
        "help_text": field.help_text,
        "section": field.section or "",
        "options": [str(value) for value, _label in field_choices(field)],
        "order": field.order,
    }


def _rubric_out(club, checklist) -> dict:
    checklist = _checklist(club, str(checklist.pk))
    return {"checklist": _checklist_out(checklist), "version_id": checklist.form_id, "criteria": [_criterion_out(field) for field in rubric_criteria(checklist)]}


def _session_out(session: EvaluationSession | None) -> dict | None:
    if session is None:
        return None
    return {"id": session.pk, "checklist_id": session.checklist_id, "started_at": session.started_at, "ended_at": session.ended_at, "is_open": session.is_open}


def _covered_player(checklist, player_id) -> Member:
    """Only a player the checklist covers (the walkthrough's own population), never an arbitrary member."""
    player = next((candidate for candidate in checklist_players(checklist) if candidate.pk == player_id), None)
    if player is None:
        raise ApiError(404, "not_found")
    return player


# --- checklists --------------------------------------------------------------------------------


@router.get(PREFIX + "/checklists", response={200: s.ChecklistListOut, **ERRORS}, summary="Evaluation checklists")
def list_checklists(request):
    """Every checklist the club ever made, active and archived, with how many evaluations each holds. Evaluation managers."""
    club = _club(request)
    return {"items": [_checklist_out(checklist) for checklist in _checklists(club)]}


@router.post(PREFIX + "/checklists", response={201: s.ChecklistOut, **ERRORS, **INVALID}, summary="Create a checklist")
def create_checklist(request, payload: s.ChecklistIn):
    """Names a new checklist; the `slug` is made from the name and is unique per club. Its rubric starts empty: fill it
    with `PUT .../checklists/{checklist_ref}/rubric`. Club admins only."""
    club = _club(request, admin=True)

    def work():
        form = EvaluationChecklistForm(post_data(payload.dict(), checkboxes=("is_active",)), instance=EvaluationChecklist(club=club))
        if not form.is_valid():
            raise form_error(form)
        checklist = form.save()
        return 201, _fresh(club, checklist)

    return idempotent(request, work)


@router.get(PREFIX + "/checklists/{checklist_ref}", response={200: s.ChecklistOut, **ERRORS}, summary="One checklist")
def get_checklist(request, checklist_ref: str):
    """By slug or id. Evaluation managers."""
    club = _club(request)
    return _checklist_out(_checklist(club, checklist_ref))


def _set_active(request, checklist_ref: str, active: bool) -> dict:
    club = _club(request, admin=True)
    checklist = _checklist(club, checklist_ref)
    set_checklist_active(checklist, active)
    return _fresh(club, checklist)


@router.post(PREFIX + "/checklists/{checklist_ref}/archive", response={200: s.ChecklistOut, **ERRORS}, summary="Archive a checklist")
def archive_checklist(request, checklist_ref: str):
    """Stops it being offered for new evaluations; never a delete, filed evaluations keep working. Repeating it is harmless. Club admins only."""
    return _set_active(request, checklist_ref, False)


@router.post(PREFIX + "/checklists/{checklist_ref}/reactivate", response={200: s.ChecklistOut, **ERRORS}, summary="Reactivate an archived checklist")
def reactivate_checklist(request, checklist_ref: str):
    """The web's archive button is a toggle; the API has the two explicit states. Club admins only."""
    return _set_active(request, checklist_ref, True)


# --- rubric ------------------------------------------------------------------------------------


@router.get(PREFIX + "/checklists/{checklist_ref}/rubric", response={200: s.RubricOut, **ERRORS}, summary="The current rubric")
def get_rubric(request, checklist_ref: str):
    """The live criteria in order (empty until a rubric was saved). Evaluation managers."""
    club = _club(request)
    return _rubric_out(club, _checklist(club, checklist_ref))


def _formset_data(criteria: list[dict]) -> dict:
    data = {"criteria-TOTAL_FORMS": str(len(criteria)), "criteria-INITIAL_FORMS": "0", "criteria-MIN_NUM_FORMS": "0", "criteria-MAX_NUM_FORMS": "1000"}
    for index, values in enumerate(criteria):
        row = post_data({**values, "options": "\n".join(values.get("options") or []), "order": index + 1}, checkboxes=("required",))
        data.update({f"criteria-{index}-{key}": value for key, value in row.items()})
    return data


@router.put(PREFIX + "/checklists/{checklist_ref}/rubric", response={200: s.RubricOut, **ERRORS, **INVALID}, summary="Replace the rubric")
def put_rubric(request, checklist_ref: str, payload: s.RubricIn):
    """Saves the whole criteria list as a new rubric version (evaluations already filed keep the questions they were
    scored against). Criteria are matched across versions by `key`, so keep a label to keep its history. Validation is the
    web editor's: `422` with `fields` named `criteria.{index}.{field}` (a duplicate label, `choice` without options) or
    `criteria` (an empty rubric). Club admins only."""
    club = _club(request, admin=True)

    def work():
        checklist = _checklist(club, checklist_ref)
        formset = RubricCriterionFormSet(_formset_data([criterion.dict() for criterion in payload.criteria]), prefix="criteria")
        if not formset.is_valid():
            fields: dict[str, list[str]] = {}
            for index, errors in enumerate(formset.errors):
                for name, messages in errors.items():
                    fields[f"criteria.{index}.{name}"] = [str(message) for message in messages]
            if formset.non_form_errors():
                fields["criteria"] = [str(message) for message in formset.non_form_errors()]
            raise ApiError(422, "validation_error", fields=fields)
        rows = [{**row, "options": row.get("options") or []} for row in formset.cleaned_data if row.get("label")]
        save_rubric(checklist, rows)
        return 200, _rubric_out(club, checklist)

    return idempotent(request, work)


# --- statistics, matrix ------------------------------------------------------------------------


def _number(value):
    return None if value is None else float(value)


@router.get(PREFIX + "/checklists/{checklist_ref}/stats", response={200: s.StatsOut, **ERRORS}, summary="Per-question statistics")
def get_stats(request, checklist_ref: str, all_seasons: bool = False):
    """Count / average / min / max for numeric criteria, response tallies for choice ones, the latest free-text answers
    otherwise, across every rubric version. Limited to the current season unless `all_seasons=true` (the web's
    `?season=all`). Evaluation managers."""
    club = _club(request)
    checklist = _checklist(club, checklist_ref)
    season = None if all_seasons else current_season(club)
    rows = []
    for row in question_stats(checklist, season=season):
        field = row["field"]
        out = {"key": field.key, "label": field.label, "section": field.section or "", "field_type": field.field_type, "kind": row["kind"], "response_count": row["response_count"]}
        if row["kind"] == "numeric":
            out.update(average=_number(row["average"]), minimum=_number(row["minimum"]), maximum=_number(row["maximum"]))
        elif row["kind"] == "distribution":
            out["distribution"] = [{"value": value, "count": count} for value, count in row["distribution"]]
        else:
            out["recent_answers"] = row["recent_answers"]
        rows.append(out)
    return {"season_id": season.pk if season else None, "show_all_seasons": all_seasons, **evaluation_totals(checklist, season=season), "rows": rows}


@router.get(PREFIX + "/checklists/{checklist_ref}/matrix", response={200: s.MatrixOut, **ERRORS}, summary="Latest result per player and question")
def get_matrix(request, checklist_ref: str):
    """One row per player ever evaluated, one column per current criterion, each cell their latest answer. Evaluation managers."""
    club = _club(request)
    matrix = results_matrix(_checklist(club, checklist_ref))
    return {
        "fields": [{"key": field.key, "label": field.label, "section": field.section or "", "field_type": field.field_type} for field in matrix["fields"]],
        "rows": [{"player": _player_out(row["player"]), "evaluation_id": row["evaluation"].pk, "evaluated_at": row["evaluation"].submission.submitted_at, "values": row["values"]} for row in matrix["rows"]],
    }


# --- walkthrough -------------------------------------------------------------------------------


def _current_out(checklist, player, players, club, session) -> dict:
    index = players.index(player)
    season = current_season(club)
    history = player_evaluation_history(checklist, player)
    outcome = session.outcomes.filter(player=player).first() if session is not None else None
    counts = member_attendance_counts(player, season) if season else None
    questions = []
    for question in history["questions"]:
        field = question["field"]
        out = {
            "key": field.key,
            "label": field.label,
            "section": field.section or "",
            "kind": question["kind"],
            "entries": [
                {
                    "evaluation_id": entry["evaluation"].pk,
                    "date": entry["evaluation"].submission.submitted_at,
                    "evaluator": entry["evaluation"].submission.member.get_full_name() if entry["evaluation"].submission.member else None,
                    "value": entry["value"],
                }
                for entry in question["entries"]
            ],
        }
        if question["kind"] == "numeric":
            out["trend"] = question["trend"]
        questions.append(out)
    return {
        "player": _player_out(player),
        "previous_player_id": players[index - 1].pk if index > 0 else None,
        "next_player_id": players[index + 1].pk if index + 1 < len(players) else None,
        "season_id": season.pk if season else None,
        "memberships": [
            {"team_id": m.team_id, "team_name": m.team.name, "position": str(m.position) if m.position_id else None, "jersey_number": m.jersey_number}
            for m in TeamMembership.objects.filter(member=player, season=season).select_related("team", "position")
        ]
        if season
        else [],
        "attendance_bars": [{"event_id": bar["event"].pk, "start": bar["event"].start, "state": bar["state"]} for bar in member_attendance_sparkline(player, season)] if season else [],
        "attendance_counts": counts,
        "evaluations": [evaluation.pk for evaluation in history["evaluations"]],
        "questions": questions,
        "notes": [{"id": note.pk, "note": note.note, "author": note.author.get_full_name() if note.author else None, "created": note.created} for note in player_notes(checklist, player)],
        "outcome": {"player": _player_out(player), "decision": outcome.decision, "modified": outcome.modified} if outcome else None,
    }


@router.get(PREFIX + "/checklists/{checklist_ref}/walkthrough", response={200: s.WalkthroughOut, **ERRORS}, summary="Walk through the evaluated players")
def get_walkthrough(request, checklist_ref: str, player_id: uuid.UUID | None = None, since: str | None = None):
    """Everyone ever evaluated on this checklist, and one of them in depth: current teams, this season's attendance,
    their answers per criterion with a trend for numeric ones, the discussion notes and, while a meeting is open, the
    recorded outcome. `player_id` picks the player (default the first; an unknown id falls back to the first, as on
    the web); `since=YYYY-MM-DD` keeps only players evaluated on or after that day (an unparsable date is ignored, as
    on the web). Read-only; the actions are the sibling POST / PUT routes. Evaluation managers."""
    club = _club(request)
    checklist = _checklist(club, checklist_ref)
    try:
        since_date = datetime.date.fromisoformat(since) if since else None
    except ValueError:
        since_date = None
    players = checklist_players(checklist, since=since_date)
    player = next((candidate for candidate in players if candidate.pk == player_id), None) if player_id else None
    player = player or (players[0] if players else None)
    session = active_session(checklist)
    return {
        "players": [_player_out(candidate) for candidate in players],
        "since": since_date,
        "session": _session_out(session),
        "latest_session": _session_out(session or latest_session(checklist)),
        "current": _current_out(checklist, player, players, club, session) if player else None,
    }


@router.post(PREFIX + "/checklists/{checklist_ref}/sessions", response={201: s.SessionOut, **ERRORS}, summary="Start a meeting")
def start_meeting(request, checklist_ref: str):
    """Opens the checklist's evaluation meeting, or returns the one already open (at most one runs at a time). Evaluation managers."""
    club = _club(request)

    def work():
        checklist = _checklist(club, checklist_ref)
        return 201, _session_out(start_session(club=club, checklist=checklist, started_by=_author(request)))

    return idempotent(request, work)


@router.post(PREFIX + "/checklists/{checklist_ref}/sessions/end", response={200: s.SessionOut, **ERRORS, **CONFLICT}, summary="End the open meeting")
def end_meeting(request, checklist_ref: str):
    """Closes the open meeting so its outcomes PDF can be downloaded. `409 no_open_session` when none is running. Evaluation managers."""
    club = _club(request)
    session = active_session(_checklist(club, checklist_ref))
    if session is None:
        raise ApiError(409, "no_open_session", _("No meeting is in progress."))
    return _session_out(end_session(session))


@router.put(PREFIX + "/checklists/{checklist_ref}/players/{uuid:player_id}/outcome", response={200: s.OutcomeOut, **ERRORS, **CONFLICT, **INVALID}, summary="Record a player's outcome decision")
def put_outcome(request, checklist_ref: str, player_id: uuid.UUID, payload: s.OutcomeIn):
    """Saves (or replaces) the open meeting's decision on this player; the player must be one the checklist covers
    (`404` otherwise). `409 no_open_session` without a running meeting; `422` on a blank `decision`. Evaluation managers."""
    club = _club(request)
    checklist = _checklist(club, checklist_ref)
    player = _covered_player(checklist, player_id)
    session = active_session(checklist)
    if session is None:
        raise ApiError(409, "no_open_session", _("Start a meeting before recording an outcome decision."))
    decision = payload.decision.strip()
    if not decision:
        raise ApiError(422, "validation_error", fields={"decision": [_("Write something before saving an outcome decision.")]})
    outcome = record_outcome(club=club, session=session, player=player, decision=decision, recorded_by=_author(request))
    return {"player": _player_out(player), "decision": outcome.decision, "modified": outcome.modified}


@router.post(PREFIX + "/checklists/{checklist_ref}/players/{uuid:player_id}/notes", response={201: s.NoteOut, **ERRORS, **INVALID}, summary="Add a discussion note")
def add_note(request, checklist_ref: str, player_id: uuid.UUID, payload: s.NoteIn):
    """Adds to the player's running discussion log on this checklist (private to evaluation managers; never in the PDF).
    `422` on a blank note. Send an `Idempotency-Key`. Evaluation managers."""
    club = _club(request)

    def work():
        checklist = _checklist(club, checklist_ref)
        player = _covered_player(checklist, player_id)
        text = payload.note.strip()
        if not text:
            raise ApiError(422, "validation_error", fields={"note": [_("Write something before adding a note.")]})
        note = add_evaluation_note(club=club, checklist=checklist, player=player, author=_author(request), note=text)
        return 201, {"id": note.pk, "note": note.note, "author": note.author.get_full_name() if note.author else None, "created": note.created}

    return idempotent(request, work)


# --- one evaluation, outcomes PDF --------------------------------------------------------------


@router.get(PREFIX + "/entries/{uuid:entry_id}", response={200: s.EntryOut, **ERRORS}, summary="One evaluation with its answers")
def get_entry(request, entry_id: uuid.UUID):
    """Any evaluator's evaluation of any player of the club, with every question of the version it was scored against.
    Evaluation managers."""
    club = _club(request)
    evaluation = get_object_or_404(_evaluations().select_related("player"), pk=entry_id, club=club)
    return {**_detail(evaluation), "player": _player_out(evaluation.player)}


@router.get(PREFIX + "/sessions/{uuid:session_id}/outcomes.pdf", response={200: None, **ERRORS, **FILE}, summary="A meeting's outcomes as a PDF download")
def session_outcomes_pdf(request, session_id: uuid.UUID):
    """`application/pdf` (attachment): name and decision per player, no scores and no discussion notes. 503
    `pdf_unavailable` when the server lacks the PDF libraries. Evaluation managers."""
    club = _club(request)
    session = get_object_or_404(EvaluationSession.objects.filter(club=club).select_related("checklist"), pk=session_id)
    context = {"club": club, "session": session, "checklist": session.checklist, "rows": session_outcome_rows(session), "generated_at": timezone.now()}
    try:
        pdf = evaluation_outcomes_pdf(context)
    except PDFExportError as error:
        raise ApiError(503, "pdf_unavailable", str(error)) from None
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="outcomes-{session.checklist.slug}-{timezone.localtime(session.started_at):%Y-%m-%d}.pdf"'
    return response
