"""Fetching and storing per-game timelines (goals, penalties, goalie changes,
...) -- see GAME_TIMELINE.md for the design and events.services.timeline_parsers
for turning each source's payload into the one normalized document.

Rides along with the score fetch: ``fetch_game`` asks the game's competition
(an events.competition class, resolved by ``competition_for``) to update the
score, then calls ``refresh_timeline`` with the same competition instance (so
CEHL/IJNL can reuse the payload the score fetch already downloaded). Which clubs
may use a competition is its feature (club.Competition.feature); there is no
separate timeline switch any more. A timeline failure never breaks the score
update: it is recorded on the GameTimeline row (``last_error``, previous data
kept) and logged.

The scheduled work (events.tasks) is ``poll_live_games`` (every minute: games
about to start, live, or just finished) and ``settle_timelines`` (nightly).

Every stored timeline also has its per-player GameStat rows (events.services.game_stats):
written when a changed document is stored, and re-checked when a game settles.

Federations correct a timeline after the final whistle (an assist added, a
scorer fixed), while poll_live_game_results stops polling a game once it's
seen finishing -- ``settle_timelines`` (the nightly settle_game_timelines job)
re-fetches recently finished games until their timeline stops changing.
"""

import datetime
import hashlib
import importlib
import json
import logging
from dataclasses import dataclass
from datetime import timedelta

from django.core.cache import cache
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy as _lazy

from events.models import Event, GameTimeline
from events.services.game_moments import PENALTY_KIND_LABELS, game_timeline_changed
from events.services.game_stats import ensure_game_stats, sync_game_stats
from events.services.timeline_parsers import SERIOUS_PENALTIES, finalize_document, parse_cehl, parse_hockeydata, parse_ijnl

logger = logging.getLogger(__name__)

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
    An unchanged document only bumps ``fetched_at``; a changed one sends
    game_moments.game_timeline_changed (with the previous document, and whether
    this is the game's first real fetch) once it's saved."""
    document = finalize_document(fetched.document)
    document["status"] = status_for(event, document)
    digest = document_hash(document)
    now = timezone.now()

    timeline, _created = GameTimeline.objects.get_or_create(event=event, defaults={"source": event.competition})
    changed = digest != timeline.data_hash
    previous, first_fetch = (timeline.data or None), not timeline.data_hash
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
    if changed:
        game_timeline_changed.send(sender=Event, event=event, previous=previous, current=document, first_fetch=first_fetch)
        try:
            sync_game_stats(event, document)
        except Exception:  # analytics must never break the timeline update, like the moments above
            logger.exception("game_timeline.stats_sync_failed event_id=%s", event.pk)
    return timeline, changed


#: Sources whose stored ``raw`` payload is all their parser needs (RBIHF's parser also needs the game's own roster data, so it can't be re-run from ``raw``).
REPARSERS = {"IJNL": parse_ijnl, "DFEL": parse_hockeydata, "CEHL": parse_cehl}


def reparse(timeline: GameTimeline, *, save: bool = True) -> bool:
    """Re-runs the source's parser on the timeline's stored ``raw`` payload, for after a parser fix -- no fetch, and
    none of the "timeline changed" signals (moments, pushes) a real change sends. Returns whether the document changed
    (and, with ``save``, was stored); a timeline without a ``raw`` payload or a re-parsable source is left alone."""
    parser = REPARSERS.get(timeline.source)
    if parser is None or not timeline.raw:
        return False
    document = finalize_document(parser(timeline.raw))
    document["status"] = status_for(timeline.event, document)
    digest = document_hash(document)
    if digest == timeline.data_hash:
        return False
    timeline.data, timeline.data_hash, timeline.status = document, digest, document["status"]
    if save:
        timeline.save(update_fields=["data", "data_hash", "status"])
    return True


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


def refresh_timeline(event, competition) -> str:
    """The score fetch's hook (``fetch_game``, and the competitions service's
    "fetch game info"): fetch and store the timeline with the same competition
    instance. Never raises."""
    return fetch_and_store(event, competition)


def settle_candidates(now=None):
    """Finished games whose timeline may still be corrected: started at least
    SETTLE_AFTER ago, within SETTLE_WINDOW, not yet finalized."""
    now = now or timezone.now()
    timelines = GameTimeline.objects.filter(finalized_at__isnull=True, event__cancelled=False, event__start__lte=now - SETTLE_AFTER, event__start__gte=now - SETTLE_WINDOW)
    return timelines.select_related("event", "event__location", "event__opponent").order_by("event__start")


FINALIZED = "finalized"


def awaiting_goalies(timeline: GameTimeline) -> bool:
    """IJNL's goalies are entered after the game (they're not part of the timeline itself), so its games aren't
    finalized until they show up -- the nightly settle keeps fetching them for the rest of the settle window."""
    return timeline.source == "IJNL" and not (timeline.data or {}).get("goalies")


def settle(timeline: GameTimeline, competition) -> str:
    """Re-fetches one finished game. A final timeline that came back unchanged
    is marked finalized and won't be fetched again. Returns FINALIZED, or
    fetch_and_store's own outcome."""
    outcome = fetch_and_store(timeline.event, competition)
    if outcome != UNCHANGED:
        return outcome
    timeline.refresh_from_db()
    # An unchanged document never went through store()'s sync, so make sure its stats
    # (GameStat) agree with it before it's marked finalized and left alone for good.
    if timeline.data and ensure_game_stats(timeline.event, timeline.data):
        logger.info("game_timeline.stats_repaired event_id=%s", timeline.event_id)
    if timeline.status != GameTimeline.Status.FINAL or awaiting_goalies(timeline):
        return outcome
    timeline.finalized_at = timezone.now()
    timeline.save(update_fields=["finalized_at"])
    return FINALIZED


