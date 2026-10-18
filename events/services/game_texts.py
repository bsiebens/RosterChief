"""Every string a live game push is made of, in one place -- edit the wording here.

A push is a title plus one body line per moment (events.services.game_push batches the moments a
poll found into one push). Rules that hold for all of them:

* **Title** = the scoreboard, always: "Leuven Chiefs 9-7 Sharks U16" -- the home team first (at an
  away location the opponent leads and the score flips with it; no location reads our team first).
  It is the score as of the latest moment known, so it is the same shape for a goal, a penalty, a
  period end and the final.
* **Body line** = ``HEADLINE TEAM`` in capitals, then details separated by `` . ``:
  ``GOAL Sharks U16 . Elsen Bob (#19) . assist Janssens Tom (#7) . P2 34:12``.
* Our team is its full team name ("Sharks U16"), theirs the opponent's name; a player is
  "Elsen Bob (#19)" -- the name exactly as the source lists it (surname first; it carries no separate first/last name to shorten).
* No emojis. Words are translated (events/locale/<lang>/django.po); penalty reasons come from the
  competition's own data and are shown as delivered.

Examples (title / body):

    Sharks U16 1-0 Leuven Chiefs  /  GOAL Sharks U16 . Elsen Bob (#19) . P1 04:10
    Sharks U16 1-0 Leuven Chiefs  /  PENALTY Leuven Chiefs . Stoffels Brais (#2) . Roughing . 2 min . P1 06:45
    Sharks U16 1-0 Leuven Chiefs  /  END OF PERIOD 1
    Sharks U16 4-2 Leuven Chiefs  /  FINAL . Sharks U16 win
"""

from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from events.models import GameMoment
from events.services.game_moments import PENALTY_KIND_LABELS

#: Between the details of one line.
SEPARATOR = " \u00b7 "
#: Between the two scores in the title.
DASH = "\u2013"


# -- building blocks -----------------------------------------------------------------------------


def team_names(event) -> tuple[str, str]:
    """(ours, theirs): our team(s) by full name ("Sharks U16"), the opponent by name."""
    ours = ", ".join(team.name for team in event.teams.all())
    return ours, event.opponent.name if event.opponent_id else _("Opponent")


def person(value) -> str:
    """ "Elsen Bob (#19)" from a stored {"name": "Elsen Bob", "number": 19}, the name exactly as the
    source delivers it (surname first); "" when there is none."""
    if not value:
        return ""
    name = (value.get("name") or "").strip()
    number = value.get("number")
    return f"{name} (#{number})".strip() if number is not None else name


def clock(payload) -> str:
    """ "P2 34:12" -- the period and the game clock, whichever the moment carries."""
    period, time = payload.get("period"), payload.get("time")
    parts = [f"P{period}" if str(period or "").isdigit() else str(period or ""), time or ""]
    return " ".join(part for part in parts if part)


def scoreboard(event, score) -> str:
    """ "Sharks U16 4-2 Leuven Chiefs", home team first (see the module docstring)."""
    ours, theirs = team_names(event)
    ours_goals, theirs_goals = score.get("for", 0), score.get("against", 0)
    if event.location_id is not None and not event.location.is_home:
        return f"{theirs} {theirs_goals}{DASH}{ours_goals} {ours}"
    return f"{ours} {ours_goals}{DASH}{theirs_goals} {theirs}"


def _line(headline, *details) -> str:
    return SEPARATOR.join(part for part in (headline, *details) if part)


# -- one line per kind of moment -----------------------------------------------------------------


def goal(event, moment) -> str:
    ours, theirs = team_names(event)
    kinds = GameMoment.Kind
    ours_side = moment.kind == kinds.GOAL_FOR
    payload = moment.payload or {}
    # Only our own goals name the assists; an opponent's is just the scorer.
    assists = [name for name in (person(p) for p in payload.get("assists") or []) if name] if ours_side else []
    assist = ngettext("assist %(assists)s", "assists %(assists)s", len(assists)) % {"assists": ", ".join(assists)} if assists else ""
    return _line(_("GOAL %(team)s") % {"team": ours if ours_side else theirs}, person(payload.get("scorer")) or "?", assist, clock(payload))


def goal_cancelled(event, moment) -> str:
    return _line(_("GOAL TAKEN BACK"), clock(moment.payload or {}))


def penalty(event, moment) -> str:
    ours, theirs = team_names(event)
    payload = moment.payload or {}
    reason = payload.get("description") or ""
    if payload.get("kind") not in (None, "", "minor"):
        reason = _("%(reason)s (%(kind)s)") % {"reason": reason, "kind": str(PENALTY_KIND_LABELS.get(payload["kind"], payload["kind"])).lower()}
    minutes = _("%(minutes)s min") % {"minutes": payload["minutes"]} if payload.get("minutes") else ""
    team = ours if moment.kind == GameMoment.Kind.PENALTY_FOR else theirs
    return _line(_("PENALTY %(team)s") % {"team": team}, person(payload.get("player")) or "?", reason, minutes, clock(payload))


def penalty_shot(event, moment) -> str:
    ours, theirs = team_names(event)
    team = ours if moment.kind == GameMoment.Kind.PENALTY_SHOT_FOR else theirs
    return _line(_("PENALTY SHOT %(team)s") % {"team": team}, clock(moment.payload or {}))


def period_ended(event, moment) -> str:
    return _("END OF PERIOD %(period)s") % {"period": (moment.payload or {}).get("period") or ""}


def game_started(event, moment) -> str:
    return _("GAME STARTED")


def game_final(event, moment) -> str:
    ours, theirs = team_names(event)
    score = (moment.payload or {}).get("score") or {}
    if score.get("for", 0) > score.get("against", 0):
        outcome = _("%(team)s win") % {"team": ours}
    elif score.get("for", 0) < score.get("against", 0):
        outcome = _("%(team)s win") % {"team": theirs}
    else:
        outcome = _("Draw")
    return _line(_("FINAL"), outcome)


LINES = {
    GameMoment.Kind.GAME_STARTED: game_started,
    GameMoment.Kind.GOAL_FOR: goal,
    GameMoment.Kind.GOAL_AGAINST: goal,
    GameMoment.Kind.GOAL_CANCELLED: goal_cancelled,
    GameMoment.Kind.PENALTY_FOR: penalty,
    GameMoment.Kind.PENALTY_AGAINST: penalty,
    GameMoment.Kind.PENALTY_SHOT_FOR: penalty_shot,
    GameMoment.Kind.PENALTY_SHOT_AGAINST: penalty_shot,
    GameMoment.Kind.PERIOD_ENDED: period_ended,
    GameMoment.Kind.GAME_FINAL: game_final,
}


# -- the whole push ------------------------------------------------------------------------------


def current_score(event, moments) -> dict:
    """The score as of the latest moment known for the game: this batch's first, then earlier ones
    (a penalty carries no score of its own). 0-0 when nothing has scored yet."""
    for moment in [*reversed(moments), *event.moments.order_by("-game_seconds", "-created_at")[:50]]:
        score = (moment.payload or {}).get("score")
        if score:
            return score
    return {"for": 0, "against": 0}


def compose(event, moments) -> tuple[str, str]:
    """(title, body) for one push: the scoreboard over one line per moment."""
    lines = [LINES.get(moment.kind, lambda _event, m: m.get_kind_display())(event, moment) for moment in moments]
    return scoreboard(event, current_score(event, moments)), "\n".join(lines)
