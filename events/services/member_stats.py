"""A member's Stats tab: everything the management app charts about one person, in one document (``member_stats_document``).

Five independent sections, each None / empty when the member has nothing to show:

- ``player`` / ``goalie`` -- from the matched GameStat rows (needs the analytics feature; the router decides).
  A game only counts once the member has a row in it (a goal, assist, penalty, goal conceded or goalie time), so
  "games" is games with a recorded stat, not games dressed.
- ``attendance`` -- past, non-cancelled events: per month (last 12), per event kind and the present-streaks.
- ``staff`` -- the member's staff assignments (coach, manager...) per team and season with that team's record.
- ``officiating`` -- the games the member refereed / officiated (real assignments, past games only): per month (last
  12, split by type), per season, fees this season, penalties per game and the games per competition bucket.
"""

from collections import defaultdict

from django.utils import timezone

from events.models import Attendance, GameMoment, GameStat, Season
from events.services import game_stats
from events.services.attendance import _rate
from teams.models import StaffAssignment

GOAL, ASSIST, PIM = GameStat.StatType.GOAL, GameStat.StatType.ASSIST, GameStat.StatType.PENALTY_MINUTES
CONCEDED, GOALIE_SECONDS = GameStat.StatType.GOAL_CONCEDED, GameStat.StatType.GOALIE_SECONDS
APPEARANCE = GameStat.StatType.APPEARANCE
FORM_GAMES = 20
MONTHS = 12
HERE = (Attendance.AttendanceStatus.PRESENT, Attendance.AttendanceStatus.SELECTED)


def _season_ref(season) -> dict | None:
    return {"id": season.pk, "name": season.name} if season else None


def _game_ref(event) -> dict:
    return {"id": event.pk, "title": event.title, "start": event.start, "opponent": event.opponent.name if event.opponent_id else None, "is_home_game": event.is_home_game, "score_for": event.score_for, "score_against": event.score_against}


def _split(rows) -> dict:
    return {"games": len(rows), "goals": sum(r["goals"] for r in rows), "assists": sum(r["assists"] for r in rows), "points": sum(r["points"] for r in rows)}


def _streaks(flags: list[bool]) -> tuple[int, int]:
    """(current run of True at the end, longest run)."""
    longest = run = 0
    for flag in flags:
        run = run + 1 if flag else 0
        longest = max(longest, run)
    return run, longest


def _per_game(rows) -> list[dict]:
    """One entry per game (oldest first) with that member's counters."""
    games = {}
    for row in rows:
        game = games.setdefault(row.event_id, {"event": row.event, "season": row.season, "goals": 0, "assists": 0, "pim": 0, "conceded": 0, "seconds": 0, "goalie": False})
        if row.stat_type == GOAL:
            game["goals"] += row.value
        elif row.stat_type == ASSIST:
            game["assists"] += row.value
        elif row.stat_type == PIM:
            game["pim"] += row.value
        elif row.stat_type == CONCEDED:
            game["conceded"] += row.value
            game["goalie"] = True
        elif row.stat_type == GOALIE_SECONDS:
            game["seconds"] += row.value
            game["goalie"] = True
        elif row.stat_type == APPEARANCE and (row.meta or {}).get("pos") == "GO":
            game["goalie"] = True  # dressed as a goalie: a goalie game even when nothing was recorded for them
    out = sorted(games.values(), key=lambda g: g["event"].start)
    for game in out:
        game["points"] = game["goals"] + game["assists"]
    return out


