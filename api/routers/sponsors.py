"""Club sponsorship management. The legacy public sponsors endpoint remains read-only."""

from datetime import date
from uuid import UUID

from django.shortcuts import get_object_or_404
from django.utils import timezone
from django_tenants.utils import get_public_schema_name
from ninja import File, Form, Router, Schema, UploadedFile
from ninja.errors import HttpError

from members.services import access
from sponsoring import services
from sponsoring.models import Sponsor

router = Router()


class SponsorIn(Schema):
    name: str
    url: str = ""
    start_date: date
    end_date: date | None = None
    remove_logo: bool = False


class SponsorOut(Schema):
    id: UUID
    name: str
    url: str
    logo_url: str | None
    start_date: date
    end_date: date | None
    status: str


def _require_club(request):
    if request.tenant.schema_name == get_public_schema_name():
        raise HttpError(404, "Not found.")


def _item(sponsor, today):
    status = "upcoming" if sponsor.start_date > today else "ended" if sponsor.end_date and sponsor.end_date < today else "active"
    return {
        "id": sponsor.pk, "name": sponsor.name, "url": sponsor.url, "logo_url": sponsor.logo.url if sponsor.logo else None,
        "start_date": sponsor.start_date, "end_date": sponsor.end_date, "status": status,
    }


@router.get("", response=list[SponsorOut], summary="List the club's sponsors")
def list_sponsors(request):
    _require_club(request)
    access.require_sponsors(request.user)
    today = timezone.localdate()
    return [_item(sponsor, today) for sponsor in Sponsor.objects.all()]


@router.get("/{sponsor_id}", response=SponsorOut, summary="View one sponsor")
def get_sponsor(request, sponsor_id: UUID):
    _require_club(request)
    access.require_sponsors(request.user)
    return _item(get_object_or_404(Sponsor, pk=sponsor_id), timezone.localdate())


def _save(request, payload, logo, sponsor_id=None):
    _require_club(request)
    values = {"name": payload.name.strip(), "url": payload.url.strip(), "start_date": payload.start_date, "end_date": payload.end_date}
    sponsor = services.save_sponsor(request.user, values, sponsor_id=sponsor_id, logo=logo, remove_logo=payload.remove_logo)
    return _item(sponsor, timezone.localdate())


@router.post("", response={201: SponsorOut}, summary="Add a sponsor and optionally upload its logo")
def create_sponsor(request, payload: Form[SponsorIn], logo: File[UploadedFile | None] = None):
    return 201, _save(request, payload, logo)


@router.post("/{sponsor_id}", response=SponsorOut, summary="Edit a sponsor and optionally replace or clear its logo")
def update_sponsor(request, sponsor_id: UUID, payload: Form[SponsorIn], logo: File[UploadedFile | None] = None):
    return _save(request, payload, logo, sponsor_id)


@router.delete("/{sponsor_id}", response={204: None}, summary="Delete a sponsor")
def delete_sponsor(request, sponsor_id: UUID):
    _require_club(request)
    services.delete_sponsor(request.user, sponsor_id)
    return 204, None
