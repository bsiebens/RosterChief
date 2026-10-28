"""Public read-only team/roster endpoints -- see api/public/urls.py for how this is
mounted. Roster building is a small helper (build_roster) rather than
inlined in the view so it can be unit-tested without going through Ninja's
request cycle.
"""

import uuid
from datetime import datetime
from itertools import groupby

from django.db.models import F
from django.utils.dateparse import parse_datetime
from ninja import Router, Schema
from ninja.errors import HttpError

from api.public.cache import cached_for_club
from api.public.errors import require_club
from club.models import ClubMembership
from club.services.access import current_season
from events.services import import_sources

from .models import Team, TeamMembership, TeamPhoto

router = Router(tags=["teams"])


class TeamOut(Schema):
    id: uuid.UUID
    name: str
    short_name: str
    photo_url: str | None
    tournament_only: bool


class PlayerOut(Schema):
    id: uuid.UUID
    first_name: str
    last_name: str
    jersey_number: int | None
    is_captain: bool
    is_alternate_captain: bool
    photo_url: str | None


class PositionGroupOut(Schema):
    position: str
    players: list[PlayerOut]


class StaffMemberOut(Schema):
    id: uuid.UUID
    first_name: str
    last_name: str
    position: str
    photo_url: str | None


class RosterOut(Schema):
    team: TeamOut
    season: str | None
    players: list[PositionGroupOut]
    staff: list[StaffMemberOut]


class StandingRowOut(Schema):
    position: int
    team_name: str
    is_own_team: bool
    played: int
    wins: int
    ot_wins: int
    ot_losses: int
    draws: int
    losses: int
    goals_for: int
    goals_against: int
    goal_difference: int
    points: int


class CompetitionStandingsOut(Schema):
    league: str
    competition: str
    competition_label: str
    points_rule: str
    has_overtime: bool
    updated_at: datetime | None
    rows: list[StandingRowOut]


class TeamStandingsOut(Schema):
    team: TeamOut
    season: str | None
    competitions: list[CompetitionStandingsOut]


def _to_team_out(team, request, photo=None) -> TeamOut:
    photo_url = request.build_absolute_uri(photo.image.url) if photo else None
    return TeamOut(id=team.pk, name=team.name, short_name=team.short_name, photo_url=photo_url, tournament_only=team.tournament_only)


def _photo_url(member, request) -> str | None:
    """``member.public_photo``'s URL, absolute like TeamOut's own photo_url --
    a relative /media/... path is useless to a club's separately-hosted public
    website. None whenever public_photo is (no photo, or consent's still off)."""
    return request.build_absolute_uri(member.public_photo.url) if member.public_photo else None


#: A roster spot can exist before the season's ClubMembership is approved
#: (placement from the Sign-up page) or after it was cancelled; the public
#: roster only lists people whose membership isn't in one of these states.
#: Members with no ClubMembership row for the season are still listed.
HIDDEN_MEMBERSHIP_STATUSES = (ClubMembership.StatusChoices.PENDING, ClubMembership.StatusChoices.CANCELLED)


