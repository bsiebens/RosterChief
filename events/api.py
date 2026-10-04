"""Public read-only game endpoints -- see api/urls.py for how this is
mounted. Also owns GET /teams/{team_id}/games/: it's an Event query through
and through, so the query logic and GameOut schema live here rather than
being duplicated in teams/api.py.
"""

import uuid
from datetime import datetime

from django.db.models import Q
from django.utils import timezone
from ninja import Field, Router, Schema
from ninja.errors import HttpError

from api.cache import CACHE_SECONDS, cached_for_club
from api.errors import require_club
from club.services.access import current_season
from teams.models import Team

from .models import ASSUMED_EVENT_DURATION, Event, GameTimeline
from .services.game_timeline import public_names_allowed, timeline_enabled, timeline_for_api
from .timeline_schemas import PeriodScoreOut, TimelineOut

router = Router(tags=["games"])

DEFAULT_UPCOMING_COUNT = 10
MAX_UPCOMING_COUNT = 50

DEFAULT_PAST_COUNT = 10
MAX_PAST_COUNT = 50

#: A game that's live right now gets a shorter cache window on /games/{id}/ --
#: its timeline is re-polled every minute (events.services.game_timeline).
LIVE_GAME_CACHE_SECONDS = 15

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
    # home_team/away_team only ever carry ONE of our own teams (the opponent on
    # the other side) -- an Event can be assigned to several teams at once, so
    # `teams` below is the complete list. The single-team pair is kept as-is
    # for the external sites already reading it.
    home_team: TeamRefOut | None = Field(description="Home side. For our club, only ONE of its teams (the requested one on /teams/{id}/games/, else the first by name). Kept for compatibility; read `teams`.")
    away_team: TeamRefOut | None = Field(description="Away side. For our club, only ONE of its teams (the requested one on /teams/{id}/games/, else the first by name). Kept for compatibility; read `teams`.")
    teams: list[TeamRefOut] = Field(description="Every one of our own club's teams assigned to this game, ordered by name. Use this rather than home_team/away_team when a game can involve more than one team.")
    competition: str
    is_friendly: bool
    is_live: bool
    status: str  # "upcoming" | "live" | "finished"
    home_score: int | None
    away_score: int | None
    # The light parts of the game's timeline (GAME_TIMELINE.md §5) -- the full
    # list of goals/penalties only comes with GET /games/{id}/. Empty/null when
    # the club doesn't have game timelines or this game has none yet.
    periods: list[PeriodScoreOut] = Field([], description="Score per period, when the game has a timeline.")
    decided_in: str | None = Field(None, description="regulation | overtime | shootout, once known.")
    timeline_updated_at: datetime | None = Field(None, description="Last time the game's timeline changed -- poll /games/{id}/ only when this moves.")


class GameDetailOut(GameOut):
    timeline: TimelineOut | None = Field(description="What happened during the game: goals, penalties, goalie changes, time-outs. Null when the club doesn't have game timelines or there's nothing yet.")


def _timeline_of(event) -> GameTimeline | None:
    """The game's timeline, or None -- reading the reverse one-to-one raises
    when there's no row."""
    try:
        return event.timeline
    except GameTimeline.DoesNotExist:
        return None


def _to_team_ref_out(request, *, id, name, logo) -> TeamRefOut:
    return TeamRefOut(id=id, name=name, logo_url=request.build_absolute_uri(logo.url) if logo else None)


def _to_game_out(event, request, club, team=None, *, timelines=False) -> GameOut:
    # Every caller prefetches "teams" -- .all() reads that cache (Team's own
    # Meta.ordering, by name), where .first() would re-query per event.
    related_teams = list(event.teams.all())
    if team is None:
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

    timeline = _timeline_of(event) if timelines else None
    summary = timeline_for_api(timeline, event, show_names=False) if timeline else None

    return GameOut(
        id=event.pk,
        start=event.start,
        end=effective_end,
        location=location,
        home_team=home_team,
        away_team=away_team,
        teams=[_to_team_ref_out(request, id=related.pk, name=related.name, logo=club.logo) for related in related_teams],
        competition=event.competition,
        is_friendly=event.is_friendly,
        is_live=event.is_live,
        status=status,
        home_score=home_score,
        away_score=away_score,
        periods=summary["periods"] if summary else [],
        decided_in=summary["decided_in"] if summary else None,
        timeline_updated_at=summary["updated_at"] if summary else None,
    )


def _games(queryset, club):
    """``queryset`` with what _to_game_out reads, plus the timeline when the
    club has them -- without its ``raw`` payload, which lists never need."""
    queryset = queryset.select_related("opponent", "location").prefetch_related("teams")
    if timeline_enabled(club):
        return queryset.select_related("timeline").defer("timeline__raw"), True
    return queryset, False


