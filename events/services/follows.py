"""Following teams and games: who gets the live game pushes (events.services.game_push).

Opt-in, per account (a login, not a member): follow a team (events.GameFollow) to hear about every game it plays, or one
game directly (events.EventFollow). Everything here runs inside the club's schema (no club column), so "this club's
teams" is simply every team in the schema. Following is the user's own business: there is no actor/permission check
beyond the API needing an account; every call is idempotent.
"""

import datetime

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from events.models import Event, EventFollow, GameFollow
from members.services.errors import Invalid
from teams.models import Team

#: A followed game is listed from this long before it starts ...
FOLLOWED_GAME_LOOKAHEAD = datetime.timedelta(hours=24)
#: ... through the time it is live, until this long after its start (a game takes ~2 hours, so the result stays up for
#: a few hours afterwards).
FOLLOWED_GAME_LOOKBACK = datetime.timedelta(hours=6)
#: At most this many followed games in ``followed_games`` (the calendar has everything).
FOLLOWED_GAMES_LIMIT = 5


def _require_game(event) -> None:
    if event.kind != Event.EventKind.GAME:
        raise Invalid("Only games can be followed.", {"event": ["Only games can be followed."]})


# -- teams ---------------------------------------------------------------------------------------------------------------


def follow_team(user, team) -> GameFollow:
    """``user`` follows ``team``'s games. Idempotent."""
    follow, _created = GameFollow.objects.get_or_create(user=user, team=team)
    return follow


def unfollow_team(user, team) -> None:
    """Idempotent."""
    GameFollow.objects.filter(user=user, team=team).delete()


def followed_team_ids(user) -> set:
    return set(GameFollow.objects.filter(user=user).values_list("team_id", flat=True))


def followed_teams(user) -> list:
    return list(Team.objects.filter(followers__user=user).order_by("ordering", "name"))


def set_followed_teams(user, team_ids) -> set:
    """Replaces ``user``'s followed teams with ``team_ids`` (unknown ids are dropped). Returns the resulting set."""
    wanted = set(Team.objects.filter(pk__in=list(team_ids)).values_list("pk", flat=True))
    with transaction.atomic():
        GameFollow.objects.filter(user=user).exclude(team_id__in=wanted).delete()
        existing = followed_team_ids(user)
        GameFollow.objects.bulk_create([GameFollow(user=user, team_id=team_id) for team_id in wanted - existing])
    return wanted


# -- games ---------------------------------------------------------------------------------------------------------------


def follow_event(user, event) -> EventFollow:
    """``user`` follows this one game. Idempotent; only games can be followed (``Invalid`` otherwise)."""
    _require_game(event)
    follow, _created = EventFollow.objects.get_or_create(user=user, event=event)
    return follow


def unfollow_event(user, event) -> None:
    """Idempotent."""
    EventFollow.objects.filter(user=user, event=event).delete()


def followed_event_ids(user) -> set:
    """The games ``user`` follows directly (not counting the ones their followed teams play)."""
    return set(EventFollow.objects.filter(user=user).values_list("event_id", flat=True))


def follow_state(user, event) -> dict:
    """``{"directly": bool, "via_team": bool}`` for ``user`` and ``event``."""
    directly = EventFollow.objects.filter(user=user, event=event).exists()
    via_team = GameFollow.objects.filter(user=user, team__in=event.teams.all()).exists()
    return {"directly": directly, "via_team": via_team}


def followers(event) -> list:
    """Active accounts following ``event``: through any of its teams, or the game itself."""
    team_followers = GameFollow.objects.filter(team__in=event.teams.all()).values_list("user_id", flat=True)
    game_followers = EventFollow.objects.filter(event=event).values_list("user_id", flat=True)
    user_ids = set(team_followers) | set(game_followers)
    return list(get_user_model().objects.filter(pk__in=user_ids, is_active=True).order_by("pk"))


def followed_games(user, *, now=None, limit=FOLLOWED_GAMES_LIMIT) -> list[dict]:
    """Rows ``{"event", "via"}`` for the games ``user`` follows -- directly or through a followed team -- that are worth
    showing right now: live, starting within the next day, or begun within the last few hours. ``via`` is ``["game"]``,
    ``["team"]`` or both. Live games first, then what is coming up soonest, then what just finished (latest first); at
    most ``limit``."""
    now = now or timezone.now()
    direct_ids = followed_event_ids(user)
    team_ids = followed_team_ids(user)
    if not direct_ids and not team_ids:
        return []

    in_window = Q(is_live=True) | Q(start__gte=now - FOLLOWED_GAME_LOOKBACK, start__lte=now + FOLLOWED_GAME_LOOKAHEAD)
    followed = Q(pk__in=direct_ids) | Q(teams__in=team_ids)
    events = list(Event.objects.filter(kind=Event.EventKind.GAME, cancelled=False).filter(followed).filter(in_window).distinct().select_related("location", "opponent").prefetch_related("teams"))

    def order(event):
        if event.is_live:
            return (0, event.start.timestamp())
        if event.start > now:
            return (1, event.start.timestamp())
        return (2, -event.start.timestamp())

    rows = []
    for event in sorted(events, key=order)[:limit]:
        via = []
        if event.pk in direct_ids:
            via.append("game")
        if any(team.pk in team_ids for team in event.teams.all()):
            via.append("team")
        rows.append({"event": event, "via": via})
    return rows
