"""Player evaluations, built on formbuilder's Form/Field/Submission/Answer
engine rather than a parallel one (see ARCHITECTURE.md §5.8): a club's
evaluation checklist *is* a formbuilder.Form, and filling one in reuses
formbuilder's own dynamic-form-class builder for validation. The one thing
formbuilder has no notion of -- who a submission is *about*, as opposed to
who submitted it, and which of a club's several checklists it was scored
against -- is what evaluations.models.PlayerEvaluation adds.

Deliberately does NOT call formbuilder.services.submission.submit_form: that
function's whole job is enforcing a FormSend's audience/response-window
rules, and an evaluation has neither -- who may submit one is gated at the
view layer instead (club.mixins.EvaluationManagerRequiredMixin), not by
FormSend membership. This module reuses the one piece of submit_form that
does apply -- validating the answers via the same dynamic Django Form the
fill-in UI renders (formbuilder.services.form_factory.build_form) -- and
persists Submission/Answer rows directly.
"""

import datetime
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from formbuilder.models import Answer, Field, Form, FormSend, Submission
from formbuilder.services.form_factory import build_form

from .models import EvaluationChecklist, PlayerEvaluation

#: Field types a per-question statistic can be numerically summarized for --
#: everything else (text/textarea/email/date/file) only gets a response count
#: and a feed of recent answers, since there's nothing to average or bucket.
NUMERIC_FIELD_TYPES = {Field.FieldType.NUMBER}
DISTRIBUTION_FIELD_TYPES = {Field.FieldType.CHOICE, Field.FieldType.MULTICHOICE, Field.FieldType.CHECKBOX}


class EvaluationRubricNotConfigured(Exception):
    """Raised when a checklist has no criteria yet -- an admin must build one
    (start_new_rubric_version) before anyone can submit an evaluation against
    it."""


class EvaluationSubmissionError(Exception):
    """Raised when submitted answers don't validate against the checklist's
    current rubric's Fields. ``errors`` maps field key -> messages, same
    shape as formbuilder.services.submission.FormSubmissionError."""

    def __init__(self, message, *, errors=None):
        super().__init__(message)
        self.errors = errors or {}


def active_checklists(club):
    """Every checklist a new evaluation can currently be filed under."""
    return EvaluationChecklist.objects.filter(club=club, is_active=True)


def current_rubric_form(checklist: EvaluationChecklist) -> Form | None:
    """The Form currently backing ``checklist``'s rubric, or None if no admin
    has built one yet."""
    return checklist.form


def _evaluation_send_for(form: Form) -> FormSend:
    """The FormSend a checklist's Submissions hang off -- plumbing only
    (formbuilder.Submission.send is a mandatory FK), not a real audience
    broadcast. Left club_wide=False with no teams/groups/invited_members, so
    formbuilder.services.audience.effective_members(send) is always empty:
    this send can never surface in anyone's general "Forms to complete"
    list (mobile home card, Me page, Forms list -- see
    formbuilder.services.audience.form_status_rows_for, which iterates
    every FormSend for the club unconditionally). is_active=False for the
    same reason, and moot anyway since submit_evaluation below never routes
    through formbuilder's own submit_form -- see this module's docstring.
    """
    send, _created = FormSend.objects.get_or_create(club=form.club, form=form, defaults={"is_active": False})
    return send


