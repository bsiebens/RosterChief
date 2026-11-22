"""Planning lunch sittings around a tournament's games.

Each ``TournamentDay`` may carry a lunch window (``lunch_start``/``lunch_end``). Inside it the window is cut into *sittings* (``lunch_duration``
long, ``lunch_interval`` apart, each holding up to ``lunch_capacity_teams`` teams); the teams that eat that day (non-withdrawn, with at least
one roster entry whose ``lunch_days`` contains the date -- eating is team-level, a team's eaters go together) are spread over them, preferring
for every team the sitting that disturbs its current games least.

Every sitting becomes one ``ScheduleBlock(kind=lunch)`` naming its teams and no field. The padding (the minimum time between a team's game
and its lunch) is stored in the block itself: ``starts_at``/``ends_at`` span the *padded* window (``lunch_start - gap_before`` to
``lunch_end + gap_after``), so ``allocate_schedule`` -- which already keeps a block's teams out of games overlapping it -- honours the padding
without knowing about it, while ``lunch_start``/``lunch_end`` hold the real sitting for display. Generated lunch blocks are the only ones
this module deletes; manual blocks are never touched.
"""

import datetime
from collections import defaultdict

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from ..models import Match, ScheduleBlock, TournamentTeam
from . import scheduling

LUNCH_TITLE = "Lunch"


def _aware(date: datetime.date, time: datetime.time) -> datetime.datetime:
    return timezone.make_aware(datetime.datetime.combine(date, time))


def build_sittings(window_start: datetime.datetime, window_end: datetime.datetime, *, duration: datetime.timedelta, interval: datetime.timedelta) -> list[tuple[datetime.datetime, datetime.datetime]]:
    """Sitting ``k`` starts at ``window_start + k * (duration + interval)`` and lasts ``duration``; as many as end by ``window_end``."""
    sittings, start = [], window_start
    while start + duration <= window_end:
        sittings.append((start, start + duration))
        start += duration + interval
    return sittings


def _team_ref(team) -> dict:
    return {"id": team.pk, "name": team.display_name}


def eating_teams(tournament, date: datetime.date) -> list[TournamentTeam]:
    """The non-withdrawn teams with at least one roster entry having lunch on ``date``, by name."""
    iso = date.isoformat()
    teams = TournamentTeam.objects.filter(tournament=tournament, withdrawn_at__isnull=True).select_related("team").prefetch_related("roster_entries")
    return sorted((t for t in teams if any(iso in (e.lunch_days or []) for e in t.roster_entries.all())), key=lambda t: t.display_name.lower())


def _games(tournament, duration) -> dict:
    """team id -> [(start, end)] of every scheduled game."""
    games = defaultdict(list)
    for match in Match.objects.filter(stage__tournament=tournament, scheduled_start__isnull=False):
        for team_id in (match.home_team_id, match.away_team_id):
            if team_id is not None:
                games[team_id].append((match.scheduled_start, match.scheduled_start + duration, match))
    return games


def _overlaps(a_start, a_end, b_start, b_end) -> bool:
    return a_start < b_end and b_start < a_end


def _assign(teams, sittings, capacity, games, before, after) -> tuple[list[list], list]:
    """Greedy: teams whose options differ most go first; each takes the sitting with room that has the fewest of its games inside the
    padded window, ties to the least filled, then the earliest. Returns (teams per sitting, teams left without room)."""
    cost = {t.pk: [sum(_overlaps(s - before, e + after, gs, ge) for gs, ge, _m in games.get(t.pk, ())) for s, e in sittings] for t in teams}
    order = sorted(teams, key=lambda t: (-(max(cost[t.pk]) - min(cost[t.pk])) if sittings else 0, t.display_name.lower()))
    filled: list[list] = [[] for _s in sittings]
    unplaced = []
    for team in order:
        options = [i for i in range(len(sittings)) if len(filled[i]) < capacity]
        if not options:
            unplaced.append(team)
            continue
        best = min(options, key=lambda i: (cost[team.pk][i], len(filled[i]), i))
        filled[best].append(team)
    return filled, unplaced


