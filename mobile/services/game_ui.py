"""Presentation of followed games and the game timeline for the member PWA -- the same data the
app API serves (events.services.game_push for who follows what, events.services.game_timeline.
timeline_for_api for the timeline, api.app.presenters.followed_game_out for the Home card), shaped
for the templates the way the iOS FollowedGameCard / GameTimelineCard read. Nothing here decides
visibility: callers resolve the club's ``game_timeline`` flag and scope the event to the club."""

from django.core.exceptions import ObjectDoesNotExist
from django.utils.translation import gettext as _

from api.app.presenters import followed_game_out
from events.models import Event, GameFollow, GameTimeline
from events.services.game_push import follow_state, followed_games
from events.services.game_timeline import timeline_for_api

SEVERE_PENALTIES = ("major", "misconduct", "game_misconduct", "match")


def is_followable(event) -> bool:
    return event.kind == Event.EventKind.GAME and not event.cancelled


def follow_context(user, club, event) -> dict:
    """The follow toggle's state: ``directly`` / ``via_team`` (same shape as the API's
    FollowStateOut), the name of the followed team playing in it, and whether the toggle is on."""
    state = follow_state(user, club, event)
    team_name = ""
    if state["via_team"]:
        team = event.teams.filter(pk__in=GameFollow.objects.filter(club=club, user=user).values("team_id")).first()
        team_name = team.name if team else _("your team")
    return {"event": event, "directly": state["directly"], "via_team": state["via_team"], "is_following": state["directly"] or state["via_team"], "team_name": team_name}


def _player(person) -> str:
    person = person or {}
    if person.get("name"):
        return person["name"]
    if person.get("number") is not None:
        return f"#{person['number']}"
    return _("Unknown")


def _team_label(event, side, our_side) -> str:
    if not side:
        return ""
    if side == our_side:
        return ", ".join(team.name for team in event.teams.all()) or _("Us")
    if event.opponent_id:
        return event.opponent.name
    return _("Home") if side == "home" else _("Away")


def _row(event, item, our_side) -> dict:
    kind = item["type"]
    row = {"type": kind, "time": item.get("time") or "", "ours": item.get("ours"), "title": "", "subtitle": "", "score_after": None, "icon": "circle", "tone": "dim"}
    if kind in ("period_start", "period_end"):
        period = _("Period %(period)s") % {"period": item["period"]} if item.get("period") else _("Period")
        label = _("Start of %(period)s") if kind == "period_start" else _("End of %(period)s")
        row["marker"] = (label % {"period": period}).upper()
    elif kind == "goal":
        goal = item.get("goal") or {}
        row["title"] = _("Goal · %(player)s") % {"player": _player(goal.get("scorer"))}
        parts = []
        if goal.get("assists"):
            parts.append(_("Assists: %(players)s") % {"players": ", ".join(_player(person) for person in goal["assists"])})
        tags = []
        if goal.get("penalty_shot"):
            tags.append(_("Penalty shot"))
        if goal.get("empty_net"):
            tags.append(_("Empty net"))
        if goal.get("game_winning"):
            tags.append(_("Game winner"))
        if goal.get("strength") and goal["strength"] != "EQ":
            tags.append(goal["strength"])
        if tags:
            parts.append(" · ".join(tags))
        row["subtitle"] = " · ".join(parts)
        row["score_after"] = goal.get("score_after")
        row["icon"], row["tone"] = "circle-dot", "club" if item.get("ours") else "dim"
    elif kind == "penalty":
        penalty = item.get("penalty") or {}
        title = _player(penalty.get("player"))
        if penalty.get("minutes") is not None:
            title += " " + _("(%(minutes)s min)") % {"minutes": penalty["minutes"]}
        row["title"] = title
        row["subtitle"] = " — ".join(part for part in (penalty.get("code"), penalty.get("description")) if part)
        row["icon"], row["tone"] = "flag", "danger" if penalty.get("kind") in SEVERE_PENALTIES else "warn"
    elif kind == "goalie_change":
        change = item.get("goalie_change") or {}
        player = _player(change.get("player"))
        row["title"] = _("%(player)s in goal") % {"player": player} if change.get("action") == "in" else _("%(player)s leaves goal") % {"player": player}
        row["icon"], row["tone"] = "shield", "ice"
    elif kind == "penalty_shot":
        row["title"] = _("%(team)s penalty shot") % {"team": _team_label(event, item.get("side"), our_side)}
        row["icon"], row["tone"] = "crosshair", "club" if item.get("ours") else "dim"
    elif kind == "timeout":
        row["title"] = _("%(team)s timeout") % {"team": _team_label(event, item.get("side"), our_side)}
        row["icon"] = "pause"
    else:
        row["title"] = kind.replace("_", " ").capitalize()
    return row


