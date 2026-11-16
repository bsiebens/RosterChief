"""Jersey numbers held by registrations that are still waiting for the billing review.

Nothing extra is stored: a *hold* is derived from the registration itself -- a player line (not excluded) of a batch that is neither confirmed nor
cancelled, with a requested team and a requested number. It counts like a roster spot of that player (same pool, the pool's age-gap rule on the
player's date of birth), so nobody else is offered or accepted for that number while the review is pending. It disappears by itself when the batch is
cancelled, the line is excluded or its team / number changes (staff review), or the batch is confirmed (then a real ``TeamMembership`` takes over).

``lock`` serialises submissions per pool so two people asking for the same number at the same time cannot both pass the check.
"""

from registration.models import RegistrationDetails
from teams.models import NumberPool, Team, TeamMembership


def awaiting_lines(season, *, exclude_batch=None):
    qs = RegistrationDetails.objects.filter(
        batch__season=season, batch__confirmed_at__isnull=True, batch__cancelled_at__isnull=True, entry_kind=RegistrationDetails.EntryKind.PLAYER,
        excluded=False, requested_jersey_number__isnull=False, requested_team__isnull=False,
    ).select_related("member", "requested_team", "requested_team__pool", "batch")  # fmt: skip
    if exclude_batch is not None:
        qs = qs.exclude(batch=exclude_batch)
    return qs


def _probe(line: RegistrationDetails) -> TeamMembership:
    """An unsaved roster spot standing for the hold."""
    return TeamMembership(team=line.requested_team, member=line.member, season=line.batch.season, jersey_number=line.requested_jersey_number)


def holders(season, team, *, exclude_batch=None) -> list[TeamMembership]:
    """The holds that compete with ``team``: those of every team in its pool, or of the team itself when it has no pool."""
    qs = awaiting_lines(season, exclude_batch=exclude_batch)
    qs = qs.filter(requested_team__pool_id=team.pool_id) if team.pool_id is not None else qs.filter(requested_team=team)
    return [_probe(line) for line in qs]


def lock(teams) -> None:
    """Take the row locks that serialise number checks (call inside a transaction, before checking): the pool of each team, or the team itself
    when it has none. Locks are taken in a fixed order so concurrent submissions cannot deadlock."""
    pool_ids = sorted({t.pool_id for t in teams if t.pool_id is not None}, key=str)
    team_ids = sorted({t.pk for t in teams if t.pool_id is None}, key=str)
    if pool_ids:
        list(NumberPool.objects.select_for_update().filter(pk__in=pool_ids).order_by("pk"))
    if team_ids:
        list(Team.objects.select_for_update().filter(pk__in=team_ids).order_by("pk"))


def pending_by_number(season, pool=None, team=None) -> dict[int, list]:
    """``{number: [hold lines]}`` for the staff numbers overview (``pool``'s teams, or just ``team`` when it has no pool)."""
    qs = awaiting_lines(season)
    qs = qs.filter(requested_team__pool=pool) if pool is not None else qs.filter(requested_team__pool__isnull=True)
    if team is not None:
        qs = qs.filter(requested_team=team)
    out: dict[int, list] = {}
    for line in qs.order_by("requested_jersey_number", "member__last_name", "member__first_name"):
        out.setdefault(line.requested_jersey_number, []).append(line)
    return out
