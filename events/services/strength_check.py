"""Checking a goal's recorded strength (EQ / PP1 / SH1 ...) against the game's own penalty
timeline -- sources get it wrong now and then (a missing badge reads as "EQ").

The penalties are replayed per side as on the ice (IIHF rules): a minor (2 min), bench minor and
double minor (two consecutive minors) and a major (5 min) / match penalty leave the team a skater
short; a misconduct (10) or game misconduct (20) puts the player in the box without costing the team a
skater. At most two penalties are served at once (later ones wait their turn), and a goal against a
team that is a skater down ends its earliest-expiring minor -- but never a major.

One player's penalties on the same second (a 2+2) are served one after the other, so cost a single skater.

A goal is only judged when the penalty timeline can say for sure: not in overtime or a shoot-out,
not for penalty shots and empty-net goals, and not when a penalty starts or ends on the very
second of the goal (a delayed penalty's goal waives the minor, and the clock can't order those).
"""

from django.utils.translation import gettext_lazy as _

#: kind -> (seconds a skater is short for, whether a goal against that team ends it early). Misconducts aren't listed: no skater is lost.
SERVED = {"minor": (120, True), "bench_minor": (120, True), "major": (300, False), "match": (300, False)}
#: Two consecutive minors, served one after the other.
DOUBLE_MINOR = "double_minor"
_KIND_FOR_MINUTES = {2: "minor", 4: DOUBLE_MINOR, 5: "major"}
REGULATION_SECONDS = 3600
#: Strengths a user can pick when fixing a goal, with how they read.
STRENGTH_LABELS = {
    "EQ": _("even strength"),
    "PP1": _("power play"),
    "PP2": _("5-on-3 power play"),
    "SH1": _("short-handed"),
    "SH2": _("short-handed"),
}
STRENGTHS = tuple(STRENGTH_LABELS)
#: Recorded strengths that say nothing about the skaters on the ice (empty net, penalty shot), so aren't judged.
UNJUDGED = {"EN", "PS"}


def strength_class(strength: str | None) -> str:
    """ "PP", "SH" or "EQ" -- PP1 and PP2 are both a power play, which is all a conflict is judged on."""
    prefix = (strength or "")[:2]
    return prefix if prefix in ("PP", "SH") else "EQ"


def expected_strength(own: int, opposing: int) -> str:
    """The strength for the scoring side with ``own`` skaters against ``opposing``."""
    if own > opposing:
        return "PP2" if own - opposing >= 2 else "PP1"
    if own < opposing:
        return "SH2" if opposing - own >= 2 else "SH1"
    return "EQ"


def _kind(penalty: dict) -> str:
    return penalty.get("kind") or _KIND_FOR_MINUTES.get(penalty.get("minutes"), "")


def _entries(kind: str, start: int, group: int, label: dict) -> list[dict]:
    if kind == DOUBLE_MINOR:
        return [{"remaining": 120, "cancellable": True, "group": group, "start": start, **label} for _part in range(2)]
    if kind in SERVED:
        seconds, cancellable = SERVED[kind]
        return [{"remaining": seconds, "cancellable": cancellable, "group": group, "start": start, **label}]
    return []


def _serving(queue: list[dict]) -> list[dict]:
    """The penalties being served right now: the first two, one per player (a double minor's second half waits)."""
    served, groups = [], set()
    for entry in queue:
        if entry["group"] in groups:
            continue
        groups.add(entry["group"])
        served.append(entry)
        if len(served) == 2:
            break
    return served


def _advance(queue: list[dict], seconds: int) -> bool:
    """Runs the penalty clocks ``seconds`` forward; returns whether one ran out on exactly the last second."""
    expired_at_end = False
    while seconds > 0:
        served = _serving(queue)
        if not served:
            return False
        step = min(seconds, *(entry["remaining"] for entry in served))
        for entry in served:
            entry["remaining"] -= step
        seconds -= step
        before = len(queue)
        queue[:] = [entry for entry in queue if entry["remaining"] > 0]
        expired_at_end = seconds == 0 and len(queue) < before
    return expired_at_end


def strength_conflicts(document: dict, overrides: dict | None = None) -> dict:
    """{goal event id: {"expected": "PP1", "penalties": [...]}} for the goals whose strength (a user's
    ``overrides`` entry, else what the source recorded) conflicts with the penalty timeline -- a goal with an
    override is never flagged: staff already looked at it. ``penalties``
    are the ones in the box at the goal: {"side", "time", "player", "kind"}."""
    overrides = overrides or {}
    events = [event for event in document.get("events", []) if event.get("seconds") is not None and event.get("side") in ("home", "away")]
    penalty_seconds = {event["seconds"] for event in events if event["type"] == "penalty"}
    goals_by_side = {(event["side"], event["seconds"]) for event in events if event["type"] == "goal"}
    queues = {"home": [], "away": []}
    conflicts = {}
    clock, group, edge = 0, 0, False
    groups = {}  # (side, second, player) -> the group a player's penalties of that second share
    for event in events:
        seconds = event["seconds"]
        if seconds > clock:  # events sharing a second share what ran out on it
            edge = _advance(queues["home"], seconds - clock) | _advance(queues["away"], seconds - clock)
            clock = seconds
        side, other = event["side"], "away" if event["side"] == "home" else "home"

        if event["type"] == "penalty":
            penalty = event.get("penalty") or {}
            kind = _kind(penalty)
            person = penalty.get("player") or {}
            who = f"#{person['number']} {person.get('name', '')}".strip() if person.get("number") is not None else person.get("name", "")
            # One player can only sit one penalty at a time: two for the same player on the same second (a 2+2) are
            # served consecutively, like a double minor, so they share a group.
            key = (side, seconds, who) if who and (kind in SERVED or kind == DOUBLE_MINOR) else None
            if key is None or key not in groups:
                group += 1
                groups[key] = group
            entries = _entries(kind, seconds, groups[key], {"time": event.get("time") or "", "player": who, "kind": kind, "side": side})
            # A minor the other side scored on the same second (a delayed penalty) is waived rather than served.
            if (other, seconds) in goals_by_side and entries and entries[0]["cancellable"]:
                entries = entries[1:]
            queues[side].extend(entries)
        elif event["type"] == "goal":
            goal = event.get("goal") or {}
            if goal.get("penalty_shot"):
                continue
            own, opposing = 5 - len(_serving(queues[side])), 5 - len(_serving(queues[other]))
            recorded = goal.get("strength") or ""
            judged = not (event.get("id") in overrides or edge or seconds in penalty_seconds or seconds > REGULATION_SECONDS or str(event.get("period") or "").upper().startswith(("OT", "SO")) or goal.get("empty_net") or recorded in UNJUDGED)
            expected = expected_strength(own, opposing)
            if judged and strength_class(recorded) != strength_class(expected):
                conflicts[event["id"]] = {"expected": expected, "penalties": [{key: entry[key] for key in ("side", "time", "player", "kind")} for entry in _serving(queues["home"]) + _serving(queues["away"])]}
            if opposing < own:  # a goal against a team that is a skater down ends its earliest-expiring minor
                cancellable = [entry for entry in _serving(queues[other]) if entry["cancellable"]]
                if cancellable:
                    queues[other].remove(min(cancellable, key=lambda entry: entry["remaining"]))
    return conflicts
