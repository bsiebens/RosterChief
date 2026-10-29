"""Management API: form responses and reporting (API.md §17).

The JSON counterpart of the responses side of the ``/manage/forms/*`` screens (management/views.py:
``FormSendResponsesView`` and ``FormSendResponsesExportView``). The form definitions, fields and sends
are the ``forms_admin`` router; this one only reads what people answered:

* ``GET /manage/forms/{form_id}/submissions``: filterable list, keyset paged, each row with its answers;
* ``GET /manage/forms/{form_id}/submissions/{id}``: one response with every answer;
* ``GET .../answers/{id}/file``: a file answer, authenticated by the bearer token;
* ``GET /manage/forms/{form_id}/sends/{send_id}/report``: counts and per-choice tallies of one send;
* ``GET .../sends/{send_id}/pending``: audience members who have not responded yet;
* ``GET .../sends/{send_id}/export``: the responses as a CSV download.

The web has no way to delete or mark a response reviewed, so neither does the API. The CSV is built
synchronously, exactly like the web download (one pass over one send's answers; a job's result is
JSON so there is nowhere to put a file), and there is no xlsx variant because the web has none.

Gate (the web's ``FormManagerRequiredMixin``): ``can_manage_forms`` and the ``forms`` module on for the
club (``404 feature_disabled`` otherwise). Everything is scoped to the host's club.
"""

import datetime
import uuid

from django.db.models import Q
from django.http import FileResponse, HttpResponse
from django.shortcuts import get_object_or_404
from django.utils.cache import patch_vary_headers
from django.utils.translation import gettext_lazy as _
from ninja import Router

from api.core.errors import ApiError
from api.core.pagination import paginate
from api.core.schemas import ErrorOut
from api.panel.guards import panel_club, require
from api.panel.schemas import forms_submissions as s
from api.protected.services import club_features
from club.services.access import can_manage_forms
from formbuilder.models import Answer, Form, FormSend, Submission
from formbuilder.services import effective_members, form_report, members_not_yet_submitted, write_report_csv
from formbuilder.services.reporting import display_value

router = Router(tags=["panel: form responses"])

PREFIX = "/manage/forms/{uuid:form_id}"
ERRORS = {401: ErrorOut, 403: ErrorOut, 404: ErrorOut}
INVALID = {422: ErrorOut}


# --- helpers -----------------------------------------------------------------------------------


def _club(request):
    club = panel_club(request)
    require(request, club, can_manage_forms)
    if not club_features(club)["forms"]:
        raise ApiError(404, "feature_disabled", _("This feature isn't available for this club."))
    return club


def _form(club, form_id) -> Form:
    return get_object_or_404(Form, pk=form_id, club=club)


def _send(club, form_id, send_id) -> FormSend:
    return get_object_or_404(FormSend.objects.select_related("form"), pk=send_id, club=club, form_id=form_id)


def _person(member) -> dict | None:
    return {"id": member.pk, "name": str(member)} if member else None


def _file_url(form_id, submission_id, answer) -> str | None:
    if not answer.file:
        return None
    return f"/manage/forms/{form_id}/submissions/{submission_id}/answers/{answer.pk}/file"


def _submission_out(submission: Submission) -> dict:
    return {
        "id": submission.pk,
        "send_id": submission.send_id,
        "member": _person(submission.member),
        "submitted_by": _person(submission.submitted_on_behalf_by),
        "submitted_at": submission.submitted_at,
        "values": {str(answer.field_id): answer.value for answer in submission.answers.all()},
    }


# --- submissions -------------------------------------------------------------------------------


@router.get(PREFIX + "/submissions", response={200: s.SubmissionPage, **ERRORS}, summary="Responses to a form, newest first")
def list_submissions(
    request,
    form_id: uuid.UUID,
    send_id: uuid.UUID | None = None,
    member_id: uuid.UUID | None = None,
    q: str | None = None,
    submitted_from: datetime.date | None = None,
    submitted_to: datetime.date | None = None,
    cursor: str | None = None,
    limit: int | None = None,
):
    """Every response to every send of the form, or of one send (`send_id`; one of another form or club is a 404).
    Filters: `member_id` (the member the response is for), `q` (part of that member's name), `submitted_from` /
    `submitted_to` (inclusive local dates). Keyset-paged by `submitted_at` descending; `values` maps field id to
    the stored answer."""
    club = _club(request)
    _form(club, form_id)
    submissions = Submission.objects.filter(send__club=club, send__form_id=form_id).select_related("member", "submitted_by").prefetch_related("answers")
    if send_id:
        _send(club, form_id, send_id)
        submissions = submissions.filter(send_id=send_id)
    if member_id:
        submissions = submissions.filter(member_id=member_id)
    for term in (q or "").split():
        submissions = submissions.filter(Q(member__first_name__icontains=term) | Q(member__last_name__icontains=term))
    if submitted_from:
        submissions = submissions.filter(submitted_at__date__gte=submitted_from)
    if submitted_to:
        submissions = submissions.filter(submitted_at__date__lte=submitted_to)
    page = paginate(submissions, sort_field="-submitted_at", cursor=cursor, limit=limit)
    return {"items": [_submission_out(row) for row in page.items], "next_cursor": page.next_cursor}


