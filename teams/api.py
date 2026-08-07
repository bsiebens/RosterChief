"""Public read-only team/roster endpoints -- see api/urls.py for how this is
mounted. Roster building is a small helper (build_roster) rather than
inlined in the view so it can be unit-tested without going through Ninja's
request cycle.
"""

import uuid
from itertools import groupby

from ninja import Router, Schema
from ninja.errors import HttpError

from api.errors import require_club
from club.models import ClubMembership
from club.services.access import current_season

from .models import Team, TeamMembership, TeamPhoto

router = Router(tags=["teams"])


class TeamOut(Schema):
    id: uuid.UUID
    name: str
    short_name: str
    photo_url: str | None


class PlayerOut(Schema):
    id: uuid.UUID
    first_name: str
    last_name: str
    jersey_number: int | None
    is_captain: bool
    is_alternate_captain: bool
    license: str | None


class PositionGroupOut(Schema):
    position: str
    players: list[PlayerOut]


class StaffMemberOut(Schema):
    id: uuid.UUID
    first_name: str
    last_name: str
    position: str


class RosterOut(Schema):
    team: TeamOut
    season: str | None
    players: list[PositionGroupOut]
    staff: list[StaffMemberOut]


def _to_team_out(team, request, photo=None) -> TeamOut:
    photo_url = request.build_absolute_uri(photo.image.url) if photo else None
    return TeamOut(id=team.pk, name=team.name, short_name=team.short_name, photo_url=photo_url)


def _to_player_out(membership, license_by_member_id) -> PlayerOut:
    return PlayerOut(
        id=membership.member_id,
        first_name=membership.member.first_name,
        last_name=membership.member.last_name,
        jersey_number=membership.jersey_number,
        is_captain=membership.is_captain,
        is_alternate_captain=membership.is_alternate_captain,
        license=license_by_member_id.get(membership.member_id) or None,
    )


def build_roster(team, request) -> RosterOut:
    """Current season's players -- grouped by position (so a consumer can pull
    just e.g. "Forward" without filtering a flat list itself), each group
    sorted by jersey number, groups themselves in Position.ordering order --
    and staff, for `team`. No current season -> empty roster, same "nothing
    to show, not an error" handling as management.views.MembershipListView."""
    season = current_season(team.club)
    if season is None:
        return RosterOut(team=_to_team_out(team, request), season=None, players=[], staff=[])

    photo = TeamPhoto.objects.filter(team=team, season=season).first()

    memberships = TeamMembership.objects.filter(team=team, season=season).select_related("member", "position").order_by("position__ordering", "position__name", "jersey_number")
    assignments = team.staff_assignments.filter(season=season).select_related("member", "position").order_by("position__ordering", "position__name", "member__last_name")

    # A player's license lives on their club-wide ClubMembership for the season, not on
    # TeamMembership -- one query for all of them rather than one per player.
    license_by_member_id = dict(ClubMembership.objects.filter(club=team.club, season=season).values_list("member_id", "license"))

    # groupby only groups consecutive runs -- relies on the queryset already
    # being ordered by position first, which it is.
    players = [PositionGroupOut(position=position_name, players=[_to_player_out(m, license_by_member_id) for m in members]) for position_name, members in groupby(memberships, key=lambda m: m.position.name)]
    staff = [
        StaffMemberOut(id=assignment.member_id, first_name=assignment.member.first_name, last_name=assignment.member.last_name, position=assignment.position.name)
        for assignment in assignments
    ]

    return RosterOut(team=_to_team_out(team, request, photo=photo), season=season.name, players=players, staff=staff)


@router.get("/", response=list[TeamOut], summary="List teams")
def list_teams(request):
    club = require_club(request)
    teams = list(Team.objects.filter(club=club).order_by("name"))

    season = current_season(club)
    photos_by_team_id = {}
    if season is not None and teams:
        photos_by_team_id = {photo.team_id: photo for photo in TeamPhoto.objects.filter(team__in=teams, season=season)}

    return [_to_team_out(team, request, photo=photos_by_team_id.get(team.pk)) for team in teams]


@router.get("/{team_id}/roster/", response=RosterOut, summary="Current season's roster")
def get_roster(request, team_id: uuid.UUID):
    club = require_club(request)
    team = _get_team_or_404(club, team_id)
    return build_roster(team, request)


def _get_team_or_404(club, team_id):
    team = Team.objects.filter(club=club, pk=team_id).first()
    if team is None:
        raise HttpError(404, "No such team.")
    return team
