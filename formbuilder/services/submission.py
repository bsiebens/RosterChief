"""Validate and store a response to a FormSend. Framework-free: the management API, the public fill page and the member
fill page all call ``submit_form``.

Entry points: ``submit_form(send, member, data, files=..., submitted_by=...)`` raises ``FormSubmissionError`` (an
``Invalid``: ``.errors`` maps field key -> messages, ``.code`` says why a send refused: ``inactive``, ``login_required``,
``not_open_yet``, ``closed``, ``max_submissions``, ``not_addressed``, ``not_guardian`` or ``invalid``);
``validate_answers(form, data, files)`` only validates.

``data`` is ``{field key: value}`` (a Django ``QueryDict`` works too; a multichoice value is a list); ``files`` is
``{field key: uploaded file}``. Keys of unknown or inactive fields are ignored.
"""

import datetime
import os
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone

from events.services.attachments import ALLOWED_EXTENSIONS, MAX_ATTACHMENT_BYTES, display_name
from formbuilder.models import Answer, Field, FormSend, Submission
from members.services.errors import Invalid

from .audience import addressed_to, is_guardian_of
from .options import allowed_values

MAX_TEXT_LENGTH = 1000
MAX_TEXTAREA_LENGTH = 10000
REQUIRED_MESSAGE = "This field is required."
TRUE_VALUES = {"true", "on", "1", "yes", "y"}

FieldType = Field.FieldType


class FormSubmissionError(Invalid):
    """A rejected submission. ``errors`` maps field key -> list of messages; ``code`` says what was wrong."""

    def __init__(self, message, *, errors=None, code="invalid"):
        super().__init__(message, errors, code)


def _is_empty(value) -> bool:
    return value is None or value == "" or value == []


def _raw(data, key):
    """The raw value for ``key``: a list for a QueryDict entry with several values, else the plain value."""
    if data is None:
        return None
    if hasattr(data, "getlist"):
        values = data.getlist(key)
        return values if len(values) > 1 else (values[0] if values else None)
    return data.get(key)


def _as_list(raw) -> list:
    if raw is None or raw == "":
        return []
    return [item for item in (raw if isinstance(raw, list | tuple | set) else [raw]) if item not in (None, "")]


def _clean_value(field, raw, upload):
    """The cleaned value for one field, ``None`` when left empty. Raises ``ValueError`` with the message to show."""
    kind = field.field_type
    if kind == FieldType.FILE:
        return _clean_upload(upload)
    if kind == FieldType.CHECKBOX:
        value = raw if isinstance(raw, bool) else str(raw or "").strip().lower() in TRUE_VALUES
        if field.required and not value:
            raise ValueError(REQUIRED_MESSAGE)
        return value
    if kind == FieldType.MULTICHOICE:
        items = [str(item) for item in _as_list(raw)]
        if not items and field.required:
            raise ValueError(REQUIRED_MESSAGE)
        allowed = allowed_values(field)
        bad = [item for item in items if item not in allowed]
        if bad:
            raise ValueError(f"Select a valid choice. {bad[0]} is not one of the available choices.")
        return list(dict.fromkeys(items)) or None
    if isinstance(raw, list | tuple):
        raise ValueError("Enter a single value.")
    text = "" if raw is None else str(raw).strip()
    if not text:
        if field.required:
            raise ValueError(REQUIRED_MESSAGE)
        return None
    if kind == FieldType.TEXT:
        if len(text) > MAX_TEXT_LENGTH:
            raise ValueError(f"Ensure this value has at most {MAX_TEXT_LENGTH} characters.")
        return text
    if kind == FieldType.TEXTAREA:
        if len(text) > MAX_TEXTAREA_LENGTH:
            raise ValueError(f"Ensure this value has at most {MAX_TEXTAREA_LENGTH} characters.")
        return text
    if kind == FieldType.NUMBER:
        try:
            number = Decimal(text.replace(",", "."))
        except InvalidOperation:
            raise ValueError("Enter a number.") from None
        if not number.is_finite() or abs(number) >= Decimal(10) ** 15:
            raise ValueError("Enter a number.")
        if field.min_value is not None and number < field.min_value:
            raise ValueError(f"Ensure this value is at least {field.min_value.normalize():f}.")
        if field.max_value is not None and number > field.max_value:
            raise ValueError(f"Ensure this value is at most {field.max_value.normalize():f}.")
        return number
    if kind == FieldType.EMAIL:
        try:
            validate_email(text)
        except ValidationError:
            raise ValueError("Enter a valid email address.") from None
        return text
    if kind == FieldType.DATE:
        try:
            return datetime.date.fromisoformat(text).isoformat()
        except ValueError:
            raise ValueError("Enter a valid date (YYYY-MM-DD).") from None
    if kind == FieldType.CHOICE:
        if text not in allowed_values(field):
            raise ValueError(f"Select a valid choice. {text} is not one of the available choices.")
        return text
    raise ValueError("Unsupported field type.")


