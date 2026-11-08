"""Forms (``/forms``, ``/form-sends``, ``/form-submissions``, ``/form-answers``): everything needs ``forms.manage_forms``
(403 otherwise). Rules live in ``formbuilder.services``."""

import uuid

from django.db.models import Q
from django.http import FileResponse, HttpResponse
from ninja import Query, Router
from ninja.errors import HttpError

from api.auth import AUTHENTICATORS
from api.schemas_forms import DuplicateIn, FieldsIn, FormDetail, FormIn, FormListOut, FormPatch, ReportOut, SendDetail, SendIn, SendListOut, SendPatch
from formbuilder.models import Answer, Form, Submission
from formbuilder.services import audience as aud
from formbuilder.services import management as svc
from formbuilder.services import reporting
from members.services import access

router = Router(auth=AUTHENTICATORS)
sends_router = Router(auth=AUTHENTICATORS)
submissions_router = Router(auth=AUTHENTICATORS)
answers_router = Router(auth=AUTHENTICATORS)

DOWNLOAD_HEADERS = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'; sandbox"}


def _gate(request):
    access.require_manage_forms(request.user)


def _ref(member) -> dict | None:
    return {"id": member.pk, "name": str(member)} if member is not None else None


def _field(f) -> dict:
    return {"id": f.pk, "key": f.key, "label": f.label, "field_type": f.field_type, "required": f.required, "help_text": f.help_text, "section": f.section, "order": f.order, "is_active": f.is_active, "options": f.options}


def _form_item(f) -> dict:
    return {
        "id": f.pk, "title": f.title, "slug": f.slug, "description": f.description, "is_active": f.is_active, "login_required": f.login_required,
        "field_count": f.field_count, "send_count": f.send_count, "response_count": f.response_count, "created": f.created,
    }  # fmt: skip


def _form_detail(form_id) -> dict:
    f = svc.get_form(form_id)
    return {**_form_item(f), "fields": [_field(x) for x in f.fields.order_by("order")]}


def _get_form(form_id) -> Form:
    f = Form.objects.filter(pk=form_id).first()
    if f is None:
        raise HttpError(404, "Not found.")
    return f


def _audience_label(send) -> str:
    parts = []
    if send.club_wide:
        parts.append("Whole club")
    for noun in (send.teams.all(), send.groups.all()):
        names = [x.name for x in noun]
        if names:
            parts.append(", ".join(names[:3]) + (f" +{len(names) - 3}" if len(names) > 3 else ""))
    invited = len(send.invited_members.all())
    if invited:
        parts.append(f"+{invited} invited")
    return " · ".join(parts) or ("Public link" if send.is_public else "No audience")


def _send_item(send, counts=None) -> dict:
    count = len(counts[send.pk]) if counts is not None else aud.audience_count(send)
    return {
        "id": send.pk, "form_id": send.form_id, "form_title": send.form.title, "audience_label": _audience_label(send),
        "opens_at": send.opens_at, "closes_at": send.closes_at, "is_active": send.is_active, "is_public": send.is_public,
        "status": aud.send_status(send), "audience_count": count, "response_count": send.response_count, "created": send.created,
        "created_by": _ref(send.created_by),
    }  # fmt: skip


def _send_detail(send_id) -> dict:
    send = svc.get_send(send_id)
    return {
        **_send_item(send),
        "teams": [{"id": t.pk, "name": t.name} for t in send.teams.all()], "groups": [{"id": g.pk, "name": g.name} for g in send.groups.all()],
        "invited_members": [_ref(m) for m in send.invited_members.all()], "excluded_members": [_ref(m) for m in send.excluded_members.all()],
        "club_wide": send.club_wide, "season": {"id": send.season.pk, "name": send.season.name} if send.season else None,
        "max_submissions_per_user": send.max_submissions_per_user, "public_token": send.public_token,
        "public_url_path": f"/f/{send.public_token}", "reminder_sent_at": send.reminder_sent_at,
    }  # fmt: skip


