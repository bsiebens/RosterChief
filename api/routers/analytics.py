"""Game analytics API, from the game sheets (``events.services.game_stats``). Three routers:

- ``teams_router`` (mounted at ``/teams``): ``GET /teams/{team_id}/analytics`` -- a team's analytics for a season.
- ``members_router`` (mounted at ``/members``): ``GET /members/{member_id}/game-stats`` -- a member's own numbers.
- ``events_router`` (mounted at ``/events``): ``PUT`` / ``DELETE /events/{event_id}/strength-overrides/{goal_id}`` --
  confirming or correcting the strength of a goal the analytics flagged.

Everything is gated on ``analytics_supported(request.tenant, request.features)`` (a hockey club with the ``analytics``
feature): 404 otherwise, like other feature-gated reads. Everyone in the management app reads a team's analytics;
a member's numbers follow ``access.can_view_member`` (404, never 403). A strength correction needs
``access.can_manage_attendance`` on the game (checked by the service: 403), 422 for an unknown goal or strength."""

import uuid
from typing import Literal

from ninja import Router
from ninja.errors import HttpError

from api.schemas_analytics import MemberGameStatsOut, StrengthIn, StrengthOverrideOut, TeamAnalyticsOut
from events.models import Event
from events.services import game_stats
from members.models import Member
from members.services import access
from members.services.errors import Invalid
from teams.services import teams as team_svc

teams_router = Router()
members_router = Router()
events_router = Router()


def require_analytics(request) -> None:
    if not game_stats.analytics_supported(request.tenant, request.features):
        raise HttpError(404, "Not found.")


def analytics_available(request) -> bool:
    """For the team detail's ``analytics_available`` flag."""
    return game_stats.analytics_supported(request.tenant, request.features)


def _season(season_id):
    season = team_svc.current_season(season_id)
    if season is None:
        message = "Unknown season." if season_id is not None else "There is no current season."
        raise Invalid(message, {"season": [message]})
    return season


def _game(event) -> dict | None:
    if event is None:
        return None
    return {
        "id": event.pk,
        "title": event.title,
        "start": event.start,
        "opponent": event.opponent.name if event.opponent_id else None,
        "is_home_game": event.is_home_game,
        "competition": game_stats.competition_name(event) or "",
        "is_friendly": event.is_friendly,
        "score_for": event.score_for,
        "score_against": event.score_against,
    }


def _for_against(row: dict | None) -> dict | None:
    if row is None:
        return None
    return {"label": row["label"], "goals_for": row["for"], "goals_against": row["against"], "for_pct": row.get("for_pct", 0), "against_pct": row.get("against_pct", 0)}


def _leaders(rows: list[dict]) -> list[dict]:
    return [{key: value for key, value in row.items() if key != "member"} for row in rows]


def analytics_document(user, team, season, *, scope="all", friendlies=True, competition="") -> dict:
    """``team_analytics`` with its model objects turned into plain references, the way TeamAnalyticsOut reads it."""
    data = game_stats.team_analytics(team, season, scope=scope, friendlies=friendlies, competition=competition)
    trend = data["trend"]
    warnings = data["strength_warnings"]
    fixable = {event.pk: access.can_manage_attendance(user, event) for event in {row["event"] for row in warnings}}
    return {
        **data,
        "team": {"id": team.pk, "name": team.name},
        "season": {"id": season.pk, "name": season.name},
        "scope": scope,
        "friendlies": friendlies,
        "competition": competition,
        "capabilities": {"analytics": True},
        "form": [{"result": row["result"], "event": _game(row["event"])} for row in data["form"]],
        "top_scorers": _leaders(data["top_scorers"]),
        "top_assists": _leaders(data["top_assists"]),
        "top_points": _leaders(data["top_points"]),
        "top_offenders": _leaders(data["top_offenders"]),
        "goalies": _leaders(data["goalies"]),
        "periods": [_for_against(row) for row in data["periods"]],
        "goal_minutes": [_for_against(row) for row in data["goal_minutes"]],
        "busiest_minutes": _for_against(data["busiest_minutes"]),
        "game_rows": [
            {
                "event": _game(row["event"]),
                "result": row["result"],
                "goals_for": row["for"],
                "goals_against": row["against"],
                "pim_for": row["pim_for"],
                "pim_against": row["pim_against"],
                "pim_for_pct": row.get("pim_for_pct", 0),
                "pim_against_pct": row.get("pim_against_pct", 0),
                "total_pct": row.get("total_pct", 0),
            }
            for row in data["game_rows"]
        ],
        "game_bars": [{"event": _game(row["event"]), "result": row["result"], "goals_for": row["for"], "goals_against": row["against"], "for_pct": row.get("for_pct", 0), "against_pct": row.get("against_pct", 0)} for row in data["game_bars"]],
        "trend": {**trend, "points": [{**point, "game": _game(point["game"])} for point in trend["points"]]} if trend else None,
        "strength_warnings": [{**row, "event": _game(row["event"]), "recorded_label": str(row["recorded_label"]), "expected_label": str(row["expected_label"]), "can_fix": fixable.get(row["event"].pk, False)} for row in warnings],
    }


