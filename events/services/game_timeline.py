"""Fetching and storing per-game timelines (goals, penalties, goalie changes,
...) -- see GAME_TIMELINE.md for the design and events.services.timeline_parsers
for turning each source's payload into the one normalized document.

Rides along with the existing score fetch: events.services.competitions.
fetch_game_info calls ``refresh_timeline`` right after a successful score
update, with the same competition instance (so CEHL/IJNL can reuse the payload
the score fetch already downloaded). Gated on the "game_timeline" flag on top
of the competition's own flag, and never allowed to break the score update: a
failure is recorded on the GameTimeline row (``last_error``, previous data
kept) and logged.

Federations correct a timeline after the final whistle (an assist added, a
scorer fixed), while poll_live_game_results stops polling a game once it's
seen finishing -- ``settle_timelines`` (the nightly settle_game_timelines job)
re-fetches recently finished games until their timeline stops changing.
"""

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import timedelta

from django.utils import timezone
from django.utils.translation import gettext as _

from events.models import Event, GameTimeline
from events.services.timeline_parsers import finalize_document

logger = logging.getLogger(__name__)

FLAG = "game_timeline"

#: A game is only settled once it's surely over...
SETTLE_AFTER = timedelta(hours=3)
#: ...and is given up on (left unsettled) after this long.
SETTLE_WINDOW = timedelta(days=7)


@dataclass
class TimelineFetch:
    """What a competition's fetch_timeline returns: the parsed (not yet
    finalized) document, and the raw payload it came from."""

    document: dict
    raw: str = ""


def timeline_enabled(club) -> bool:
    from features.models import Flag  # deferred: features imports back into club/events models

    flag = Flag.objects.filter(name=FLAG).first()
    return flag is not None and flag.is_active_for_club(club)