def game_sections(member) -> tuple[dict | None, dict | None]:
    rows = list(GameStat.objects.filter(member=member, is_ours=True).select_related("event", "event__opponent", "season"))
    if not rows:
        return None, None
    games = _per_game(rows)
    skater = [g for g in games if not g["goalie"] or g["goals"] or g["assists"] or g["pim"]]
    player = None
    if skater:
        by_season = defaultdict(list)
        for g in skater:
            by_season[g["season"].pk if g["season"] else None].append(g)
        seasons = []
        for items in by_season.values():
            seasons.append(
                {
                    "season": _season_ref(items[0]["season"]),
                    "games": len(items),
                    "goals": sum(g["goals"] for g in items),
                    "assists": sum(g["assists"] for g in items),
                    "points": sum(g["points"] for g in items),
                    "penalty_minutes": sum(g["pim"] for g in items),
                    "_start": items[0]["event"].start,
                }
            )
        seasons.sort(key=lambda s: s["_start"])
        for s in seasons:
            del s["_start"]
        run, longest = _streaks([g["points"] > 0 for g in skater])
        goals = sum(g["goals"] for g in skater)
        points = sum(g["points"] for g in skater)
        best = max(skater, key=lambda g: (g["points"], g["goals"]))
        player = {
            "seasons": seasons,
            "form": [{"event": _game_ref(g["event"]), "goals": g["goals"], "assists": g["assists"], "points": g["points"], "penalty_minutes": g["pim"]} for g in skater[-FORM_GAMES:]],
            "home": _split([g for g in skater if g["event"].is_home_game]),
            "away": _split([g for g in skater if not g["event"].is_home_game]),
            "goals_per_game": round(goals / len(skater), 2),
            "points_per_game": round(points / len(skater), 2),
            "current_point_streak": run,
            "longest_point_streak": longest,
            "multi_point_games": sum(1 for g in skater if g["points"] >= 2),
            "best_game": {"event": _game_ref(best["event"]), "points": best["points"], "goals": best["goals"]} if best["points"] else None,
        }
    goalie = None
    nets = [g for g in games if g["goalie"]]
    if nets:
        by_season = defaultdict(list)
        for g in nets:
            by_season[g["season"].pk if g["season"] else None].append(g)

        def counters(items):
            conceded, seconds = sum(g["conceded"] for g in items), sum(g["seconds"] for g in items)
            return {
                "games": len(items),
                "conceded": conceded,
                "minutes": round(seconds / 60) if seconds else None,
                "gaa": round(conceded * 3600 / seconds, 2) if seconds else None,
                "shutouts": sum(1 for g in items if g["seconds"] and not g["conceded"]),
            }

        seasons = sorted(({"season": _season_ref(items[0]["season"]), "_start": items[0]["event"].start, **counters(items)} for items in by_season.values()), key=lambda s: s["_start"])
        for s in seasons:
            del s["_start"]
        goalie = {
            **counters(nets),
            "seasons": seasons,
            "form": [
                {"event": _game_ref(g["event"]), "conceded": g["conceded"], "minutes": round(g["seconds"] / 60) if g["seconds"] else None, "gaa": round(g["conceded"] * 3600 / g["seconds"], 2) if g["seconds"] else None} for g in nets[-FORM_GAMES:]
            ],
        }
    return player, goalie


def attendance_section(member) -> dict | None:
    now = timezone.now()
    rows = list(Attendance.objects.filter(member=member, event__start__lt=now, event__cancelled=False).select_related("event").order_by("event__start"))
    answered = [r for r in rows if r.status in HERE or r.status == Attendance.AttendanceStatus.ABSENT]
    if not answered:
        return None
    local = timezone.localtime
    today = local(now)
    keys = []
    year, month = today.year, today.month
    for _ in range(MONTHS):
        keys.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    keys.reverse()
    months = {key: [0, 0] for key in keys}
    kinds = defaultdict(lambda: [0, 0])
    for r in answered:
        here = r.status in HERE
        key = local(r.event.start).strftime("%Y-%m")
        if key in months:
            months[key][0 if here else 1] += 1
        kinds[r.event.kind][0 if here else 1] += 1
    run, longest = _streaks([r.status in HERE for r in answered])
    present = sum(1 for r in answered if r.status in HERE)
    return {
        "present": present,
        "absent": len(answered) - present,
        "rate": _rate(present, len(answered) - present),
        "months": [{"month": key, "present": p, "absent": a, "rate": _rate(p, a)} for key, (p, a) in months.items()],
        "kinds": sorted(({"kind": kind, "present": p, "absent": a, "rate": _rate(p, a)} for kind, (p, a) in kinds.items()), key=lambda k: -(k["present"] + k["absent"])),
        "current_streak": run,
        "longest_streak": longest,
    }