@router.get(PREFIX + "/submissions/{uuid:submission_id}", response={200: s.SubmissionDetailOut, **ERRORS}, summary="One response with all its answers")
def submission_detail(request, form_id: uuid.UUID, submission_id: uuid.UUID):
    club = _club(request)
    submission = get_object_or_404(Submission.objects.select_related("member", "submitted_by"), pk=submission_id, send__club=club, send__form_id=form_id)
    answers = submission.answers.select_related("field").order_by("field__order")
    return {
        "id": submission.pk,
        "send_id": submission.send_id,
        "form_id": form_id,
        "member": _person(submission.member),
        "submitted_by": _person(submission.submitted_on_behalf_by),
        "submitted_at": submission.submitted_at,
        "answers": [
            {
                "id": answer.pk,
                "field_id": answer.field_id,
                "key": answer.field.key,
                "label": answer.field.label,
                "field_type": answer.field.field_type,
                "value": answer.value,
                "display": display_value(answer.value),
                "file_url": _file_url(form_id, submission.pk, answer),
            }
            for answer in answers
        ],
    }


@router.get(PREFIX + "/submissions/{uuid:submission_id}/answers/{uuid:answer_id}/file", response={200: None, **ERRORS}, summary="Download a file answer")
def answer_file(request, form_id: uuid.UUID, submission_id: uuid.UUID, answer_id: uuid.UUID):
    """The uploaded file as an attachment (`application/octet-stream`, never rendered inline). 404 for an answer
    without a file or a file missing from storage."""
    club = _club(request)
    answer = get_object_or_404(Answer.objects.select_related("submission"), pk=answer_id, submission_id=submission_id, submission__send__club=club, submission__send__form_id=form_id)
    if not answer.file:
        raise ApiError(404, "not_found", _("No such file."))
    try:
        handle = answer.file.open("rb")
    except FileNotFoundError:
        raise ApiError(404, "not_found", _("No such file.")) from None
    response = FileResponse(handle, as_attachment=True, filename=answer.file_name, content_type="application/octet-stream")
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = "default-src 'none'; sandbox"
    patch_vary_headers(response, ["Authorization"])
    return response


# --- reporting ---------------------------------------------------------------------------------


@router.get(PREFIX + "/sends/{uuid:send_id}/report", response={200: s.ReportOut, **ERRORS}, summary="Response counts and per-choice tallies of one send")
def send_report(request, form_id: uuid.UUID, send_id: uuid.UUID):
    """The aggregation behind the web Responses page: how many responded out of the audience, one column per
    field and, for choice, multichoice and checkbox fields, how often each answer was given. The rows
    themselves are `GET /manage/forms/{form_id}/submissions?send_id=`."""
    club = _club(request)
    send = _send(club, form_id, send_id)
    report = form_report(send)
    audience = effective_members(send).count()
    return {
        "send_id": send.pk,
        "form_id": send.form_id,
        "title": send.form.title,
        "submission_count": report.count,
        "audience_count": audience,
        "not_yet_count": max(audience - report.count, 0),
        "columns": [
            {
                "field_id": column.pk,
                "key": column.key,
                "label": column.label,
                "field_type": column.field_type,
                "section": column.section,
                "is_active": column.is_active,
                "summary": dict(sorted(report.summaries[column.id].items())) if column.id in report.summaries else None,
            }
            for column in report.columns
        ],
    }


@router.get(PREFIX + "/sends/{uuid:send_id}/pending", response={200: s.PendingOut, **ERRORS}, summary="Audience members who have not responded")
def send_pending(request, form_id: uuid.UUID, send_id: uuid.UUID):
    club = _club(request)
    send = _send(club, form_id, send_id)
    members = list(members_not_yet_submitted(send).order_by("last_name", "first_name", "pk"))
    return {"items": [{"id": member.pk, "name": str(member)} for member in members], "total": len(members)}


@router.get(PREFIX + "/sends/{uuid:send_id}/export", response={200: None, **ERRORS}, summary="Responses of one send as a CSV download")
def send_export(request, form_id: uuid.UUID, send_id: uuid.UUID):
    """`text/csv` attachment, one row per response and one column per field, built synchronously like the web
    download. Spreadsheet formula triggers in free text are neutralised."""
    club = _club(request)
    send = _send(club, form_id, send_id)
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{send.form.slug}-responses.csv"'
    write_report_csv(form_report(send), response)
    return response
