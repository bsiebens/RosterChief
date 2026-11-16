import random
import uuid
from datetime import date

from ninja import Router, Schema

from sponsoring.models import Sponsor

from .scope import require_club

router = Router(tags=["sponsors"])


class SponsorOut(Schema):
    id: uuid.UUID
    name: str
    logo_url: str | None
    logo_width: int | None
    logo_height: int | None
    url: str | None
    start_date: date
    end_date: date | None


@router.get("/", response=list[SponsorOut], summary="Active sponsors", operation_id="club_api_list_sponsors")
def list_sponsors(request, randomize: bool = False):
    require_club(request)
    sponsors = list(Sponsor.active())
    if randomize:
        random.shuffle(sponsors)
    return [
        SponsorOut(
            id=sponsor.pk,
            name=sponsor.name,
            logo_url=request.build_absolute_uri(sponsor.logo.url) if sponsor.logo else None,
            logo_width=sponsor.logo_width,
            logo_height=sponsor.logo_height,
            url=sponsor.url or None,
            start_date=sponsor.start_date,
            end_date=sponsor.end_date,
        )
        for sponsor in sponsors
    ]