def games_for_backfill(*, since):
    """Played games with a competition game id (for a manual backfill)."""
    games = Event.objects.filter(kind=Event.EventKind.GAME, cancelled=False, start__gte=since, start__lte=timezone.now()).exclude(competition="").exclude(external_game_id="")
    return games.select_related("location", "opponent").order_by("start")


def _person(person) -> str:
    """ "#78 Sam Scorer" -- number first, the way a scoresheet reads."""
    if not person:
        return ""
    number, name = person.get("number"), person.get("name", "")
    return f"#{number} {name}".strip() if number is not None else name


#: Between the two sides of a score, as on a scoreboard (an en dash, not a hyphen).
EN_DASH = "\u2013"


def _period_title(label: str) -> str:
    if label.isdigit():
        return _("Period %(number)s") % {"number": label}
    if label == "SO":
        return _("Shoot-out")
    if label.startswith("OT"):
        return _("Overtime")
    return label


#: The kinds of timeline event a row is shown for -- period start/end markers
#: are what the period headings already say, so they get no row of their own.
_ROW_TYPES = ("goal", "penalty", "penalty_shot", "goalie_change", "timeout")
_STRENGTHS = {"PP1": _lazy("power play"), "PP2": _lazy("5-on-3 power play"), "SH1": _lazy("short-handed"), "SH2": _lazy("short-handed"), "EN": _lazy("empty net"), "PS": _lazy("penalty shot")}


