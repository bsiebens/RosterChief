"""Player evaluations for coaches (API.md §7.3): checklists to render, a player's evaluations, and
filing a new one.

Not under ``/teams/{team_id}`` on purpose. The web grants evaluations club-wide
(``club.services.access.can_manage_evaluations``, the EvaluationManager grant; a technical director
may hold it without staffing any team) and lets the holder evaluate any club member, so a team in the
path would wrongly 404 them. The gate here is the web's: the club's ``evaluations`` module on
(`404 feature_disabled`) and the grant (`403 forbidden`). Evaluations are staff-only content and
never appear in any member endpoint. Validation is evaluations.services.submit_evaluation, the same
dynamic form the web renders.
"""

import json
import uuid

from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils.translation import gettext_lazy as _
from ninja import Router

from api.core.errors import ApiError
from api.core.idempotency import idempotent
from api.core.pagination import paginate
from api.core.scope import ClubScope, club_scope
from club.services.access import can_manage_evaluations, current_season
from evaluations.models import PlayerEvaluation
from evaluations.services import EvaluationRubricNotConfigured, EvaluationSubmissionError, active_checklists, submit_evaluation
from members.models import Member
from rosterchief.uploads import DOCUMENT_ACCEPT, MAX_DOCUMENT_UPLOAD_BYTES

from ..coach_schemas import ChecklistListOut, EvaluationDetailOut, EvaluationListOut
from ..schemas import ErrorOut
from .forms import field_out

router = Router(tags=["coach"])

ERRORS = {401: ErrorOut, 403: ErrorOut, 404: ErrorOut}

SUBMIT_BODY = {
    "requestBody": {
        "required": True,
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "required": ["checklist_slug", "answers"],
                    "properties": {
                        "checklist_slug": {"type": "string", "description": "From `GET /evaluations/checklists`. Always explicit: the web's pick-the-only-checklist shortcut is a screen convenience."},
                        "answers": {"type": "object", "description": "Field `key` → value, as for forms: string, number, `YYYY-MM-DD`, bool, or a list of option values for `multichoice`."},
                    },
                }
            },
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["checklist_slug"],
                    "properties": {"checklist_slug": {"type": "string"}},
                    "additionalProperties": True,
                    "description": "Only needed for checklists with `file` fields: one part per field `key`, files as file parts.",
                }
            },
        },
    }
}


def _gate(request) -> ClubScope:
    scope = club_scope(request)
    scope.require_feature("evaluations")
    if not can_manage_evaluations(request.user, scope.club):
        raise ApiError(403, "forbidden", _("You can't work with evaluations in this club."))
    return scope


def _player(scope: ClubScope, member_id) -> Member:
    """Anyone attached to the club at all, as on the web (club-wide grant)."""
    club = scope.club
    attached = Member.objects.filter(Q(member_of__club=club) | Q(team_memberships__team__club=club) | Q(staff_assignments__team__club=club) | Q(roles__club=club)).distinct()
    return get_object_or_404(attached, pk=member_id)


def _summary(evaluation) -> dict:
    evaluator = evaluation.submission.member
    return {
        "id": evaluation.pk,
        "checklist": {"slug": evaluation.checklist.slug, "name": evaluation.checklist.name},
        "season_id": evaluation.season_id,
        "evaluator": {"first_name": evaluator.first_name, "last_name": evaluator.last_name} if evaluator else None,
        "created": evaluation.created,
    }


def _detail(evaluation) -> dict:
    values = {answer.field_id: answer.value for answer in evaluation.submission.answers.all()}
    fields = evaluation.submission.send.form.fields.order_by("order")
    return {**_summary(evaluation), "answers": [{"key": field.key, "label": field.label, "type": field.field_type, "value": values.get(field.id)} for field in fields]}


def _evaluations():
    return PlayerEvaluation.objects.select_related("checklist", "submission__member", "submission__send__form").prefetch_related("submission__answers")


