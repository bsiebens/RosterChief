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

from ..models import BreakRule, Match, ScheduleBlock, Tournament, TournamentGroup, TournamentStage, TournamentTeam
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


class GroupRankSeed:
    """A bracket-seed placeholder standing in for "whichever team finishes
    Nth in this group" -- an entry a caller can pass to
    ``generate_stage_matches`` (single/double-elim only) instead of a
    concrete ``TournamentTeam`` when that slot isn't decided yet.
    ``_persist_bracket`` recognises it and populates a ``Match``'s
    ``*_source_group``/``*_source_rank`` instead of ``*_team``;
    ``resolve_match_slots`` (already fully built, no changes needed here)
    fills the real team in once that group's own matches complete."""

    __slots__ = ("group", "rank")

    def __init__(self, group, rank: int):
        self.group = group
        self.rank = rank


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
    a subset should enter (e.g. a plate/consolation stage). For
    ``SINGLE_ELIM``/``DOUBLE_ELIM`` only, an entry may also be a
    ``GroupRankSeed`` in place of a concrete team, for a slot that isn't
    decided until an earlier group stage finishes.
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


def _slot_team_kwargs(prefix: str, value) -> dict:
    """``value`` is whatever a round-1 slot's ``home_team``/``away_team``
    entry actually is: a concrete ``TournamentTeam`` (or ``None``, a bye
    seat) goes straight into ``<prefix>_team``; a ``GroupRankSeed`` instead
    goes into ``<prefix>_source_group``/``<prefix>_source_rank``, leaving
    ``<prefix>_team`` unset until ``resolve_match_slots`` fills it in."""
    if isinstance(value, GroupRankSeed):
        return {f"{prefix}_team": None, f"{prefix}_source_group": value.group, f"{prefix}_source_rank": value.rank}
    return {f"{prefix}_team": value}


