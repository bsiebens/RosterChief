"""Turning a change in a game's timeline into "moments" -- the things a person
would call news, from the club's own side: a goal for or against, a goal
taken back, a penalty, a period or the game ending (GAME_TIMELINE.md §4).

events.services.game_timeline.store sends ``game_timeline_changed`` whenever
a fetch actually changed a game's timeline; ``record_moments`` (connected in
events/signals.py) derives the moments and stores each once as an
events.GameMoment. Nothing is pushed yet -- that's phase 4, and it will read
GameMoment rows (``notifiable``, ``delivered_at``) rather than re-deriving.

``derive_moments`` is pure: the previous and current documents in, moments
out, so the rules are tested without fetching anything. The guards that keep
this from becoming spam live here too:

- a game's first fetch yields nothing (a deploy mid-game, a freshly imported
  or backfilled game would otherwise "detect" every goal at once);
- changes to a game that was already final are post-game corrections, which
  update the timeline but aren't news;
- a moment is only ``notifiable`` when it happened recently in game time
  (within RECENT_SECONDS of the newest event), so catching up after a source
  outage doesn't replay a whole period.
"""

import logging
from dataclasses import dataclass, field

import django.dispatch
from django.db import IntegrityError, transaction
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy as _lazy
from django.utils.translation import ngettext

from events.models import GameMoment

logger = logging.getLogger(__name__)

#: sender=Event; kwargs: previous (document or None), current (document),
#: first_fetch (bool). Sent by game_timeline.store when the document changed.
game_timeline_changed = django.dispatch.Signal()

#: How far behind the newest event (in game seconds) a moment may be and still
#: be worth telling someone about right now.
RECENT_SECONDS = 5 * 60

#: events.services.timeline_parsers.PENALTY_MINUTES' kinds, as people read them.
PENALTY_KIND_LABELS = {
    "minor": _lazy("Minor"),
    "bench_minor": _lazy("Bench minor"),
    "double_minor": _lazy("Double minor"),
    "major": _lazy("Major"),
    "misconduct": _lazy("Misconduct"),
    "game_misconduct": _lazy("Game misconduct"),
    "match": _lazy("Match penalty"),
}

GAME_STARTED = "game_started"
GOAL_FOR, GOAL_AGAINST, GOAL_CANCELLED = "goal_for", "goal_against", "goal_cancelled"
PENALTY_FOR, PENALTY_AGAINST = "penalty_for", "penalty_against"
PENALTY_SHOT_FOR, PENALTY_SHOT_AGAINST = "penalty_shot_for", "penalty_shot_against"
PERIOD_STARTED = "period_started"
PERIOD_ENDED = "period_ended"
GAME_FINAL = "game_final"


@dataclass
class Moment:
    kind: str
    key: str  # unique per game -- the kind plus the timeline event id it's about
    payload: dict = field(default_factory=dict)
    game_seconds: int | None = None
    notifiable: bool = True


def _score(document: dict) -> dict:
    score = (document or {}).get("score") or {}
    return {"home": score.get("home") or 0, "away": score.get("away") or 0}


def _ours(side: str | None, our_side: str) -> bool | None:
    return None if side is None else side == our_side


def _for_against(score: dict, our_side: str) -> dict:
    their_side = "away" if our_side == "home" else "home"
    return {"for": score.get(our_side, 0), "against": score.get(their_side, 0)}


