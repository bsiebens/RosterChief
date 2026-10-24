"""Flattening a game's timeline into GameStat rows, and summing them back up for a
team's analytics page (management's team_analytics view).

``sync_game_stats`` is called from game_timeline.store whenever a timeline changed:
it replaces the game's rows wholesale, so a federation correcting an assist after
the final whistle just re-syncs. The timeline document (GAME_TIMELINE.md §2.2) is
ice hockey today; the rows it produces are generic (see GameStat), and the
aggregation below only knows stat-type slugs.
"""

import unicodedata
from collections import defaultdict

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from club.models import Season
from events.models import Event, GameStat
from teams.models import TeamMembership

GOAL, ASSIST, PIM = GameStat.StatType.GOAL, GameStat.StatType.ASSIST, GameStat.StatType.PENALTY_MINUTES
SHOT, PP_OPPORTUNITY, CONCEDED = GameStat.StatType.SHOT, GameStat.StatType.POWER_PLAY_OPPORTUNITY, GameStat.StatType.GOAL_CONCEDED
#: Penalty kinds that put the other side on a power play (a major or misconduct isn't counted when the source says so).
POWER_PLAY_KINDS = ("minor", "bench_minor", "double_minor", "major", "")
POWER_PLAY = ("PP1", "PP2")


def _tokens(name: str) -> frozenset:
    """Lower-cased, accent-free words of a name, so "Kowalski, Jan" matches "Jan Kowalski"."""
    folded = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    return frozenset(word for word in folded.replace(",", " ").replace(".", " ").split() if word)


class _Roster:
    """The team's season roster, to match a timeline person to a Member: by jersey number when
    exactly one player wears it, otherwise by the same set of name words."""

    def __init__(self, team, season):
        memberships = TeamMembership.objects.filter(team=team, season=season).select_related("member") if season is not None else []
        by_number = defaultdict(list)
        self.by_name = {}
        for membership in memberships:
            if membership.jersey_number is not None:
                by_number[membership.jersey_number].append(membership.member_id)
            self.by_name[_tokens(membership.member.get_full_name())] = membership.member_id
        self.by_number = {number: ids[0] for number, ids in by_number.items() if len(ids) == 1}

    def match(self, person) -> int | None:
        person = person or {}
        number = person.get("number")
        if number is not None and number in self.by_number:
            return self.by_number[number]
        tokens = _tokens(person.get("name", ""))
        return self.by_name.get(tokens) if tokens else None


def event_season(event):
    """The event's own season, else the one its start date falls in (Event.season is "explicit, else derived")."""
    return event.season or Season.covering(event.club_id, timezone.localtime(event.start).date())


def _rows_for(event, team, document, roster) -> list[GameStat]:
    our_side = "home" if event.is_home_game else "away"
    rows = []

    def add(item, stat_type, person, *, value=1, slot="", meta=None, ours=None):
        ours = item.get("side") == our_side if ours is None else ours
        person = person or {}
        rows.append(
            GameStat(
                club_id=event.club_id,
                event=event,
                team=team,
                season_id=getattr(event_season(event), "pk", None),
                stat_type=stat_type,
                value=value,
                is_ours=ours,
                member_id=roster.match(person) if ours else None,
                player_name=person.get("name", "") or "",
                player_number=person.get("number"),
                period=str(item.get("period") or ""),
                game_seconds=item.get("seconds"),
                source_key=f"{item['id']}{slot}",
                meta=meta or {},
            )
        )

    goalie = None  # who is in our net right now, as far as the timeline says
    opportunities = {"home": 0, "away": 0}
    for item in document.get("events", []):
        side = item.get("side")
        if not side:
            continue
        if item["type"] == "goalie_change" and side == our_side:
            change = item.get("goalie_change") or {}
            goalie = change.get("player") if change.get("action") == "in" else None
        elif item["type"] == "goal":
            goal = item.get("goal") or {}
            meta = {"strength": goal.get("strength") or "", "empty_net": bool(goal.get("empty_net")), "penalty_shot": bool(goal.get("penalty_shot")), "game_winning": bool(goal.get("game_winning"))}
            add(item, GOAL, goal.get("scorer"), meta=meta)
            for index, assist in enumerate(goal.get("assists") or []):
                add(item, ASSIST, assist, slot=f"#a{index}")
            if side != our_side and goalie and not goal.get("empty_net"):
                add(item, CONCEDED, goalie, slot="#c", ours=True)
        elif item["type"] == "penalty":
            penalty = item.get("penalty") or {}
            if penalty.get("minutes"):
                add(item, PIM, penalty.get("player"), value=penalty["minutes"], meta={"code": penalty.get("code") or "", "description": penalty.get("description") or "", "kind": penalty.get("kind") or ""})
                if (penalty.get("kind") or "") in POWER_PLAY_KINDS:
                    opportunities["away" if side == "home" else "home"] += 1

    # Team totals the source gives (hockeydata): shots, and the power plays it counted. Without
    # them, a power play is counted from the other side's penalties above.
    stats = document.get("team_stats") or {}
    for side in ("home", "away"):
        ours = side == our_side
        totals = stats.get(side) or {}
        item = {"id": f"team_stats:{side}", "side": side}
        if totals.get("shots_on_goal") is not None:
            add(item, SHOT, None, value=totals["shots_on_goal"], ours=ours)
        count = totals.get("pp_opportunities")
        count = opportunities[side] if count is None else count
        if count:
            add(item, PP_OPPORTUNITY, None, value=count, ours=ours)
    return rows


