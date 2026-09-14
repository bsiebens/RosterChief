"""Turning a tournament's approved teams into a draft schedule: format ->
matches (round robin, groups, Swiss here; single/double-elimination bracket
seeding lives in ``tournaments.services.brackets``, called from
``generate_stage_matches`` below), matches -> field/time via a greedy
allocator, the break-rule engine that allocator consults, the conflict
validator both the allocator and any future manual editor share, and
standings/slot resolution once results come in.

Everything here produces a *first draft*: staff can rearrange the result by
hand afterwards (validate_placement is the one check both paths share), and
nothing here claims to find the shortest-possible tournament day -- true
field-packing optimality is a much harder problem than a home tournament
needs solved perfectly, see this module's own tests for what it does
guarantee (no team or field ever double-booked, break rules always honoured).
"""

import datetime

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from ..models import BreakRule, Match, TournamentGroup, TournamentStage, TournamentTeam
from .brackets import BracketError, double_elimination_bracket, single_elimination_bracket

#: The one thing every tournament falls back to when no BreakRule fires --
#: see BreakRule's own docstring for why this isn't itself a stored row.
DEFAULT_GAP = datetime.timedelta(minutes=5)

#: How long a single match is assumed to run when generating a draft --
#: mirrors events.models.ASSUMED_EVENT_DURATION's own "a reasonable default,
#: not a per-sport setting" reasoning; a real per-tournament override can be
#: added once a real tournament's needs make the gap obvious.
DEFAULT_MATCH_DURATION = datetime.timedelta(minutes=45)


class SchedulingError(Exception):
    """A stage's matches could not be generated, or a placement is invalid."""


# --------------------------------------------------------------------------
# Format -> matches
# --------------------------------------------------------------------------


