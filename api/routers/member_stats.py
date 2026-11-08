"""``GET /members/{id}/stats``: the data behind the member page's Stats tab (``events.services.member_stats``).
Visibility follows ``access.can_view_member`` (404, never 403). The game sections (player, goalie) are only filled for a
club with game analytics (hockey + the ``analytics`` feature); attendance and staff history need no feature."""

import uuid

from ninja import Router
from ninja.errors import HttpError

from api.schemas_member_stats import MemberStatsOut, ShareOut
from events.services import game_stats, member_stats, stats_refresh
from members.models import Member
from members.services import access, stats_sharing

router = Router()


@router.get("/{member_id}/stats", response=MemberStatsOut, summary="A member's stats for charts (player, goalie, attendance, staff)", operation_id="member_stats")
def get_member_stats(request, member_id: uuid.UUID):
    member = Member.objects.filter(pk=member_id).first()
    if member is None or not access.can_view_member(request.user, member):
        raise HttpError(404, "Not found.")
    document = member_stats.member_stats_document(member, with_games=game_stats.analytics_supported(request.tenant, request.features))
    document["update_in_progress"] = stats_refresh.in_progress(member)
    document["share_token"] = member.stats_share_token if access.can_manage_members(request.user) else None
    return document


def _member(request, member_id) -> Member:
    access.require_admin(request.user)
    member = Member.objects.filter(pk=member_id).first()
    if member is None:
        raise HttpError(404, "Not found.")
    return member


@router.post("/{member_id}/stats-share", response=ShareOut, summary="Switch on (or with rotate=true replace) the personal public link to this person's game stats (admin)", operation_id="member_stats_share_enable")
def enable_share(request, member_id: uuid.UUID, rotate: bool = False):
    """Returns the secret; the link is ``/stats/<share_token>`` on the club's host. Replacing it kills the old link."""
    return {"share_token": stats_sharing.enable(request.user, _member(request, member_id), rotate=rotate)}


@router.delete("/{member_id}/stats-share", response={204: None}, summary="Switch the personal public stats link off (admin)", operation_id="member_stats_share_disable")
def disable_share(request, member_id: uuid.UUID):
    stats_sharing.disable(request.user, _member(request, member_id))
    return 204, None
