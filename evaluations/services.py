"""Player evaluations, built on formbuilder's Form/Field/Submission/Answer
engine rather than a parallel one (see ARCHITECTURE.md §5.8): a club's
evaluation rubric *is* a formbuilder.Form, and filling one in reuses
formbuilder's own dynamic-form-class builder for validation. The one thing
formbuilder has no notion of -- who a submission is *about*, as opposed to
who submitted it -- is what evaluations.models.PlayerEvaluation adds.

Deliberately does NOT call formbuilder.services.submission.submit_form: that
function's whole job is enforcing a FormSend's audience/response-window
rules, and an evaluation has neither -- who may submit one is gated at the
view layer instead (club.mixins.EvaluationManagerRequiredMixin), not by
FormSend membership. This module reuses the one piece of submit_form that
does apply -- validating the answers via the same dynamic Django Form the
fill-in UI renders (formbuilder.services.form_factory.build_form) -- and
persists Submission/Answer rows directly.
"""

from django.db import transaction
from django.utils.translation import gettext_lazy as _

from formbuilder.models import Answer, Field, Form, FormSend, Submission
from formbuilder.services.form_factory import build_form

from .models import EvaluationSettings, PlayerEvaluation


class EvaluationRubricNotConfigured(Exception):
    """Raised when a club has no EvaluationSettings yet -- an admin must
    build a rubric (start_new_rubric_version) before anyone can submit an
    evaluation."""


class EvaluationSubmissionError(Exception):
    """Raised when submitted answers don't validate against the current
    rubric's Fields. ``errors`` maps field key -> messages, same shape as
    formbuilder.services.submission.FormSubmissionError."""

    def __init__(self, message, *, errors=None):
        super().__init__(message)
        self.errors = errors or {}


def current_rubric_form(club) -> Form | None:
    """The Form currently backing this club's evaluation rubric, or None if
    no admin has built one yet."""
    settings = EvaluationSettings.objects.filter(club=club).select_related("form").first()
    return settings.form if settings else None


def _evaluation_send_for(form: Form) -> FormSend:
    """The FormSend a rubric's Submissions hang off -- plumbing only
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
def submit_evaluation(*, club, player, season, evaluator, data, files=None) -> PlayerEvaluation:
    """Validate ``data`` against the club's current rubric and persist a
    Submission + Answers + the PlayerEvaluation envelope around them.

    Raises ``EvaluationRubricNotConfigured`` / ``EvaluationSubmissionError``.
    """
    form = current_rubric_form(club)
    if form is None:
        raise EvaluationRubricNotConfigured(_("This club hasn't set up an evaluation form yet."))

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

    return PlayerEvaluation.objects.create(club=club, player=player, season=season, submission=submission)


@transaction.atomic
def start_new_rubric_version(club) -> Form:
    """Create a new Form, pre-filled with a plain copy of the current
    rubric's Fields, and re-point this club's EvaluationSettings at it --
    the entire versioning mechanism (see EvaluationSettings' own docstring).
    Every edit to the rubric's criteria goes through here rather than
    mutating Fields on the current Form in place, so a PlayerEvaluation
    created under the old Form keeps rendering with the exact questions it
    was scored against, unaffected by later edits.
    """
    current = current_rubric_form(club)

    new_form = Form.objects.create(club=club, title=current.title if current is not None else str(_("Evaluation form")))
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

    EvaluationSettings.objects.update_or_create(club=club, defaults={"form": new_form})
    return new_form
