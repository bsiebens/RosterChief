"""Dashboard statistics (computed across club schemas, cached for two minutes)."""

from uuid import UUID

from django.shortcuts import get_object_or_404
from ninja import Router

from club.models import Club
from controlpanel import stats

router = Router(tags=["statistics"])


@router.get("/stats/dashboard", summary="Platform totals, attention list, funnel, plan and feature adoption, charts and clubs by risk")
def dashboard(request, refresh: bool = False):
    return stats.snapshot(refresh=refresh)


@router.get("/clubs/{club_id}/stats", summary="Numbers for one club")
def club_stats(request, club_id: UUID):
    return stats.club_stats(get_object_or_404(Club.objects.exclude(schema_name="public"), pk=club_id))