@transaction.atomic
def submit_evaluation(*, club, checklist, player, season, evaluator, data, files=None) -> PlayerEvaluation:
    """Validate ``data`` against ``checklist``'s current rubric and persist a
    Submission + Answers + the PlayerEvaluation envelope around them.

    Raises ``EvaluationRubricNotConfigured`` / ``EvaluationSubmissionError``.
    """
    form = current_rubric_form(checklist)
    if form is None:
        raise EvaluationRubricNotConfigured(_("This checklist hasn't been set up yet."))

    bound_form = build_form(form, data=data, files=files or {})
    if not bound_form.is_valid():
        errors = {key: list(messages) for key, messages in bound_form.errors.items()}
        raise EvaluationSubmissionError(_("The evaluation has errors."), errors=errors)

    # Same "blank optional answers validate but aren't stored" rule as
    # formbuilder.services.submission._clean_answers -- an Answer row exists
    # only where the evaluator actually scored something.
    fields_by_key = {field.key: field for field in form.fields.filter(is_active=True)}
    answers = [(fields_by_key[key], value) for key, value in bound_form.cleaned_data.items() if value not in (None, "", [])]

    send = _evaluation_send_for(form)
    submission = Submission.objects.create(send=send, member=evaluator)
    Answer.objects.bulk_create([Answer(submission=submission, field=field, value=value) for field, value in answers])

    return PlayerEvaluation.objects.create(club=club, checklist=checklist, player=player, season=season, submission=submission)


@transaction.atomic
def start_new_rubric_version(checklist: EvaluationChecklist) -> Form:
    """Create a new Form, pre-filled with a plain copy of the current
    rubric's Fields, and re-point ``checklist`` at it -- the entire
    versioning mechanism (see EvaluationChecklist's own docstring). Every
    edit to a checklist's criteria goes through here rather than mutating
    Fields on the current Form in place, so a PlayerEvaluation created under
    the old Form keeps rendering with the exact questions it was scored
    against, unaffected by later edits.
    """
    current = current_rubric_form(checklist)

    new_form = Form.objects.create(club=checklist.club, title=current.title if current is not None else checklist.name)
    if current is not None:
        Field.objects.bulk_create(
            [
                Field(
                    form=new_form,
                    key=field.key,
                    label=field.label,
                    field_type=field.field_type,
                    required=field.required,
                    help_text=field.help_text,
                    order=field.order,
                    is_active=field.is_active,
                    options=field.options,
                )
                for field in current.fields.all()
            ]
        )

    checklist.form = new_form
    checklist.save(update_fields=["form", "modified"])
    return new_form


def question_stats(checklist: EvaluationChecklist, *, season=None):
    """Per-question aggregates for ``checklist``, spanning every version it's
    ever had -- Answers are matched to the *current* version's Fields by
    ``key`` (stable across versions, see EvaluationChecklist's own
    docstring), not by Field id, since each edit creates brand-new Field
    rows.

    Returns a list of rows in the current rubric's own order, each shaped
    for the statistics template: numeric fields get count/average/min/max,
    choice-type fields get a response breakdown, and free-text/file/date
    fields just get a count plus their most recent answers (nothing else is
    meaningful to aggregate over free text).
    """
    form = current_rubric_form(checklist)
    if form is None:
        return []

    evaluations = PlayerEvaluation.objects.filter(checklist=checklist)
    if season is not None:
        evaluations = evaluations.filter(season=season)
    submission_ids = evaluations.values_list("submission_id", flat=True)

    rows = []
    for field in form.fields.filter(is_active=True).order_by("order"):
        answers = Answer.objects.filter(submission_id__in=submission_ids, field__key=field.key).exclude(value__isnull=True)
        row = {"field": field, "response_count": answers.count()}

        if field.field_type in NUMERIC_FIELD_TYPES:
            row["kind"] = "numeric"
            # Computed in Python, not a DB Avg/Min/Max -- Answer.value is a
            # JSONField (it also stores choice/text/list answers), stored as
            # a JSON string for a Decimal (see Answer's own docstring), so
            # there's no numeric column an aggregate could operate on
            # portably across sqlite/postgres.
            numbers = []
            for value in answers.exclude(value="").values_list("value", flat=True):
                try:
                    numbers.append(Decimal(str(value)))
                except InvalidOperation:
                    continue
            if numbers:
                row.update(average=sum(numbers) / len(numbers), minimum=min(numbers), maximum=max(numbers))
            else:
                row.update(average=None, minimum=None, maximum=None)
        elif field.field_type in DISTRIBUTION_FIELD_TYPES:
            row["kind"] = "distribution"
            # MULTICHOICE/CHECKBOX answers can be a list -- tally each
            # selected option separately rather than the raw value combination.
            tally = {}
            for value in answers.values_list("value", flat=True):
                selected = value if isinstance(value, list) else [value]
                for option in selected:
                    tally[option] = tally.get(option, 0) + 1
            row["distribution"] = sorted(tally.items(), key=lambda item: item[1], reverse=True)
        else:
            row["kind"] = "text"
            row["recent_answers"] = list(answers.order_by("-submission__submitted_at").values_list("value", flat=True)[:5])

        rows.append(row)

    return rows


