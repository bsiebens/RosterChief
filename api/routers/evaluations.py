"""Evaluations (``/evaluations``). Two permissions: ``evaluations.manage_evaluations`` builds checklists (the criteria), and
``evaluations.execute_evaluations`` gives evaluations, runs evaluation meetings and is the only way to read evaluations back
(403 otherwise). Reading a checklist's definition needs either. Rules live in ``evaluations.services``."""

import datetime
import uuid

from django.http import HttpResponse
from django_tenants.utils import get_public_schema_name
from ninja import Router
from ninja.errors import HttpError

from api.auth import AUTHENTICATORS
from api.schemas_evaluations import (
    ChecklistDetail,
    ChecklistIn,
    ChecklistListOut,
    ChecklistPatch,
    CriteriaIn,
    EvaluateIn,
    EvaluationOut,
    MatrixOut,
    MeetingOut,
    NoteIn,
    NoteOut,
    OutcomeIn,
    OutcomeOut,
    PlayerEvaluationsOut,
    PlayerListOut,
    ReleaseIn,
    ReleaseItemsIn,
    ReleasePreviewOut,
    ReviewOut,
    SeasonOut,
    StatsOut,
)
from evaluations.models import EvaluationChecklist, EvaluationNote, EvaluationSession, PlayerEvaluation
from evaluations.services import checklists as svc
from evaluations.services import insights, meetings
from events.models import Season
from members.models import Member
from members.services import access
from members.services.errors import Invalid, NotAllowed
from shop.services.invoices import PDFUnavailable

router = Router(auth=AUTHENTICATORS)


# --- gates and lookups -----------------------------------------------------------------------------------------------


def _club_only(request):
    """404 on the platform host and for a club whose plan doesn't include the ``evaluations`` feature (after the permission check, 403 first)."""
    if request.tenant.schema_name == get_public_schema_name() or not request.features.has("evaluations"):
        raise HttpError(404, "Not found.")


def _manage(request):
    access.require_manage_evaluations(request.user)
    _club_only(request)


def _execute(request):
    access.require_execute_evaluations(request.user)
    _club_only(request)


def _either(request):
    if not (access.can_manage_evaluations(request.user) or access.can_execute_evaluations(request.user)):
        raise NotAllowed("You are not allowed to see evaluations.")
    _club_only(request)


def _checklist(checklist_id) -> EvaluationChecklist:
    checklist = svc.get_checklist(checklist_id)
    if checklist is None:
        raise HttpError(404, "Not found.")
    return checklist


def _player(request, player_id) -> Member:
    """The member, 404 unless the caller may see them (guardian-only people included, as on the member page)."""
    member = Member.objects.filter(pk=player_id).first()
    if member is None or not access.can_view_member(request.user, member):
        raise HttpError(404, "Not found.")
    return member


def _season(season_id) -> Season | None:
    if season_id is None:
        return None
    season = Season.objects.filter(pk=season_id).first()
    if season is None:
        raise Invalid("Unknown season.", {"season_id": ["Unknown season."]})
    return season


def _session(session_id) -> EvaluationSession:
    session = EvaluationSession.objects.select_related("checklist", "started_by").filter(pk=session_id).first()
    if session is None:
        raise HttpError(404, "Not found.")
    return session


# --- serializers -----------------------------------------------------------------------------------------------------


def _ref(member) -> dict | None:
    return {"id": member.pk, "name": str(member)} if member is not None else None


def _named(obj) -> dict:
    return {"id": obj.pk, "name": obj.name}


def _field(f) -> dict:
    return {"id": f.pk, "key": f.key, "label": f.label, "field_type": f.field_type, "required": f.required, "help_text": f.help_text, "section": f.section, "order": f.order, "is_active": f.is_active, "options": f.options,
        "min_value": f.min_value, "max_value": f.max_value, "visible_to_family": f.visible_to_family}


def _checklist_item(c) -> dict:
    return {"id": c.pk, "name": c.name, "slug": c.slug, "description": c.description, "is_active": c.is_active, "criteria_count": c.criteria_count, "evaluation_count": c.evaluation_count, "created": c.created}


def _checklist_detail(checklist_id) -> dict:
    c = _checklist(checklist_id)
    return {**_checklist_item(c), "fields": [_field(f) for f in c.form.fields.order_by("order")]}


def _evaluation(e) -> dict:
    answers = sorted(e.submission.answers.all(), key=lambda a: a.field.order)
    return {
        "id": e.pk, "checklist": _named(e.checklist), "season": _named(e.season), "player": _ref(e.player), "evaluator": _ref(e.submission.submitted_by), "submitted_at": e.submission.submitted_at,
        "public_note": e.public_note, "release": e.release, "released_at": e.released_at,
        "answers": [
            {"field_id": a.field_id, "key": a.field.key, "label": a.field.label, "field_type": a.field.field_type, "section": a.field.section, "order": a.field.order, "is_active": a.field.is_active, "options": a.field.options, "value": a.value}
            for a in answers
        ],
    }  # fmt: skip