@teams_router.get("/{team_id}/analytics", response=TeamAnalyticsOut, summary="A team's game analytics for a season (analytics feature, hockey)", operation_id="team_analytics")
def team_analytics(request, team_id: uuid.UUID, season: uuid.UUID | None = None, scope: Literal["all", "home", "away", "last5"] = "all", friendlies: bool = True, competition: str = ""):
    """The team's finished games with a score in ``season`` (default: the current one), narrowed to ``scope`` (home,
    away or the last 5), one ``competition`` (a name from ``competitions``) and, with ``friendlies=false``, without
    friendly games. Goal, penalty, player and period figures only cover the games with a game sheet
    (``timeline_games``); shot figures only the games whose source reports shots (``shot_games``)."""
    require_analytics(request)
    team = team_svc.get_team(team_id)
    if team is None:
        raise HttpError(404, "Not found.")
    return analytics_document(request.user, team, _season(season), scope=scope, friendlies=friendlies, competition=competition.strip())


def member_document(member, season) -> dict:
    data = game_stats.member_stats(member, season)
    return {
        **data,
        "season": {"id": season.pk, "name": season.name} if season else None,
        "teams": [{**row, "team": {"id": row["team"].pk, "name": row["team"].name}, "season": {"id": row["season"].pk, "name": row["season"].name} if row["season"] else None} for row in data["teams"]],
    }


@members_router.get("/{member_id}/game-stats", response=MemberGameStatsOut, summary="A member's own game numbers (analytics feature, hockey)", operation_id="member_game_stats")
def member_game_stats(request, member_id: uuid.UUID, season: uuid.UUID | None = None):
    """Goals, assists, points, penalty minutes, power-play and game-winning goals and goalie numbers from the game
    sheets matched to this member, in total and per team and season (newest first). Every season without ``season``."""
    require_analytics(request)
    member = Member.objects.filter(pk=member_id).first()
    if member is None or not access.can_view_member(request.user, member):
        raise HttpError(404, "Not found.")
    chosen = None
    if season is not None:
        chosen = _season(season)
    return member_document(member, chosen)


def _event(event_id) -> Event:
    event = Event.objects.filter(pk=event_id).first()
    if event is None:
        raise HttpError(404, "Not found.")
    return event


@events_router.put("/{event_id}/strength-overrides/{goal_id}", response=StrengthOverrideOut, summary="Correct or confirm the strength of a flagged goal (game manager)", operation_id="set_strength_override")
def set_strength_override(request, event_id: uuid.UUID, goal_id: str, payload: StrengthIn):
    """Stored on the game's timeline (a re-fetch keeps it); the game's stats are re-synced, so the goal counts with
    ``strength`` and is no longer flagged."""
    require_analytics(request)
    event = _event(event_id)
    timeline = game_stats.set_strength_override(request.user, event, goal_id, payload.strength)
    return {"event_id": event.pk, "goal": goal_id, "overrides": timeline.strength_overrides or {}}


@events_router.delete("/{event_id}/strength-overrides/{goal_id}", response=StrengthOverrideOut, summary="Undo a goal strength correction (game manager)", operation_id="clear_strength_override")
def clear_strength_override(request, event_id: uuid.UUID, goal_id: str):
    require_analytics(request)
    event = _event(event_id)
    timeline = game_stats.clear_strength_override(request.user, event, goal_id)
    return {"event_id": event.pk, "goal": goal_id, "overrides": timeline.strength_overrides or {}}
