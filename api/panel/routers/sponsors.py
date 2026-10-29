"""Management sponsors (``/manage/sponsors``): the API behind the /manage sponsor screens.

Club admins only (``ClubAdminRequiredMixin``). Validation is ``management.forms.SponsorForm``.
Create and update are multipart (the optional `logo` beside the text fields); update is a POST on the
item because Django does not parse multipart PUT/PATCH bodies, so it replaces all fields.
"""

import datetime
import uuid

from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from ninja import File, Form, Router, UploadedFile

from api.core.errors import ApiError
from api.core.forms import form_error
from api.core.idempotency import idempotent
from api.core.pagination import paginate_keyset
from api.core.schemas import ErrorOut
from api.core.uploads import IMAGE_TYPES, validate_upload
from api.panel.guards import panel_club, require
from api.panel.schemas import sponsors as s
from club.models import Sponsor
from club.services.access import is_club_admin
from management.forms import SponsorForm

router = Router(tags=["panel: sponsors"])

PREFIX = "/manage/sponsors"
ERRORS = {401: ErrorOut, 403: ErrorOut, 404: ErrorOut}
UPLOAD_ERRORS = {413: ErrorOut, 415: ErrorOut, 422: ErrorOut}


def _admin(request):
    club = panel_club(request)
    require(request, club, is_club_admin)
    return club


def _get(club, sponsor_id: uuid.UUID) -> Sponsor:
    sponsor = Sponsor.objects.filter(club=club, pk=sponsor_id).first()
    if sponsor is None:
        raise ApiError(404, "not_found", _("Not found."))
    return sponsor


def _out(request, sponsor: Sponsor) -> dict:
    today = timezone.localdate()
    return {
        "id": sponsor.pk,
        "name": sponsor.name,
        "url": sponsor.url,
        "start_date": sponsor.start_date,
        "end_date": sponsor.end_date,
        "logo_url": request.build_absolute_uri(sponsor.logo.url) if sponsor.logo else None,
        "logo_width": sponsor.logo_width,
        "logo_height": sponsor.logo_height,
        "is_active": sponsor.start_date <= today and (sponsor.end_date is None or sponsor.end_date >= today),
    }


def _save(club, instance: Sponsor, *, name, url, start_date, end_date, logo: UploadedFile | None, clear_logo: bool = False) -> Sponsor:
    files = {}
    if logo is not None:
        files["logo"] = validate_upload(logo, field="logo", content_types=IMAGE_TYPES)
    data = {"name": name, "url": url, "start_date": start_date.isoformat() if start_date else "", "end_date": end_date.isoformat() if end_date else ""}
    if clear_logo and logo is None:
        data["logo-clear"] = "on"
    instance.club = club
    form = SponsorForm(data, files, instance=instance)
    if not form.is_valid():
        raise form_error(form)
    return form.save()


@router.get(PREFIX, response={200: s.SponsorListOut, **ERRORS}, summary="The club's sponsors")
def list_sponsors(request, cursor: str | None = None, limit: int | None = None):
    """By name, keyset paged. Club admins."""
    club = _admin(request)
    page = paginate_keyset(Sponsor.objects.filter(club=club), fields=["name", "pk"], cursor=cursor, limit=limit)
    return {"items": [_out(request, sponsor) for sponsor in page.items], "next_cursor": page.next_cursor}


@router.post(PREFIX, response={201: s.SponsorOut, **ERRORS, **UPLOAD_ERRORS}, summary="Add a sponsor")
def create_sponsor(request, name: Form[str], start_date: Form[datetime.date], url: Form[str] = "", end_date: Form[datetime.date] = None, logo: File[UploadedFile] = None):
    """Multipart: `name`, `start_date`, optional `url`, `end_date` (not before the start) and `logo` image. Honours `Idempotency-Key`. Club admins."""
    club = _admin(request)

    def work():
        sponsor = _save(club, Sponsor(club=club), name=name, url=url, start_date=start_date, end_date=end_date, logo=logo)
        return 201, _out(request, sponsor)

    return idempotent(request, work)


@router.get(PREFIX + "/{uuid:sponsor_id}", response={200: s.SponsorOut, **ERRORS}, summary="One sponsor")
def get_sponsor(request, sponsor_id: uuid.UUID):
    club = _admin(request)
    return _out(request, _get(club, sponsor_id))


@router.post(PREFIX + "/{uuid:sponsor_id}", response={200: s.SponsorOut, **ERRORS, **UPLOAD_ERRORS}, summary="Update a sponsor")
def update_sponsor(
    request,
    sponsor_id: uuid.UUID,
    name: Form[str],
    start_date: Form[datetime.date],
    url: Form[str] = "",
    end_date: Form[datetime.date] = None,
    logo: File[UploadedFile] = None,
    clear_logo: Form[bool] = False,
):
    """Multipart POST (all fields are replaced): a new `logo` replaces the old one, `clear_logo=true` removes it, no logo part keeps it. Club admins."""
    club = _admin(request)
    sponsor = _get(club, sponsor_id)
    return _out(request, _save(club, sponsor, name=name, url=url, start_date=start_date, end_date=end_date, logo=logo, clear_logo=clear_logo))


@router.delete(PREFIX + "/{uuid:sponsor_id}", response={204: None, **ERRORS}, summary="Delete a sponsor")
def delete_sponsor(request, sponsor_id: uuid.UUID):
    club = _admin(request)
    _get(club, sponsor_id).delete()
    return 204, None
