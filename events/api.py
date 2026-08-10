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

from .models import ASSUMED_EVENT_DURATION, Event

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


class TeamRefOut(Schema):
    id: uuid.UUID
    name: str
    logo_url: str | None


class GameOut(Schema):
    id: uuid.UUID
    start: datetime
    end: datetime
    location: LocationOut | None
    home_team: TeamRefOut | None
    away_team: TeamRefOut | None
    competition: str
    is_live: bool
    status: str  # "upcoming" | "live" | "finished"
    home_score: int | None
    away_score: int | None


def _to_team_ref_out(request, *, id, name, logo) -> TeamRefOut:
    return TeamRefOut(id=id, name=name, logo_url=request.build_absolute_uri(logo.url) if logo else None)


def _to_game_out(event, request, club, team=None) -> GameOut:
    if team is None:
        # .first() would re-query even with teams prefetched; go through the
        # prefetch cache instead.
        related_teams = list(event.teams.all())
        team = related_teams[0] if related_teams else None

    # Our own team has no logo of its own -- it's shown under the club's badge. The
    # opponent is an events.models.Opponent, which does carry its own logo.
    team_ref = _to_team_ref_out(request, id=team.pk, name=team.name, logo=club.logo) if team is not None else None
    opponent_ref = _to_team_ref_out(request, id=event.opponent_id, name=event.opponent.name, logo=event.opponent.logo) if event.opponent_id else None

    if event.is_home_game:
        home_team, away_team = team_ref, opponent_ref
        home_score, away_score = event.score_for, event.score_against
    else:
        home_team, away_team = opponent_ref, team_ref
        home_score, away_score = event.score_against, event.score_for

    now = timezone.now()
    effective_end = event.end or (event.start + ASSUMED_EVENT_DURATION)

    if event.is_live:
        status = "live"
    elif event.start > now:
        status = "upcoming"
    elif effective_end > now:
        # Started, not manually flagged live, but our own assumed/explicit
        # window says it isn't over yet -- matches list_upcoming_games'
        # "not finished" inclusion below, so a game returned there never
        # turns around and calls itself "finished".
        status = "live"
    else:
        status = "finished"

    location = None
    if event.location_id:
        location = LocationOut(name=event.location.name, address=event.location.address, city=event.location.city, zip_code=event.location.zip_code, country=str(event.location.country), is_home=event.location.is_home)

    return GameOut(
        id=event.pk,
        start=event.start,
        end=effective_end,
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
    """The next `count` non-cancelled games and tournaments that aren't finished
    yet, club-wide -- a game already in progress (started, not yet past its
    explicit or assumed end) still counts, not just ones that haven't started."""
    club = require_club(request)
    count = max(1, min(count, MAX_UPCOMING_COUNT))
    now = timezone.now()

    events = (
        Event.objects.filter(club=club, kind__in=UPCOMING_KINDS, cancelled=False)
        .filter(Q(end__gte=now) | Q(end__isnull=True, start__gte=now - ASSUMED_EVENT_DURATION))
        .select_related("opponent", "location")
        .prefetch_related("teams")
        .order_by("start")[:count]
    )

    return [_to_game_out(event, request, club) for event in events]


@router.get("/games/live/", response=list[GameOut], summary="Live games")
def list_live_games(request):
    club = require_club(request)

    events = Event.objects.filter(club=club, kind=Event.EventKind.GAME, cancelled=False, is_live=True).select_related("opponent", "location").prefetch_related("teams").order_by("start")

    return [_to_game_out(event, request, club) for event in events]


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

    return [_to_game_out(event, request, club, team=team) for event in events]