def derive_moments(previous: dict | None, current: dict, *, our_side: str, first_fetch: bool) -> list[Moment]:
    """What's new in ``current`` compared to ``previous``, from ``our_side``'s
    ("home"/"away") point of view. See the module docstring for the guards."""
    if first_fetch or not previous:
        return []
    previous_status, status = previous.get("status"), current.get("status")
    if previous_status == "final":
        return []  # a post-game correction: the timeline updates, nobody is told

    previous_events = {item["id"]: item for item in previous.get("events", [])}
    current_events = {item["id"]: item for item in current.get("events", [])}
    latest = max((item.get("seconds") or 0 for item in current_events.values()), default=0)
    score_now = _for_against(_score(current), our_side)
    moments = []

    if previous_status == "scheduled" and status in ("live", "final"):
        moments.append(Moment(kind=GAME_STARTED, key=GAME_STARTED, payload={"score": score_now}))

    for event_id, item in current_events.items():
        if event_id in previous_events:
            continue
        ours = _ours(item.get("side"), our_side)
        seconds = item.get("seconds")
        recent = seconds is None or latest - seconds <= RECENT_SECONDS
        base = {"timeline_event_id": event_id, "period": item.get("period"), "time": item.get("time"), "ours": ours}
        if item["type"] == "goal" and ours is not None:
            goal = item.get("goal") or {}
            score_after = _for_against(goal["score_after"], our_side) if goal.get("score_after") else score_now
            kind = GOAL_FOR if ours else GOAL_AGAINST
            moments.append(
                Moment(kind=kind, key=f"{kind}:{event_id}", game_seconds=seconds, notifiable=recent, payload={**base, "scorer": goal.get("scorer"), "assists": goal.get("assists", []), "strength": goal.get("strength"), "score": score_after})
            )
        elif item["type"] == "penalty" and ours is not None:
            penalty = item.get("penalty") or {}
            kind = PENALTY_FOR if ours else PENALTY_AGAINST
            moments.append(
                Moment(
                    kind=kind,
                    key=f"{kind}:{event_id}",
                    game_seconds=seconds,
                    notifiable=recent,
                    payload={**base, "player": penalty.get("player"), "description": penalty.get("description"), "minutes": penalty.get("minutes"), "kind": penalty.get("kind")},
                )
            )
        elif item["type"] == "penalty_shot" and ours is not None:
            kind = PENALTY_SHOT_FOR if ours else PENALTY_SHOT_AGAINST
            moments.append(Moment(kind=kind, key=f"{kind}:{event_id}", game_seconds=seconds, notifiable=recent, payload={**base, "score": score_now}))
        elif item["type"] == "period_start":
            moments.append(Moment(kind=PERIOD_STARTED, key=f"{PERIOD_STARTED}:{item.get('period') or event_id}", game_seconds=seconds, notifiable=recent, payload={**base, "score": score_now}))
        elif item["type"] == "period_end":
            moments.append(Moment(kind=PERIOD_ENDED, key=f"{PERIOD_ENDED}:{item.get('period') or event_id}", game_seconds=seconds, notifiable=recent, payload={**base, "score": score_now}))

    # A goal that vanished only counts as taken back when the score actually went
    # down -- otherwise it's the same goal re-keyed (a corrected scorer changes
    # RBIHF/CEHL's synthesized id), which isn't news.
    previous_total, current_total = sum(_score(previous).values()), sum(_score(current).values())
    if current_total < previous_total:
        for event_id, item in previous_events.items():
            if item["type"] == "goal" and event_id not in current_events:
                ours = _ours(item.get("side"), our_side)
                moments.append(
                    Moment(kind=GOAL_CANCELLED, key=f"{GOAL_CANCELLED}:{event_id}", game_seconds=item.get("seconds"), payload={"timeline_event_id": event_id, "period": item.get("period"), "time": item.get("time"), "ours": ours, "score": score_now})
                )

    if status == "final" and previous_status != "final":
        moments.append(Moment(kind=GAME_FINAL, key=GAME_FINAL, payload={"score": score_now, "decided_in": current.get("decided_in")}))

    return moments


