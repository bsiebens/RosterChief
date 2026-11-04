"""Club settings API (``/api/v1/club/settings``): details, branding, logo and the document previews. Settings permission only (403),
and only inside a club (404 on the platform host). Writes go through ``club.services_settings``."""

from urllib.parse import quote

from django.http import HttpResponse
from django_countries import countries
from django_tenants.utils import get_public_schema_name
from ninja import File, Router, UploadedFile
from ninja.errors import HttpError

from api.schemas_club_settings import ClubSettingsOut, ClubSettingsPatch, PreviewOut
from club import previews
from club import services_settings as svc
from members.services import access

router = Router()


def _club(request):
    """Permission first (403), then the tenant club; the platform schema has no club settings (404)."""
    access.require_settings(request.user)
    club = request.tenant
    if club.schema_name == get_public_schema_name():
        raise HttpError(404, "Not found.")
    return club


@router.get("/settings", response=ClubSettingsOut, summary="The club's details and branding (settings permission)")
def get_settings(request):
    return svc.settings_out(_club(request))


@router.get("/settings/countries", response=list[dict], summary="Countries (ISO code and name) for the address field (settings permission)")
def list_countries(request):
    _club(request)
    return [{"code": c.code, "name": str(c.name)} for c in countries]


@router.patch("/settings", response=ClubSettingsOut, summary="Edit the club's details and branding (settings permission)")
def patch_settings(request, payload: ClubSettingsPatch):
    """Only sent fields change. 422 ``{detail, code, errors}`` with per-field messages; an empty string clears a colour."""
    club = _club(request)
    svc.update_settings(request.user, club, payload.dict(exclude_unset=True))
    return svc.settings_out(club)


@router.post("/settings/logo", response=ClubSettingsOut, summary="Upload the club logo (settings permission)")
def upload_logo(request, file: File[UploadedFile]):
    """Multipart, field ``file``: JPEG, PNG or WebP up to 5 MB (422 on field ``file`` otherwise)."""
    club = _club(request)
    svc.set_logo(request.user, club, file)
    return svc.settings_out(club)


@router.delete("/settings/logo", response=ClubSettingsOut, summary="Remove the club logo (settings permission)")
def delete_logo(request):
    club = _club(request)
    svc.remove_logo(request.user, club)
    return svc.settings_out(club)


@router.get("/settings/previews", response=list[PreviewOut], summary="Club-branded outgoing documents (settings permission)")
def list_previews(request):
    _club(request)
    return [p.__dict__ for p in previews.PREVIEWS]


def _preview(key: str) -> previews.Preview:
    preview = previews.BY_KEY.get(key)
    if preview is None:
        raise HttpError(404, "Not found.")
    return preview


@router.get("/settings/previews/{key}/html", response={200: None}, summary="HTML of a document, with the saved branding (settings permission)")
def preview_html(request, key: str, lang: str = "en"):
    """``text/html``; the subject is in the url-encoded ``X-Preview-Subject`` header. ``lang`` (en, nl, fr) picks the language."""
    _club(request)
    preview = _preview(key)
    subject, html = previews.render_email(request, preview.key, lang if lang in previews.LANGUAGES else "en")
    response = HttpResponse(html, content_type="text/html; charset=utf-8")
    response["X-Preview-Subject"] = quote(subject)
    response["Access-Control-Expose-Headers"] = "X-Preview-Subject"
    return response


@router.get("/settings/previews/{key}/file", response={200: None}, summary="PDF of a document (admin; kind=pdf only)")
def preview_file(request, key: str, lang: str = "en"):
    """``application/pdf``. 404 for an unknown key or a document that is not a PDF; 503 when PDF rendering is unavailable."""
    _club(request)
    preview = _preview(key)
    if preview.kind != "pdf":
        raise HttpError(404, "Not found.")
    _subject, html = previews.render_email(request, preview.key, lang if lang in previews.LANGUAGES else "en")
    try:
        pdf = previews.render_pdf(html)
    except RuntimeError as e:
        raise HttpError(503, str(e)) from e
    return HttpResponse(pdf, content_type="application/pdf")
