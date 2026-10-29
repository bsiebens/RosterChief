"""Management club settings (``/manage/settings``): the club's own identity and branding, its
onboarding requirements and the sample email / PDF renders. Club-scoped (``management``) only;
nothing from the control panel.

Same rules as management.views: identity, branding and previews are club admin only
(``ClubAdminRequiredMixin``); onboarding requirements need ``can_manage_members``
(``MemberAdminRequiredMixin``). Validation is the web forms' (``ClubSettingsForm``,
``OnboardingRequirementForm``). The logo and event background are multipart uploads on their own
sub-resources (``POST``) and are removed with ``DELETE``.
"""

import uuid

from django.http import HttpResponse
from django.utils.translation import gettext_lazy as _
from ninja import File, Router, UploadedFile

from api.core.errors import ApiError
from api.core.forms import form_error, post_data
from api.core.schemas import ErrorOut
from api.core.uploads import IMAGE_TYPES, validate_upload
from api.panel.guards import panel_club, require
from api.panel.schemas import club_settings as s
from club.models import OnboardingRequirement
from club.services.access import can_manage_members, is_club_admin
from management.email_previews import EMAIL_PREVIEWS, EMAIL_PREVIEWS_BY_KEY, render_preview
from management.forms import ClubSettingsForm, OnboardingRequirementForm
from management.pdf_previews import PDF_PREVIEWS, PDF_PREVIEWS_BY_KEY, render_pdf_preview

router = Router(tags=["panel: club settings"])

PREFIX = "/manage/settings"
ERRORS = {401: ErrorOut, 403: ErrorOut, 404: ErrorOut}
INVALID = {422: ErrorOut}
UPLOAD_ERRORS = {413: ErrorOut, 415: ErrorOut, 422: ErrorOut}

FIELDS = ("name", "legal_name", "legal_address", "legal_zip_code", "legal_city", "payment_instructions", "contact_email", "website", "primary_color", "secondary_color", "primary_text_color", "secondary_text_color")


def _admin(request):
    club = panel_club(request)
    require(request, club, is_club_admin)
    return club


def _members_admin(request):
    club = panel_club(request)
    require(request, club, can_manage_members)
    return club


def _url(request, field) -> str | None:
    return request.build_absolute_uri(field.url) if field else None


def _out(request, club) -> dict:
    values = {name: getattr(club, name) for name in ("id", "slug", "vat_id", *FIELDS)}
    return {**values, "logo_url": _url(request, club.logo), "event_background_url": _url(request, club.event_background), "public_api_url": request.build_absolute_uri("/api/v1/")}


def _current(club) -> dict:
    return {name: getattr(club, name) for name in FIELDS}


def _save(club, values: dict, files: dict | None = None, *, clear: str | None = None) -> None:
    data = dict(values)
    if clear:
        data[f"{clear}-clear"] = "on"
    form = ClubSettingsForm(data, files or {}, instance=club)
    if not form.is_valid():
        raise form_error(form)
    form.save()


# --- identity and branding ----------------------------------------------------------------------


@router.get(PREFIX, response={200: s.ClubSettingsOut, **ERRORS}, summary="The club's identity and branding")
def get_settings(request):
    """Club admins."""
    return _out(request, _admin(request))


@router.patch(PREFIX, response={200: s.ClubSettingsOut, **ERRORS, **INVALID}, summary="Edit the club's identity and branding")
def update_settings(request, payload: s.ClubSettingsPatch):
    """Send only the fields to change; an empty string clears an optional field. The slug is not editable. Club admins."""
    club = _admin(request)
    values = _current(club)
    values.update({key: ("" if value is None else value) for key, value in payload.dict(exclude_unset=True).items()})
    _save(club, values)
    club.refresh_from_db()
    return _out(request, club)


def _upload(request, field: str, upload: UploadedFile) -> dict:
    club = _admin(request)
    checked = validate_upload(upload, field=field, content_types=IMAGE_TYPES)
    _save(club, _current(club), {field: checked})
    club.refresh_from_db()
    return _out(request, club)


def _remove(request, field: str) -> dict:
    club = _admin(request)
    _save(club, _current(club), clear=field)
    club.refresh_from_db()
    return _out(request, club)


@router.post(PREFIX + "/logo", response={200: s.ClubSettingsOut, **ERRORS, **UPLOAD_ERRORS}, summary="Upload the club logo")
def upload_logo(request, logo: File[UploadedFile] = None):
    """Multipart: a PNG, JPEG, GIF or WebP `logo` (SVG is not accepted). Replaces the current one. Club admins."""
    return _upload(request, "logo", logo)


@router.delete(PREFIX + "/logo", response={200: s.ClubSettingsOut, **ERRORS}, summary="Remove the club logo")
def remove_logo(request):
    return _remove(request, "logo")


@router.post(PREFIX + "/event-background", response={200: s.ClubSettingsOut, **ERRORS, **UPLOAD_ERRORS}, summary="Upload the event background photo")
def upload_event_background(request, event_background: File[UploadedFile] = None):
    """Multipart: the `event_background` photo shown on event screens. Club admins."""
    return _upload(request, "event_background", event_background)