@router.get("/evaluations/checklists", response={200: ChecklistListOut, **ERRORS}, summary="The club's evaluation checklists")
def checklists(request):
    """Every active checklist that has questions, with the fields to render. Needs the club's
    `evaluations` module and the evaluation grant (`permissions.can_manage_evaluations`)."""
    scope = _gate(request)
    items = []
    for checklist in active_checklists(scope.club).exclude(form=None):
        fields = [field_out(field) for field in checklist.form.fields.filter(is_active=True).order_by("order")]
        items.append({"slug": checklist.slug, "name": checklist.name, "description": checklist.description, "fields": fields})
    return {"items": items, "file_accept": DOCUMENT_ACCEPT, "max_file_bytes": MAX_DOCUMENT_UPLOAD_BYTES}


@router.get("/players/{member_id}/evaluations", response={200: EvaluationListOut, **ERRORS}, summary="A player's evaluations")
def list_evaluations(request, member_id: uuid.UUID, cursor: str | None = None, limit: int | None = None):
    """Everybody's evaluations of this player, newest first (several coaches may each file one per
    season). Same gate as `GET /evaluations/checklists`; the player may be anyone in the club."""
    scope = _gate(request)
    player = _player(scope, member_id)
    page = paginate(_evaluations().filter(club=scope.club, player=player), sort_field="-created", cursor=cursor, limit=limit)
    return {"items": [_summary(evaluation) for evaluation in page.items], "next_cursor": page.next_cursor}


@router.get("/players/{member_id}/evaluations/{evaluation_id}", response={200: EvaluationDetailOut, **ERRORS}, summary="One evaluation with its answers")
def get_evaluation(request, member_id: uuid.UUID, evaluation_id: uuid.UUID):
    """Every question of the version it was scored against, answered or not."""
    scope = _gate(request)
    player = _player(scope, member_id)
    return _detail(get_object_or_404(_evaluations(), pk=evaluation_id, club=scope.club, player=player))


def _read_body(request):
    """``(checklist_slug, data, files)`` from a JSON or a multipart body."""
    if request.content_type.startswith("multipart/"):
        data = request.POST.copy()
        return data.pop("checklist_slug", [""])[0], data, request.FILES
    try:
        body = json.loads(request.body or b"{}")
    except ValueError:
        raise ApiError(400, "bad_request") from None
    answers = body.get("answers") if isinstance(body, dict) else None
    if not isinstance(answers, dict):
        raise ApiError(422, "validation_error", fields={"answers": [_("Send the answers as an object keyed by field.")]})
    return body.get("checklist_slug", ""), answers, None


@router.post("/players/{member_id}/evaluations", response={201: EvaluationDetailOut, **ERRORS, 409: ErrorOut, 422: ErrorOut}, summary="File an evaluation", openapi_extra=SUBMIT_BODY)
def create_evaluation(request, member_id: uuid.UUID):
    """Scores the player against one checklist; the evaluator is the signed-in account's member.
    `checklist_slug` is always required. `422` carries `fields` per question `key`; `404` for an
    unknown or inactive checklist; `409 checklist_not_configured` when it has no questions yet;
    `409 no_current_season` without a season covering today. Send an `Idempotency-Key`."""
    scope = _gate(request)
    player = _player(scope, member_id)
    slug, data, files = _read_body(request)
    checklist = get_object_or_404(active_checklists(scope.club).exclude(form=None), slug=slug)
    season = current_season(scope.club)
    if season is None:
        raise ApiError(409, "no_current_season", _("The club has no current season."))

    def work():
        try:
            evaluation = submit_evaluation(club=scope.club, checklist=checklist, player=player, season=season, evaluator=scope.me, data=data, files=files)
        except EvaluationRubricNotConfigured as error:
            raise ApiError(409, "checklist_not_configured", str(error)) from error
        except EvaluationSubmissionError as error:
            raise ApiError(422, "validation_error", str(error), fields={key: [str(message) for message in messages] for key, messages in error.errors.items()}) from error
        return 201, _detail(get_object_or_404(_evaluations(), pk=evaluation.pk))

    return idempotent(request, work)
