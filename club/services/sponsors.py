"""Which sponsors are currently "live" -- shared by the public API
(club/api.py, the club's own external website) and the mobile member app's
Home screen (mobile/views.py), so both read the same definition of "active"
rather than each re-deriving it.
"""

import random

from django.db.models import Q
from django.utils import timezone

from ..models import Sponsor


def active_sponsors(club, *, randomize=False):
    """Sponsors currently live for ``club``: ``start_date`` has passed and
    either there's no ``end_date`` (runs indefinitely once started) or it
    hasn't passed yet. Both bounds are inclusive of today.

    ``randomize=True`` shuffles the result (e.g. for a sponsor strip that
    shouldn't always lead with the same one) -- shuffled in Python after a
    stable-ordered fetch rather than an ORDER BY RANDOM(), which sponsor
    counts are far too small to need and which SQLite/Postgres don't even
    express the same way.
    """
    today = timezone.localdate()
    sponsors = list(Sponsor.objects.filter(club=club, start_date__lte=today).filter(Q(end_date__isnull=True) | Q(end_date__gte=today)).order_by("name"))
    if randomize:
        random.shuffle(sponsors)
    return sponsors
