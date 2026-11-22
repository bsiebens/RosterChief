"""A match's timeline (``MatchEvent``) and the attributes derived from it.

Goal events fill in the score: whenever they change, ``score_home``/``score_away`` become the number of goal events per side (only while
the match has at least one goal event -- deleting the last one leaves the score as it was). A match that was already complete takes the
new score through ``record_result`` -- the same path a result edit uses -- so the bracket slots it feeds follow; when that is not safe
(a later match that depends on it was already played, or the match would stop having a winner) ``MatchLocked`` is raised and the
caller must roll the change back (call this inside ``transaction.atomic``). Timeline attributes (``MatchAttribute.source == "timeline"``)
are recomputed from the events on every change and are never edited by hand.
"""

from django.db.models import Q

from ..models import Match, MatchAttribute, MatchAttributeValue, MatchEvent
from .scheduling import record_result


class MatchLocked(Exception):
    """The new score of a completed match cannot be pushed through to the matches that depend on it."""


def recompute_timeline_attributes(match: Match, attributes=None) -> None:
    """Rewrites the values of the tournament's timeline attributes (or just ``attributes``) from this match's events."""
    if attributes is None:
        attributes = MatchAttribute.objects.filter(tournament_id=match.stage.tournament_id, source=MatchAttribute.Source.TIMELINE)
    attributes = list(attributes)
    if not attributes:
        return
    events = list(match.events.all())
    for attribute in attributes:
        totals = {"home": 0, "away": 0}
        for event in events:
            if event.type != attribute.event_type or event.side not in totals:
                continue
            totals[event.side] += 1 if attribute.aggregate == MatchAttribute.Aggregate.COUNT else (event.value or 0)
        MatchAttributeValue.objects.update_or_create(match=match, attribute=attribute, defaults={"home_value": totals["home"], "away_value": totals["away"]})


def recompute_attribute(attribute: MatchAttribute) -> None:
    """After a timeline attribute was created or changed: refresh it for every match of the tournament that has events."""
    for match in Match.objects.filter(stage__tournament_id=attribute.tournament_id, events__isnull=False).distinct().select_related("stage"):
        recompute_timeline_attributes(match, [attribute])


def _dependents(match: Match) -> list[Match]:
    return list(Match.objects.filter(Q(home_source_match=match) | Q(away_source_match=match)))


def sync_match(match: Match) -> None:
    """Brings the match in line with its events: timeline attributes, then the score from the goals (see the module docstring)."""
    recompute_timeline_attributes(match)
    goals = list(match.events.filter(type=MatchEvent.Type.GOAL))
    if not goals:
        return
    home = sum(1 for e in goals if e.side == MatchEvent.Side.HOME)
    away = sum(1 for e in goals if e.side == MatchEvent.Side.AWAY)
    if (match.score_home, match.score_away) == (home, away):
        return
    if match.status != Match.Status.COMPLETE:
        match.score_home, match.score_away = home, away
        match.save(update_fields=["score_home", "score_away"])
        return

    old_winner_id = match.winner.pk if match.winner else None
    match.score_home, match.score_away = home, away  # to judge the new winner; record_result saves
    new_winner_id = match.winner.pk if match.winner else None
    dependents = _dependents(match)
    if dependents and old_winner_id != new_winner_id:
        if new_winner_id is None:
            raise MatchLocked("This match feeds a later match and would no longer have a winner.")
        if any(d.status in (Match.Status.COMPLETE, Match.Status.IN_PROGRESS) for d in dependents):
            raise MatchLocked("This change would alter the winner of a match that a later, already started match depends on.")
    record_result(match, score_home=home, score_away=away, went_to_overtime=match.went_to_overtime, penalty_home=match.penalty_home, penalty_away=match.penalty_away)
