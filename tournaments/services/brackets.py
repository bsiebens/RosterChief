"""Single- and double-elimination bracket *seeding* -- pure functions, no
database access, no Django models beyond the entries passed in. Each
returns a list of rounds; each round is a list of "slot" dicts describing
one match:

- ``{"home_team": entry, "away_team": entry}`` -- both sides already known
  (round 1, seeded straight from ``entries``).
- ``{"home_source_index": i, "home_source_is_winner": bool, ...}`` -- a side
  that isn't known yet, resolved once an earlier match completes.
  ``*_source_index`` is a 0-based index into the *flat*, round-by-round
  concatenation of every round this function returns (round 1's matches
  first, then round 2's, ...) -- exactly the order
  ``tournaments.services.scheduling._persist_bracket`` creates ``Match``
  rows in, so an index here always lands on the right already-created row.

``tournaments.services.scheduling._persist_bracket`` is the only caller;
it turns these dicts into real ``Match`` rows and wires the source indices
into real self-referential FKs.
"""

import math

from django.utils.translation import gettext_lazy as _


class BracketError(Exception):
    """A bracket could not be seeded from the given entries."""


def _next_power_of_two(value: int) -> int:
    power = 1
    while power < value:
        power *= 2
    return power


def _seed_order(size: int) -> list[int]:
    """The standard bracket seed order for a field of ``size`` (a power of
    two) -- e.g. for 8: ``[1, 8, 4, 5, 2, 7, 3, 6]``, so round 1 pairs
    1v8, 4v5, 2v7, 3v6 (every pairing sums to size+1, and the strongest
    seeds are kept apart for as many rounds as possible). Recursive doubling:
    each half's own order is mirrored to produce the next size up."""
    if size == 1:
        return [1]
    previous = _seed_order(size // 2)
    order = []
    for seed in previous:
        order.append(seed)
        order.append(size + 1 - seed)
    return order


def _round_label(round_number: int, total_rounds: int) -> str:
    names = {0: _("Final"), 1: _("Semifinal"), 2: _("Quarterfinal"), 3: _("Round of 16")}
    remaining = total_rounds - round_number
    return str(names.get(remaining, _("Round %(n)s") % {"n": round_number}))


def _ordinal(n: int) -> str:
    suffix = "th" if 11 <= (n % 100) <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _placement_round_label(hi: int, lo: int, sub_total_rounds: int) -> str:
    """The label for one round of a placement sub-bracket deciding ranks
    ``hi``-``lo`` (see ``single_elimination_placement_bracket``'s own
    docstring) -- a deciding 2-entry match is just named after the better
    rank it settles ("3rd place", "5th place", ...), the same way a real
    "3rd place playoff" is; a bigger sub-bracket still mid-way through gets
    its own rank range prefixed onto its own Semifinal/Quarterfinal/...
    label (``sub_total_rounds`` is *that sub-bracket's own*, not the whole
    tournament's), e.g. "Ranks 5-8 · Semifinal"."""
    if lo - hi == 1:
        return str(_("%(rank)s place") % {"rank": _ordinal(hi)})
    return str(_("Ranks %(hi)s-%(lo)s · %(round)s") % {"hi": hi, "lo": lo, "round": _round_label(1, sub_total_rounds)})


def single_elimination_bracket(entries: list) -> list[list[dict]]:
    """A standard seeded knockout bracket. ``entries`` should already be
    seed-ordered (strongest first) -- lower seeds get a bye (a round-1 slot
    with only one side filled, the other ``None``) when ``len(entries)``
    isn't a power of two; ``_persist_bracket`` turns a lone-sided slot into a
    ``WALKOVER`` match and auto-advances the present team."""
    if len(entries) < 2:
        raise BracketError(_("Need at least two teams."))

    size = _next_power_of_two(len(entries))
    seeded = [entries[position - 1] if position <= len(entries) else None for position in _seed_order(size)]
    total_rounds = int(math.log2(size))

    round1 = [{"home_team": seeded[i], "away_team": seeded[i + 1], "round_label": _round_label(1, total_rounds)} for i in range(0, len(seeded), 2)]
    rounds = [round1]

    previous_round_start, previous_round_size = 0, len(round1)
    round_number = 2
    while previous_round_size > 1:
        this_round = [
            {
                "home_source_index": previous_round_start + i * 2,
                "away_source_index": previous_round_start + i * 2 + 1,
                "round_label": _round_label(round_number, total_rounds),
            }
            for i in range(previous_round_size // 2)
        ]
        rounds.append(this_round)
        previous_round_start += previous_round_size
        previous_round_size //= 2
        round_number += 1

    return rounds


def single_elimination_placement_bracket(entries: list) -> list[list[dict]]:
    """A single-elimination bracket that keeps going: every loser drops into
    a placement sub-bracket against the other losers from that same round,
    recursively, until every single rank -- 1st through last, not just "how
    far a team got" -- is decided by a real match. The main bracket (the
    "winners" lineage) is identical to ``single_elimination_bracket``; every
    other match is new.

    Requires an exact power-of-two number of teams (4, 8, 16, ...), for the
    same reason ``double_elimination_bracket`` imposes the same restriction:
    a bye's cascading effect on which placement match receives "the loser
    that didn't exist" is real complexity this first pass doesn't attempt.

    Construction is a plain FIFO worklist of "sub-brackets still needing a
    round played", not a naive recursive call tree -- every match's
    ``*_source_index`` must point at a *strictly earlier* position in the
    one flat, round-by-round list this returns (see the module's own
    docstring), and processing the queue in FIFO order guarantees a
    sub-bracket's own round is always fully emitted (with valid indices)
    before either of the two child sub-brackets it just spawned (its
    winners, continuing toward a better rank; its losers, dropping toward a
    worse one) get their own turn. Each sub-bracket tracks only the rank
    range (``hi``-``lo``) it's deciding -- its own remaining round count
    (Quarterfinal/Semifinal/Final-equivalent) falls straight out of
    ``len(frontier)`` at each step, with no separate counter needed, since a
    sub-bracket only ever plays exactly one round before splitting in two.

    The ``hi == 1`` lineage (started by the very first sub-bracket) *is*
    ``single_elimination_bracket``'s own bracket, unprefixed, so
    placement-off vs placement-on main-bracket labels read identically;
    every other sub-bracket's matches get a rank-range label instead (see
    ``_placement_round_label``)."""
    if len(entries) < 2 or (len(entries) & (len(entries) - 1)) != 0:
        raise BracketError(_("A full placement bracket needs an exact power-of-two number of teams (4, 8, 16, ...) for now -- byes aren't supported for this option yet."))

    size = len(entries)
    seeded = [entries[position - 1] for position in _seed_order(size)]

    rounds: list[list[dict]] = []
    flat_index = 0
    queue = [{"frontier": seeded, "hi": 1, "lo": size}]

    while queue:
        bracket = queue.pop(0)
        frontier = bracket["frontier"]
        if len(frontier) <= 1:
            continue

        hi, lo = bracket["hi"], bracket["lo"]
        sub_total_rounds = int(math.log2(len(frontier)))
        label = _round_label(1, sub_total_rounds) if hi == 1 else _placement_round_label(hi, lo, sub_total_rounds)

        this_round: list[dict] = []
        winners_frontier: list = []
        losers_frontier: list = []
        for i in range(0, len(frontier), 2):
            slot = {"round_label": label}
            for prefix, value in (("home", frontier[i]), ("away", frontier[i + 1])):
                if isinstance(value, dict):
                    slot[f"{prefix}_source_index"] = value["ref_index"]
                    slot[f"{prefix}_source_is_winner"] = value["is_winner"]
                else:
                    slot[f"{prefix}_team"] = value
            this_round.append(slot)

            match_index = flat_index + len(this_round) - 1
            winners_frontier.append({"ref_index": match_index, "is_winner": True})
            losers_frontier.append({"ref_index": match_index, "is_winner": False})

        rounds.append(this_round)
        flat_index += len(this_round)

        mid = (hi + lo) // 2
        queue.append({"frontier": winners_frontier, "hi": hi, "lo": mid})
        queue.append({"frontier": losers_frontier, "hi": mid + 1, "lo": lo})

    return rounds


def double_elimination_bracket(entries: list) -> list[list[dict]]:
    """Winners bracket (identical to ``single_elimination_bracket``) plus a
    losers bracket built from it, plus a grand final. Losers-bracket rounds
    alternate between consuming the next winners-round's losers and an
    internal round where the previous losers-round's own survivors play each
    other -- the standard double-elimination shape, in natural pairing order
    rather than the more elaborate rematch-avoiding seed tables some bracket
    software uses (a deliberate, documented simplification for a
    home-tournament tool). No "bracket reset" match if the losers-bracket
    finalist wins the grand final -- that's a live, results-dependent branch
    a pre-generated draft can't express; add it by hand if it happens.

    Requires ``len(entries)`` to be an exact power of two (4, 8, 16, ...):
    a bye's cascading effect on which losers-bracket match receives "the
    loser that didn't exist" is real complexity this first pass doesn't
    attempt -- raises ``BracketError`` otherwise."""
    if len(entries) < 2 or (len(entries) & (len(entries) - 1)) != 0:
        raise BracketError(_("Double-elimination needs an exact power-of-two number of teams (4, 8, 16, ...) for now -- byes aren't supported for this format yet."))

    winners_rounds = single_elimination_bracket(entries)
    total_winners_rounds = len(winners_rounds)

    winners_round_start = [0]
    for round_matches in winners_rounds:
        winners_round_start.append(winners_round_start[-1] + len(round_matches))
    total_winners_matches = winners_round_start[-1]

    losers_rounds: list[list[dict]] = []
    losers_round_start = [total_winners_matches]

    first_losers_round = [
        {
            "home_source_index": winners_round_start[0] + i,
            "home_source_is_winner": False,
            "away_source_index": winners_round_start[0] + i + 1,
            "away_source_is_winner": False,
            "round_label": str(_("Losers round 1")),
        }
        for i in range(0, len(winners_rounds[0]), 2)
    ]
    losers_rounds.append(first_losers_round)
    losers_round_start.append(losers_round_start[-1] + len(first_losers_round))

    next_winners_round_to_consume = 1
    losers_round_number = 2
    while len(losers_rounds[-1]) > 1 or next_winners_round_to_consume < total_winners_rounds - 1:
        previous_start, previous_size = losers_round_start[-2], len(losers_rounds[-1])

        if next_winners_round_to_consume < total_winners_rounds - 1:
            winners_start = winners_round_start[next_winners_round_to_consume]
            this_round = [
                {
                    "home_source_index": previous_start + i,
                    "home_source_is_winner": True,
                    "away_source_index": winners_start + i,
                    "away_source_is_winner": False,
                    "round_label": str(_("Losers round %(n)s") % {"n": losers_round_number}),
                }
                for i in range(previous_size)
            ]
            next_winners_round_to_consume += 1
        else:
            this_round = [
                {
                    "home_source_index": previous_start + i,
                    "home_source_is_winner": True,
                    "away_source_index": previous_start + i + 1,
                    "away_source_is_winner": True,
                    "round_label": str(_("Losers round %(n)s") % {"n": losers_round_number}),
                }
                for i in range(0, previous_size, 2)
            ]

        losers_rounds.append(this_round)
        losers_round_start.append(losers_round_start[-1] + len(this_round))
        losers_round_number += 1

    grand_final = [
        {
            "home_source_index": winners_round_start[-2],
            "home_source_is_winner": True,
            "away_source_index": losers_round_start[-2],
            "away_source_is_winner": True,
            "round_label": str(_("Grand final")),
        }
    ]

    return [*winners_rounds, *losers_rounds, grand_final]