def results_matrix(checklist: EvaluationChecklist, *, season=None):
    """One row per player who's ever been evaluated against ``checklist``,
    each showing their *latest* answer per current-rubric question -- the
    "overview of the latest results per question" helper. Same population
    source as the walkthrough queue below: there's no team/group tied to a
    checklist, so "who's in scope" is defined entirely by who's already been
    evaluated against it at least once.
    """
    form = current_rubric_form(checklist)
    if form is None:
        return {"fields": [], "rows": []}

    fields = list(form.fields.filter(is_active=True).order_by("order"))
    evaluations = PlayerEvaluation.objects.filter(checklist=checklist)
    if season is not None:
        evaluations = evaluations.filter(season=season)
    evaluations = evaluations.select_related("player", "submission").order_by("player_id", "-submission__submitted_at")

    latest_by_player = {}
    for evaluation in evaluations:
        latest_by_player.setdefault(evaluation.player_id, evaluation)

    rows = []
    for evaluation in sorted(latest_by_player.values(), key=lambda item: item.player.get_full_name()):
        answers_by_key = {answer.field.key: answer.value for answer in evaluation.submission.answers.select_related("field")}
        rows.append({"player": evaluation.player, "evaluation": evaluation, "values": [answers_by_key.get(field.key) for field in fields]})

    return {"fields": fields, "rows": rows}


def walkthrough_queue(checklist: EvaluationChecklist, *, cutoff_date, exclude_player_ids=()):
    """Members due a fresh look under ``checklist``: everyone with at least
    one evaluation against it whose *newest* one predates ``cutoff_date``,
    most-overdue first. Scoped strictly to this checklist's own evaluations
    -- a player moving up a level (say U8 -> U10) getting a fresh U10
    evaluation doesn't retroactively resolve a stale U8 entry for them; in
    practice nobody keeps filing new U8 evaluations for a promoted player,
    so their U8 entry just goes untouched rather than being actively
    removed. No team/roster bookkeeping needed either way; archiving a
    checklist (EvaluationChecklist.is_active) is the deliberate way to stop
    offering its walkthrough at all.

    Deliberately excludes anyone with zero evaluations on this checklist --
    the walkthrough is a re-confirm queue, not a way to find first-timers.
    Those are added the ordinary way, from the player's own member page.

    ``exclude_player_ids`` is a per-run "not right now" list -- the view
    threads it through the page as skipped players are passed over, so the
    same overdue player isn't served up again and again in one sitting
    without needing any state persisted server-side.
    """
    # Built explicitly as a midnight-local datetime, aware if the project
    # uses timezone-aware datetimes -- rather than handing Django's ORM the
    # bare date, which would otherwise coerce it itself (with a
    # RuntimeWarning) using this exact same "local midnight" rule, just
    # implicitly.
    cutoff = datetime.datetime.combine(cutoff_date, datetime.time.min)
    if settings.USE_TZ:
        cutoff = timezone.make_aware(cutoff)

    queryset = PlayerEvaluation.objects.filter(checklist=checklist)
    if exclude_player_ids:
        queryset = queryset.exclude(player_id__in=exclude_player_ids)
    return queryset.values("player_id", "player__first_name", "player__last_name").annotate(latest=Max("created")).filter(latest__lt=cutoff).order_by("latest")
