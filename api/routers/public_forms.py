"""``GET`` / ``POST /public/forms/{token}``: fill in a form through a public link, no login. Only sends that are public
(``FormSend.is_public``) of a form that needs no login answer; everything else (unknown token, not public, login required,
the platform host) is a plain 404. The page gets the questions and the send's state, never other responses, the audience,
counts or stored files. A submission is anonymous (``submit_form(send, None, ...)``); the shared ``Invalid`` handler turns a
refusal into 422 ``{detail, code, errors: {field key: [messages]}}`` (``code``: ``closed``, ``not_open_yet``, ``inactive``,
``invalid``, ...).

Abuse protection: this repo had no throttling, so POSTs are limited per client address and link with the Django cache
(``MAX_POSTS`` per ``WINDOW_SECONDS``; every attempt counts, valid or not). Over the limit: 429 with ``Retry-After``."""

from django.core.cache import cache
from django.http import HttpResponse
from django_tenants.utils import get_public_schema_name
from ninja import Router
from ninja.errors import HttpError

from api.schemas_public_forms import PublicFormOut, PublicFormSubmittedOut
from events.services.attachments import ALLOWED_EXTENSIONS, MAX_ATTACHMENT_BYTES
from formbuilder.models import FormSend
from formbuilder.services import audience
from formbuilder.services.options import field_choices
from formbuilder.services.submission import submit_form

router = Router()

MAX_POSTS = 10
WINDOW_SECONDS = 600


def _find_send(request, token: str) -> FormSend:
    if request.tenant.schema_name == get_public_schema_name():
        raise HttpError(404, "Not found.")
    send = FormSend.objects.select_related("form").filter(public_token=token, is_public=True, form__login_required=False).first()
    if send is None:
        raise HttpError(404, "Not found.")
    return send


def _client_address(request) -> str:
    """The address the SvelteKit proxy saw (it sets ``X-Forwarded-For`` itself); the last entry is the nearest hop."""
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return forwarded.split(",")[-1].strip() or request.META.get("REMOTE_ADDR", "") or "unknown"


def _hit(request, token: str) -> int | None:
    """Count this POST; ``None`` when allowed, else the seconds to wait."""
    key = f"formbuilder:public-post:{request.tenant.schema_name}:{token}:{_client_address(request)}"
    if cache.add(key, 1, WINDOW_SECONDS):
        return None
    try:
        count = cache.incr(key)
    except ValueError:  # expired between add and incr
        cache.add(key, 1, WINDOW_SECONDS)
        return None
    return None if count <= MAX_POSTS else WINDOW_SECONDS


def _state(send: FormSend) -> dict:
    status = audience.send_status(send)
    return {"status": "not_open_yet" if status == "scheduled" else status, "opens_at": send.opens_at, "closes_at": send.closes_at}


def _field(f) -> dict:
    options = [{"value": value, "label": label} for value, label in field_choices(f)] if f.field_type in ("choice", "multichoice") else []
    return {"id": f.pk, "key": f.key, "label": f.label, "type": f.field_type, "required": f.required, "help_text": f.help_text, "section": f.section, "options": options}


@router.get("/{token}", auth=None, response=PublicFormOut, summary="A form behind a public link", operation_id="public_form")
def get_form(request, token: str):
    send = _find_send(request, token)
    fields = send.form.fields.filter(is_active=True).order_by("order")
    return {
        "title": send.form.title,
        "description": send.form.description,
        "state": _state(send),
        "fields": [_field(f) for f in fields],
        "max_file_mb": MAX_ATTACHMENT_BYTES // (1024 * 1024),
        "allowed_file_extensions": sorted(ALLOWED_EXTENSIONS),
    }


@router.post("/{token}", auth=None, response={201: PublicFormSubmittedOut}, summary="Submit a form through a public link", operation_id="public_form_submit")
def submit(request, token: str):
    """Multipart / form-encoded: every field by its ``key`` (a multichoice repeats the key), file fields as files."""
    wait = _hit(request, token)
    if wait is not None:
        response = HttpResponse(b'{"detail": "Too many attempts. Try again later.", "code": "rate_limited"}', status=429, content_type="application/json")
        response["Retry-After"] = str(wait)
        return response
    send = _find_send(request, token)
    submit_form(send, None, request.POST, files=request.FILES)
    return 201, {"submitted": True}