def _row(item: dict, *, our_side: str, ours_label: str, theirs_label: str) -> dict:
    side = item.get("side")
    ours = side == our_side if side else None
    row = {"time": item.get("time") or "", "type": item["type"], "ours": ours, "team": (ours_label if ours else theirs_label) if side else ""}
    if item["type"] == "goal":
        goal = item.get("goal") or {}
        assists = ", ".join(_person(person) for person in goal.get("assists", []))
        detail = _("Assists: %(names)s") % {"names": assists} if assists else _("Unassisted")
        strength = _STRENGTHS.get(goal.get("strength") or "")
        if goal.get("empty_net"):
            strength = _("empty net")
        row.update(icon="goal", kind=_("Goal"), who=_person(goal.get("scorer")) or _("Unknown scorer"), detail=detail, tag=strength or "")
        score = goal.get("score_after")
        if score:
            row["score"] = f"{score[our_side]}{EN_DASH}{score['away' if our_side == 'home' else 'home']}"
    elif item["type"] == "penalty":
        penalty = item.get("penalty") or {}
        reason = penalty.get("description") or penalty.get("code") or ""
        detail = _("%(reason)s · %(minutes)s min") % {"reason": reason, "minutes": penalty["minutes"]} if penalty.get("minutes") else reason
        kind = penalty.get("kind") or ""
        # Anything beyond a plain minor is tagged with its kind; the serious ones
        # (major, misconduct, game misconduct, match) are highlighted on the page.
        # No player: a team-level call (bench, or the foul a penalty shot was awarded for).
        row.update(icon="timer", kind=_("Penalty"), who=_person(penalty.get("player")) or row["team"], detail=detail, tag=str(PENALTY_KIND_LABELS.get(kind, "")) if kind not in ("", "minor") else "", serious=kind in SERIOUS_PENALTIES)
    elif item["type"] == "goalie_change":
        change = item.get("goalie_change") or {}
        row.update(icon="shield-user", kind=_("Goalie"), who=_person(change.get("player")), detail=_("In") if change.get("action") == "in" else _("Out"))
    elif item["type"] == "penalty_shot":
        row.update(icon="target", kind=_("Penalty shot"), who=row["team"], detail=_("Penalty shot awarded"))
    else:
        row.update(icon="circle-pause", kind=_("Time-out"), who=row["team"], detail="")
    return row


def display_timeline(timeline: GameTimeline, event) -> dict:
    """``timeline`` laid out for the management event page from the club's own
    side ("ours" -- same Event.is_home_game convention as the score): the
    score and per-period scores as ours-theirs, and one row per goal, penalty,
    goalie change or time-out, grouped by period. Translated here, so the
    template only lays it out."""
    data = timeline.data or {}
    our_side = "home" if event.is_home_game else "away"
    their_side = "away" if our_side == "home" else "home"
    ours_label = ", ".join(team.name for team in event.teams.all()) or _club_name()
    theirs_label = event.opponent.name if event.opponent_id else _("Opponent")

    period_scores = {str(period.get("label")): f"{period.get(our_side, 0)}{EN_DASH}{period.get(their_side, 0)}" for period in data.get("periods", [])}
    periods = []
    for item in data.get("events", []):
        label = str(item.get("period") or "?")
        if not periods or periods[-1]["label"] != label:
            periods.append({"label": label, "title": _period_title(label), "score": period_scores.get(label, ""), "rows": []})
        if item["type"] in _ROW_TYPES:
            periods[-1]["rows"].append(_row(item, our_side=our_side, ours_label=ours_label, theirs_label=theirs_label))
    score = data.get("score") or {}
    return {
        "ours_label": ours_label,
        "theirs_label": theirs_label,
        "score": f"{score.get(our_side, 0)}{EN_DASH}{score.get(their_side, 0)}" if score else "",
        "period_scores": [{"label": label, "score": value} for label, value in period_scores.items()],
        "periods": periods,
    }


def _api_player(person, show_names: bool) -> dict:
    person = person or {}
    result = {"number": person.get("number")}
    if show_names and person.get("name"):
        result["name"] = person["name"]
    return result


def _decided_in(timeline: GameTimeline, data: dict) -> str | None:
    """For sources that don't say (RBIHF, CEHL, IJNL): from the period labels
    once the game is final -- a shoot-out or overtime period means it went
    past regulation."""
    if timeline.status != GameTimeline.Status.FINAL or not data.get("events"):
        return None
    labels = {str(period.get("label", "")).upper() for period in data.get("periods", [])}
    if "SO" in labels:
        return "shootout"
    if any(label.startswith("OT") for label in labels):
        return "overtime"
    return "regulation"