@router.delete(PREFIX + "/event-background", response={200: s.ClubSettingsOut, **ERRORS}, summary="Remove the event background photo")
def remove_event_background(request):
    return _remove(request, "event_background")


# --- previews -----------------------------------------------------------------------------------


@router.get(PREFIX + "/email-previews", response={200: s.PreviewListOut, **ERRORS}, summary="Sample renders of the club's emails")
def list_email_previews(request):
    """Subject and plain text of every branded email, rendered with sample data. Club admins."""
    club = _admin(request)
    items = []
    for preview in EMAIL_PREVIEWS:
        rendered = render_preview(preview, club=club, request=request)
        items.append({"key": preview.key, "label": str(preview.label), "description": str(preview.description), "subject": rendered["subject"], "text": rendered["text"]})
    return {"items": items}


@router.get(PREFIX + "/email-previews/{str:key}/render", response={200: None, **ERRORS}, summary="One email preview as an HTML document")
def render_email_preview(request, key: str):
    """The email's HTML body (`text/html`). Club admins."""
    club = _admin(request)
    preview = EMAIL_PREVIEWS_BY_KEY.get(key)
    if preview is None:
        raise ApiError(404, "not_found", _("Not found."))
    return HttpResponse(render_preview(preview, club=club, request=request)["html"], content_type="text/html; charset=utf-8")


@router.get(PREFIX + "/pdf-previews", response={200: s.PreviewListOut, **ERRORS}, summary="The PDF documents the club can preview")
def list_pdf_previews(request):
    _admin(request)
    return {"items": [{"key": preview.key, "label": str(preview.label), "description": str(preview.description)} for preview in PDF_PREVIEWS]}


@router.get(PREFIX + "/pdf-previews/{str:key}/render", response={200: None, **ERRORS}, summary="One PDF preview as the HTML it is made from")
def render_pdf(request, key: str):
    """The HTML WeasyPrint turns into the PDF (`text/html`). Club admins."""
    club = _admin(request)
    preview = PDF_PREVIEWS_BY_KEY.get(key)
    if preview is None:
        raise ApiError(404, "not_found", _("Not found."))
    return HttpResponse(render_pdf_preview(preview, club=club, request=request), content_type="text/html; charset=utf-8")


# --- onboarding requirements --------------------------------------------------------------------


def _requirement(club, requirement_id: uuid.UUID) -> OnboardingRequirement:
    requirement = OnboardingRequirement.objects.filter(club=club, pk=requirement_id).first()
    if requirement is None:
        raise ApiError(404, "not_found", _("Not found."))
    return requirement


def _requirement_out(requirement: OnboardingRequirement) -> dict:
    return {
        "id": requirement.pk,
        "name": requirement.name,
        "description": requirement.description,
        "requires_document": requirement.requires_document,
        "blocked_event_kinds": list(requirement.blocked_event_kinds),
        "is_active": requirement.is_active,
    }


def _save_requirement(club, instance: OnboardingRequirement, values: dict) -> OnboardingRequirement:
    data = post_data(values, multiple={"blocked_event_kinds": "blocked_event_kinds"}, checkboxes=("requires_document", "is_active"))
    form = OnboardingRequirementForm(data, club=club, instance=instance)
    if not form.is_valid():
        raise form_error(form)
    instance.club = club
    return form.save()


@router.get(PREFIX + "/onboarding-requirements", response={200: s.OnboardingRequirementListOut, **ERRORS}, summary="Onboarding requirements")
def list_requirements(request):
    """What the club requires from every member after sign-up, by name. No pagination. Club admins and member admins."""
    club = _members_admin(request)
    return {"items": [_requirement_out(r) for r in OnboardingRequirement.objects.filter(club=club)]}


@router.post(PREFIX + "/onboarding-requirements", response={201: s.OnboardingRequirementOut, **ERRORS, **INVALID}, summary="Add an onboarding requirement")
def create_requirement(request, payload: s.OnboardingRequirementIn):
    club = _members_admin(request)
    return 201, _requirement_out(_save_requirement(club, OnboardingRequirement(club=club), payload.dict()))


@router.patch(PREFIX + "/onboarding-requirements/{uuid:requirement_id}", response={200: s.OnboardingRequirementOut, **ERRORS, **INVALID}, summary="Edit an onboarding requirement")
def update_requirement(request, requirement_id: uuid.UUID, payload: s.OnboardingRequirementPatch):
    """Send only the fields to change."""
    club = _members_admin(request)
    requirement = _requirement(club, requirement_id)
    values = _requirement_out(requirement)
    values.pop("id")
    values.update({key: value for key, value in payload.dict(exclude_unset=True).items() if value is not None})
    return _requirement_out(_save_requirement(club, requirement, values))


@router.delete(PREFIX + "/onboarding-requirements/{uuid:requirement_id}", response={204: None, **ERRORS}, summary="Delete an onboarding requirement")
def delete_requirement(request, requirement_id: uuid.UUID):
    """Existing checklists keep whatever was already recorded for it."""
    club = _members_admin(request)
    _requirement(club, requirement_id).delete()
    return 204, None
