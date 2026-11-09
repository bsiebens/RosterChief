"""Jersey-number rules (no earlier implementation existed; implemented from the model help texts).

* A number is "held" by a team membership in the given season. Numbers are only compared within one season.
* Teams sharing a ``NumberPool`` compete for its numbers; a team without a pool only competes with itself.
* Two holders may share a number only when the pool's ``min_age_gap_years`` > 0, both have a date of birth, are
  on different teams, and were born at least that many years apart. Two players on the same team never share.
* A ``NumberReservation`` blocks its number for everyone.
"""

import datetime

from teams.models import NumberReservation, TeamMembership


def reserver_name(reservation) -> str | None:
    by = reservation.reserved_by
    if by is None:
        return None
    return (getattr(by, "get_full_name", lambda: "")() or getattr(by, "email", None)) or None


def can_release(actor, reservation) -> bool:
    """A reservation can be released by a settings admin or by the person who made it."""
    from members.services.access import can_manage_settings

    if actor is None or not getattr(actor, "is_authenticated", False):
        return False
    return can_manage_settings(actor) or (reservation.reserved_by_id is not None and reservation.reserved_by_id == actor.pk)


def reservation_info(reservation, actor) -> dict:
    return {"id": reservation.pk, "note": reservation.note, "reserved_by_name": reserver_name(reservation), "can_release": can_release(actor, reservation)}


def add_years(d: datetime.date, years: int) -> datetime.date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:  # 29 Feb
        return d.replace(year=d.year + years, day=28)


def can_share(a: TeamMembership, b: TeamMembership, pool) -> bool:
    """Two roster spots may carry the same number when they are the same person on different teams (a player keeping
    their number when they also play up a team), or when the age gap rule of the pool allows it."""
    if a.member_id == b.member_id:
        return a.team_id != b.team_id
    if pool is None or not pool.min_age_gap_years or a.team_id == b.team_id:
        return False
    da, db = a.member.date_of_birth, b.member.date_of_birth
    if da is None or db is None:
        return False
    older, younger = (da, db) if da <= db else (db, da)
    return add_years(older, pool.min_age_gap_years) <= younger


def memberships_for(season, team=None, pool=None):
    """Numbered memberships of ``season`` in ``pool``'s teams (or just ``team`` when it has no pool)."""
    qs = TeamMembership.objects.filter(season=season, jersey_number__isnull=False).select_related("member", "team")
    if pool is not None:
        return qs.filter(team__pool=pool)
    return qs.filter(team=team)


def holders_of(number, season, team, exclude_pk=None):
    pool = team.pool
    qs = memberships_for(season, team, pool).filter(jersey_number=number)
    if exclude_pk is not None:
        qs = qs.exclude(pk=exclude_pk)
    return list(qs)


def clash_message(number, season, team, member, exclude_pk=None) -> str | None:
    """``None`` if ``member`` may wear ``number`` on ``team`` in ``season``, else a sentence naming the blocker."""
    pool = team.pool
    if pool is not None:
        res = NumberReservation.objects.filter(pool=pool, number=number).first()
        if res is not None:
            return f"Number {number} is reserved" + (f": {res.note}." if res.note else ".")
    probe = TeamMembership(team=team, member=member, season=season, jersey_number=number)
    for other in holders_of(number, season, team, exclude_pk):
        if not can_share(probe, other, pool):
            where = "this team" if other.team_id == team.pk else other.team.name
            return f"Number {number} is already held by {other.member} ({where})."
    return None


def range_message(number, team) -> str | None:
    pool = team.pool
    if pool is not None and not (pool.min_number <= number <= pool.max_number):
        return f"Must be between {pool.min_number} and {pool.max_number} (pool '{pool.name}')."
    return None


def probe_membership(team, season, date_of_birth=None, number=None) -> TeamMembership:
    """An unsaved roster spot for a person who is not a member yet (only the date of birth matters for the age gap rule). Each probe is a
    different person, so two probes never count as one player keeping their number across teams."""
    from members.models import Member

    return TeamMembership(team=team, member=Member(date_of_birth=date_of_birth), season=season, jersey_number=number)


def number_available(number, season, team, date_of_birth=None, extra=()) -> bool:
    """Whether a new player born on ``date_of_birth`` (``None`` = unknown: nobody shares) may wear ``number`` on ``team`` in ``season``. ``extra``
    are further unsaved ``probe_membership`` spots (people in the same registration) that count as holders. No names are ever involved."""
    if range_message(number, team) is not None:
        return False
    pool = team.pool
    if pool is not None and NumberReservation.objects.filter(pool=pool, number=number).exists():
        return False
    probe = probe_membership(team, season, date_of_birth, number)
    holders = holders_of(number, season, team) + [e for e in extra if e.jersey_number == number]
    return all(can_share(probe, other, pool) for other in holders)


def available_numbers(team, season, date_of_birth=None, taken_in_batch=(), batch=()) -> dict:
    """The numbers a new player born on ``date_of_birth`` could wear on ``team`` in ``season``.

    ``{"pool": {"name", "min", "max"} | None, "numbers": [free numbers] | None, "taken": [numbers in the range that are not free]}``.
    A team without a pool has no range: ``numbers`` is ``None`` (any number) and ``taken`` lists the numbers its own roster holds.
    ``taken_in_batch`` are numbers to leave out whatever their holders (people in the same form); ``batch`` are unsaved ``probe_membership``
    spots that count as holders like roster members (the age gap rule can still let a younger / older sibling on another team share)."""
    pool = team.pool
    blocked = {int(n) for n in taken_in_batch}
    by_number: dict[int, list] = {}
    for holder in [*memberships_for(season, team, pool), *batch]:
        by_number.setdefault(holder.jersey_number, []).append(holder)
    if pool is None:
        return {"pool": None, "numbers": None, "taken": sorted(set(by_number) | blocked)}
    reserved = set(NumberReservation.objects.filter(pool=pool).values_list("number", flat=True))
    probe = probe_membership(team, season, date_of_birth)
    free, taken = [], []
    for n in range(pool.min_number, pool.max_number + 1):
        ok = n not in reserved and n not in blocked and all(can_share(probe, other, pool) for other in by_number.get(n, ()))
        (free if ok else taken).append(n)
    return {"pool": {"name": pool.name, "min": pool.min_number, "max": pool.max_number}, "numbers": free, "taken": taken}


def status_for(holders, reservation, pool) -> str:
    if reservation is not None:
        return "conflict" if holders else "reserved"
    if not holders:
        return "free"
    if len(holders) == 1:
        return "taken"
    if pool is None:
        return "shared"  # independent teams without a pool never clash with each other
    for i, a in enumerate(holders):
        for b in holders[i + 1 :]:
            if not can_share(a, b, pool):
                return "conflict"
    return "shared"
