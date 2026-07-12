"""Validate and persist a submission against a Form's fields and rules."""

from django.db import transaction
from django.utils import timezone

from formbuilder.models import Answer, Field, Submission

from .options import allowed_values


class FormSubmissionError(Exception):
    """Raised when a submission is rejected. ``errors`` maps field key -> message."""

    def __init__(self, message, *, errors=None):
        super().__init__(message)
        self.errors = errors or {}


def _is_empty(value):
    return value is None or value == "" or value == []


@transaction.atomic
def submit_form(form, member, data, *, when=None):
    """Create a Submission (with Answers) for ``data`` or raise FormSubmissionError."""
    when = when or timezone.now()

    _check_open(form, member, when)
    cleaned = _clean_answers(form, data)

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


def _clean_answers(form, data):
    errors = {}
    cleaned = []

    for field in form.fields.filter(is_active=True):
        raw = data.get(field.key)
        if _is_empty(raw):
            if field.required:
                errors[field.key] = "This field is required."
            continue

        message = _validate_choice(field, raw)
        if message is not None:
            errors[field.key] = message
            continue

        cleaned.append((field, raw))

    if errors:
        raise FormSubmissionError("The submission has errors.", errors=errors)
    return cleaned


def _validate_choice(field, raw):
    if field.field_type == Field.FieldType.CHOICE:
        allowed = allowed_values(field)
        if allowed and raw not in allowed:
            return "Select a valid choice."
    elif field.field_type == Field.FieldType.MULTICHOICE:
        allowed = allowed_values(field)
        values = raw if isinstance(raw, list) else [raw]
        if allowed and not set(values) <= allowed:
            return "Select valid choices."
    return None