def timeline_for_api(timeline: GameTimeline, event, *, show_names: bool) -> dict:
    """The stored document as the API serves it (events.timeline_schemas.
    TimelineOut): our side resolved ("ours", same Event.is_home_game convention
    as the score), and player names left out unless ``show_names``."""
    data = timeline.data or {}
    our_side = "home" if event.is_home_game else "away"
    score = data.get("score") or {}
    events = []
    for item in data.get("events", []):
        side = item.get("side")
        row = {"id": item["id"], "type": item["type"], "period": item.get("period"), "time": item.get("time"), "seconds": item.get("seconds"), "side": side, "ours": (side == our_side) if side else None}
        if item["type"] == "goal":
            goal = item.get("goal") or {}
            row["goal"] = {
                "scorer": _api_player(goal.get("scorer"), show_names),
                "assists": [_api_player(person, show_names) for person in goal.get("assists", [])],
                "strength": goal.get("strength"),
                "empty_net": bool(goal.get("empty_net")),
                "penalty_shot": bool(goal.get("penalty_shot")),
                "game_winning": bool(goal.get("game_winning")),
                "score_after": goal.get("score_after"),
            }
        elif item["type"] == "penalty":
            penalty = item.get("penalty") or {}
            row["penalty"] = {"player": _api_player(penalty.get("player"), show_names), "code": penalty.get("code", ""), "description": penalty.get("description", ""), "minutes": penalty.get("minutes"), "kind": penalty.get("kind") or None}
        elif item["type"] == "goalie_change":
            change = item.get("goalie_change") or {}
            row["goalie_change"] = {"player": _api_player(change.get("player"), show_names), "action": change.get("action", "in")}
        events.append(row)
    return {
        "status": timeline.status,
        "our_side": our_side,
        "home_score": score.get("home"),
        "away_score": score.get("away"),
        "periods": data.get("periods", []),
        "decided_in": data.get("decided_in") or _decided_in(timeline, data),
        "updated_at": timeline.changed_at,
        "final": timeline.finalized_at is not None,
        "events": events,
    }


def _club_name() -> str:
    from events.services.attendance import current_club  # deferred: a sibling service with heavier imports

    club = current_club()
    return club.name if club is not None else ""


# -- resolving a game's competition and fetching it ----------------------------------------------------------------------


class CompetitionFetchError(Exception):
    """A game's info could not be fetched from its competition's data source (or the competition is misconfigured)."""


def _own_competition_for(event):
    """Fallback resolver while events.services.competitions doesn't exist: club.Competition by name (public schema), the
    club's entitlement to its feature, then ``<module>.<name>`` instantiated."""
    from club.models import Competition
    from events.services.attendance import current_club

    competition = Competition.objects.filter(name=event.competition).select_related("feature").first()
    club = current_club()
    if competition is None or club is None:
        return None
    if competition.feature_id is not None:
        from features.entitlements import resolve_entitlements

        if not resolve_entitlements(club).has(competition.feature.code):
            return None
    try:
        module = importlib.import_module(competition.module)
        competition_class = getattr(module, competition.name)
    except (ImportError, AttributeError) as error:
        raise CompetitionFetchError(f"“{competition.name}” isn't wired up to a real data source yet.") from error
    return competition_class()


def competition_for(event):
    """An instance of ``event``'s competition class (events.competition.*: ``update_game_information(event=)`` for the
    score and live flag, ``fetch_timeline(event) -> TimelineFetch | None``), or None when there is no data source this
    club may use. Delegates to events.services.competitions.competition_for when that service exists (its
    ``CompetitionFetchError`` is raised as ours). Tests patch this function to inject a fake competition."""
    if not event.competition or not event.external_game_id:
        return None
    try:
        from events.services import competitions
    except ImportError:
        return _own_competition_for(event)
    try:
        return competitions.competition_for(event)
    except Exception as error:
        if error.__class__.__name__ == "CompetitionFetchError":
            raise CompetitionFetchError(str(error)) from error
        raise


