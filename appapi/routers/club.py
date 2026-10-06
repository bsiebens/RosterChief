"""Resource plane (API.md §4, §7.2): club subdomain. Everything a club screen needs to
know about the caller in one call."""

from ninja import Router

from club.services.access import can_add_news, can_manage_evaluations, current_season, has_management_access, teams_staffed_by
from members.models import Member
from members.services.family import managed_people
from notifications.services import unread_count

from .. import services
from ..auth import require_club_member
from ..schemas import ContextOut, ErrorOut

router = Router(tags=["club"])


@router.get("/context", response={200: ContextOut, 401: ErrorOut, 403: ErrorOut, 404: ErrorOut}, summary="The caller in this club")
def context(request):
    """Call when a club is opened (and after pull-to-refresh): the season, the people the
    account acts for (`member_id` values for other endpoints), coached teams, permissions
    and features. `403 not_a_member` means the account has no tie to this club."""
    club = require_club_member(request)
    user = request.user
    me = Member.objects.filter(user=user).first()
    people = managed_people(me, club)
    season = current_season(club)
    return {
        "club": {
            "id": club.pk,
            "slug": club.slug,
            "name": club.name,
            "sport_type": club.sport_type,
            "logo_url": services.logo_url(request, club),
            "event_background_url": services.event_background_url(request, club),
            **services.club_colors(club),
        },
        "season": season,
        "me": me,
        "managed_people": [{"id": person.pk, "first_name": person.first_name, "last_name": person.last_name, "date_of_birth": person.date_of_birth, "is_self": person.pk == me.pk} for person in people],
        "staffed_teams": list(teams_staffed_by(user, club).order_by("name")),
        "permissions": {"has_management_access": has_management_access(user, club), "can_add_news": can_add_news(user, club), "can_manage_evaluations": can_manage_evaluations(user, club)},
        "features": services.club_features(club),
        "unread_notifications": unread_count(club, people),
    }
