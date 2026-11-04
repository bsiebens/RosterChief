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