def _send_page(qs, limit, offset) -> dict:
    count = qs.count()
    page = list(qs[offset : offset + limit])
    counts = aud.audience_ids_by_send(page)
    return {"count": count, "results": [_send_item(s, counts) for s in page]}


def _get_send(send_id):
    send = svc.get_send(send_id)
    if send is None:
        raise HttpError(404, "Not found.")
    return send


# --- forms ---------------------------------------------------------------------------------------------------------


@router.get("", response=FormListOut, summary="List forms", operation_id="list_forms")
def list_forms(request, q: str | None = None, active: bool | None = None, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    _gate(request)
    qs = svc.forms_queryset()
    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(description__icontains=q))
    if active is not None:
        qs = qs.filter(is_active=active)
    return {"count": qs.count(), "results": [_form_item(f) for f in qs[offset : offset + limit]]}


@router.post("", response={201: FormDetail}, summary="Create a form", operation_id="create_form")
def create_form(request, payload: FormIn):
    _gate(request)
    return 201, _form_detail(svc.create_form(request.user, **payload.dict()).pk)


@router.get("/{form_id}", response=FormDetail, summary="One form with its fields", operation_id="get_form")
def get_form(request, form_id: uuid.UUID):
    _gate(request)
    return _form_detail(_get_form(form_id).pk)


@router.patch("/{form_id}", response=FormDetail, summary="Edit a form", operation_id="update_form")
def update_form(request, form_id: uuid.UUID, payload: FormPatch):
    _gate(request)
    form = svc.update_form(request.user, _get_form(form_id), **payload.dict(exclude_unset=True))
    return _form_detail(form.pk)


@router.delete("/{form_id}", response={204: None}, summary="Delete a form (409 has_responses when answered)", operation_id="delete_form")
def delete_form(request, form_id: uuid.UUID):
    _gate(request)
    svc.delete_form(request.user, _get_form(form_id))
    return 204, None


@router.post("/{form_id}/duplicate", response={201: FormDetail}, summary="Copy a form with its fields", operation_id="duplicate_form")
def duplicate_form(request, form_id: uuid.UUID, payload: DuplicateIn):
    _gate(request)
    return 201, _form_detail(svc.duplicate_form(request.user, _get_form(form_id), title=payload.title).pk)


@router.put("/{form_id}/fields", response=FormDetail, summary="Replace the whole ordered field list", operation_id="replace_form_fields")
def replace_fields(request, form_id: uuid.UUID, payload: FieldsIn):
    _gate(request)
    form = _get_form(form_id)
    svc.replace_fields(request.user, form, [f.dict() for f in payload.fields])
    return _form_detail(form.pk)