#: A source's "live" flag can read false for a moment mid-game (a stopped clock, a time-out), so a live game only counts
#: as ended after this many consecutive not-live reads -- one blip must not mark it final or stop the live poll.
NOT_LIVE_READS_TO_END = 3
NOT_LIVE_READS_TTL = 60 * 60


def _debounce_not_live(event, was_live: bool) -> None:
    """Keeps a live game live until the source said "not live" NOT_LIVE_READS_TO_END times in a row (the source's update
    has already saved False by now, so it is put back)."""
    key = f"events:not-live-reads:{event.pk}"
    if event.is_live or not was_live:
        cache.delete(key)
        return
    cache.add(key, 0, NOT_LIVE_READS_TTL)
    if cache.incr(key) < NOT_LIVE_READS_TO_END:
        event.is_live = True
        event.save(update_fields=["is_live"])
    else:
        cache.delete(key)


def fetch_game(event, competition=None) -> str | None:
    """Score, live flag and timeline of one game in one go: ``competition.update_game_information(event=event)`` (raises
    CompetitionFetchError on failure), the not-live debounce, then the timeline (never raises). ``competition`` defaults
    to ``competition_for(event)``; returns None when the game has no usable data source, else the timeline outcome."""
    source = competition if competition is not None else competition_for(event)
    if source is None:
        return None
    was_live = event.is_live
    try:
        source.update_game_information(event=event)
    except Exception as error:
        raise CompetitionFetchError(f"Could not fetch game info from “{event.competition}”: {error}") from error
    _debounce_not_live(event, was_live)
    return refresh_timeline(event, source)


# -- the scheduled sweeps -------------------------------------------------------------------------------------------------

#: How long before kickoff / after the planned end a game is worth polling.
POLL_LEAD_TIME = datetime.timedelta(minutes=20)
POLL_TRAIL_TIME = datetime.timedelta(hours=1)


def poll_candidates(now=None):
    """Games due to start within POLL_LEAD_TIME, under way, or within POLL_TRAIL_TIME of their planned end, that have a
    competition game id and haven't been seen finishing yet (``live_score_polling_done_at``). A game still flagged live
    is always polled, however late it runs."""
    from django.db.models import Q

    now = now or timezone.now()
    window = Q(start__lte=now + POLL_LEAD_TIME, end__gte=now - POLL_TRAIL_TIME) | Q(is_live=True)
    return list(Event.objects.filter(window, kind=Event.EventKind.GAME, cancelled=False, live_score_polling_done_at__isnull=True).exclude(competition="").exclude(external_game_id="").select_related("location", "opponent").order_by("start"))


def _game_finished(event) -> None:
    """Hook for when a polled game is seen finishing: the league standings of its teams are recomputed when the standings
    service exists (events.services.rbihf_standings; a no-op otherwise)."""
    try:
        from events.services.rbihf_standings import refresh_for_finished_game
    except ImportError:
        return
    try:
        refresh_for_finished_game(event)
    except Exception:
        logger.warning("game_timeline.standings_failed event_id=%s", event.pk, exc_info=True)