def _session_out(s) -> dict | None:
    return {"id": s.pk, "started_at": s.started_at, "ended_at": s.ended_at, "is_open": s.is_open, "started_by": _ref(s.started_by)} if s is not None else None


def _note(n) -> dict:
    return {"id": n.pk, "note": n.note, "author": _ref(n.author), "created": n.created}


# --- checklists ------------------------------------------------------------------------------------------------------


@router.get("/checklists", response=ChecklistListOut, summary="List checklists", operation_id="list_evaluation_checklists")
def list_checklists(request, active: bool | None = None):
    _either(request)
    qs = svc.checklists_queryset()
    if active is not None:
        qs = qs.filter(is_active=active)
    return {"count": qs.count(), "results": [_checklist_item(c) for c in qs]}


@router.post("/checklists", response={201: ChecklistDetail}, summary="Create a checklist", operation_id="create_evaluation_checklist")
def create_checklist(request, payload: ChecklistIn):
    _manage(request)
    return 201, _checklist_detail(svc.create_checklist(request.user, **payload.dict()).pk)


@router.get("/checklists/{checklist_id}", response=ChecklistDetail, summary="One checklist with its criteria", operation_id="get_evaluation_checklist")
def get_checklist(request, checklist_id: uuid.UUID):
    _either(request)
    return _checklist_detail(_checklist(checklist_id).pk)


@router.patch("/checklists/{checklist_id}", response=ChecklistDetail, summary="Edit a checklist", operation_id="update_evaluation_checklist")
def update_checklist(request, checklist_id: uuid.UUID, payload: ChecklistPatch):
    _manage(request)
    checklist = svc.update_checklist(request.user, _checklist(checklist_id), **payload.dict(exclude_unset=True))
    return _checklist_detail(checklist.pk)


@router.put("/checklists/{checklist_id}/criteria", response=ChecklistDetail, summary="Replace the whole ordered list of criteria", operation_id="set_evaluation_criteria")
def set_criteria(request, checklist_id: uuid.UUID, payload: CriteriaIn):
    _manage(request)
    checklist = _checklist(checklist_id)
    svc.set_criteria(request.user, checklist, [f.dict() for f in payload.fields])
    return _checklist_detail(checklist.pk)


@router.delete("/checklists/{checklist_id}", response={204: None}, summary="Delete a checklist (409 has_evaluations when used)", operation_id="delete_evaluation_checklist")
def delete_checklist(request, checklist_id: uuid.UUID):
    _manage(request)
    svc.delete_checklist(request.user, _checklist(checklist_id))
    return 204, None


@router.get("/seasons", response=list[SeasonOut], summary="The club's seasons, newest first (to pick one to evaluate in)", operation_id="evaluation_seasons")
def seasons(request):
    _execute(request)
    current = Season.get_current()
    return [{"id": s.pk, "name": s.name, "start_date": s.start_date, "end_date": s.end_date, "is_current": current is not None and s.pk == current.pk} for s in Season.objects.order_by("-start_date")]


# --- giving and reading evaluations ----------------------------------------------------------------------------------


@router.post("/checklists/{checklist_id}/evaluations", response={201: EvaluationOut}, summary="Evaluate a player", operation_id="create_player_evaluation")
def evaluate(request, checklist_id: uuid.UUID, payload: EvaluateIn):
    """``answers`` is ``{criterion key: value}``. The season defaults to the current one. 422 with per-criterion errors."""
    _execute(request)
    checklist = _checklist(checklist_id)
    player = _player(request, payload.player_id)
    evaluation = svc.submit_evaluation(request.user, checklist, player, _season(payload.season_id), payload.answers, payload.public_note)
    return 201, _evaluation(svc.evaluations_queryset().get(pk=evaluation.pk))


@router.get("/players/{player_id}", response=PlayerEvaluationsOut, summary="Every evaluation of a player (the member page)", operation_id="player_evaluations")
def player_evaluations(request, player_id: uuid.UUID):
    _execute(request)
    rows = list(svc.player_evaluations(_player(request, player_id)))
    return {"count": len(rows), "results": [_evaluation(e) for e in rows]}


@router.put("/{evaluation_id}/release", response=EvaluationOut, summary="Share an evaluation with the player: not at all, the public note only, or the note and every score", operation_id="release_player_evaluation")
def release(request, evaluation_id: uuid.UUID, payload: ReleaseIn):
    _execute(request)
    evaluation = svc.evaluations_queryset().filter(pk=evaluation_id).first()
    if evaluation is None:
        raise HttpError(404, "Not found.")
    svc.release_evaluation(request.user, evaluation, payload.release, payload.public_note)
    return _evaluation(svc.evaluations_queryset().get(pk=evaluation_id))