def _expected_rows(event: Event, document: dict) -> list[GameStat]:
    """The rows ``document`` should produce, one set per team of the club playing the game."""
    rows = []
    for team in event.teams.all():
        rows.extend(_rows_for(event, team, document, _Roster(team, event_season(event))))
    return rows


def _fingerprint(rows) -> set:
    return {(row.team_id, row.stat_type, row.source_key, row.value, row.is_ours, row.member_id, row.player_name, row.player_number, row.period, row.game_seconds, str(sorted(row.meta.items()))) for row in rows}


def sync_game_stats(event: Event, document: dict) -> int:
    """Replaces ``event``'s GameStat rows from the timeline ``document``; returns how
    many rows were written."""
    rows = _expected_rows(event, document)
    with transaction.atomic():
        GameStat.objects.filter(event=event).delete()
        GameStat.objects.bulk_create(rows)
    return len(rows)


def stats_out_of_sync(event: Event, document: dict) -> bool:
    """Whether ``event``'s rows differ from what ``document`` (and the current roster) would produce."""
    return _fingerprint(_expected_rows(event, document)) != _fingerprint(GameStat.objects.filter(event=event))


def ensure_game_stats(event: Event, document: dict) -> bool:
    """Re-syncs ``event``'s rows only when they no longer match what ``document`` (and the
    current roster) would produce -- missing for a game stored before stats existed, or
    stale after a roster fix. Returns whether anything was rewritten. This is what keeps the
    nightly settle (and the rebuild_game_stats command) in agreement with the stored timeline."""
    if not stats_out_of_sync(event, document):
        return False
    sync_game_stats(event, document)
    return True


# ---- aggregation ---------------------------------------------------------------------------

#: Players listed per leaderboard.
LEADERS = 5


def played_games(team, season):
    """The team's finished games with a score, friendlies included."""
    # Event.season is "explicit, else derived from the start date" (events/models.py), so a game with no stored season still belongs to the season its date falls in.
    in_season = Q(season=season) | Q(season__isnull=True, start__date__gte=season.start_date, start__date__lte=season.end_date)
    return list(Event.objects.filter(in_season, teams=team, kind=Event.EventKind.GAME, cancelled=False, is_live=False, score_for__isnull=False, score_against__isnull=False).select_related("opponent", "location").order_by("start"))


def _leaderboard(stats, *types, limit=LEADERS):
    """Our players ranked by the summed ``value`` of ``types``: [{name, number, member_id, total, pct}], ``pct`` being the share of the top total (for a bar)."""
    totals = defaultdict(lambda: {"total": 0, "name": "", "number": None, "member_id": None})
    for stat in stats:
        if not stat.is_ours or stat.stat_type not in types:
            continue
        key = stat.member_id or (stat.player_name, stat.player_number)
        entry = totals[key]
        entry["total"] += stat.value
        entry["member_id"] = stat.member_id
        entry["number"] = stat.player_number
        entry["name"] = stat.member.get_full_name() if stat.member_id else stat.player_name
    ranked = sorted((entry for entry in totals.values() if entry["name"] or entry["number"] is not None), key=lambda entry: (-entry["total"], entry["name"]))
    ranked = ranked[:limit]
    top = ranked[0]["total"] if ranked else 0
    for entry in ranked:
        entry["pct"] = round(100 * entry["total"] / top) if top else 0
    return ranked


def _percent(numerator, denominator):
    return round(100 * numerator / denominator, 1) if denominator else None


#: Game-clock buckets for the "when are goals scored" table, in minutes (regulation is 60).
MINUTE_BUCKETS = ((0, 10), (10, 20), (20, 30), (30, 40), (40, 50), (50, 60))


#: The game filters the analytics page offers (its ``?games=`` values).
SCOPES = ("all", "home", "away", "last5")