def staff_section(member) -> list[dict]:
    out = []
    for a in StaffAssignment.objects.filter(member=member).select_related("team", "season", "position").order_by("-season__start_date", "team__name"):
        record = {"games": 0, "won": 0, "drawn": 0, "lost": 0, "gf": 0, "ga": 0}
        for game in game_stats.played_games(a.team, a.season):
            record["games"] += 1
            record["gf"] += game.score_for
            record["ga"] += game.score_against
            record["won" if game.score_for > game.score_against else "lost" if game.score_for < game.score_against else "drawn"] += 1
        out.append({"season": _season_ref(a.season), "team": {"id": a.team.pk, "name": a.team.name}, "position": a.position.name, **record})
    return out


PENALTIES = (GameMoment.Kind.PENALTY_FOR, GameMoment.Kind.PENALTY_AGAINST, GameMoment.Kind.PENALTY_SHOT_FOR, GameMoment.Kind.PENALTY_SHOT_AGAINST)


def _officiating_kind(rows, seasons, current) -> dict | None:
    """One kind's numbers from the member's assignment rows (past games only), or None without any."""
    if not rows:
        return None
    today = timezone.localdate()
    names = {}  # type name -> games, for the legend order
    for row in rows:
        name = row.referee_type.name if row.referee_type_id else ""
        names[name] = names.get(name, 0) + 1
    types = sorted(names, key=lambda n: (-names[n], n))

    def season_of(event):
        if event.season_id:
            return next((x for x in seasons if x.pk == event.season_id), None)
        day = timezone.localtime(event.start).date()
        return next((x for x in seasons if x.start_date <= day <= x.end_date), None)

    months = []
    year, month = today.year, today.month
    for _ in range(MONTHS):
        months.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    months.reverse()
    per_month = {m: dict.fromkeys(types, 0) for m in months}
    per_season, buckets, fees, this_season = {}, {}, 0, 0
    for row in rows:
        event = row.event
        name = row.referee_type.name if row.referee_type_id else ""
        key = timezone.localtime(event.start).strftime("%Y-%m")
        if key in per_month:
            per_month[key][name] += 1
        season = season_of(event)
        if season:
            per_season[season.pk] = per_season.get(season.pk, 0) + 1
            if current and season.pk == current.pk:
                this_season += 1
                fees += row.total_payable
        teams = ", ".join(t.short_name or t.name for t in event.teams.all())
        buckets[(event.competition, teams)] = buckets.get((event.competition, teams), 0) + 1
    event_ids = {row.event_id for row in rows}
    moments = GameMoment.objects.filter(event_id__in=event_ids)
    with_moments = moments.values("event_id").distinct().count()
    penalties = moments.filter(kind__in=PENALTIES).count()
    return {
        "games": len(rows),
        "games_this_season": this_season,
        "games_per_month": round(sum(sum(v.values()) for v in per_month.values()) / MONTHS, 1),
        "penalties_per_game": round(penalties / with_moments, 1) if with_moments else None,
        "fees": fees,
        "types": types,
        "months": [{"month": m, "counts": [per_month[m][n] for n in types]} for m in months],
        "seasons": [{"season": {"id": x.pk, "name": x.name}, "games": per_season[x.pk]} for x in sorted(seasons, key=lambda x: x.start_date) if x.pk in per_season],
        "competitions": [{"competition": c, "teams": t, "games": n} for (c, t), n in sorted(buckets.items(), key=lambda kv: (-kv[1], kv[0]))],
    }  # fmt: skip


def officiating_section(member) -> dict | None:
    """The member's refereeing and officiating numbers; None when they never did either."""
    seasons = list(Season.objects.all())
    current = Season.get_current()
    now = timezone.now()
    out = {}
    for code, related in (("referee", "referee_assignments"), ("official", "official_assignments")):
        rows = list(getattr(member, related).filter(event__start__lte=now).select_related("event", "referee_type").prefetch_related("event__teams"))
        out[code] = _officiating_kind(rows, seasons, current)
    return out if any(out.values()) else None


def member_stats_document(member, *, with_games: bool) -> dict:
    player, goalie = game_sections(member) if with_games else (None, None)
    return {"analytics_available": with_games, "player": player, "goalie": goalie, "attendance": attendance_section(member), "staff": staff_section(member), "officiating": officiating_section(member)}
