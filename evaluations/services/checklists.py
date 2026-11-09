"""Checklists (the rubrics) and the evaluations filled in against them. Every write starts with a ``require_*`` check;
broken rules raise ``Invalid`` (422), refused deletes raise ``Conflict`` (409).

A checklist's criteria are the fields of its ``formbuilder.Form``, edited with ``formbuilder.services.management.apply_fields``
(an answered criterion can't change type, and one that is removed is kept deactivated so past evaluations still show it).
Filling one in stores a formbuilder ``Submission`` (``member`` = the player, ``submitted_by`` = the evaluator) behind a shadow,
never-active ``FormSend`` -- ``Submission.send`` is mandatory, and an inactive send with no audience can never reach anyone's
"forms to complete" list. ``formbuilder.services.submission.submit_form`` is deliberately not used: it enforces a send's
audience and response window, which don't apply here. Its ``validate_answers`` is, for real per-type validation.
"""

from django.db import transaction
from django.db.models import Count, Q

from evaluations.models import EvaluationChecklist, PlayerEvaluation
from events.models import Season
from formbuilder.models import Answer, Field, Form, FormSend, Submission
from formbuilder.services.management import Conflict, apply_fields
from formbuilder.services.submission import validate_answers
from members.models import Member
from members.services import access
from members.services.errors import Invalid, NotAllowed

CHECKLIST_FIELDS = ("name", "description", "is_active")


# --- checklists ----------------------------------------------------------------------------------------------------


def checklists_queryset():
    """Checklists with ``criteria_count`` (active criteria) and ``evaluation_count`` annotated."""
    return EvaluationChecklist.objects.select_related("form").annotate(
        criteria_count=Count("form__fields", filter=Q(form__fields__is_active=True), distinct=True),
        evaluation_count=Count("evaluations", distinct=True),
    )


def get_checklist(checklist_id) -> EvaluationChecklist | None:
    return checklists_queryset().filter(pk=checklist_id).first()


def _check(values: dict) -> None:
    if "name" in values and not (values["name"] or "").strip():
        raise Invalid("Check the checklist.", {"name": ["This field is required."]})


@transaction.atomic
def create_checklist(actor, **fields) -> EvaluationChecklist:
    access.require_manage_evaluations(actor)
    values = {k: fields[k] for k in CHECKLIST_FIELDS if fields.get(k) is not None}
    values.setdefault("name", "")
    _check(values)
    values["name"] = values["name"].strip()
    form = Form.objects.create(title=values["name"])
    return EvaluationChecklist.objects.create(form=form, **values)


@transaction.atomic
def update_checklist(actor, checklist: EvaluationChecklist, **fields) -> EvaluationChecklist:
    access.require_manage_evaluations(actor)
    values = {k: fields[k] for k in CHECKLIST_FIELDS if fields.get(k) is not None}
    _check(values)
    for name, value in values.items():
        setattr(checklist, name, value.strip() if name == "name" else value)
    if values:
        checklist.save(update_fields=[*values, "modified"])
        if "name" in values and checklist.form.title != checklist.name:
            checklist.form.title = checklist.name
            checklist.form.save(update_fields=["title", "modified"])
    return checklist


@transaction.atomic
def set_criteria(actor, checklist: EvaluationChecklist, items: list[dict]) -> EvaluationChecklist:
    """Replace the whole ordered list of criteria. File uploads make no sense in a rubric."""
    access.require_manage_evaluations(actor)
    errors = {f"fields.{i}.field_type": ["A checklist can't have file uploads."] for i, item in enumerate(items) if item.get("field_type") == Field.FieldType.FILE}
    if errors:
        raise Invalid("Check the criteria.", errors)
    apply_fields(checklist.form, items)
    return checklist


@transaction.atomic
def delete_checklist(actor, checklist: EvaluationChecklist) -> None:
    access.require_manage_evaluations(actor)
    if checklist.evaluations.exists() or checklist.notes.exists() or checklist.sessions.exists():
        raise Conflict("This checklist has evaluations, notes or meetings and cannot be deleted. Deactivate it instead.", "has_evaluations")
    form = checklist.form
    checklist.delete()
    form.sends.all().delete()
    form.delete()


def active_criteria(checklist: EvaluationChecklist) -> list[Field]:
    return list(checklist.form.fields.filter(is_active=True).order_by("order"))


def history_criteria(checklist: EvaluationChecklist) -> list[Field]:
    """Active criteria, plus removed (deactivated) ones that still have answers, so past evaluations show everything scored."""
    return list(checklist.form.fields.filter(Q(is_active=True) | Q(answers__isnull=False)).distinct().order_by("order"))


# --- evaluations ---------------------------------------------------------------------------------------------------


def evaluations_queryset():
    return PlayerEvaluation.objects.select_related("player", "season", "checklist", "submission__submitted_by").prefetch_related("submission__answers__field")


def _shadow_send(form: Form) -> FormSend:
    return form.sends.first() or FormSend.objects.create(form=form, is_active=False)


@transaction.atomic
def submit_evaluation(actor, checklist: EvaluationChecklist, player: Member, season: Season | None, data: dict) -> PlayerEvaluation:
    """Validate ``data`` ({criterion key: value}) against the checklist and store the evaluation. Needs
    ``evaluations.execute_evaluations``."""
    access.require_execute_evaluations(actor)
    if not checklist.is_active:
        raise Invalid("This checklist is not active.", {"checklist_id": ["This checklist is not active."]})
    if season is None:
        season = Season.get_current()
    if season is None:
        raise Invalid("There is no current season.", {"season_id": ["Choose a season."]})
    if not active_criteria(checklist):
        raise Invalid("This checklist has no criteria yet.", {"checklist_id": ["This checklist has no criteria yet."]})
    cleaned = validate_answers(checklist.form, data)  # raises FormSubmissionError (an Invalid) with per-criterion errors
    if not cleaned:
        raise Invalid("Score at least one criterion.", {"__all__": ["Score at least one criterion."]})
    submission = Submission.objects.create(send=_shadow_send(checklist.form), member=player, submitted_by=Member.objects.filter(user=actor).first())
    Answer.objects.bulk_create([Answer(submission=submission, field=field, value=value) for field, value in cleaned])
    return PlayerEvaluation.objects.create(checklist=checklist, player=player, season=season, submission=submission)


@transaction.atomic
def delete_evaluation(actor, evaluation: PlayerEvaluation) -> None:
    """The evaluator who gave it, or a checklist manager."""
    access.require_execute_evaluations(actor)
    given_by = evaluation.submission.submitted_by
    if not ((given_by is not None and given_by.user_id == actor.pk) or access.can_manage_evaluations(actor)):
        raise NotAllowed("You may only delete your own evaluations.")
    evaluation.submission.delete()  # cascades to the evaluation


def player_evaluations(player: Member):
    """Every evaluation of ``player``, newest first."""
    return evaluations_queryset().filter(player=player).order_by("-submission__submitted_at")