def _in_scope(games, scope):
    if scope == "home":
        return [game for game in games if game.is_home_game]
    if scope == "away":
        return [game for game in games if not game.is_home_game]
    if scope == "last5":
        return games[-5:]
    return games


def _scaled(rows, *keys):
    """Add a ``<key>_pct`` (0-100, against the largest value across ``keys``) to each row, for drawing bars."""
    top = max((row[key] or 0 for row in rows for key in keys), default=0)
    for row in rows:
        for key in keys:
            row[f"{key}_pct"] = round(100 * (row[key] or 0) / top) if top else 0
    return rows


def _trend(games):
    """The running goal difference after each game, as points in a 0-100 box (y grows downwards) for an SVG sparkline."""
    values, running = [0], 0
    for game in games:
        running += game.score_for - game.score_against
        values.append(running)
    low, high = min(values), max(values)
    span = (high - low) or 1
    steps = len(values) - 1 or 1
    points = [{"x": round(100 * index / steps, 2), "y": round(14 + 72 * (high - value) / span, 2), "value": value, "game": games[index - 1] if index else None} for index, value in enumerate(values)]
    zero_y = round(14 + 72 * high / span, 2)
    line = " ".join(f"{point['x']},{point['y']}" for point in points)
    return {"points": points, "line": line, "area": f"{line} {points[-1]['x']},{zero_y} 0,{zero_y}", "zero_y": zero_y, "last": values[-1] - values[-2] if len(values) > 1 else 0}


def _opponents(games):
    """Per opponent: games, W-D-L and goals, best-played first."""
    table = {}
    for game in games:
        name = game.opponent.name if game.opponent_id else ""
        row = table.setdefault(name, {"name": name, "games": 0, "won": 0, "drawn": 0, "lost": 0, "gf": 0, "ga": 0})
        row["games"] += 1
        row["won" if game.score_for > game.score_against else "lost" if game.score_for < game.score_against else "drawn"] += 1
        row["gf"] += game.score_for
        row["ga"] += game.score_against
    return sorted(table.values(), key=lambda row: (-row["games"], row["name"]))