@router.get("/checklists/{checklist_id}/release", summary="How many private evaluations of this season are waiting to be shared", operation_id="evaluation_release_count")
def release_count(request, checklist_id: uuid.UUID, season_id: uuid.UUID | None = None):
    _execute(request)
    checklist, season = _checklist(checklist_id), _season(season_id)
    return {"count": len(svc.unreleased(checklist, season)), "total": len(svc.season_evaluations(checklist, season))}


@router.get("/checklists/{checklist_id}/release/preview", response=ReleasePreviewOut, summary="Every evaluation of the season with what the family would see", operation_id="evaluation_release_preview")
def release_preview(request, checklist_id: uuid.UUID, season_id: uuid.UUID | None = None):
    """``visible`` marks the answers a full release would show (the criterion is ticked "visible to the family")."""
    _execute(request)
    checklist, season = _checklist(checklist_id), _season(season_id) or Season.get_current()
    rows = []
    for e in svc.season_evaluations(checklist, season):
        answers = sorted(e.submission.answers.all(), key=lambda a: a.field.order)
        rows.append({
            "id": e.pk, "player": _ref(e.player), "evaluator": _ref(e.submission.submitted_by), "submitted_at": e.submission.submitted_at, "release": e.release, "public_note": e.public_note,
            "answers": [{"label": a.field.label, "field_type": a.field.field_type, "options": a.field.options, "value": a.value, "visible": a.field.visible_to_family} for a in answers],
        })  # fmt: skip
    return {"checklist": _named(checklist), "season": _named(season) if season else None, "results": rows}


@router.put("/checklists/{checklist_id}/release", summary="Set what each listed evaluation shares (nothing, notes or details) and its public note; families are told", operation_id="release_checklist_evaluations")
def release_checklist(request, checklist_id: uuid.UUID, payload: ReleaseItemsIn):
    _execute(request)
    return {"count": svc.apply_release(request.user, _checklist(checklist_id), [i.dict() for i in payload.items])}


@router.delete("/{evaluation_id}", response={204: None}, summary="Delete an evaluation (its evaluator, or a checklist manager)", operation_id="delete_player_evaluation")
def delete_evaluation(request, evaluation_id: uuid.UUID):
    _execute(request)
    evaluation = svc.evaluations_queryset().filter(pk=evaluation_id).first()
    if evaluation is None:
        raise HttpError(404, "Not found.")
    svc.delete_evaluation(request.user, evaluation)
    return 204, None


# --- results ---------------------------------------------------------------------------------------------------------


@router.get("/checklists/{checklist_id}/stats", response=StatsOut, summary="Per-criterion statistics", operation_id="evaluation_stats")
def stats(request, checklist_id: uuid.UUID, season_id: uuid.UUID | None = None):
    """One season, or every season when ``season_id`` is left out."""
    _execute(request)
    checklist, season = _checklist(checklist_id), _season(season_id)
    count = PlayerEvaluation.objects.filter(checklist=checklist, **({"season": season} if season else {})).count()
    rows = [{**row, "field": _field(row["field"])} for row in insights.question_stats(checklist, season=season)]
    return {"season": _named(season) if season else None, "evaluation_count": count, "rows": rows}


@router.get("/checklists/{checklist_id}/matrix", response=MatrixOut, summary="Each player's latest answer per criterion", operation_id="evaluation_matrix")
def matrix(request, checklist_id: uuid.UUID, season_id: uuid.UUID | None = None):
    _execute(request)
    checklist, season = _checklist(checklist_id), _season(season_id)
    data = insights.results_matrix(checklist, season=season)
    rows = [{"player": _ref(r["player"]), "evaluation_id": r["evaluation"].pk, "evaluated_at": r["evaluation"].submission.submitted_at, "values": r["values"]} for r in data["rows"]]
    return {"season": _named(season) if season else None, "fields": [_field(f) for f in data["fields"]], "rows": rows}


# --- the player review and the meeting -------------------------------------------------------------------------------


@router.get("/checklists/{checklist_id}/players", response=PlayerListOut, summary="Players evaluated on a checklist", operation_id="evaluation_players")
def players(request, checklist_id: uuid.UUID, since: datetime.date | None = None):
    """Alphabetical. ``since`` keeps players with an evaluation entered on or after that date."""
    _execute(request)
    rows = insights.checklist_players(_checklist(checklist_id), since=since)
    return {"count": len(rows), "results": [_ref(m) for m in rows if access.can_view_member(request.user, m)]}


