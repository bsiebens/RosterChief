"""Validate and persist a submission against a Form's fields and rules."""

from django.db import transaction
from django.utils import timezone

from formbuilder.models import Answer, Submission

from .form_factory import build_form


class FormSubmissionError(Exception):
    """Raised when a submission is rejected. ``errors`` maps field key -> messages."""

    def __init__(self, message, *, errors=None):
        super().__init__(message)
        self.errors = errors or {}


def _is_empty(value):
    return value is None or value == "" or value == []


@transaction.atomic
def submit_form(form, member, data, *, files=None, when=None):
    """Create a Submission (with Answers) for ``data``/``files`` or raise FormSubmissionError.

    Validation goes through ``build_form`` — the same dynamic Django Form the UI would
    render — so a NUMBER field is actually checked as a decimal, an EMAIL as an email, a
    CHOICE against its real options, and so on, rather than a hand-rolled subset of that.
    """
    when = when or timezone.now()

    _check_open(form, member, when)
    cleaned = _clean_answers(form, data, files)

    submission = Submission.objects.create(form=form, member=member)
    Answer.objects.bulk_create([Answer(submission=submission, field=field, value=value) for field, value in cleaned])
    return submission


def _check_open(form, member, when):
    if not form.is_active:
        raise FormSubmissionError("This form is not accepting submissions.")
    if form.login_required and member is None:
        raise FormSubmissionError("You must be signed in to submit this form.")
    if form.opens_at is not None and when < form.opens_at:
        raise FormSubmissionError("This form is not open yet.")
    if form.closes_at is not None and when > form.closes_at:
        raise FormSubmissionError("This form has closed.")
    if form.max_submissions_per_user is not None and member is not None:
        used = form.submissions.filter(member=member).count()
        if used >= form.max_submissions_per_user:
            raise FormSubmissionError("You have reached the maximum number of submissions for this form.")


def _clean_answers(form, data, files):
    bound_form = build_form(form, data=data, files=files or {})
    if not bound_form.is_valid():
        errors = {key: list(messages) for key, messages in bound_form.errors.items()}
        raise FormSubmissionError("The submission has errors.", errors=errors)

    # Blank optional answers are validated (they may legitimately be empty) but not
    # stored — an Answer row exists only where the submitter actually said something.
    fields_by_key = {field.key: field for field in form.fields.filter(is_active=True)}
    return [(fields_by_key[key], value) for key, value in bound_form.cleaned_data.items() if not _is_empty(value)]
