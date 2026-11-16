from django.utils import timezone
from django_tenants.utils import get_public_schema_name
from ninja.errors import HttpError

from events.models import Season


def require_club(request):
    club = getattr(request, "tenant", None)
    if club is None or club.schema_name == get_public_schema_name():
        raise HttpError(404, "No club found for this host.")
    return club


def current_season():
    return Season.get_current(timezone.localdate())


def season_label(season):
    return f"{season.start_date:%y}-{season.end_date:%y}"