@router.get("/games/upcoming/", response=list[GameOut], summary="Upcoming games")
def list_upcoming_games(request, count: int = DEFAULT_UPCOMING_COUNT):
    """The next `count` non-cancelled games and tournaments that aren't finished
    yet, club-wide -- a game already in progress (started, not yet past its
    explicit or assumed end) still counts, not just ones that haven't started."""
    club = require_club(request)
    count = max(1, min(count, MAX_UPCOMING_COUNT))

    def compute():
        now = timezone.now()
        events, timelines = _games(Event.objects.filter(club=club, kind__in=UPCOMING_KINDS, cancelled=False).filter(Q(end__gte=now) | Q(end__isnull=True, start__gte=now - ASSUMED_EVENT_DURATION)), club)
        return [_to_game_out(event, request, club, timelines=timelines) for event in events.order_by("start")[:count]]

    return cached_for_club(club.pk, f"games:upcoming:{count}", compute)


@router.get("/games/past/", response=list[GameOut], summary="Past games")
def list_past_games(request, count: int = DEFAULT_PAST_COUNT):
    """The most recent `count` fully completed games, club-wide, newest first --
    Game-only (like /games/live/: scores are game-specific), not cancelled, not
    flagged live, both scores filled in, and finished by the exact complement of
    list_upcoming_games' "not finished" filter, so every row here reports
    status "finished". A finished game with no score entered yet is left out
    rather than shown as a result without one. Current season only, never an
    older one -- same "explicit season, else derived from start date" scoping
    as list_team_games; empty when the club has no current season."""
    club = require_club(request)
    count = max(1, min(count, MAX_PAST_COUNT))

    def compute():
        season = current_season(club)
        if season is None:
            return []

        now = timezone.now()
        events, timelines = _games(
            Event.objects.filter(club=club, kind=Event.EventKind.GAME, cancelled=False, is_live=False, score_for__isnull=False, score_against__isnull=False)
            .filter(Q(season=season) | Q(season__isnull=True, start__date__gte=season.start_date, start__date__lte=season.end_date))
            .filter(Q(end__lte=now) | Q(end__isnull=True, start__lte=now - ASSUMED_EVENT_DURATION)),
            club,
        )
        return [_to_game_out(event, request, club, timelines=timelines) for event in events.order_by("-start")[:count]]

    return cached_for_club(club.pk, f"games:past:{count}", compute)


@router.get("/games/live/", response=list[GameOut], summary="Live games")
def list_live_games(request):
    # Deliberately not cached (see api/cache.py's own module docstring for every
    # other endpoint here) -- this is the one route whose entire purpose is a
    # score that's changing right now; even the short CACHE_SECONDS window would
    # directly work against what an external site is polling this for.
    club = require_club(request)

    events, timelines = _games(Event.objects.filter(club=club, kind=Event.EventKind.GAME, cancelled=False, is_live=True), club)

    return [_to_game_out(event, request, club, timelines=timelines) for event in events.order_by("start")]


@router.get("/teams/{team_id}/games/", response=list[GameOut], summary="A team's current-season games")
def list_team_games(request, team_id: uuid.UUID):
    """All of this team's current-season games, past and upcoming -- past ones
    include both teams' scores. Same "explicit season, else derived from
    start date" scoping management.views.EventListView already applies."""
    club = require_club(request)

    def compute():
        team = Team.objects.filter(club=club, pk=team_id).first()
        if team is None:
            raise HttpError(404, "No such team.")

        season = current_season(club)
        if season is None:
            return []

        events, timelines = _games(
            Event.objects.filter(club=club, teams=team, kind=Event.EventKind.GAME, cancelled=False).filter(Q(season=season) | Q(season__isnull=True, start__date__gte=season.start_date, start__date__lte=season.end_date)),
            club,
        )
        return [_to_game_out(event, request, club, team=team, timelines=timelines) for event in events.order_by("start")]

    return cached_for_club(club.pk, f"games:team:{team_id}", compute)


@router.get("/games/{game_id}/", response=GameDetailOut, summary="One game, with its timeline")
def get_game(request, game_id: uuid.UUID):
    """One game -- the same fields as the lists, plus ``timeline``: goals,
    penalties, goalie changes and time-outs as the federation reported them
    (GAME_TIMELINE.md). Player names are only included when every one of our
    teams on the game shows them publicly (Team.show_player_names_publicly);
    otherwise players are jersey numbers only. A game that's live right now is
    cached for LIVE_GAME_CACHE_SECONDS instead of the usual window."""
    club = require_club(request)
    is_live = Event.objects.filter(club=club, pk=game_id, kind=Event.EventKind.GAME, cancelled=False).values_list("is_live", flat=True).first()
    if is_live is None:
        raise HttpError(404, "No such game.")

    def compute():
        events, timelines = _games(Event.objects.filter(club=club, pk=game_id), club)
        event = events.first()
        if event is None:
            raise HttpError(404, "No such game.")
        game = _to_game_out(event, request, club, timelines=timelines)
        timeline = _timeline_of(event) if timelines else None
        detail = timeline_for_api(timeline, event, show_names=public_names_allowed(event)) if timeline else None
        return GameDetailOut(**game.dict(), timeline=detail)

    return cached_for_club(club.pk, f"games:detail:{game_id}", compute, seconds=LIVE_GAME_CACHE_SECONDS if is_live else CACHE_SECONDS)