def final_label(decided_in) -> str:
    if decided_in == "overtime":
        return _("Final · OT")
    if decided_in == "shootout":
        return _("Final · SO")
    return _("Final")


def timeline_context(event) -> dict:
    """What _game_timeline.html renders: ``timeline`` is None when the game has none (the card
    hides itself, as on iOS). Player names are always shown -- this is for logged-in members, like
    the app API's ``game_timeline``."""
    try:
        stored = GameTimeline.objects.filter(event=event).defer("raw").first()
    except ObjectDoesNotExist:
        stored = None
    if stored is None:
        return {"event": event, "timeline": None}
    data = timeline_for_api(stored, event, show_names=True)
    if not data["events"] and data["status"] != "live":
        return {"event": event, "timeline": None}
    our_side = data["our_side"]
    ours = ", ".join(team.name for team in event.teams.all()) or _("Us")
    theirs = event.opponent.name if event.opponent_id else _("Them")
    ours_logo = event.club.logo.url if event.club.logo else ""
    theirs_logo = event.opponent.logo.url if event.opponent_id and event.opponent.logo else ""
    home_score, away_score = data["home_score"], data["away_score"]
    final = data["final"] or data["status"] == "final"
    decided = home_score is not None and away_score is not None and home_score != away_score
    return {
        "event": event,
        "timeline": data,
        "live": data["status"] == "live",
        "final": final,
        "status_label": final_label(data["decided_in"]) if data["status"] == "final" else _("Live"),
        "home_name": ours if our_side == "home" else theirs,
        "away_name": ours if our_side == "away" else theirs,
        "home_logo": ours_logo if our_side == "home" else theirs_logo,
        "away_logo": ours_logo if our_side == "away" else theirs_logo,
        "home_dim": final and decided and home_score < away_score,
        "away_dim": final and decided and away_score < home_score,
        "rows": [_row(event, item, our_side) for item in data["events"]],
    }


def home_followed_games(user, club, flag_on: bool) -> list[dict]:
    """Home's followed-game cards in iOS order: live ones first, then the rest as the service
    ordered them (soonest upcoming, then just finished)."""
    if not flag_on:
        return []
    cards = []
    for row in followed_games(user, club):
        card = followed_game_out(row["event"], row["via"])
        card["event_obj"] = row["event"]
        card["pk"] = row["event"].pk
        card["location"] = row["event"].location
        event = row["event"]
        ours_logo = event.club.logo.url if event.club.logo else ""
        theirs_logo = event.opponent.logo.url if event.opponent_id and event.opponent.logo else ""
        card["home_logo"], card["away_logo"] = (ours_logo, theirs_logo) if event.is_home_game else (theirs_logo, ours_logo)
        card["start"] = row["event"].start
        card["finished_label"] = final_label(card["decided_in"]) if card["status"] == "finished" else ""
        cards.append(card)
    live = [card for card in cards if card["status"] == "live"]
    return live + [card for card in cards if card["status"] != "live"]


def directly_followed_games(user, club, *, limit=50):
    """Single games ``user`` follows directly that are live, upcoming or began in the last few hours
    -- the same list as the app API's ``GET /game-follows`` ``games`` (unfollow-able one by one)."""
    from django.db.models import Q
    from django.utils import timezone

    from events.services.game_push import FOLLOWED_GAME_LOOKBACK, followed_event_ids

    since = timezone.now() - FOLLOWED_GAME_LOOKBACK
    direct = Event.objects.filter(club=club, pk__in=followed_event_ids(user, club), kind=Event.EventKind.GAME, cancelled=False).filter(Q(start__gte=since) | Q(is_live=True))
    return list(direct.select_related("location", "opponent").prefetch_related("teams").order_by("start")[:limit])
