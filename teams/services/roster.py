from django.utils import timezone

from club.models import ClubMembership, Season
from members.models import Member

from ..models import TeamMembership
from .numbers import is_number_available


def place_member_on_team(member, team, season, *, jersey_number=None):
    """Creates (or, if already placed, just returns) ``member``'s
    TeamMembership on ``team`` for ``season`` -- the one shared placement
    path for both a manual "Place in" click (management.views.
    SignupPlaceInTeamView) and the Registrations review screen's own
    confirm-time auto-placement, so the two can never drift apart.

    ``position`` is always left ``None`` -- the team's own call to set
    correctly later, same reasoning SignupTeamPlacementForm's own docstring
    already gives for placing a still-PENDING member before their details
    are fully sorted out.

    ``jersey_number`` is only ever applied if the team has a number pool and
    the number is still actually available in it right now (silently dropped
    otherwise, not raised, and always dropped for a team with no pool at all
    -- nothing to check it against) -- never trusted blindly, since someone
    else may have taken it in the meantime. Also idempotent: calling this for
    someone already on the team this season just returns their existing row
    unchanged rather than raising on ``unique_member_per_team_per_season``."""
    existing = TeamMembership.objects.filter(team=team, member=member, season=season).first()
    if existing is not None:
        return existing

    number = None
    if jersey_number is not None and team.pool_id is not None and is_number_available(team.pool, season, jersey_number, for_member=member):
        number = jersey_number

    membership = TeamMembership(team=team, member=member, season=season, jersey_number=number)
    membership.full_clean(exclude=["position"])
    membership.save()
    return membership


def eligible_roster_members(club):
    """Members eligible to be added to a team's roster or staff: active (paid) for
    the club this season or next, regardless of which season's roster/staff list is
    currently being edited (the team page's season switcher can point at either)."""
    today = timezone.localdate()
    eligible_seasons = [season for season in (Season.covering(club, today), Season.next_after(club, today)) if season is not None]
    return Member.objects.filter(
        member_of__club=club,
        member_of__season__in=eligible_seasons,
        member_of__status=ClubMembership.StatusChoices.ACTIVE,
        # Guardians are attached to the club only as a parent of a member, so they
        # are not eligible for a roster spot *or* a staff one. A parent who
        # volunteers as a coach is a member of the club and marked as one.
        member_of__kind=ClubMembership.Kind.MEMBER,
    ).distinct()
