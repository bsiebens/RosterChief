"""Validate and persist a submission against a FormSend's fields and rules."""

from django.core.files.uploadedfile import UploadedFile
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from formbuilder.models import Answer, Submission

from .audience import addressed_to
from .form_factory import build_form


class FormSubmissionError(Exception):
    """Raised when a submission is rejected. ``errors`` maps field key -> messages."""

    def __init__(self, message, *, errors=None):
        super().__init__(message)
        self.errors = errors or {}


def _is_empty(value):
    return value is None or value == "" or value == []


@transaction.atomic
def submit_form(send, member, data, *, files=None, when=None, submitted_by=None):
    """Create a Submission (with Answers) for ``data``/``files`` or raise FormSubmissionError.

    ``member`` is who the response is *for*: the audience check and the
    per-member limit are theirs. ``submitted_by`` is who filled it in -- a
    parent/guardian answering for a child -- and defaults to ``member``.
    Whether ``submitted_by`` may act for ``member`` at all is the caller's
    call (mobile.views.FormFillView checks its own managed people).

    Validation goes through ``build_form`` — the same dynamic Django Form the UI would
    render — so a NUMBER field is actually checked as a decimal, an EMAIL as an email, a
    CHOICE against its real options, and so on, rather than a hand-rolled subset of that.
    """
    when = when or timezone.now()

    _check_open(send, member, when)
    cleaned = _clean_answers(send.form, data, files)

    submission = Submission.objects.create(send=send, member=member, submitted_by=submitted_by or member)
    answers = []
    try:
        for field, value in cleaned:
            answer = Answer(submission=submission, field=field)
            if field.field_type == field.FieldType.FILE:
                if not isinstance(value, UploadedFile):
                    # ClearableFileInput's "clear" box yields False, never a file.
                    continue
                _attach_file(answer, value)
            else:
                answer.value = value
            answers.append(answer)
        Answer.objects.bulk_create(answers)
    except Exception:
        # The rows roll back with the transaction; files already written don't.
        for answer in answers:
            if answer.file:
                answer.file.delete(save=False)
        raise
    return submission


def _attach_file(answer, upload):
    """Write ``upload`` to private storage (Answer.file's upload_to decides the
    path) and keep only its metadata in ``value`` -- a JSONField can't hold
    the file itself."""
    answer.value = {"name": upload.name, "size": upload.size, "content_type": upload.content_type or ""}
    answer.file.save(upload.name, upload, save=False)


def _check_open(send, member, when):
    if not send.is_active:
        raise FormSubmissionError(_("This form is not accepting submissions."))
    # Anonymous responses only exist for a send explicitly opted into a
    # public link (and whose form doesn't require sign-in): without a member
    # there is no audience or per-member limit to enforce, so anything else
    # needs a signed-in member.
    if member is None and (send.form.login_required or not send.is_public):
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
    # anonymous submission (public send only, see above) has no member to
    # resolve membership for; the public view rate-limits those per client IP
    # instead (formbuilder.views.PublicFormFillView).
    if member is not None and not addressed_to(send, member):
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
