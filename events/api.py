"""Public read-only game endpoints -- see api/urls.py for how this is
mounted. Also owns GET /teams/{team_id}/games/: it's an Event query through
and through, so the query logic and GameOut schema live here rather than
being duplicated in teams/api.py.
"""

import uuid
from datetime import datetime

from django.db.models import Q
from django.utils import timezone
from ninja import Router, Schema
from ninja.errors import HttpError

from api.errors import require_club
from club.services.access import current_season
from teams.models import Team

from .models import Event

router = Router(tags=["games"])

DEFAULT_UPCOMING_COUNT = 10
MAX_UPCOMING_COUNT = 50

#: /games/upcoming/ covers anything worth putting on a public fixture list --
#: not just Game, but Tournament too. The other game endpoints (live,
#: per-team) stay Game-only: is_live/score_for/score_against are genuinely
#: game-specific (events/models.py), so a tournament wouldn't have anything
#: meaningful to show there anyway.
UPCOMING_KINDS = [Event.EventKind.GAME, Event.EventKind.TOURNAMENT]


class LocationOut(Schema):
    name: str
    address: str
    city: str
    zip_code: str
    country: str
    is_home: bool


class GameOut(Schema):
    id: uuid.UUID
    start: datetime
    location: LocationOut | None
    home_team: str | None
    away_team: str | None
    competition: str
    is_live: bool
    status: str  # "upcoming" | "live" | "finished"
    home_score: int | None
    away_score: int | None


def _to_game_out(event, team=None) -> GameOut:
    if team is None:
        # .first() would re-query even with teams prefetched; go through the
        # prefetch cache instead.
        related_teams = list(event.teams.all())
        team = related_teams[0] if related_teams else None

    team_name = team.name if team is not None else None
    opponent_name = event.opponent.name if event.opponent_id else None

    if event.is_home_game:
        home_team, away_team = team_name, opponent_name
        home_score, away_score = event.score_for, event.score_against
    else:
        home_team, away_team = opponent_name, team_name
        home_score, away_score = event.score_against, event.score_for

    if event.is_live:
        status = "live"
    elif event.start > timezone.now():
        status = "upcoming"
    else:
        status = "finished"

    location = None
    if event.location_id:
        location = LocationOut(name=event.location.name, address=event.location.address, city=event.location.city, zip_code=event.location.zip_code, country=str(event.location.country), is_home=event.location.is_home)

    return GameOut(
        id=event.pk,
        start=event.start,
        location=location,
        home_team=home_team,
        away_team=away_team,
        competition=event.competition,
        is_live=event.is_live,
        status=status,
        home_score=home_score,
        away_score=away_score,
    )


@router.get("/games/upcoming/", response=list[GameOut], summary="Upcoming games")
def list_upcoming_games(request, count: int = DEFAULT_UPCOMING_COUNT):
    """The next `count` non-cancelled upcoming games and tournaments, club-wide."""
    club = require_club(request)
    count = max(1, min(count, MAX_UPCOMING_COUNT))

    events = Event.objects.filter(club=club, kind__in=UPCOMING_KINDS, cancelled=False, start__gte=timezone.now()).select_related("opponent", "location").prefetch_related("teams").order_by("start")[:count]

    return [_to_game_out(event) for event in events]


@router.get("/games/live/", response=list[GameOut], summary="Live games")
def list_live_games(request):
    club = require_club(request)

    events = Event.objects.filter(club=club, kind=Event.EventKind.GAME, cancelled=False, is_live=True).select_related("opponent", "location").prefetch_related("teams").order_by("start")

    return [_to_game_out(event) for event in events]


@router.get("/teams/{team_id}/games/", response=list[GameOut], summary="A team's current-season games")
def list_team_games(request, team_id: uuid.UUID):
    """All of this team's current-season games, past and upcoming -- past ones
    include both teams' scores. Same "explicit season, else derived from
    start date" scoping management.views.EventListView already applies."""
    club = require_club(request)
    team = Team.objects.filter(club=club, pk=team_id).first()
    if team is None:
        raise HttpError(404, "No such team.")

    season = current_season(club)
    if season is None:
        return []

    events = (
        Event.objects.filter(club=club, teams=team, kind=Event.EventKind.GAME, cancelled=False)
        .filter(Q(season=season) | Q(season__isnull=True, start__date__gte=season.start_date, start__date__lte=season.end_date))
        .select_related("opponent", "location")
        .order_by("start")
    )

    return [_to_game_out(event, team=team) for event in events]