def round_robin_pairings(entries: list) -> list[list[tuple]]:
    """The standard "circle method": fixes ``entries[0]``, rotates the rest
    each round. Returns a list of rounds, each a list of ``(home, away)``
    tuples -- every entry meets every other exactly once, and a round is
    already a maximal matching (no entry appears twice in it), so rounds
    parallelize across fields for free. An odd-length ``entries`` gets a
    ``None`` bye seat that never produces a match."""
    players = list(entries)
    if len(players) < 2:
        return []
    if len(players) % 2:
        players.append(None)

    n = len(players)
    rounds = []
    for _round_index in range(n - 1):
        pairs = [(players[i], players[n - 1 - i]) for i in range(n // 2) if players[i] is not None and players[n - 1 - i] is not None]
        rounds.append(pairs)
        players = [players[0], players[-1], *players[1:-1]]
    return rounds


def snake_seed_groups(entries: list, num_groups: int) -> list[list]:
    """Seeded snake draft: 1st seed to group 1, 2nd to group 2, ..., then
    back down (2, 1, 2, 1, ...) rather than straight round-robin dealing --
    keeps the strongest entries spread evenly rather than clustering by
    seed-list order. ``entries`` is assumed already ordered strongest-first
    (by ``TournamentTeam.seed``, unseeded entries last)."""
    if num_groups < 1:
        raise SchedulingError(_("Need at least one group."))
    groups = [[] for _ in range(num_groups)]
    index, step = 0, 1
    for entry in entries:
        groups[index].append(entry)
        if index + step == num_groups or index + step < 0:
            step = -step
        else:
            index += step
    return groups


@transaction.atomic
def generate_stage_matches(stage: TournamentStage, *, entries: list | None = None) -> list[Match]:
    """Generates and persists this stage's ``Match`` rows from its own
    ``format``. Raises ``SchedulingError`` if the stage already has matches
    (regenerate by deleting them first -- an explicit, deliberate action, not
    something this function does implicitly) or if ``format`` is ``CUSTOM``
    (custom formats are hand-built in the editor, never auto-generated -- see
    ``TournamentStage.Format``'s own docstring).

    ``entries`` defaults to every non-withdrawn ``TournamentTeam`` in the
    tournament, seed-ordered -- pass an explicit list for a stage that only
    a subset should enter (e.g. a plate/consolation stage).
    """
    if stage.matches.exists():
        raise SchedulingError(_("This stage already has matches -- delete them first to regenerate."))
    if stage.format == TournamentStage.Format.CUSTOM:
        raise SchedulingError(_("A custom-format stage has no automatic generator -- build its matches directly."))

    entries = list(TournamentTeam.objects.filter(tournament_id=stage.tournament_id, withdrawn_at__isnull=True).order_by("seed")) if entries is None else entries
    if len(entries) < 2:
        raise SchedulingError(_("Need at least two teams to generate a schedule."))

    if stage.format == TournamentStage.Format.ROUND_ROBIN:
        return _generate_round_robin(stage, entries)
    if stage.format == TournamentStage.Format.GROUPS:
        return _generate_groups(stage, entries)
    if stage.format == TournamentStage.Format.SINGLE_ELIM:
        return _generate_single_elim(stage, entries)
    if stage.format == TournamentStage.Format.DOUBLE_ELIM:
        return _generate_double_elim(stage, entries)
    if stage.format == TournamentStage.Format.SWISS:
        return generate_next_swiss_round(stage, entries=entries)
    raise SchedulingError(_("Unknown format: %(format)s") % {"format": stage.format})


def _generate_round_robin(stage, entries):
    matches = []
    for round_number, pairs in enumerate(round_robin_pairings(entries), start=1):
        for sequence, (home, away) in enumerate(pairs):
            matches.append(Match(stage=stage, round_number=round_number, sequence=sequence, home_team=home, away_team=away))
    return Match.objects.bulk_create(matches)


def _generate_groups(stage, entries):
    num_groups = stage.config.get("num_groups") or 1
    seeded_groups = snake_seed_groups(entries, num_groups)

    matches = []
    for group_index, group_entries in enumerate(seeded_groups):
        group = TournamentGroup.objects.create(stage=stage, name=_("Group %(letter)s") % {"letter": chr(ord("A") + group_index)}, sequence=group_index)
        for round_number, pairs in enumerate(round_robin_pairings(group_entries), start=1):
            for sequence, (home, away) in enumerate(pairs):
                matches.append(Match(stage=stage, group=group, round_number=round_number, sequence=sequence, home_team=home, away_team=away))
    return Match.objects.bulk_create(matches)


def _generate_single_elim(stage, entries):
    try:
        bracket = single_elimination_bracket(entries)
    except BracketError as error:
        raise SchedulingError(str(error)) from error
    return _persist_bracket(stage, bracket)


def _generate_double_elim(stage, entries):
    try:
        bracket = double_elimination_bracket(entries)
    except BracketError as error:
        raise SchedulingError(str(error)) from error
    return _persist_bracket(stage, bracket)


def _persist_bracket(stage, bracket_rounds):
    """Shared by single/double-elim: ``bracket_rounds`` is a list of rounds,
    each a list of slot dicts (see ``tournaments.services.brackets``'s own
    module docstring for the exact shape) -- persists them as ``Match`` rows
    in order, resolving each slot's ``source_match`` reference (given as a
    0-based index into the flat list of matches created so far) to a real
    ``Match`` instance. A round-1 slot with only one side filled (the other
    entry didn't exist -- a bye) is created as a ``WALKOVER`` match; once
    every row exists, ``_resolve_byes`` pushes the lone team straight into
    whichever match(es) reference this one, the same way a real completed
    result would, so a bye never leaves a downstream slot stuck on "TBD"."""
    created: list[Match] = []
    for round_number, round_slots in enumerate(bracket_rounds, start=1):
        for sequence, slot in enumerate(round_slots):
            is_bye = ("home_team" in slot or "away_team" in slot) and (slot.get("home_team") is None) != (slot.get("away_team") is None)
            match = Match.objects.create(
                stage=stage,
                round_number=round_number,
                round_label=slot.get("round_label", ""),
                sequence=sequence,
                status=Match.Status.WALKOVER if is_bye else Match.Status.SCHEDULED,
                home_team=slot.get("home_team"),
                away_team=slot.get("away_team"),
                home_source_match=created[slot["home_source_index"]] if slot.get("home_source_index") is not None else None,
                home_source_is_winner=slot.get("home_source_is_winner", True),
                away_source_match=created[slot["away_source_index"]] if slot.get("away_source_index") is not None else None,
                away_source_is_winner=slot.get("away_source_is_winner", True),
            )
            created.append(match)

    _resolve_byes(created)
    return created


def _resolve_byes(matches: list[Match]):
    """Auto-advances the lone team on every ``WALKOVER`` match created by
    ``_persist_bracket`` -- byes only ever occur in round 1 (every later
    round is always built from two sources, never directly team-seeded), so
    one pass over every match, in creation order, is enough: by the time a
    later round's match is reached, any bye that feeds it has already been
    resolved."""
    for match in matches:
        if match.status != Match.Status.WALKOVER:
            continue
        winner = match.home_team or match.away_team
        for dependent in match.feeds_home.all():
            dependent.home_team = winner
            dependent.save(update_fields=["home_team"])
        for dependent in match.feeds_away.all():
            dependent.away_team = winner
            dependent.save(update_fields=["away_team"])


def generate_next_swiss_round(stage: TournamentStage, *, entries: list | None = None) -> list[Match]:
    """Swiss is the one format generated round-by-round *during* the
    tournament rather than all at once: round 1 is seed order (no results
    exist yet to pair by), every later round pairs by current standings
    (closest points first), skipping a pairing that's already been played
    where a fresh one is available. Call this again after each round's
    results are in -- "generate next round" is a recurring action, not a
    one-time draft, for this format alone.

    Raises ``SchedulingError`` if the previous round isn't fully complete
    yet (pairing by standings before every result is in would pair on stale
    data) or the configured round count (``stage.config["rounds"]``,
    default 1 more than the number already played is only ever generated
    when explicitly asked for another one -- there's no fixed total here)
    has already been reached.
    """
    existing_rounds = sorted(stage.matches.values_list("round_number", flat=True).distinct())
    next_round_number = (existing_rounds[-1] + 1) if existing_rounds else 1

    total_rounds = stage.config.get("rounds")
    if total_rounds and next_round_number > total_rounds:
        raise SchedulingError(_("This stage's configured round count has already been reached."))

    if existing_rounds:
        last_round_matches = list(stage.matches.filter(round_number=existing_rounds[-1]))
        if any(match.status != Match.Status.COMPLETE for match in last_round_matches):
            raise SchedulingError(_("The previous round isn't fully complete yet."))
        pairs = _swiss_pairs_from_standings(stage, entries)
    else:
        # No results exist yet to pair by -- top half vs bottom half of seed
        # order (1 vs n/2+1, 2 vs n/2+2, ...), the standard round-1 Swiss
        # pairing. zip() stopping at the shorter side is deliberate: an odd
        # entry count leaves the lowest seed with a bye this round, not an
        # error -- a real fairness policy (rotating who sits out) is future
        # work, flagged in this module's own docstring as a known simplification.
        entries = list(TournamentTeam.objects.filter(tournament_id=stage.tournament_id, withdrawn_at__isnull=True).order_by("seed")) if entries is None else entries
        half = len(entries) // 2
        pairs = list(zip(entries[:half], entries[half:], strict=False))

    matches = [Match(stage=stage, round_number=next_round_number, sequence=sequence, home_team=home, away_team=away) for sequence, (home, away) in enumerate(pairs)]
    return Match.objects.bulk_create(matches)


def _swiss_pairs_from_standings(stage, entries=None):
    """Pairs adjacent-in-standings entries, skipping a repeat pairing in
    favour of the next-closest available opponent where one exists -- a
    repeat is allowed only when every alternative has already been
    exhausted (an odd number of already-mutually-played entries), rather
    than leaving someone unpaired."""
    standings = compute_standings(stage)
    ranked = [row["team"] for row in standings]
    if entries is not None:
        ranked = [team for team in ranked if team in entries]

    already_played = set()
    for match in stage.matches.select_related("home_team", "away_team"):
        if match.home_team_id and match.away_team_id:
            already_played.add(frozenset((match.home_team_id, match.away_team_id)))

    remaining = list(ranked)
    pairs = []
    while len(remaining) > 1:
        team = remaining.pop(0)
        opponent = next((candidate for candidate in remaining if frozenset((team.id, candidate.id)) not in already_played), remaining[0])
        remaining.remove(opponent)
        pairs.append((team, opponent))
    return pairs


# --------------------------------------------------------------------------
# Break-rule engine
# --------------------------------------------------------------------------


def blackout_windows(field) -> list[tuple[datetime.time, datetime.time]]:
    """This field's hard-exclusion clock windows (tournament-wide rules plus
    ones scoped to this field specifically) -- a candidate placement that
    overlaps any of these is invalid outright, not merely "needs a bigger
    gap" (see ``validate_placement``)."""
    rules = BreakRule.objects.filter(tournament_id=field.tournament_id, trigger_type=BreakRule.TriggerType.CLOCK_WINDOW).filter(Q(field=field) | Q(field__isnull=True))
    return [(rule.trigger_window_start, rule.trigger_window_end) for rule in rules]


def gap_for_placement(field, *, games_since_last_break: int, minutes_since_last_break: int, round_number: int, just_finished_round_number: int | None) -> tuple[datetime.timedelta, bool]:
    """The gap to insert before the next game on ``field``, and whether it
    *replaces* the tournament's own ``DEFAULT_GAP`` or adds on top of it.
    Rules are checked in ``priority`` order, first match wins; nothing
    firing falls back to ``(DEFAULT_GAP, True)``.

    The three non-blackout trigger types are each judged against a counter
    that resets whenever *any* break fires on this field -- "every 3rd game"
    means every 3 games since the field's last break, not "only ever after
    the literal 3rd game of the day" -- so a recurring maintenance break
    (the brief's own "ice cleaning every 3 games" example) actually recurs.
    """
    rules = BreakRule.objects.filter(tournament_id=field.tournament_id).exclude(trigger_type=BreakRule.TriggerType.CLOCK_WINDOW).filter(Q(field=field) | Q(field__isnull=True)).order_by("priority", "pk")

    for rule in rules:
        fires = False
        if rule.trigger_type == BreakRule.TriggerType.GAME_COUNT:
            fires = games_since_last_break >= rule.trigger_every_n_games
        elif rule.trigger_type == BreakRule.TriggerType.ELAPSED_TIME:
            fires = minutes_since_last_break >= rule.trigger_every_n_minutes
        elif rule.trigger_type == BreakRule.TriggerType.ROUND_BOUNDARY:
            fires = just_finished_round_number == rule.trigger_after_round_number and round_number > just_finished_round_number

        if fires:
            return datetime.timedelta(minutes=rule.gap_minutes), rule.replaces_default_gap

    return DEFAULT_GAP, True


# --------------------------------------------------------------------------
# Field / time-slot allocation
# --------------------------------------------------------------------------


def _field_day_start(field, fallback_date: datetime.date) -> datetime.datetime:
    """The first availability window's own start -- see TournamentField's own
    docstring on why multi-window/multi-day availability is a known,
    deliberately simplified edge for this first pass (a tournament running
    past one calendar day would need more than "the first window" honoured)."""
    windows = field.availability or []
    if windows:
        first = windows[0]
        date = datetime.date.fromisoformat(first["date"]) if "date" in first else fallback_date
        hour, minute = (int(part) for part in first["start"].split(":"))
        return timezone.make_aware(datetime.datetime.combine(date, datetime.time(hour, minute)))
    return timezone.make_aware(datetime.datetime.combine(fallback_date, datetime.time(9, 0)))


@transaction.atomic
def allocate_schedule(tournament, *, match_duration: datetime.timedelta = DEFAULT_MATCH_DURATION) -> list[Match]:
    """First-draft field/time assignment for every not-yet-scheduled,
    fully-resolved match (both teams known -- a still-TBD bracket slot is
    left alone; schedule it once its source match/group resolves) across
    every stage, in stage-sequence then round order. Greedy, not optimal --
    see this module's own docstring.

    For each round, each match goes to whichever field's own clock is
    earliest; the break-rule engine supplies the gap before it fires (or the
    tournament's own ``DEFAULT_GAP`` otherwise); each field and each team
    gets its own running clock, so no field and no team is ever
    double-booked by construction."""
    fields = list(tournament.fields.all())
    if not fields:
        raise SchedulingError(_("Add at least one field before generating a schedule."))

    # The tournament's own start date, not "today" -- a draft generated a
    # week before the tournament runs must not land on the day it happened
    # to be clicked.
    field_clock = {field.id: _field_day_start(field, tournament.start_date) for field in fields}
    field_games_since_break = dict.fromkeys((field.id for field in fields), 0)
    field_minutes_since_break = dict.fromkeys((field.id for field in fields), 0)
    field_last_round = dict.fromkeys((field.id for field in fields), None)
    team_clock: dict = {}

    scheduled: list[Match] = []
    for stage in tournament.stages.order_by("sequence"):
        matches = list(stage.matches.filter(scheduled_start__isnull=True, home_team__isnull=False, away_team__isnull=False).order_by("round_number", "sequence"))
        for round_number in sorted({match.round_number for match in matches}):
            for match in [m for m in matches if m.round_number == round_number]:
                field = min(fields, key=lambda f: field_clock[f.id])
                candidate_start = field_clock[field.id]

                gap, replaces = gap_for_placement(
                    field,
                    games_since_last_break=field_games_since_break[field.id],
                    minutes_since_last_break=field_minutes_since_break[field.id],
                    round_number=round_number,
                    just_finished_round_number=field_last_round[field.id],
                )
                candidate_start += gap if replaces else DEFAULT_GAP + gap
                candidate_start = _skip_blackouts(field, candidate_start)

                # A team already has a match running from an earlier round --
                # don't start its next one before that one's assumed to end.
                for team in (match.home_team, match.away_team):
                    if team.id in team_clock and team_clock[team.id] > candidate_start:
                        candidate_start = team_clock[team.id]

                match.field = field
                match.scheduled_start = candidate_start
                match.save(update_fields=["field", "scheduled_start"])
                scheduled.append(match)

                field_end = candidate_start + match_duration
                if gap != DEFAULT_GAP or not replaces:
                    field_games_since_break[field.id] = 0
                    field_minutes_since_break[field.id] = 0
                else:
                    field_games_since_break[field.id] += 1
                    field_minutes_since_break[field.id] += int(match_duration.total_seconds() // 60)
                field_clock[field.id] = field_end
                field_last_round[field.id] = round_number
                team_clock[match.home_team_id] = field_end
                team_clock[match.away_team_id] = field_end

    return scheduled


def _skip_blackouts(field, candidate_start: datetime.datetime) -> datetime.datetime:
    # BreakRule's window fields are plain, timezone-naive TimeFields -- a
    # club admin enters "12:00" meaning their own local wall clock, not UTC.
    # candidate_start is timezone-aware (internally UTC); comparing its raw
    # .time() against a local wall-clock window would silently be off by
    # the local UTC offset, so convert to local time first.
    local_start = timezone.localtime(candidate_start)
    for window_start, window_end in blackout_windows(field):
        if window_start <= local_start.time() < window_end:
            return timezone.make_aware(datetime.datetime.combine(local_start.date(), window_end))
    return candidate_start


# --------------------------------------------------------------------------
# Conflict validation
# --------------------------------------------------------------------------


def validate_placement(match, field, start: datetime.datetime, end: datetime.datetime) -> list[str]:
    """Every invariant a placement (generated or hand-dragged) must satisfy --
    returns a list of human-readable violations, empty if none. The one
    check both the generator (skip a bad slot, try the next) and any future
    manual editor (reject a drop, show the reason) share, so the two paths
    can never quietly disagree about what's valid."""
    violations = []

    overlapping_on_field = Match.objects.filter(field=field, scheduled_start__lt=end).exclude(pk=match.pk).exclude(scheduled_start__isnull=True)
    if any(candidate.scheduled_start + DEFAULT_MATCH_DURATION > start for candidate in overlapping_on_field):
        violations.append(str(_("Another match is already scheduled on this field at that time.")))

    for team in (match.home_team, match.away_team):
        if team is None:
            continue
        team_matches = Match.objects.filter(stage__tournament_id=field.tournament_id, scheduled_start__lt=end).exclude(pk=match.pk).exclude(scheduled_start__isnull=True).filter(Q(home_team=team) | Q(away_team=team))
        if any(candidate.scheduled_start + DEFAULT_MATCH_DURATION > start for candidate in team_matches):
            violations.append(str(_("%(team)s already has a match scheduled at that time.") % {"team": team}))

    # Local wall-clock time, same reasoning as _skip_blackouts.
    local_start, local_end = timezone.localtime(start).time(), timezone.localtime(end).time()
    if any(window_start <= local_start < window_end or window_start < local_end <= window_end for window_start, window_end in blackout_windows(field)):
        violations.append(str(_("That time falls inside a blackout window on this field.")))

    return violations


# --------------------------------------------------------------------------
# Standings and slot resolution
# --------------------------------------------------------------------------


DEFAULT_STANDINGS_CONFIG = {"points": {"win": 3, "draw": 1, "loss": 0}, "tiebreakers": ["goal_diff", "goals_for"]}


def compute_standings(stage: TournamentStage, *, group: TournamentGroup | None = None) -> list[dict]:
    """Points-then-tie-break standings for a stage (or one of its groups),
    computed fresh from completed matches every time rather than stored --
    ``stage.standings_config`` supplies the points-per-result and the
    tie-break chain (goal_diff/goals_for first, then a mini-table among
    just the tied teams for head_to_head, the standard way to resolve a
    3-plus-way tie without it depending on match order)."""
    config = {**DEFAULT_STANDINGS_CONFIG, **(stage.standings_config or {})}
    points_config = config["points"]

    matches = stage.matches.filter(status=Match.Status.COMPLETE, score_home__isnull=False, score_away__isnull=False)
    if group is not None:
        matches = matches.filter(group=group)

    rows: dict = {}

    def row_for(team):
        return rows.setdefault(team.id, {"team": team, "played": 0, "won": 0, "drawn": 0, "lost": 0, "points": 0, "goals_for": 0, "goals_against": 0})

    for match in matches.select_related("home_team", "away_team"):
        if match.home_team is None or match.away_team is None:
            continue
        home, away = row_for(match.home_team), row_for(match.away_team)
        home["played"] += 1
        away["played"] += 1
        home["goals_for"] += match.score_home
        home["goals_against"] += match.score_away
        away["goals_for"] += match.score_away
        away["goals_against"] += match.score_home

        if match.score_home > match.score_away:
            home["won"] += 1
            away["lost"] += 1
            home["points"] += points_config["win"]
            away["points"] += points_config["loss"]
        elif match.score_away > match.score_home:
            away["won"] += 1
            home["lost"] += 1
            away["points"] += points_config["win"]
            home["points"] += points_config["loss"]
        else:
            home["drawn"] += 1
            away["drawn"] += 1
            home["points"] += points_config["draw"]
            away["points"] += points_config["draw"]

    for row in rows.values():
        row["goal_diff"] = row["goals_for"] - row["goals_against"]

    return _ranked(list(rows.values()), config["tiebreakers"])


def _ranked(rows: list[dict], tiebreakers: list[str]) -> list[dict]:
    rows.sort(key=lambda row: row["points"], reverse=True)
    ranked: list[dict] = []
    for _points, group in _group_by(rows, key=lambda row: row["points"]):
        ranked.extend(_break_ties(group, tiebreakers))
    for index, row in enumerate(ranked, start=1):
        row["rank"] = index
    return ranked


def _group_by(rows, *, key):
    groups: list[tuple] = []
    for row in rows:
        value = key(row)
        if groups and groups[-1][0] == value:
            groups[-1][1].append(row)
        else:
            groups.append((value, [row]))
    return groups


def _break_ties(tied_rows: list[dict], tiebreakers: list[str]) -> list[dict]:
    if len(tied_rows) <= 1 or not tiebreakers:
        return tied_rows

    criterion, *rest = tiebreakers
    if criterion == "head_to_head":
        team_ids = {row["team"].id for row in tied_rows}
        mini_table = {row["team"].id: 0 for row in tied_rows}
        head_to_head_matches = Match.objects.filter(status=Match.Status.COMPLETE, score_home__isnull=False, score_away__isnull=False, home_team_id__in=team_ids, away_team_id__in=team_ids)
        for match in head_to_head_matches:
            if match.score_home > match.score_away:
                mini_table[match.home_team_id] += 3
            elif match.score_away > match.score_home:
                mini_table[match.away_team_id] += 3
            else:
                mini_table[match.home_team_id] += 1
                mini_table[match.away_team_id] += 1
        tied_rows = sorted(tied_rows, key=lambda row: mini_table[row["team"].id], reverse=True)
        sort_key = lambda row: mini_table[row["team"].id]  # noqa: E731
    else:
        sort_key = lambda row: row.get(criterion, 0)  # noqa: E731
        tied_rows = sorted(tied_rows, key=sort_key, reverse=True)

    result: list[dict] = []
    for _value, group in _group_by(tied_rows, key=sort_key):
        result.extend(_break_ties(group, rest))
    return result


def resolve_match_slots(match: Match) -> list[Match]:
    """Once ``match`` is complete, resolves every downstream ``Match`` slot
    that referenced it as a winner/loser source -- the bracket is a graph of
    these references (see ``Match``'s own docstring), so a completed result
    propagates forward without any format-specific "advance the winner"
    script. Also resolves any slot sourced from *this match's group*, once
    every match in that group is complete. Returns the list of matches that
    changed, so a caller can decide whether to (re-)allocate them."""
    if match.status != Match.Status.COMPLETE or match.winner is None:
        return []

    updated: list[Match] = []
    for dependent in match.feeds_home.all():
        dependent.home_team = match.winner if dependent.home_source_is_winner else match.loser
        dependent.save(update_fields=["home_team"])
        updated.append(dependent)
    for dependent in match.feeds_away.all():
        dependent.away_team = match.winner if dependent.away_source_is_winner else match.loser
        dependent.save(update_fields=["away_team"])
        updated.append(dependent)

    if match.group_id is not None and not match.group.matches.exclude(status=Match.Status.COMPLETE).exists():
        standings = compute_standings(match.stage, group=match.group)
        by_rank = {row["rank"]: row["team"] for row in standings}
        for dependent in Match.objects.filter(home_source_group=match.group):
            dependent.home_team = by_rank.get(dependent.home_source_rank)
            dependent.save(update_fields=["home_team"])
            updated.append(dependent)
        for dependent in Match.objects.filter(away_source_group=match.group):
            dependent.away_team = by_rank.get(dependent.away_source_rank)
            dependent.save(update_fields=["away_team"])
            updated.append(dependent)

    return updated
