"""Validate and persist a submission against a FormSend's fields and rules."""

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from formbuilder.models import Answer, Submission

from .audience import effective_members
from .form_factory import build_form


class FormSubmissionError(Exception):
    """Raised when a submission is rejected. ``errors`` maps field key -> messages."""

    def __init__(self, message, *, errors=None):
        super().__init__(message)
        self.errors = errors or {}


def _is_empty(value):
    return value is None or value == "" or value == []


@transaction.atomic
def submit_form(send, member, data, *, files=None, when=None):
    """Create a Submission (with Answers) for ``data``/``files`` or raise FormSubmissionError.

    Validation goes through ``build_form`` — the same dynamic Django Form the UI would
    render — so a NUMBER field is actually checked as a decimal, an EMAIL as an email, a
    CHOICE against its real options, and so on, rather than a hand-rolled subset of that.
    """
    when = when or timezone.now()

    _check_open(send, member, when)
    cleaned = _clean_answers(send.form, data, files)

    submission = Submission.objects.create(send=send, member=member)
    Answer.objects.bulk_create([Answer(submission=submission, field=field, value=value) for field, value in cleaned])
    return submission


def _check_open(send, member, when):
    if not send.is_active:
        raise FormSubmissionError(_("This form is not accepting submissions."))
    if send.form.login_required and member is None:
        raise FormSubmissionError(_("You must be signed in to submit this form."))
    if send.opens_at is not None and when < send.opens_at:
        raise FormSubmissionError(_("This form is not open yet."))
    if send.closes_at is not None and when > send.closes_at:
        raise FormSubmissionError(_("This form has closed."))
    if send.max_submissions_per_user is not None and member is not None:
        used = send.submissions.filter(member=member).count()
        if used >= send.max_submissions_per_user:
            raise FormSubmissionError(_("You have reached the maximum number of submissions for this form."))
    # Only a signed-in submitter can be checked against the audience -- an
    # anonymous (login_required=False) submission has no member to resolve
    # membership for, and is already only reachable however the form's own
    # link was shared.
    if member is not None and not effective_members(send).filter(pk=member.pk).exists():
        raise FormSubmissionError(_("This form isn't addressed to you."))


def _clean_answers(form, data, files):
    bound_form = build_form(form, data=data, files=files or {})
    if not bound_form.is_valid():
        errors = {key: list(messages) for key, messages in bound_form.errors.items()}
        raise FormSubmissionError(_("The submission has errors."), errors=errors)

    # Blank optional answers are validated (they may legitimately be empty) but not
    # stored — an Answer row exists only where the submitter actually said something.
    fields_by_key = {field.key: field for field in form.fields.filter(is_active=True)}
    return [(fields_by_key[key], value) for key, value in bound_form.cleaned_data.items() if not _is_empty(value)]