def _clean_upload(upload):
    if upload is None or upload is False or upload == "":
        return None
    name = display_name(getattr(upload, "name", ""))
    extension = os.path.splitext(name)[1].lower()
    if not name or extension not in ALLOWED_EXTENSIONS:
        raise ValueError("This file type is not allowed.")
    size = getattr(upload, "size", 0) or 0
    if size <= 0:
        raise ValueError("The submitted file is empty.")
    if size > MAX_ATTACHMENT_BYTES:
        raise ValueError(f"The file is larger than {MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB.")
    return upload


def validate_answers(form, data, files=None) -> list:
    """``[(field, value)]`` for every active field that was answered (blank optional answers are dropped). Raises
    ``FormSubmissionError`` with per-key errors when anything is invalid."""
    cleaned, errors = [], {}
    for field in form.fields.filter(is_active=True).order_by("order"):
        upload = files.get(field.key) if files else None
        try:
            value = _clean_value(field, _raw(data, field.key), upload)
        except ValueError as exc:
            errors[field.key] = [str(exc)]
            continue
        if field.field_type == FieldType.FILE and value is None and field.required:
            errors[field.key] = [REQUIRED_MESSAGE]
        elif not _is_empty(value):
            cleaned.append((field, value))
    if errors:
        raise FormSubmissionError("The submission has errors.", errors=errors)
    return cleaned


def check_can_submit(send, member, *, when=None, submitted_by=None) -> None:
    """Raise ``FormSubmissionError`` unless the send accepts a response for ``member`` (``None`` = anonymous, public sends
    of a form that needs no login only) right now."""
    when = when or timezone.now()
    if not send.is_active:
        raise FormSubmissionError("This form is not accepting submissions.", code="inactive")
    if member is None and (send.form.login_required or not send.is_public):
        raise FormSubmissionError("You must be signed in to submit this form.", code="login_required")
    if send.opens_at is not None and when < send.opens_at:
        raise FormSubmissionError("This form is not open yet.", code="not_open_yet")
    if send.closes_at is not None and when > send.closes_at:
        raise FormSubmissionError("This form has closed.", code="closed")
    if member is not None:
        if submitted_by is not None and submitted_by.pk != member.pk and not is_guardian_of(submitted_by, member):
            raise FormSubmissionError("You may not answer for this person.", code="not_guardian")
        if send.max_submissions_per_user is not None and send.submissions.filter(member=member).count() >= send.max_submissions_per_user:
            raise FormSubmissionError("You have reached the maximum number of submissions for this form.", code="max_submissions")
        if not addressed_to(send, member):
            raise FormSubmissionError("This form isn't addressed to you.", code="not_addressed")


@transaction.atomic
def submit_form(send, member, data, *, files=None, when=None, submitted_by=None) -> Submission:
    """Create a Submission with its Answers for ``data`` / ``files`` or raise ``FormSubmissionError``.

    ``member`` is who the response is *for* (the audience check and the per-member limit are theirs, ``None`` for an
    anonymous public response). ``submitted_by`` is who filled it in -- a parent / guardian answering for a child, who must
    be a guardian of ``member`` -- and defaults to ``member``."""
    FormSend.objects.select_for_update().filter(pk=send.pk).first()  # serialise concurrent submissions (max per user)
    send.refresh_from_db()
    check_can_submit(send, member, when=when, submitted_by=submitted_by)
    cleaned = validate_answers(send.form, data, files)

    submission = Submission.objects.create(send=send, member=member, submitted_by=submitted_by or member)
    answers = []
    try:
        for field, value in cleaned:
            answer = Answer(submission=submission, field=field)
            if field.field_type == FieldType.FILE:
                answer.value = {"name": display_name(value.name), "size": value.size, "content_type": getattr(value, "content_type", "") or ""}
                answer.file.save(value.name, value, save=False)
            else:
                answer.value = value
            answers.append(answer)
        Answer.objects.bulk_create(answers)
    except Exception:
        for answer in answers:  # the rows roll back with the transaction; files already written do not
            if answer.file:
                answer.file.storage.delete(answer.file.name)
        raise
    return submission
