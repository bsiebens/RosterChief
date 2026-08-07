"""Public read-only sponsors endpoint -- see api/urls.py for how this is
mounted.
"""

import random
import uuid
from datetime import date

from django.db.models import Q
from django.utils import timezone
from ninja import Router, Schema

from api.errors import require_club

from .models import Sponsor

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
    """Sponsors currently "live": start_date has passed and either there's no
    end_date (runs indefinitely once started) or it hasn't passed yet. Both
    bounds are inclusive of today.

    `randomize=true` shuffles the result (e.g. for a sponsor strip that
    shouldn't always lead with the same one) -- shuffled in Python after a
    stable-ordered fetch rather than an ORDER BY RANDOM(), which sponsor
    counts are far too small to need and which SQLite/Postgres don't even
    express the same way."""
    club = require_club(request)
    today = timezone.localdate()

    sponsors = list(Sponsor.objects.filter(club=club, start_date__lte=today).filter(Q(end_date__isnull=True) | Q(end_date__gte=today)).order_by("name"))
    if randomize:
        random.shuffle(sponsors)

    return [_to_sponsor_out(sponsor, request) for sponsor in sponsors]