def team_analytics(team, season, scope="all") -> dict:
    """Everything the team analytics page shows for ``team`` in ``season``, narrowed to ``scope`` (one of SCOPES)."""
    games = _in_scope(played_games(team, season), scope)
    stats = list(GameStat.objects.filter(team=team, event__in=[game.pk for game in games]).select_related("member"))

    record = {"won": 0, "drawn": 0, "lost": 0}
    split = {side: {"games": 0, "won": 0, "drawn": 0, "lost": 0, "gf": 0, "ga": 0} for side in ("home", "away")}
    form, game_bars, clean_sheets = [], [], 0
    for game in games:
        result = "won" if game.score_for > game.score_against else "lost" if game.score_for < game.score_against else "drawn"
        record[result] += 1
        form.append({"result": result[0].upper(), "event": game})
        game_bars.append({"event": game, "result": result[0].upper(), "for": game.score_for, "against": game.score_against})
        clean_sheets += game.score_against == 0
        where = split["home" if game.is_home_game else "away"]
        where["games"] += 1
        where[result] += 1
        where["gf"] += game.score_for
        where["ga"] += game.score_against
    goals_for = sum(game.score_for for game in games)
    goals_against = sum(game.score_against for game in games)
    played = len(games)
    for where in split.values():
        where["diff"] = where["gf"] - where["ga"]
        where["avg_for"] = round(where["gf"] / where["games"], 1) if where["games"] else None
        where["avg_against"] = round(where["ga"] / where["games"], 1) if where["games"] else None
        where["share_for"] = round(100 * where["gf"] / (where["gf"] + where["ga"])) if where["gf"] + where["ga"] else 50

    def total(stat_type, ours, **meta):
        return sum(stat.value for stat in stats if stat.stat_type == stat_type and stat.is_ours is ours and all(stat.meta.get(k) in v if isinstance(v, tuple) else stat.meta.get(k) == v for k, v in meta.items()))

    periods = defaultdict(lambda: {"for": 0, "against": 0})
    for stat in stats:
        if stat.stat_type == GOAL and stat.period:
            periods[stat.period]["for" if stat.is_ours else "against"] += stat.value

    # Shots, only for the games whose source reports them -- the save/shooting percentages are
    # taken over those games alone, so a game without shot data can't skew them.
    shot_game_ids = {stat.event_id for stat in stats if stat.stat_type == SHOT}
    shots_for = sum(stat.value for stat in stats if stat.stat_type == SHOT and stat.is_ours)
    shots_against = sum(stat.value for stat in stats if stat.stat_type == SHOT and not stat.is_ours)
    shot_games = [game for game in games if game.pk in shot_game_ids]
    shot_goals_for, shot_goals_against = sum(game.score_for for game in shot_games), sum(game.score_against for game in shot_games)

    minutes = [{"label": f"{start}-{end}", "for": 0, "against": 0} for start, end in MINUTE_BUCKETS]
    overtime = {"label": "60+", "for": 0, "against": 0}
    for stat in stats:
        if stat.stat_type == GOAL and stat.game_seconds is not None:
            bucket = next((row for row, (start, end) in zip(minutes, MINUTE_BUCKETS, strict=True) if start * 60 <= stat.game_seconds < end * 60), overtime)
            bucket["for" if stat.is_ours else "against"] += stat.value
    if overtime["for"] or overtime["against"]:
        minutes.append(overtime)

    period_rows = _scaled([{"label": label, **value} for label, value in sorted(periods.items())], "for", "against")
    _scaled(minutes, "for", "against")
    busiest = max(minutes, key=lambda row: row["for"] + row["against"])
    busiest = busiest if busiest["for"] + busiest["against"] else None

    per_game_pim = defaultdict(lambda: {"for": 0, "against": 0})
    for stat in stats:
        if stat.stat_type == PIM:
            per_game_pim[stat.event_id]["for" if stat.is_ours else "against"] += stat.value
    game_rows = [
        {
            "event": game,
            "result": bar["result"],
            "for": game.score_for,
            "against": game.score_against,
            "pim_for": per_game_pim[game.pk]["for"] if game.pk in per_game_pim else None,
            "pim_against": per_game_pim[game.pk]["against"] if game.pk in per_game_pim else None,
        }
        for game, bar in zip(reversed(games), reversed(game_bars), strict=True)
    ]
    busiest_game = max((row["for"] + row["against"] for row in game_rows), default=0)
    for row in game_rows:  # a common scale, so the results table's goal bars compare across games
        row["total_pct"] = round(100 * (row["for"] + row["against"]) / busiest_game) if busiest_game else 0
    _scaled(game_rows, "pim_for", "pim_against")
    _scaled(game_bars, "for", "against")

    pim_for, pim_against = total(PIM, True), total(PIM, False)
    pp_goals_for, pp_goals_against = total(GOAL, True, strength=POWER_PLAY), total(GOAL, False, strength=POWER_PLAY)
    points = _leaderboard(stats, GOAL, ASSIST)
    goalies = _leaderboard(stats, CONCEDED, limit=None)
    goalies_total = sum(row["total"] for row in goalies)
    return {
        "games": played,
        "timeline_games": len({stat.event_id for stat in stats}),
        "record": record,
        "points_table": record["won"] * 3 + record["drawn"],
        "goals_for": goals_for,
        "goals_against": goals_against,
        "goal_difference": goals_for - goals_against,
        "avg_for": round(goals_for / played, 2) if played else None,
        "avg_against": round(goals_against / played, 2) if played else None,
        "clean_sheets": clean_sheets,
        "form": form[-5:],
        "split": split,
        "pim_for": pim_for,
        "pim_against": pim_against,
        "avg_pim": round(pim_for / played, 1) if played else None,
        "pp_goals_for": pp_goals_for,
        "pp_goals_against": pp_goals_against,
        "game_winning_goals": total(GOAL, True, game_winning=True),
        "empty_net_goals": total(GOAL, True, empty_net=True),
        "periods": period_rows,
        "top_scorers": _leaderboard(stats, GOAL),
        "top_assists": _leaderboard(stats, ASSIST),
        "top_points": points,
        "top_offenders": _leaderboard(stats, PIM),
        "shots_for": shots_for if shot_games else None,
        "shots_against": shots_against if shot_games else None,
        "shot_games": len(shot_games),
        "shooting_pct": _percent(shot_goals_for, shots_for),
        "save_pct": _percent(shots_against - shot_goals_against, shots_against),
        "pp_opportunities": total(PP_OPPORTUNITY, True),
        "pk_opportunities": total(PP_OPPORTUNITY, False),
        "pk_killed": total(PP_OPPORTUNITY, False) - pp_goals_against,
        "pp_pct": _percent(pp_goals_for, total(PP_OPPORTUNITY, True)),
        "pk_pct": _percent(total(PP_OPPORTUNITY, False) - pp_goals_against, total(PP_OPPORTUNITY, False)),
        "goal_minutes": minutes,
        "opponents": _opponents(games),
        "game_rows": game_rows,
        "goalies": goalies,
        "goalie_unattributed": max(goals_against - goalies_total, 0),
        "busiest_minutes": busiest,
        "game_bars": game_bars,
        "trend": _trend(games) if games else None,
    }
