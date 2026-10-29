"""Club forms (API.md §7.2), club host: the list, one form's schema, submitting, and a
submitted response. Validation is formbuilder.services.submit_form -- the same dynamic
form the website renders -- so the app can't accept what the website would refuse."""

import json
import uuid

from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from ninja import Router

from api.core.errors import ApiError
from api.core.idempotency import idempotent
from api.core.scope import club_scope
from formbuilder.models import FormSend, Submission
from formbuilder.services import FormSubmissionError, addressed_people, form_status_rows_for, is_send_open, submit_form
from formbuilder.services.options import field_choices
from rosterchief.uploads import DOCUMENT_ACCEPT, MAX_DOCUMENT_UPLOAD_BYTES

from ..schemas import ErrorOut, FormListOut, FormOut, SubmissionOut

router = Router(tags=["forms"])

ERRORS = {401: ErrorOut, 403: ErrorOut, 404: ErrorOut}

SUBMIT_BODY = {
    "requestBody": {
        "required": True,
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "required": ["member_id", "answers"],
                    "properties": {
                        "member_id": {"type": "string", "format": "uuid", "description": "Who the response is for (one of the form's `addressed` people)."},
                        "answers": {"type": "object", "description": "Field `key` → value: string, number, `YYYY-MM-DD`, bool, or a list of option values for `multichoice`."},
                    },
                }
            },
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["member_id"],
                    "properties": {"member_id": {"type": "string", "format": "uuid"}},
                    "additionalProperties": True,
                    "description": "Only needed for forms with `file` fields: one part per field `key` (repeat a key for `multichoice`, `true`/`on` for a checkbox), files as file parts.",
                }
            },
        },
    }
}


def field_out(field) -> dict:
    """One question as the app renders it (shared with the evaluation checklists)."""
    return {
        "key": field.key,
        "label": field.label,
        "type": field.field_type,
        "required": field.required,
        "help_text": field.help_text,
        "section": field.section or "",
        "options": [{"value": str(value), "label": str(label)} for value, label in field_choices(field)],
    }


def _open_send(scope, send_id) -> FormSend:
    # Not gated on the "formbuilder" flag, same as the member web app: the flag licenses
    # *creating* forms (management); a form already sent to the family stays answerable.
    send = get_object_or_404(FormSend.objects.select_related("form").filter(club=scope.club), pk=send_id)
    if not addressed_people(send, scope.people):
        # Not addressed to anyone the account acts for: its questions stay private.
        raise ApiError(404, "not_found")
    return send


@router.get("/forms", response={200: FormListOut, **ERRORS}, summary="Forms for the family")
def forms(request):
    """Every form any managed person is (or was) asked to fill in, one row per person,
    newest first; `submitted_at` is null while still to do."""
    scope = club_scope(request)
    now = timezone.now()
    items = []
    for row in form_status_rows_for(scope.people, scope.club):
        send, submission = row["send"], row["submission"]
        items.append(
            {
                "send_id": send.pk,
                "title": send.form.title,
                "member_id": row["member"].pk,
                "is_open": is_send_open(send, now),
                "closes_at": send.closes_at,
                "submitted_at": row["submitted_at"],
                "submission_id": submission.pk if submission else None,
            }
        )
    return {"items": items}


@router.get("/forms/{send_id}", response={200: FormOut, **ERRORS}, summary="One form, to fill in")
def form_detail(request, send_id: uuid.UUID):
    """The fields to render. Always readable while addressed to the family, even when
    closed (`is_open` false): submitting then answers `409 form_unavailable`."""
    scope = club_scope(request)
    send = _open_send(scope, send_id)
    people = addressed_people(send, scope.people)
    counts = {person.pk: send.submissions.filter(member=person).count() for person in people}
    fields = [field_out(field) for field in send.form.fields.filter(is_active=True).order_by("order")]
    return {
        "send_id": send.pk,
        "title": send.form.title,
        "description": send.form.description,
        "opens_at": send.opens_at,
        "closes_at": send.closes_at,
        "is_open": is_send_open(send),
        "max_submissions_per_user": send.max_submissions_per_user,
        "addressed": [{"member_id": person.pk, "submissions": counts[person.pk]} for person in people],
        "fields": fields,
        "file_accept": DOCUMENT_ACCEPT,
        "max_file_bytes": MAX_DOCUMENT_UPLOAD_BYTES,
    }


def _submission_out(submission) -> dict:
    values = {answer.field_id: answer.value for answer in submission.answers.all()}
    return {
        "id": submission.pk,
        "send_id": submission.send_id,
        "title": submission.send.form.title,
        "member_id": submission.member_id,
        "submitted_at": submission.submitted_at,
        "answers": [{"key": field.key, "label": field.label, "type": field.field_type, "value": values.get(field.id)} for field in submission.send.form.fields.order_by("order")],
    }


def _read_submission_body(request):
    """``(member_id, data, files)`` from a JSON or a multipart body."""
    if request.content_type.startswith("multipart/"):
        data = request.POST.copy()
        member_id = data.pop("member_id", [""])[0]
        return member_id, data, request.FILES
    try:
        body = json.loads(request.body or b"{}")
    except ValueError:
        raise ApiError(400, "bad_request") from None
    answers = body.get("answers") if isinstance(body, dict) else None
    if not isinstance(answers, dict):
        raise ApiError(422, "validation_error", fields={"answers": [_("Send the answers as an object keyed by field.")]})
    return body.get("member_id", ""), answers, None


@router.post("/forms/{send_id}/submit", response={201: SubmissionOut, **ERRORS, 409: ErrorOut, 422: ErrorOut}, summary="Submit a form", openapi_extra=SUBMIT_BODY)
def submit(request, send_id: uuid.UUID):
    """Submit for one of the form's `addressed` people. Send an `Idempotency-Key`: a
    repeated request returns the first response instead of a second submission.
    `422` carries `fields` per field `key`; `409 form_unavailable` means closed, not yet
    open, or the maximum number of responses was reached."""
    scope = club_scope(request)
    send = _open_send(scope, send_id)
    member_id, data, files = _read_submission_body(request)
    person = next((person for person in addressed_people(send, scope.people) if str(person.pk) == str(member_id)), None)
    if person is None:
        raise ApiError(404, "not_found", _("This form isn't for that person."))

    def work():
        try:
            submission = submit_form(send, person, data, files=files, submitted_by=scope.me)
        except FormSubmissionError as error:
            if error.errors:
                raise ApiError(422, "validation_error", str(error), fields={key: [str(message) for message in messages] for key, messages in error.errors.items()}) from error
            raise ApiError(409, "form_unavailable", str(error)) from error
        return 201, _submission_out(submission)

    return idempotent(request, work)


@router.get("/forms/submissions/{submission_id}", response={200: SubmissionOut, **ERRORS}, summary="A submitted response")
def submission_detail(request, submission_id: uuid.UUID):
    """Read-only, for a managed person's own response. Every field of the form is listed;
    `value` is null where it was left blank."""
    scope = club_scope(request)
    submission = get_object_or_404(Submission.objects.select_related("send__form").prefetch_related("answers").filter(send__club=scope.club, member__in=scope.people), pk=submission_id)
    return _submission_out(submission)
