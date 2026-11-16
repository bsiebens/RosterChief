"""``GET /public/player-stats/{token}``: one person's game stats for a personal public link, no login. Only the game
numbers (the same figures as the "Game stats" part of the member's Stats tab) and the name: no attendance, staff history,
officiating or team analytics. The token is the opt-in (``members.services.stats_sharing``): unknown, switched off or
replaced tokens, clubs without game analytics and the platform host all answer 404."""

import uuid

from django_tenants.utils import get_public_schema_name
from ninja import Router
from ninja.errors import HttpError

from api.routers.analytics import member_document
from api.schemas_member_stats import PublicPlayerStatsOut
from events.models import Season
from events.services import game_stats, member_stats
from members.services import stats_sharing

router = Router()


@router.get("/player-stats/{token}", auth=None, response=PublicPlayerStatsOut, summary="A person's game stats through their personal public link", operation_id="public_player_stats")
def player_stats(request, token: str, season: uuid.UUID | None = None):
    """``season``: narrows the ``game_stats`` totals (and team rows) to that season; every season without it."""
    club = request.tenant
    if club.schema_name == get_public_schema_name() or not game_stats.analytics_supported(club, request.features):
        raise HttpError(404, "Not found.")
    member = stats_sharing.member_for_token(token)
    if member is None:
        raise HttpError(404, "Not found.")
    player, goalie = member_stats.game_sections(member)
    chosen = None
    if season is not None:
        chosen = Season.objects.filter(pk=season).first()
        if chosen is None:
            raise HttpError(404, "Not found.")
    return {"first_name": member.first_name, "last_name": member.last_name, "game_stats": member_document(member, chosen), "player": player, "goalie": goalie}