def _persist_bracket(stage, bracket_rounds):
    """Shared by single/double-elim: ``bracket_rounds`` is a list of rounds,
    each a list of slot dicts (see ``tournaments.services.brackets``'s own
    module docstring for the exact shape) -- persists them as ``Match`` rows
    in order, resolving each slot's ``source_match`` reference (given as a
    0-based index into the flat list of matches created so far) to a real
    ``Match`` instance. A round-1 slot with only one side filled by a real
    team (the other entry didn't exist -- a bye) is created as a
    ``WALKOVER`` match; once every row exists, ``_resolve_byes`` pushes the
    lone team straight into whichever match(es) reference this one, the same
    way a real completed result would, so a bye never leaves a downstream
    slot stuck on "TBD".

    A lone ``GroupRankSeed`` side is deliberately *not* treated as a bye --
    there's no team yet to advance, only a placeholder that resolves later
    (see ``GroupRankSeed``'s own docstring) -- it becomes a normal
    ``SCHEDULED`` match with a genuinely empty other side, left for staff to
    notice and handle by hand once that group resolves. A known, tested edge
    case (a bracket built to avoid it never hits it at all), not an
    automated one."""
    created: list[Match] = []
    for round_number, round_slots in enumerate(bracket_rounds, start=1):
        for sequence, slot in enumerate(round_slots):
            home, away = slot.get("home_team"), slot.get("away_team")
            is_bye = ("home_team" in slot or "away_team" in slot) and (home is None) != (away is None) and not isinstance(home, GroupRankSeed) and not isinstance(away, GroupRankSeed)
            match = Match.objects.create(
                stage=stage,
                round_number=round_number,
                round_label=slot.get("round_label", ""),
                sequence=sequence,
                status=Match.Status.WALKOVER if is_bye else Match.Status.SCHEDULED,
                home_source_match=created[slot["home_source_index"]] if slot.get("home_source_index") is not None else None,
                home_source_is_winner=slot.get("home_source_is_winner", True),
                away_source_match=created[slot["away_source_index"]] if slot.get("away_source_index") is not None else None,
                away_source_is_winner=slot.get("away_source_is_winner", True),
                **_slot_team_kwargs("home", home),
                **_slot_team_kwargs("away", away),
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


def _field_availability_windows(field, fallback_date: datetime.date) -> list[tuple[datetime.datetime, datetime.datetime]]:
    """``field.availability`` as sorted, timezone-aware ``(start, end)`` pairs
    -- each entry is already day-scoped (see ``TournamentField.availability``'s
    own docstring), so an entry missing a "date" falls back to
    ``fallback_date`` rather than being treated as spanning every day."""
    windows = []
    for entry in field.availability or []:
        date = datetime.date.fromisoformat(entry["date"]) if entry.get("date") else fallback_date
        start_hour, start_minute = (int(part) for part in entry["start"].split(":"))
        end_hour, end_minute = (int(part) for part in entry["end"].split(":"))
        windows.append(
            (
                timezone.make_aware(datetime.datetime.combine(date, datetime.time(start_hour, start_minute))),
                timezone.make_aware(datetime.datetime.combine(date, datetime.time(end_hour, end_minute))),
            )
        )
    windows.sort(key=lambda pair: pair[0])
    return windows


def _field_day_start(field, fallback_date: datetime.date) -> datetime.datetime:
    """The earliest availability window's own start, or 09:00 on
    ``fallback_date`` when the field has no availability defined at all --
    that "no availability means unbounded" fallback is a deliberately kept
    simplified edge (see ``_skip_to_available_window``, which is what
    actually rolls a fully-defined multi-day field into its next window)."""
    windows = _field_availability_windows(field, fallback_date)
    if windows:
        return windows[0][0]
    return timezone.make_aware(datetime.datetime.combine(fallback_date, datetime.time(9, 0)))


def _skip_to_available_window(field, candidate_start: datetime.datetime, match_duration: datetime.timedelta, fallback_date: datetime.date) -> datetime.datetime:
    """Rolls ``candidate_start`` forward into whichever of the field's own
    availability windows can actually fit a match starting there -- a field
    with no window left today (its clock ran past today's "end", or today
    has no window at all) moves to the start of its next defined day rather
    than scheduling indefinitely later into a day no one's there.

    A field with no availability defined at all is left alone (the
    "unbounded, starts at 09:00" fallback from ``_field_day_start`` -- there
    is nothing to roll into). Likewise, once every defined window is
    exhausted, ``candidate_start`` is returned as-is rather than raising --
    a tournament under-provisioning its own field availability shouldn't
    hard-fail a schedule generation over it."""
    windows = _field_availability_windows(field, fallback_date)
    if not windows:
        return candidate_start
    for window_start, window_end in windows:
        if candidate_start < window_start:
            candidate_start = window_start
        if candidate_start + match_duration <= window_end:
            return candidate_start
    return candidate_start


@transaction.atomic
def allocate_schedule(tournament, *, match_duration: datetime.timedelta = DEFAULT_MATCH_DURATION, reset: bool = False) -> list[Match]:
    """First-draft field/time assignment for every not-yet-scheduled,
    fully-resolved match (both teams known -- a still-TBD bracket slot is
    left alone; schedule it once its source match/group resolves) across
    every stage, in stage-sequence then round order. Greedy, not optimal --
    see this module's own docstring.

    For each round, each match goes to whichever field's own clock is
    earliest; the break-rule engine supplies the gap before it fires (or the
    tournament's own ``DEFAULT_GAP`` otherwise); each field and each team
    gets its own running clock, so no field and no team is ever
    double-booked *among matches this call itself places*. Any match this
    call is *not* placing but that already has a ``scheduled_start`` --
    COMPLETE/IN_PROGRESS, or hand-moved by staff via ``reschedule_match`` --
    is treated as reserved too (``_skip_occupied_windows``, the same
    mechanism a ``ScheduleBlock`` already used), so a fresh placement can
    never land on top of one of those either.

    ``reset=True`` (the "Generate schedule" button's own mode) first clears
    every match this function placed on an earlier run (``auto_scheduled``,
    not yet played) so it can be replanned under today's parameters -- a
    match staff hand-moved via ``reschedule_match`` is never touched, since
    that call clears its own ``auto_scheduled`` flag the moment a human
    takes over its placement (and, per the paragraph above, is still
    respected as a reservation once this run replans everything else around
    it). Every match this function itself places (both below and by
    extension any left alone from a previous run) gets
    ``auto_scheduled=True``, so a *later* reset run can still tell the two
    apart. Also clears ``Tournament.schedule_needs_regeneration`` -- the
    Schedule tab's own "you moved a match by hand -- run this to replan the
    rest" banner -- since a run of this function is exactly that
    acknowledgement, reset or not."""
    if reset:
        (Match.objects.filter(stage__tournament=tournament, auto_scheduled=True).exclude(status__in=(Match.Status.COMPLETE, Match.Status.IN_PROGRESS)).update(field=None, scheduled_start=None, auto_scheduled=False))

    fields = list(tournament.fields.all())
    if not fields:
        raise SchedulingError(_("Add at least one field before generating a schedule."))

    # Fetched once, not per-match -- a tournament has few enough blocks/
    # already-placed matches that re-checking every one of them against
    # every candidate placement is cheap. "Already placed" here means any
    # match this run isn't itself scheduling: COMPLETE/IN_PROGRESS ones
    # (their slot is history) and, now that "Generate schedule" only resets
    # auto_scheduled matches, any match staff hand-moved -- both must be
    # treated as reserved the same way a ScheduleBlock is, or a fresh
    # placement could double-book straight through them (see
    # _skip_occupied_windows's own docstring).
    schedule_blocks = list(ScheduleBlock.objects.filter(tournament=tournament).select_related("field").prefetch_related("teams"))
    occupied = [_occupied_window(block.starts_at, block.ends_at, field_id=block.field_id, team_ids=frozenset(team.id for team in block.teams.all())) for block in schedule_blocks]
    occupied += [
        _occupied_window(match.scheduled_start, match.scheduled_start + match_duration, field_id=match.field_id, team_ids=frozenset(team_id for team_id in (match.home_team_id, match.away_team_id) if team_id))
        for match in Match.objects.filter(stage__tournament=tournament, scheduled_start__isnull=False)
    ]

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
                pre_roll_date = candidate_start.date()

                # Each push below (blackouts, an already-busy team, a
                # reserved window, rolling into the field's next available
                # day) only ever moves candidate_start forward -- but one
                # push can reintroduce a conflict another already cleared
                # (rolling into tomorrow can land inside tomorrow's own
                # lunch block), so settle in a small bounded loop rather
                # than a single pass.
                for _pass in range(5):
                    before = candidate_start
                    candidate_start = _skip_blackouts(field, candidate_start)
                    for team in (match.home_team, match.away_team):
                        if team.id in team_clock and team_clock[team.id] > candidate_start:
                            candidate_start = team_clock[team.id]
                    if occupied:
                        candidate_start = _skip_occupied_windows(candidate_start, match_duration, field_id=field.id, team_ids={match.home_team_id, match.away_team_id}, occupied=occupied)
                    candidate_start = _skip_to_available_window(field, candidate_start, match_duration, tournament.start_date)
                    if candidate_start == before:
                        break

                match.field = field
                match.scheduled_start = candidate_start
                match.auto_scheduled = True
                match.save(update_fields=["field", "scheduled_start", "auto_scheduled"])
                scheduled.append(match)

                field_end = candidate_start + match_duration
                # Rolling into a new day is its own implicit break -- carrying
                # a "games/minutes since break" count across a day boundary
                # would fire an intraday break rule on the very first game of
                # the next morning.
                if gap != DEFAULT_GAP or not replaces or candidate_start.date() != pre_roll_date:
                    field_games_since_break[field.id] = 0
                    field_minutes_since_break[field.id] = 0
                else:
                    field_games_since_break[field.id] += 1
                    field_minutes_since_break[field.id] += int(match_duration.total_seconds() // 60)
                field_clock[field.id] = field_end
                field_last_round[field.id] = round_number
                team_clock[match.home_team_id] = field_end
                team_clock[match.away_team_id] = field_end

    Tournament.objects.filter(pk=tournament.pk, schedule_needs_regeneration=True).update(schedule_needs_regeneration=False)
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


def _occupied_window(start, end, *, field_id=None, team_ids: frozenset = frozenset()) -> dict:
    return {"start": start, "end": end, "field_id": field_id, "team_ids": team_ids}


def _skip_occupied_windows(candidate_start: datetime.datetime, match_duration: datetime.timedelta, *, field_id, team_ids: set, occupied: list) -> datetime.datetime:
    """Pushes ``candidate_start`` past any reserved window (a ``ScheduleBlock``,
    or a match this run isn't placing -- already played, or hand-rescheduled
    by staff, see ``allocate_schedule``'s own docstring for why those must be
    treated the same way a block is) that touches either this field or one
    of ``team_ids`` and overlaps the candidate window -- the auto-generator's
    own counterpart to the field/team clocks it already keeps for matches
    it's placing *this run*, just reserved in advance rather than by one of
    those.

    Pushing past one window can newly overlap another (a team's own
    reservation might start right where the field's just pushed to), so this
    re-checks every window against the *updated* candidate window in a
    bounded loop rather than a single pass -- each pass only ever pushes
    forward, so 5 passes is always enough headroom for the handful of
    reservations a real tournament has, and this always terminates."""
    for _pass in range(5):
        candidate_end = candidate_start + match_duration
        moved = False
        for window in occupied:
            if window["start"] >= candidate_end or candidate_start >= window["end"]:
                continue
            applies = window["field_id"] == field_id or bool(team_ids & window["team_ids"])
            if applies:
                candidate_start = max(candidate_start, window["end"])
                moved = True
        if not moved:
            break
    return candidate_start


# --------------------------------------------------------------------------
# Timeline / board (gaps by field)
# --------------------------------------------------------------------------

#: How tall one minute of schedule renders as, on the per-field timeline
#: board -- a plain module constant (not a per-tournament setting, there's
#: no reason a club would ever want a different scale) shared between the
#: server-computed slot heights below and the board's own JS, which reads it
#: back from a data attribute to convert a drop position back into a time.
PX_PER_MINUTE = 4


def _slot_height_px(minutes: float) -> int:
    return max(1, round(minutes * PX_PER_MINUTE))


def _px_offset(minutes: float) -> int:
    """Same scale as ``_slot_height_px``, without its 1px floor -- used for a
    *position* (an hour mark can legitimately sit at 0px, the very top of the
    day), where ``_slot_height_px``'s floor exists only so a real but
    tiny/rounds-to-zero slot still renders as a visible sliver."""
    return round(minutes * PX_PER_MINUTE)


def _hour_marks(window_start: datetime.datetime, window_end: datetime.datetime) -> list[dict]:
    """Hour gridlines for one day's own timeline column -- every whole clock
    hour between ``window_start`` and ``window_end``, each with its own pixel
    offset from the top, so the board can render a calendar-style ruler
    (hour labels down the left edge, a matching gridline across each field's
    column) without doing any arithmetic in the template."""
    mark = window_start.replace(minute=0, second=0, microsecond=0)
    if mark < window_start:
        mark += datetime.timedelta(hours=1)
    marks = []
    while mark <= window_end:
        marks.append({"label": mark.strftime("%H:%M"), "top_px": _px_offset((mark - window_start).total_seconds() / 60)})
        mark += datetime.timedelta(hours=1)
    return marks


def _gap_slot(start: datetime.datetime, end: datetime.datetime, blackout_rules: list) -> dict:
    """One idle stretch on a field's own timeline -- ``rule`` is whichever
    blackout (``CLOCK_WINDOW``) rule explains it, already priority-ordered
    by the caller, or ``None`` for plain idle time between/around matches.
    The other three ``BreakRule`` trigger types are stateful (a running
    "games/minutes since the last break" count -- see ``gap_for_placement``)
    and not safely reconstructible here after a possible manual reschedule,
    so this deliberately doesn't try to attribute a gap to one of those;
    ``field_timeline``'s own caller shows a field's full rule list
    separately, priority-ordered, as context instead."""
    minutes = round((end - start).total_seconds() / 60)
    rule = next((r for r in blackout_rules if r.trigger_window_start < end.time() and r.trigger_window_end > start.time()), None)
    return {"type": "gap", "start": start, "end": end, "minutes": minutes, "height_px": _slot_height_px(minutes), "rule": rule}


def field_timeline(tournament) -> list[dict]:
    """Per-field board data for the Schedule tab's "Board view": each field's
    own placed matches, in start order, with the idle time before/between/
    after them surfaced as gap slots (see ``_gap_slot``) instead of left
    implicit -- what ``tournament_schedule.html``'s plain table has no way to
    show. Returns ``[{"field", "rules", "days": [{"date", "start", "end", "slots"}]}, ...]``,
    one entry per ``TournamentField`` in the tournament's own field order.

    A field with no ``availability`` defined at all falls back to one
    synthetic window per date it actually has matches on (spanning that
    day's own first match start to last match end) -- same "nothing to roll
    into, left alone" spirit ``_skip_to_available_window`` already applies,
    just for display rather than allocation."""
    duration = datetime.timedelta(minutes=tournament.default_match_duration_minutes)
    result = []

    for field in tournament.fields.all():
        matches = list(Match.objects.filter(field=field, scheduled_start__isnull=False).select_related("home_team", "away_team", "stage").order_by("scheduled_start"))

        windows = _field_availability_windows(field, tournament.start_date)
        if not windows:
            by_date: dict = {}
            for match in matches:
                by_date.setdefault(timezone.localtime(match.scheduled_start).date(), []).append(match)
            windows = [(timezone.localtime(day_matches[0].scheduled_start), timezone.localtime(day_matches[-1].scheduled_start) + duration) for day_matches in by_date.values()]

        field_rules = list(BreakRule.objects.filter(tournament=tournament).exclude(trigger_type=BreakRule.TriggerType.CLOCK_WINDOW).filter(Q(field=field) | Q(field__isnull=True)).order_by("priority", "pk"))
        blackout_rules = list(BreakRule.objects.filter(tournament=tournament, trigger_type=BreakRule.TriggerType.CLOCK_WINDOW).filter(Q(field=field) | Q(field__isnull=True)).order_by("priority", "pk"))

        days = []
        for window_start, window_end in windows:
            day_matches = [match for match in matches if window_start <= timezone.localtime(match.scheduled_start) < window_end]
            slots, cursor = [], window_start
            for index, match in enumerate(day_matches):
                match_start = timezone.localtime(match.scheduled_start)
                # No gap before the *first* match of the day, even if the
                # field's own window opens earlier -- "5 min free" before a
                # single game has even happened yet reads as a bug, not
                # useful idle time to plan around. Every later gap (between
                # matches, or after the last one) still shows.
                if index > 0 and match_start > cursor:
                    slots.append(_gap_slot(cursor, match_start, blackout_rules))
                match_end = match_start + duration
                match_minutes = duration.total_seconds() / 60
                slots.append({"type": "match", "match": match, "start": match_start, "end": match_end, "minutes": match_minutes, "height_px": _slot_height_px(match_minutes)})
                cursor = max(cursor, match_end)
            if cursor < window_end:
                slots.append(_gap_slot(cursor, window_end, blackout_rules))

            # Absolute-positioning offset, one running total per day -- kept
            # here rather than computed in the template, which has no way to
            # accumulate a value across a loop.
            top_px = 0
            for slot in slots:
                slot["top_px"] = top_px
                top_px += slot["height_px"]

            days.append({"date": window_start.date(), "start": window_start, "end": window_end, "slots": slots, "height_px": top_px, "hour_marks": _hour_marks(window_start, window_end)})

        result.append({"field": field, "rules": field_rules, "days": days})

    return result


# --------------------------------------------------------------------------
# Conflict validation
# --------------------------------------------------------------------------


def validate_placement(match, field, start: datetime.datetime, end: datetime.datetime) -> list[str]:
    """Every invariant a placement (generated or hand-dragged) must satisfy --
    returns a list of human-readable violations, empty if none. The one
    check both the generator (skip a bad slot, try the next) and any future
    manual editor (reject a drop, show the reason) share, so the two paths
    can never quietly disagree about what's valid.

    Every *other* already-placed match is assumed to run for this
    tournament's own ``default_match_duration_minutes`` -- not the module's
    fallback ``DEFAULT_MATCH_DURATION`` -- since that's the actual duration
    ``allocate_schedule``'s own default run (and the board's own gap/height
    math) used to place it."""
    violations = []
    other_duration = datetime.timedelta(minutes=field.tournament.default_match_duration_minutes)

    overlapping_on_field = Match.objects.filter(field=field, scheduled_start__lt=end).exclude(pk=match.pk).exclude(scheduled_start__isnull=True)
    if any(candidate.scheduled_start + other_duration > start for candidate in overlapping_on_field):
        violations.append(str(_("Another match is already scheduled on this field at that time.")))

    for team in (match.home_team, match.away_team):
        if team is None:
            continue
        team_matches = Match.objects.filter(stage__tournament_id=field.tournament_id, scheduled_start__lt=end).exclude(pk=match.pk).exclude(scheduled_start__isnull=True).filter(Q(home_team=team) | Q(away_team=team))
        if any(candidate.scheduled_start + other_duration > start for candidate in team_matches):
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


# --------------------------------------------------------------------------
# Manual editing
# --------------------------------------------------------------------------


def reschedule_match(match: Match, *, field, scheduled_start: datetime.datetime) -> Match:
    """The manual-editor counterpart to the auto-generator's own placements --
    runs the placement through the exact same ``validate_placement`` check
    the allocator uses, so a hand-dragged slot can never quietly violate an
    invariant the draft itself would have avoided. Raises ``SchedulingError``
    (carrying every violation, joined, so a caller can show them all at
    once) rather than silently refusing; saves and returns the match on
    success.

    Always clears ``auto_scheduled`` -- a human just took over this match's
    placement, so a later "Generate schedule" re-run (``allocate_schedule``
    with ``reset=True``) must leave it alone rather than replanning over
    this edit. Also sets ``Tournament.schedule_needs_regeneration`` -- the
    Schedule tab's own warning banner telling staff a hand-move has happened
    and the rest of the draft hasn't been replanned around it yet. Left set
    across any number of further manual edits (each one is a no-op update
    once it's already True) so staff can make several changes before
    running Generate schedule just once -- see ``allocate_schedule``, which
    clears it."""
    end = scheduled_start + datetime.timedelta(minutes=field.tournament.default_match_duration_minutes)
    violations = validate_placement(match, field, scheduled_start, end)
    if violations:
        raise SchedulingError(" ".join(violations))

    match.field = field
    match.scheduled_start = scheduled_start
    match.auto_scheduled = False
    match.save(update_fields=["field", "scheduled_start", "auto_scheduled"])
    Tournament.objects.filter(pk=field.tournament_id, schedule_needs_regeneration=False).update(schedule_needs_regeneration=True)
    return match


def clear_schedule(tournament) -> int:
    """ "Delete schedule" -- unschedules (clears ``field``/``scheduled_start``)
    every not-yet-played match in the tournament, auto-placed or
    hand-dragged alike, without touching the ``Match`` rows themselves (the
    bracket/round-robin/group structure survives; only the timing is wiped).
    A ``COMPLETE``/``IN_PROGRESS`` match already happened -- its placement is
    history, not a draft to discard -- so it's left untouched, same guard
    ``allocate_schedule``'s own ``reset=True`` uses. Also clears
    ``schedule_needs_regeneration`` -- with nothing left scheduled, there's
    nothing left for a hand-move to be out of sync with. Returns the number
    of matches cleared."""
    count = Match.objects.filter(stage__tournament=tournament).exclude(status__in=(Match.Status.COMPLETE, Match.Status.IN_PROGRESS)).update(field=None, scheduled_start=None, auto_scheduled=False)
    Tournament.objects.filter(pk=tournament.pk, schedule_needs_regeneration=True).update(schedule_needs_regeneration=False)
    return count


def record_result(match: Match, *, score_home: int, score_away: int, went_to_overtime: bool = False, penalty_home: int | None = None, penalty_away: int | None = None) -> list[Match]:
    """Enters a match's final score, marks it complete, and immediately
    cascades the result forward via ``resolve_match_slots`` -- entering a
    result and advancing the bracket are one action from the editor's own
    point of view, not two separate steps a staff member has to remember to
    do in order. Returns whatever ``resolve_match_slots`` returns (the
    matches that changed as a result), so a caller can flag them for
    re-allocation."""
    match.score_home = score_home
    match.score_away = score_away
    match.went_to_overtime = went_to_overtime
    match.penalty_home = penalty_home
    match.penalty_away = penalty_away
    match.status = Match.Status.COMPLETE
    match.save(update_fields=["score_home", "score_away", "went_to_overtime", "penalty_home", "penalty_away", "status"])
    return resolve_match_slots(match)
