"""Public read-only sponsors endpoint -- see api/urls.py for how this is
mounted.
"""

import uuid
from datetime import date

from ninja import Router, Schema

from api.errors import require_club

from .services.sponsors import active_sponsors

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


def _to_sponsor_out(sponsor, request) -> SponsorOut:
    return SponsorOut(
        id=sponsor.pk,
        name=sponsor.name,
        logo_url=request.build_absolute_uri(sponsor.logo.url) if sponsor.logo else None,
        logo_width=sponsor.logo_width,
        logo_height=sponsor.logo_height,
        url=sponsor.url or None,
        start_date=sponsor.start_date,
        end_date=sponsor.end_date,
    )


@router.get("/", response=list[SponsorOut], summary="Active sponsors")
def list_sponsors(request, randomize: bool = False):
    """See club.services.sponsors.active_sponsors for what "active" means and
    why `randomize=true` shuffles in Python rather than in SQL."""
    club = require_club(request)
    sponsors = active_sponsors(club, randomize=randomize)
    return [_to_sponsor_out(sponsor, request) for sponsor in sponsors]
