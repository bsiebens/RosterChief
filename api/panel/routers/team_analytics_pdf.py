"""Management API: the team analytics as a PDF download (API.md §17).

``GET /manage/teams/{team_id}/analytics/pdf`` is the file counterpart of ``GET /manage/teams/{team_id}/analytics``
(team_extras router) and of the web ``TeamAnalyticsPdfView``: same visibility (feature on, ice hockey club,
the team page's rule), same filters, same numbers. The context is built by teams.services.analytics, the PDF
by management.pdf.
"""

import uuid
from typing import Annotated

from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from ninja import Query, Router

from api.core.errors import ApiError
from api.core.schemas import ErrorOut
from api.panel.guards import panel_club
from api.panel.routers.team_extras import _analytics_team
from club.models import Season
from club.services.access import current_season
from events.services.game_stats import SCOPES
from management.pdf import PDFExportError, team_analytics_pdf
from teams.services.analytics import analytics_pdf_context, analytics_pdf_filename

router = Router(tags=["panel: team analytics"])

ERRORS = {401: ErrorOut, 403: ErrorOut, 404: ErrorOut}


@router.get("/manage/teams/{uuid:team_id}/analytics/pdf", response={200: None, **ERRORS, 409: ErrorOut, 503: ErrorOut}, summary="A team's analytics as a PDF download")
def analytics_pdf(
    request,
    team_id: uuid.UUID,
    season_id: uuid.UUID | None = None,
    games: Annotated[str, Query(description="`all` (default), `home`, `away` or `last5`.")] = "all",
    friendlies: bool = True,
    competition: str = "",
):
    """`application/pdf` (attachment) with the filtered analytics of `GET .../analytics`. 404 where the
    "analytics" feature is off or the club's sport has none; 409 `no_games` when there are no finished games
    with a score to export; 503 `pdf_unavailable` without the PDF libraries."""
    club = panel_club(request)
    team = _analytics_team(request, club, team_id)
    season = get_object_or_404(Season.objects.filter(club=club), pk=season_id) if season_id else current_season(club)
    scope = games if games in SCOPES else "all"
    context = analytics_pdf_context(team, club, season, scope, friendlies, competition, timezone.now())
    if context is None:
        raise ApiError(409, "no_games", _("There are no finished games with a score to export."))
    try:
        pdf = team_analytics_pdf(context)
    except PDFExportError as error:
        raise ApiError(503, "pdf_unavailable", str(error)) from None
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{analytics_pdf_filename(team, season)}"'
    return response
