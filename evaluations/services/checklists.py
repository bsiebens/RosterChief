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
from django.utils import timezone

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
def submit_evaluation(actor, checklist: EvaluationChecklist, player: Member, season: Season | None, data: dict, public_note: str = "") -> PlayerEvaluation:
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
    return PlayerEvaluation.objects.create(checklist=checklist, player=player, season=season, submission=submission, public_note=(public_note or "").strip())


@transaction.atomic
def delete_evaluation(actor, evaluation: PlayerEvaluation) -> None:
    """The evaluator who gave it, or a checklist manager."""
    access.require_execute_evaluations(actor)
    given_by = evaluation.submission.submitted_by
    if not ((given_by is not None and given_by.user_id == actor.pk) or access.can_manage_evaluations(actor)):
        raise NotAllowed("You may only delete your own evaluations.")
    evaluation.submission.delete()  # cascades to the evaluation


def _set_release(actor, evaluation: PlayerEvaluation, release: str, public_note: str | None) -> bool:
    """Store the level and note on ``evaluation``; True when it just went from private to shared."""
    if release not in PlayerEvaluation.Release.values:
        raise Invalid("Unknown release level.", {"release": ["Choose nothing, notes or details."]})
    if public_note is not None:
        evaluation.public_note = public_note.strip()
    shared_now = bool(release) and not evaluation.release
    if release != evaluation.release:
        evaluation.release = release
        evaluation.released_at = timezone.now() if release else None
        evaluation.released_by = Member.objects.filter(user=actor).first() if release else None
    evaluation.save(update_fields=["public_note", "release", "released_at", "released_by", "modified"])
    return shared_now


@transaction.atomic
def release_evaluation(actor, evaluation: PlayerEvaluation, release: str, public_note: str | None = None) -> PlayerEvaluation:
    """Decide what the player and their family see of an evaluation: nothing, the public note only, or the note and every visible score.
    The evaluator who gave it or a checklist manager may do this; the public note can be rewritten at any time."""
    access.require_execute_evaluations(actor)
    given_by = evaluation.submission.submitted_by
    if not ((given_by is not None and given_by.user_id == actor.pk) or access.can_manage_evaluations(actor)):
        raise NotAllowed("You may only share your own evaluations.")
    if _set_release(actor, evaluation, release, public_note):
        tell_family(actor, evaluation)
    return evaluation


@transaction.atomic
def apply_release(actor, checklist: EvaluationChecklist, items: list[dict]) -> int:
    """Set the level and public note of several evaluations of ``checklist`` at once (the end of an evaluation meeting, where whoever
    wrote them no longer matters: the group has checked them). Each player and their family are told once. Returns how many are now shared."""
    access.require_execute_evaluations(actor)
    rows = {e.pk: e for e in evaluations_queryset().filter(checklist=checklist, pk__in=[i["evaluation_id"] for i in items])}
    told, shared = set(), 0
    for item in items:
        evaluation = rows.get(item["evaluation_id"])
        if evaluation is None:
            raise Invalid("Unknown evaluation.", {"items": ["Unknown evaluation."]})
        if _set_release(actor, evaluation, item.get("release", ""), item.get("public_note")) and evaluation.player_id not in told:
            told.add(evaluation.player_id)
            tell_family(actor, evaluation)
        shared += bool(evaluation.release)
    return shared


def tell_family(actor, evaluation: PlayerEvaluation) -> None:
    """Inbox entry plus push for the player and their guardians (the person who shared it is left out). Never blocks the release."""
    from events.services.attendance import current_club
    from members.services import recipients
    from notifications import inbox

    club = current_club()
    if club is None:
        return
    try:
        recipients.deliver(
            evaluation.player, club, title="A new evaluation is available", body=f"{evaluation.checklist.name} · {evaluation.season.name}",
            skip_user_id=getattr(actor, "pk", None), kind=inbox.Kind.INFO, href="/member/forms", push=True, category="form",
        )
    except Exception:
        import logging

        logging.getLogger(__name__).exception("evaluations.notify_failed")


def season_evaluations(checklist: EvaluationChecklist, season: Season | None = None) -> list[PlayerEvaluation]:
    """Every evaluation of ``checklist`` in ``season`` (the current one by default), by player."""
    season = season or Season.get_current()
    rows = evaluations_queryset().filter(checklist=checklist)
    if season is not None:
        rows = rows.filter(season=season)
    return sorted(rows, key=lambda e: (e.player.last_name.lower(), e.player.first_name.lower(), e.submission.submitted_at))


def unreleased(checklist: EvaluationChecklist, season: Season | None = None) -> list[PlayerEvaluation]:
    """The private evaluations of this season. Whoever wrote them: at the end of a meeting they have been checked together."""
    return [e for e in season_evaluations(checklist, season) if not e.release]


def player_evaluations(player: Member):
    """Every evaluation of ``player``, newest first."""
    return evaluations_queryset().filter(player=player).order_by("-submission__submitted_at")
