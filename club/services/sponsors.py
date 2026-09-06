"""Which sponsors are currently "live" -- shared by the public API
(club/api.py, the club's own external website) and the mobile member app's
Home screen (mobile/views.py), so both read the same definition of "active"
rather than each re-deriving it.
"""

import random

from django.core.cache import cache
from django.db.models import Q
from django.utils import timezone

from ..models import Sponsor

#: Cache key template for one club's unshuffled active-sponsor list -- see
#: active_sponsors' own docstring, and club.signals' bust-on-save/delete.
SPONSOR_CACHE_KEY = "club:%s:active_sponsors"
SPONSOR_CACHE_SECONDS = 300


def invalidate_active_sponsors_cache(club_id) -> None:
    """Drop `club_id`'s cached active-sponsor list -- called from
    club.signals the moment a Sponsor is saved or deleted. A no-op when
    nothing was cached yet."""
    cache.delete(SPONSOR_CACHE_KEY % club_id)


def active_sponsors(club, *, randomize=False):
    """Sponsors currently live for ``club``: ``start_date`` has passed and
    either there's no ``end_date`` (runs indefinitely once started) or it
    hasn't passed yet. Both bounds are inclusive of today.

    The underlying query is cached, unshuffled, for SPONSOR_CACHE_SECONDS --
    hit from both the public API (every external website's own polling) and
    every mobile Home load, for rows that in practice change on the order of
    weeks. ``randomize=True`` shuffles a copy of the (possibly cached) result
    in Python after that read (e.g. for a sponsor strip that shouldn't
    always lead with the same one), rather than an ORDER BY RANDOM(), which
    sponsor counts are far too small to need, which SQLite/Postgres don't
    even express the same way, and which caching the query result would
    defeat anyway."""
    cache_key = SPONSOR_CACHE_KEY % club.pk
    sponsors = cache.get(cache_key)
    if sponsors is None:
        today = timezone.localdate()
        sponsors = list(Sponsor.objects.filter(club=club, start_date__lte=today).filter(Q(end_date__isnull=True) | Q(end_date__gte=today)).order_by("name"))
        cache.set(cache_key, sponsors, SPONSOR_CACHE_SECONDS)

    if randomize:
        sponsors = list(sponsors)
        random.shuffle(sponsors)
    return sponsors
