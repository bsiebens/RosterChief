"""Reading evaluations back: per-question statistics, the results matrix and the player review. Plain data (dicts / lists), no
HTTP. The caller has already checked ``evaluations.execute_evaluations``."""

import datetime
from decimal import Decimal, InvalidOperation

from django.utils import timezone

from evaluations.models import EvaluationChecklist, EvaluationNote, PlayerEvaluation
from evaluations.services.checklists import active_criteria, history_criteria
from events.models import Season
from events.services import attendance
from formbuilder.models import Answer, Field
from members.models import Member
from teams.models import TeamMembership

#: Types a statistic can be summed up numerically for; the rest only get a response count and recent answers.
NUMERIC = {Field.FieldType.NUMBER}
DISTRIBUTION = {Field.FieldType.CHOICE, Field.FieldType.MULTICHOICE, Field.FieldType.CHECKBOX}


def _number(value):
    """``Answer.value`` is JSON, so a number comes back as the string it was stored as. ``None`` for junk instead of failing a page."""
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _evaluations(checklist: EvaluationChecklist, season: Season | None = None):
    qs = PlayerEvaluation.objects.filter(checklist=checklist)
    return qs.filter(season=season) if season is not None else qs


def question_stats(checklist: EvaluationChecklist, *, season: Season | None = None) -> list[dict]:
    """One row per active criterion, in rubric order: numeric ones get count / average / min / max, choice-like ones a breakdown,
    text a count and the five most recent answers."""
    submission_ids = _evaluations(checklist, season).values_list("submission_id", flat=True)
    rows = []
    for field in active_criteria(checklist):
        answers = Answer.objects.filter(submission_id__in=submission_ids, field=field).exclude(value__isnull=True)
        row = {"field": field, "response_count": answers.count()}
        if field.field_type in NUMERIC:
            numbers = [n for n in (_number(v) for v in answers.values_list("value", flat=True)) if n is not None]
            row.update(kind="numeric", average=float(sum(numbers) / len(numbers)) if numbers else None, minimum=float(min(numbers)) if numbers else None, maximum=float(max(numbers)) if numbers else None)
        elif field.field_type in DISTRIBUTION:
            tally: dict = {}
            for value in answers.values_list("value", flat=True):
                for option in value if isinstance(value, list) else [value]:
                    tally[str(option)] = tally.get(str(option), 0) + 1
            row.update(kind="distribution", distribution=sorted(tally.items(), key=lambda item: item[1], reverse=True))
        else:
            row.update(kind="text", recent_answers=[str(v) for v in answers.order_by("-submission__submitted_at").values_list("value", flat=True)[:5]])
        rows.append(row)
    return rows


def results_matrix(checklist: EvaluationChecklist, *, season: Season | None = None) -> dict:
    """One row per player evaluated on the checklist with their *latest* answer to each active criterion: who is missing a score."""
    fields = active_criteria(checklist)
    evaluations = _evaluations(checklist, season).select_related("player", "submission").prefetch_related("submission__answers").order_by("player_id", "-submission__submitted_at")
    latest: dict = {}
    for evaluation in evaluations:
        latest.setdefault(evaluation.player_id, evaluation)
    rows = []
    for evaluation in sorted(latest.values(), key=lambda e: (e.player.last_name.lower(), e.player.first_name.lower())):
        values = {a.field_id: a.value for a in evaluation.submission.answers.all()}
        rows.append({"player": evaluation.player, "evaluation": evaluation, "values": {str(f.pk): values.get(f.pk) for f in fields}})
    return {"fields": fields, "rows": rows}


def checklist_players(checklist: EvaluationChecklist, *, since: datetime.date | None = None) -> list[Member]:
    """Everyone ever evaluated on the checklist, by name -- who an evaluation meeting steps through. ``since`` narrows it to players with
    an evaluation entered on or after that date ("who is new since the last meeting")."""
    evaluations = PlayerEvaluation.objects.filter(checklist=checklist)
    if since is not None:
        evaluations = evaluations.filter(created__gte=timezone.make_aware(datetime.datetime.combine(since, datetime.time.min)))
    return list(Member.objects.filter(pk__in=evaluations.values_list("player_id", flat=True)).order_by("first_name", "last_name"))


def player_review(checklist: EvaluationChecklist, player: Member) -> dict:
    """What a review card is built from: the player's evaluations on this checklist (newest first), for each criterion every answer
    they got (newest first) and, for a numeric one, a chronological trend; plus their notes, current teams and this season's attendance."""
    evaluations = list(PlayerEvaluation.objects.filter(checklist=checklist, player=player).select_related("season", "submission__submitted_by").prefetch_related("submission__answers").order_by("submission__submitted_at"))
    answers = {e.pk: {a.field_id: a.value for a in e.submission.answers.all()} for e in evaluations}
    questions = []
    for field in history_criteria(checklist):
        entries = [{"evaluation": e, "value": answers[e.pk][field.pk]} for e in evaluations if answers[e.pk].get(field.pk) not in (None, "")]
        question = {"field": field, "entries": list(reversed(entries)), "trend": []}
        if field.field_type in NUMERIC:
            question["trend"] = [{"evaluation_id": str(x["evaluation"].pk), "at": x["evaluation"].submission.submitted_at, "value": float(n)} for x in entries if (n := _number(x["value"])) is not None]
        questions.append(question)
    season = Season.get_current()
    teams = list(TeamMembership.objects.filter(member=player, season=season).select_related("team").order_by("team__name")) if season else []
    counts = attendance.member_attendance_counts(player, season)
    answered = counts["present"] + counts["absent"]
    return {
        "evaluations": list(reversed(evaluations)), "questions": questions, "notes": player_notes(checklist, player), "teams": [m.team for m in teams],
        "attendance": {"season": season, "present": counts["present"], "absent": counts["absent"], "rate": round(100 * counts["present"] / answered) if answered else None},
    }  # fmt: skip


def player_notes(checklist: EvaluationChecklist, player: Member) -> list[EvaluationNote]:
    return list(EvaluationNote.objects.filter(checklist=checklist, player=player).select_related("author").order_by("-created"))