def _to_player_out(membership, request) -> PlayerOut:
    return PlayerOut(
        id=membership.member_id,
        first_name=membership.member.first_name,
        last_name=membership.member.last_name,
        jersey_number=membership.jersey_number,
        is_captain=membership.is_captain,
        is_alternate_captain=membership.is_alternate_captain,
        photo_url=_photo_url(membership.member, request),
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

    # nulls_last: a roster spot placed from the Sign-up page (management.forms.
    # SignupTeamPlacementForm) has no position yet -- those trail every real
    # position group instead of sorting first (NULL's default position varies by
    # database backend otherwise).
    hidden_member_ids = ClubMembership.objects.filter(club=team.club, season=season, status__in=HIDDEN_MEMBERSHIP_STATUSES).values("member_id")
    memberships = (
        TeamMembership.objects.filter(team=team, season=season)
        .exclude(member_id__in=hidden_member_ids)
        .select_related("member", "position")
        .order_by(F("position__ordering").asc(nulls_last=True), F("position__name").asc(nulls_last=True), "jersey_number")
    )
    assignments = team.staff_assignments.filter(season=season).exclude(member_id__in=hidden_member_ids).select_related("member", "position").order_by("position__ordering", "position__name", "member__last_name")

    # groupby only groups consecutive runs -- relies on the queryset already
    # being ordered by position first, which it is. A member placed with no
    # position yet (position_id is None -- see the nulls_last comment above)
    # groups together under an empty "" label rather than crashing on
    # m.position.name; it's the caller's job to label that group, not this API's.
    players = [PositionGroupOut(position=position_name or "", players=[_to_player_out(m, request) for m in members]) for position_name, members in groupby(memberships, key=lambda m: m.position.name if m.position_id else None)]
    staff = [StaffMemberOut(id=assignment.member_id, first_name=assignment.member.first_name, last_name=assignment.member.last_name, position=assignment.position.name, photo_url=_photo_url(assignment.member, request)) for assignment in assignments]

    return RosterOut(team=_to_team_out(team, request, photo=photo), season=season.name, players=players, staff=staff)


def build_standings(team, season, request) -> TeamStandingsOut:
    """``team``'s league standings for ``season``, read from what
    events.services.rbihf_standings stored on its RBIHF import sources -- never
    fetched from RBIHF here. Only meaningful while the club has the RBIHF feature;
    callers check that."""
    competitions = []
    if season is not None:
        for entry in import_sources.sources_for(team, season):
            standings = entry.get("standings") or {}
            if entry.get("source") != import_sources.SOURCE_RBIHF or not standings.get("competitions"):
                continue
            own_id = standings.get("rbihf_team_id")
            updated_at = parse_datetime(standings["updated_at"]) if standings.get("updated_at") else None
            for competition in standings["competitions"]:
                rows = [StandingRowOut(**{key: row[key] for key in StandingRowOut.model_fields if key != "is_own_team"}, is_own_team=row.get("team_id") == own_id) for row in competition.get("rows", [])]
                competitions.append(
                    CompetitionStandingsOut(
                        league=standings.get("league", ""),
                        competition=competition.get("name", ""),
                        competition_label=entry.get("competition_label", ""),
                        points_rule=competition.get("points_rule", ""),
                        has_overtime=competition.get("has_overtime", False),
                        updated_at=updated_at,
                        rows=rows,
                    )
                )
    return TeamStandingsOut(team=_to_team_out(team, request), season=season.name if season else None, competitions=competitions)


@router.get("/", response=list[TeamOut], summary="List teams")
def list_teams(request, include_tournament_only: bool = True):
    """`include_tournament_only=false` leaves out teams marked tournament-only (shown by default)."""
    club = require_club(request)

    def compute():
        queryset = Team.objects.filter(club=club).order_by(F("ordering").asc(nulls_last=True), "name")
        if not include_tournament_only:
            queryset = queryset.filter(tournament_only=False)
        teams = list(queryset)

        season = current_season(club)
        photos_by_team_id = {}
        if season is not None and teams:
            photos_by_team_id = {photo.team_id: photo for photo in TeamPhoto.objects.filter(team__in=teams, season=season)}

        return [_to_team_out(team, request, photo=photos_by_team_id.get(team.pk)) for team in teams]

    return cached_for_club(club.pk, f"teams:list:{int(include_tournament_only)}", compute)


@router.get("/{team_id}/roster/", response=RosterOut, summary="Current season's roster")
def get_roster(request, team_id: uuid.UUID):
    club = require_club(request)

    def compute():
        team = _get_team_or_404(club, team_id)
        return build_roster(team, request)

    return cached_for_club(club.pk, f"teams:roster:{team_id}", compute)


@router.get("/standings/", response=list[TeamStandingsOut], summary="Current season's league standings of every team")
def list_standings(request):
    """Only teams that have standings are listed -- an empty list when the club
    has no RBIHF-imported teams (or the RBIHF feature is off)."""
    club = require_club(request)

    def compute():
        if not import_sources.source_enabled(club, import_sources.SOURCE_RBIHF):
            return []
        season = current_season(club)
        results = [build_standings(team, season, request) for team in Team.objects.filter(club=club).order_by("name")]
        return [result for result in results if result.competitions]

    return cached_for_club(club.pk, "teams:standings", compute)


@router.get("/{team_id}/standings/", response=TeamStandingsOut, summary="Current season's league standings of one team")
def get_standings(request, team_id: uuid.UUID):
    club = require_club(request)

    def compute():
        team = _get_team_or_404(club, team_id)
        season = current_season(club)
        if not import_sources.source_enabled(club, import_sources.SOURCE_RBIHF):
            return TeamStandingsOut(team=_to_team_out(team, request), season=season.name if season else None, competitions=[])
        return build_standings(team, season, request)

    return cached_for_club(club.pk, f"teams:standings:{team_id}", compute)


def _get_team_or_404(club, team_id):
    team = Team.objects.filter(club=club, pk=team_id).first()
    if team is None:
        raise HttpError(404, "No such team.")
    return team