@router.get("/checklists/{checklist_id}/players/{player_id}", response=ReviewOut, summary="One player's review card", operation_id="evaluation_review")
def review(request, checklist_id: uuid.UUID, player_id: uuid.UUID):
    """Their evaluations on this checklist, every answer per criterion with a trend for numbers, the notes, teams and this season's attendance."""
    _execute(request)
    checklist, player = _checklist(checklist_id), _player(request, player_id)
    data = insights.player_review(checklist, player)
    by_id = {e.pk: e for e in data["evaluations"]}
    questions = [
        {
            "field": _field(q["field"]), "trend": q["trend"],
            "entries": [
                {"evaluation_id": x["evaluation"].pk, "evaluator": _ref(x["evaluation"].submission.submitted_by), "at": x["evaluation"].submission.submitted_at, "season": _named(by_id[x["evaluation"].pk].season), "value": x["value"]}
                for x in q["entries"]
            ],
        }
        for q in data["questions"]
    ]  # fmt: skip
    attendance = data["attendance"]
    season = attendance["season"]
    full = {e.pk: e for e in svc.evaluations_queryset().filter(pk__in=by_id)}
    return {
        "player": _ref(player), "evaluations": [_evaluation(full[e.pk]) for e in data["evaluations"]], "questions": questions, "notes": [_note(n) for n in data["notes"]],
        "teams": [_named(t) for t in data["teams"]], "attendance": {**attendance, "season": _named(season) if season else None},
    }  # fmt: skip


@router.post("/checklists/{checklist_id}/players/{player_id}/notes", response={201: NoteOut}, summary="Leave a discussion note on a player", operation_id="add_evaluation_note")
def add_note(request, checklist_id: uuid.UUID, player_id: uuid.UUID, payload: NoteIn):
    _execute(request)
    note = meetings.add_note(request.user, _checklist(checklist_id), _player(request, player_id), payload.note)
    return 201, _note(EvaluationNote.objects.select_related("author").get(pk=note.pk))


@router.delete("/notes/{note_id}", response={204: None}, summary="Delete a note (its author, or a checklist manager)", operation_id="delete_evaluation_note")
def delete_note(request, note_id: uuid.UUID):
    _execute(request)
    note = EvaluationNote.objects.select_related("author").filter(pk=note_id).first()
    if note is None:
        raise HttpError(404, "Not found.")
    meetings.delete_note(request.user, note)
    return 204, None


def _meeting(checklist) -> dict:
    active, latest = meetings.active_session(checklist), meetings.latest_session(checklist)
    outcomes = [{"player": _ref(o.player), "decision": o.decision, "recorded_by": _ref(o.recorded_by), "modified": o.modified} for o in meetings.session_outcomes(latest)] if latest else []
    return {"active": _session_out(active), "latest": _session_out(latest), "outcomes": outcomes}


@router.get("/checklists/{checklist_id}/meeting", response=MeetingOut, summary="The open evaluation meeting, the latest one and its decisions", operation_id="evaluation_meeting")
def get_meeting(request, checklist_id: uuid.UUID):
    _execute(request)
    return _meeting(_checklist(checklist_id))


@router.post("/checklists/{checklist_id}/meeting", response={201: MeetingOut}, summary="Start a meeting (the open one when there already is one)", operation_id="start_evaluation_meeting")
def start_meeting(request, checklist_id: uuid.UUID):
    _execute(request)
    checklist = _checklist(checklist_id)
    meetings.start_session(request.user, checklist)
    return 201, _meeting(checklist)


@router.post("/sessions/{session_id}/end", response=MeetingOut, summary="End a meeting", operation_id="end_evaluation_meeting")
def end_meeting(request, session_id: uuid.UUID):
    _execute(request)
    session = _session(session_id)
    meetings.end_session(request.user, session)
    return _meeting(session.checklist)


@router.put("/sessions/{session_id}/outcomes/{player_id}", response=OutcomeOut | None, summary="Record the decision on a player (blank removes it)", operation_id="record_evaluation_outcome")
def record_outcome(request, session_id: uuid.UUID, player_id: uuid.UUID, payload: OutcomeIn):
    _execute(request)
    outcome = meetings.record_outcome(request.user, _session(session_id), _player(request, player_id), payload.decision)
    if outcome is None:
        return None
    return {"player": _ref(outcome.player), "decision": outcome.decision, "recorded_by": _ref(outcome.recorded_by), "modified": outcome.modified}


@router.get("/sessions/{session_id}/outcomes.pdf", summary="The decisions of a meeting as a PDF", operation_id="evaluation_outcomes_pdf")
def outcomes_pdf(request, session_id: uuid.UUID):
    _execute(request)
    session = _session(session_id)
    try:
        pdf = meetings.render_outcomes_pdf(session)
    except PDFUnavailable:
        raise HttpError(503, "PDF rendering is not available on this server.") from None
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{session.checklist.slug}-outcomes-{session.started_at:%Y-%m-%d}.pdf"'
    return response