def record_moments(sender, *, event, previous, current, first_fetch, **kwargs):
    """game_timeline_changed receiver: stores each derived moment once, then
    hands the notifiable ones to events.services.game_push for delivery. The
    unique (event, key) constraint makes a repeat (a retry, two pollers racing)
    a no-op rather than a duplicate -- and a duplicate is never pushed twice."""
    from events.services.game_push import schedule_delivery  # deferred: game_push imports describe_moment from here

    our_side = "home" if event.is_home_game else "away"
    recorded = []
    for moment in derive_moments(previous, current, our_side=our_side, first_fetch=first_fetch):
        try:
            with transaction.atomic():
                stored = GameMoment.objects.create(event=event, key=moment.key, kind=moment.kind, payload=moment.payload, game_seconds=moment.game_seconds, notifiable=moment.notifiable)
        except IntegrityError:
            continue
        logger.info("game_moment event_id=%s kind=%s key=%s notifiable=%s", event.pk, moment.kind, moment.key, moment.notifiable)
        if stored.notifiable:
            recorded.append(stored.pk)
    schedule_delivery(event, recorded)


def person_label(person) -> str:
    if not person:
        return ""
    number = person.get("number")
    return f"{person.get('name', '')} (#{number})".strip() if number is not None else person.get("name", "")


def describe_moment(moment: GameMoment) -> str:
    """One translated line for a stored moment (the management event page)."""
    payload = moment.payload or {}
    score = payload.get("score") or {}
    score_text = f"{score.get('for', 0)}-{score.get('against', 0)}" if score else ""
    if moment.kind == GameMoment.Kind.GAME_STARTED:
        return _("The game started.")
    if moment.kind == GameMoment.Kind.GOAL_FOR:
        # Our own goals name who assisted; an opponent's goal is just the scorer (below), and an
        # unassisted one reads as it always has.
        assists = [name for name in (person_label(person) for person in payload.get("assists") or []) if name]
        if assists:
            template = ngettext("Goal for us by %(player)s (assist: %(assists)s), %(score)s.", "Goal for us by %(player)s (assists: %(assists)s), %(score)s.", len(assists))
            return template % {"player": person_label(payload.get("scorer")) or "?", "assists": ", ".join(assists), "score": score_text}
        return _("Goal for us by %(player)s, %(score)s.") % {"player": person_label(payload.get("scorer")) or "?", "score": score_text}
    if moment.kind == GameMoment.Kind.GOAL_AGAINST:
        return _("Goal against by %(player)s, %(score)s.") % {"player": person_label(payload.get("scorer")) or "?", "score": score_text}
    if moment.kind == GameMoment.Kind.GOAL_CANCELLED:
        return _("A goal was taken back, %(score)s.") % {"score": score_text}
    if moment.kind in (GameMoment.Kind.PENALTY_FOR, GameMoment.Kind.PENALTY_AGAINST):
        template = _("Penalty for us: %(player)s, %(reason)s.") if moment.kind == GameMoment.Kind.PENALTY_FOR else _("Penalty for the opponent: %(player)s, %(reason)s.")
        reason = payload.get("description") or ""
        if payload.get("kind") not in (None, "", "minor"):
            reason = _("%(reason)s (%(kind)s)") % {"reason": reason, "kind": str(PENALTY_KIND_LABELS.get(payload["kind"], payload["kind"])).lower()}
        return template % {"player": person_label(payload.get("player")) or "?", "reason": reason}
    if moment.kind == GameMoment.Kind.PENALTY_SHOT_FOR:
        return _("Penalty shot for us.")
    if moment.kind == GameMoment.Kind.PENALTY_SHOT_AGAINST:
        return _("Penalty shot for the opponent.")
    if moment.kind == GameMoment.Kind.PERIOD_STARTED:
        return _("Period %(period)s started.") % {"period": payload.get("period") or ""}
    if moment.kind == GameMoment.Kind.PERIOD_ENDED:
        return _("Period %(period)s ended, %(score)s.") % {"period": payload.get("period") or "", "score": score_text}
    if moment.kind == GameMoment.Kind.GAME_FINAL:
        return _("Final result %(score)s.") % {"score": score_text}
    return moment.get_kind_display()