def document_hash(document: dict) -> str:
    return hashlib.sha256(json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def status_for(event, document: dict) -> str:
    """The source's own status when it has one (IJNL); otherwise live while the
    score fetch says so, final once a started game has anything on it."""
    if document.get("status") in GameTimeline.Status.values:
        return document["status"]
    if event.is_live:
        return GameTimeline.Status.LIVE
    if document.get("events") and event.start <= timezone.now():
        return GameTimeline.Status.FINAL
    return GameTimeline.Status.SCHEDULED


def store(event, fetched: TimelineFetch) -> tuple[GameTimeline, bool]:
    """Upserts ``event``'s timeline from ``fetched``; returns (timeline, changed).
    An unchanged document only bumps ``fetched_at``."""
    document = finalize_document(fetched.document)
    document["status"] = status_for(event, document)
    digest = document_hash(document)
    now = timezone.now()

    timeline, _created = GameTimeline.objects.get_or_create(event=event, defaults={"source": event.competition})
    changed = digest != timeline.data_hash
    timeline.fetched_at = now
    timeline.last_error = ""
    if changed:
        timeline.source = event.competition
        timeline.data = document
        timeline.data_hash = digest
        timeline.status = document["status"]
        timeline.raw = fetched.raw or ""
        timeline.changed_at = now
    timeline.save()
    return timeline, changed


def record_error(event, error) -> None:
    timeline, _created = GameTimeline.objects.get_or_create(event=event, defaults={"source": event.competition})
    timeline.last_error = str(error)[:2000] or error.__class__.__name__
    timeline.save(update_fields=["last_error"])


#: fetch_and_store outcomes.
CHANGED, UNCHANGED, FAILED, UNAVAILABLE = "changed", "unchanged", "failed", "unavailable"


def fetch_and_store(event, competition) -> str:
    """One fetch for ``event`` through ``competition`` (an events.competition
    instance). Returns CHANGED/UNCHANGED, FAILED (recorded on the timeline row
    and logged -- never raised), or UNAVAILABLE when the competition has no
    timeline to offer."""
    try:
        fetched = competition.fetch_timeline(event)
    except Exception as error:
        logger.warning("game_timeline.fetch_failed event_id=%s competition=%s error=%s", event.pk, event.competition, error)
        record_error(event, error)
        return FAILED
    if fetched is None:
        return UNAVAILABLE
    _timeline, changed = store(event, fetched)
    return CHANGED if changed else UNCHANGED


def refresh_timeline(event, competition) -> str | None:
    """fetch_game_info's hook: fetch and store, but only for clubs with the
    "game_timeline" flag."""
    if not timeline_enabled(event.club):
        return None
    return fetch_and_store(event, competition)


def settle_candidates(now=None):
    """Finished games whose timeline may still be corrected: started at least
    SETTLE_AFTER ago, within SETTLE_WINDOW, not yet finalized."""
    now = now or timezone.now()
    return GameTimeline.objects.filter(finalized_at__isnull=True, event__cancelled=False, event__start__lte=now - SETTLE_AFTER, event__start__gte=now - SETTLE_WINDOW).select_related("event", "event__club", "event__location").order_by("event__start")


FINALIZED = "finalized"


def settle(timeline: GameTimeline, competition) -> str:
    """Re-fetches one finished game. A final timeline that came back unchanged
    is marked finalized and won't be fetched again. Returns FINALIZED, or
    fetch_and_store's own outcome."""
    outcome = fetch_and_store(timeline.event, competition)
    if outcome != UNCHANGED:
        return outcome
    timeline.refresh_from_db()
    if timeline.status != GameTimeline.Status.FINAL:
        return outcome
    timeline.finalized_at = timezone.now()
    timeline.save(update_fields=["finalized_at"])
    return FINALIZED


def games_for_backfill(*, since, club=None):
    """Played games with a competition game id, for the manual
    fetch_game_timelines command."""
    games = Event.objects.filter(kind=Event.EventKind.GAME, cancelled=False, start__gte=since, start__lte=timezone.now()).exclude(competition="").exclude(external_game_id="")
    if club is not None:
        games = games.filter(club=club)
    return games.select_related("club", "location").order_by("start")


def _person(person) -> str:
    if not person:
        return ""
    number = person.get("number")
    return f"{person.get('name', '')} (#{number})" if number is not None else person.get("name", "")


def _describe(item: dict) -> str:
    kind = item["type"]
    if kind == "goal":
        goal = item.get("goal") or {}
        text = _("Goal: %(scorer)s") % {"scorer": _person(goal.get("scorer"))}
        if goal.get("assists"):
            text += " " + _("(assists: %(assists)s)") % {"assists": ", ".join(_person(person) for person in goal["assists"])}
        return text
    if kind == "penalty":
        penalty = item.get("penalty") or {}
        reason = penalty.get("description") or penalty.get("code") or ""
        if penalty.get("minutes"):
            return _("Penalty: %(player)s, %(reason)s, %(minutes)s min") % {"player": _person(penalty.get("player")), "reason": reason, "minutes": penalty["minutes"]}
        return _("Penalty: %(player)s, %(reason)s") % {"player": _person(penalty.get("player")), "reason": reason}
    if kind == "goalie_change":
        change = item.get("goalie_change") or {}
        template = _("Goalie in: %(player)s") if change.get("action") == "in" else _("Goalie out: %(player)s")
        return template % {"player": _person(change.get("player"))}
    if kind == "timeout":
        return _("Time-out")
    if kind == "period_start":
        return _("Period %(period)s started") % {"period": item.get("period") or ""}
    if kind == "period_end":
        return _("Period %(period)s ended") % {"period": item.get("period") or ""}
    return kind


def display_periods(timeline: GameTimeline, event) -> list[dict]:
    """``timeline``'s events grouped by period for a template, each row with
    who it was from the club's side ("ours" -- same Event.is_home_game
    convention as the score), the score after a goal from our side, and a
    translated one-line description."""
    our_side = "home" if event.is_home_game else "away"
    ours_label = ", ".join(team.short_name or team.name for team in event.teams.all())
    theirs_label = event.opponent.name if event.opponent_id else _("Opponent")
    periods = []
    for item in (timeline.data or {}).get("events", []):
        label = item.get("period") or "?"
        if not periods or periods[-1]["label"] != label:
            periods.append({"label": label, "rows": []})
        side = item.get("side")
        row = {"time": item.get("time") or "", "type": item["type"], "ours": side == our_side if side else None, "team": (ours_label if side == our_side else theirs_label) if side else "", "text": _describe(item)}
        score = (item.get("goal") or {}).get("score_after")
        if score:
            row["score"] = f"{score[our_side]}-{score['away' if our_side == 'home' else 'home']}"
        periods[-1]["rows"].append(row)
    return periods