def clear_lunch_schedule(tournament) -> int:
    """Delete the generated lunch blocks; games are not touched. Returns how many blocks went."""
    blocks = ScheduleBlock.objects.filter(tournament=tournament, kind=ScheduleBlock.Kind.LUNCH)
    count = blocks.count()
    blocks.delete()
    return count


def generate_lunch_schedule(tournament, match_duration: datetime.timedelta | None = None) -> dict:
    """Rebuild the lunch blocks and re-plan the games around them. Returns ``{sittings_created, games_replanned, problems, note}``;
    ``problems`` are ``{type: no_room|game_close|no_window, date, team, message}`` and never raise."""
    duration_m = match_duration or datetime.timedelta(minutes=tournament.default_match_duration_minutes)
    lunch_duration = datetime.timedelta(minutes=tournament.lunch_duration_minutes)
    interval = datetime.timedelta(minutes=tournament.lunch_interval_minutes)
    before = datetime.timedelta(minutes=tournament.lunch_gap_before_minutes)
    after = datetime.timedelta(minutes=tournament.lunch_gap_after_minutes)
    problems: list[dict] = []
    created: list[tuple[ScheduleBlock, list]] = []

    with transaction.atomic():
        clear_lunch_schedule(tournament)
        games = _games(tournament, duration_m)
        for day in tournament.days.order_by("date"):
            teams = eating_teams(tournament, day.date)
            if not teams:
                continue
            if day.lunch_start is None or day.lunch_end is None:
                problems.append({"type": "no_window", "date": day.date, "team": None, "message": _("%(count)d team(s) eat on %(date)s but no lunch window is set for that day.") % {"count": len(teams), "date": day.date}})
                continue
            sittings = build_sittings(_aware(day.date, day.lunch_start), _aware(day.date, day.lunch_end), duration=lunch_duration, interval=interval)
            filled, unplaced = _assign(teams, sittings, tournament.lunch_capacity_teams, games, before, after)
            for team in unplaced:
                problems.append({"type": "no_room", "date": day.date, "team": _team_ref(team), "message": _("%(team)s could not get a lunch sitting on %(date)s: the lunch window is full.") % {"team": team.display_name, "date": day.date}})
            for (start, end), members in zip(sittings, filled, strict=True):
                if not members:
                    continue
                block = ScheduleBlock.objects.create(
                    tournament=tournament, title=LUNCH_TITLE, kind=ScheduleBlock.Kind.LUNCH, starts_at=start - before, ends_at=end + after, lunch_start=start, lunch_end=end
                )  # fmt: skip
                block.teams.set(members)
                created.append((block, members))

        note, replanned = None, 0
        if not tournament.fields.exists() or not Match.objects.filter(stage__tournament=tournament).exists():
            note = _("Lunch blocks were created; there are no fields or matches to plan around yet.")
        else:
            replanned = len(scheduling.allocate_schedule(tournament, match_duration=duration_m, reset=True))
            games = _games(tournament, duration_m)
            for block, members in created:
                for team in members:
                    for gs, ge, match in games.get(team.pk, ()):
                        if _overlaps(block.starts_at, block.ends_at, gs, ge):
                            local = timezone.localtime(gs)
                            problems.append({
                                "type": "game_close", "date": timezone.localtime(block.lunch_start).date(), "team": _team_ref(team),
                                "message": _("%(team)s plays %(match)s at %(time)s, too close to its lunch (%(lunch)s-%(end)s).") % {
                                    "team": team.display_name, "match": str(match), "time": f"{local:%H:%M}",
                                    "lunch": f"{timezone.localtime(block.lunch_start):%H:%M}", "end": f"{timezone.localtime(block.lunch_end):%H:%M}",
                                },
                            })  # fmt: skip

    return {"sittings_created": len(created), "games_replanned": replanned, "problems": problems, "note": note}


def lunch_blocks(tournament):
    return tournament.schedule_blocks.filter(kind=ScheduleBlock.Kind.LUNCH).prefetch_related("teams").order_by("lunch_start")