def poll_live_games(now=None) -> dict:
    """One live-score sweep of the active club schema (the every-minute task): each candidate is fetched (score, live
    flag, timeline). A game that was live and no longer is gets ``live_score_polling_done_at`` -- it is finished, the
    nightly settle takes over -- and its standings are refreshed. One failing game never stops the sweep. Returns
    counts: ``{"checked", "fetched", "failed", "finished"}``."""
    now = now or timezone.now()
    counts = {"checked": 0, "fetched": 0, "failed": 0, "finished": 0}
    for event in poll_candidates(now):
        counts["checked"] += 1
        was_live = event.is_live
        try:
            if fetch_game(event) is None:
                continue
        except CompetitionFetchError:
            counts["failed"] += 1
            logger.warning("game_timeline.poll_failed event_id=%s", event.pk, exc_info=True)
            continue
        except Exception:
            counts["failed"] += 1
            logger.exception("game_timeline.poll_crashed event_id=%s", event.pk)
            continue
        counts["fetched"] += 1
        if was_live and not event.is_live:
            event.live_score_polling_done_at = now
            event.save(update_fields=["live_score_polling_done_at"])
            counts["finished"] += 1
            _game_finished(event)
    return counts


def settle_timelines(now=None) -> dict:
    """The nightly sweep of the active club schema: re-fetches each ``settle_candidates`` game until its timeline stops
    changing (then FINALIZED). Returns counts per outcome (``finalized``, ``changed``, ``unchanged``, ``failed``,
    ``unavailable``, ``skipped``)."""
    counts: dict = {}
    for timeline in settle_candidates(now):
        event = timeline.event
        try:
            source = competition_for(event)
        except CompetitionFetchError:
            logger.warning("game_timeline.settle_misconfigured event_id=%s competition=%s", event.pk, event.competition)
            source = None
        if source is None:
            counts["skipped"] = counts.get("skipped", 0) + 1
            continue
        outcome = settle(timeline, source)
        counts[outcome] = counts.get(outcome, 0) + 1
    return counts


def backfill_timelines(*, since, refetch=False, dry_run=False, now=None) -> dict:
    """Fetches the timelines of played games since ``since`` in the active club schema (a manual backfill, the old
    fetch_game_timelines command). Games already holding a timeline are skipped unless ``refetch``; games older than the
    settle window are finalized right away (no moments are recorded for them: a first fetch is always silent). Returns
    ``{"games", "changed", "unchanged", "failed", "unavailable", "skipped", "finalized"}``."""
    now = now or timezone.now()
    counts = {"games": 0, CHANGED: 0, UNCHANGED: 0, FAILED: 0, UNAVAILABLE: 0, "skipped": 0, FINALIZED: 0}
    for event in games_for_backfill(since=since):
        if not refetch and GameTimeline.objects.filter(event=event).exclude(data_hash="").exists():
            continue
        counts["games"] += 1
        if dry_run:
            continue
        try:
            source = competition_for(event)
        except CompetitionFetchError:
            source = None
        if source is None:
            counts["skipped"] += 1
            continue
        outcome = fetch_and_store(event, source)
        counts[outcome] += 1
        if outcome in (CHANGED, UNCHANGED) and event.start < now - SETTLE_WINDOW:
            GameTimeline.objects.filter(event=event, finalized_at__isnull=True).update(finalized_at=now)
            counts[FINALIZED] += 1
    return counts


def update_past_games(team) -> dict:
    """Score, live flag and game sheet of every played, imported game of ``team`` (the import page's "update past
    games"). A game whose source fails is counted and skipped. Returns counts."""
    now = timezone.now()
    games = Event.objects.filter(teams=team, kind=Event.EventKind.GAME, cancelled=False, start__lte=now).exclude(competition="").exclude(external_game_id="").select_related("location", "opponent").order_by("start")
    counts = {"games": 0, "updated": 0, "failed": 0, "skipped": 0, "with_score": 0}
    for event in games:
        counts["games"] += 1
        try:
            outcome = fetch_game(event)
        except CompetitionFetchError:
            counts["failed"] += 1
            continue
        if outcome is None:
            counts["skipped"] += 1
            continue
        counts["updated"] += 1
        if event.start < now - SETTLE_WINDOW:  # long over: the source won't change it any more, like the backfill
            GameTimeline.objects.filter(event=event, finalized_at__isnull=True).update(finalized_at=now)
        event.refresh_from_db(fields=["score_for", "score_against"])
        counts["with_score"] += event.score_for is not None
    return counts