@router.get("/{form_id}/sends", response=SendListOut, summary="Sends of a form", operation_id="list_form_sends_of_form")
def list_form_sends_of_form(request, form_id: uuid.UUID, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    _gate(request)
    return _send_page(svc.sends_queryset().filter(form=_get_form(form_id)), limit, offset)


@router.post("/{form_id}/sends", response={201: SendDetail}, summary="Send a form to an audience", operation_id="create_form_send")
def create_send(request, form_id: uuid.UUID, payload: SendIn):
    _gate(request)
    send = svc.create_send(request.user, _get_form(form_id), **payload.dict())
    return 201, _send_detail(send.pk)


# --- sends ---------------------------------------------------------------------------------------------------------


@sends_router.get("", response=SendListOut, summary="List sends of all forms", operation_id="list_form_sends")
def list_sends(request, status: str = "all", limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    """``open`` = switched on and not past its close (scheduled ones included); ``closed`` = switched off or past its close."""
    _gate(request)
    return _send_page(svc.filter_sends_by_status(svc.sends_queryset(), status), limit, offset)


@sends_router.get("/{send_id}", response=SendDetail, summary="One send", operation_id="get_form_send")
def get_send(request, send_id: uuid.UUID):
    _gate(request)
    return _send_detail(_get_send(send_id).pk)


@sends_router.patch("/{send_id}", response=SendDetail, summary="Edit a send", operation_id="update_form_send")
def update_send(request, send_id: uuid.UUID, payload: SendPatch):
    _gate(request)
    send = svc.update_send(request.user, _get_send(send_id), **payload.dict(exclude_unset=True))
    return _send_detail(send.pk)


@sends_router.delete("/{send_id}", response={204: None}, summary="Delete a send (409 has_responses when answered)", operation_id="delete_form_send")
def delete_send(request, send_id: uuid.UUID):
    _gate(request)
    svc.delete_send(request.user, _get_send(send_id))
    return 204, None


@sends_router.post("/{send_id}/close", response=SendDetail, summary="Close a send now", operation_id="close_form_send")
def close_send(request, send_id: uuid.UUID):
    _gate(request)
    return _send_detail(svc.close_send(request.user, _get_send(send_id)).pk)


@sends_router.post("/{send_id}/reopen", response=SendDetail, summary="Reopen a send", operation_id="reopen_form_send")
def reopen_send(request, send_id: uuid.UUID):
    _gate(request)
    return _send_detail(svc.reopen_send(request.user, _get_send(send_id)).pk)


def _report_data(send) -> reporting.FormReport:
    return reporting.form_report(send)


@sends_router.get("/{send_id}/report", response=ReportOut, summary="Responses of a send", operation_id="form_send_report")
def report(request, send_id: uuid.UUID):
    _gate(request)
    send = _get_send(send_id)
    rep = _report_data(send)
    rows = []
    for row in rep.rows:
        s = row.submission
        rows.append({
            "submission_id": s.pk, "member": _ref(s.member), "submitted_by": _ref(s.submitted_by), "on_behalf": s.submitted_on_behalf_by is not None,
            "submitted_at": s.submitted_at, "values": {str(k): v for k, v in row.values.items()},
            "files": {str(k): {"answer_id": a.pk, "name": a.file_name} for k, a in row.files.items()},
        })  # fmt: skip
    return {
        "send": _send_item(send), "columns": [_field(c) for c in rep.columns], "rows": rows,
        "summaries": {str(k): v for k, v in rep.summaries.items()}, "count": rep.count, "not_responded": [_ref(m) for m in rep.not_responded],
    }  # fmt: skip


@sends_router.get("/{send_id}/report.csv", summary="Responses of a send as CSV", operation_id="form_send_report_csv")
def report_csv(request, send_id: uuid.UUID):
    _gate(request)
    send = _get_send(send_id)
    response = HttpResponse(reporting.report_csv(_report_data(send)), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{send.form.slug}-responses.csv"'
    return response


# --- submissions and files -----------------------------------------------------------------------------------------


@submissions_router.delete("/{submission_id}", response={204: None}, summary="Delete a response", operation_id="delete_form_submission")
def delete_submission(request, submission_id: uuid.UUID):
    _gate(request)
    submission = Submission.objects.filter(pk=submission_id).first()
    if submission is None:
        raise HttpError(404, "Not found.")
    svc.delete_submission(request.user, submission)
    return 204, None


@answers_router.get("/{answer_id}/file", summary="Download an uploaded file", operation_id="download_form_answer_file")
def answer_file(request, answer_id: uuid.UUID):
    _gate(request)
    answer = Answer.objects.filter(pk=answer_id).first()
    if answer is None:
        raise HttpError(404, "Not found.")
    try:
        handle = svc.open_answer_file(request.user, answer)
    except FileNotFoundError:
        raise HttpError(404, "The file is gone.") from None
    response = FileResponse(handle, as_attachment=True, filename=answer.file_name or "file", content_type="application/octet-stream")
    for header, value in DOWNLOAD_HEADERS.items():
        response[header] = value
    return response
