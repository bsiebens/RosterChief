"""Jersey-number-pool availability rules.

A number is unique *within a pool* (a club-defined grouping of one or more
Teams, see teams.models.NumberPool/Team.pool), not per team -- two teams
sharing a pool can never field the same number at once. "In use" counts both
a real, placed TeamMembership.jersey_number and a pending, not-yet-placed
registration.models.RegistrationDetails.requested_jersey_number, so a number
is blocked the moment someone registers for it, paid or not (see
RegistrationDetails' own docstring on why a request isn't a placement yet).

The one exception -- two different people sharing a number -- is a 5+ year
age gap, sized in days (5 * 365.25) rather than naively diffing years, so it
doesn't misfire around a birthday. A missing date_of_birth on either side is
treated conservatively as "not available": the gap can't be verified, so it
isn't assumed.
"""

import datetime

from club.models import Season
from members.models import Member
from registration.models import RegistrationDetails
from teams.models import NumberReservation, TeamMembership

_FIVE_YEARS = datetime.timedelta(days=round(5 * 365.25))


def _previous_season(season: Season) -> Season | None:
    return Season.objects.filter(club_id=season.club_id, start_date__lt=season.start_date).order_by("-start_date").first()


def numbers_taken(pool, season: Season) -> dict[int, list[Member]]:
    """Every number currently claimed in ``pool`` for ``season`` or the
    season immediately before it, keyed by number, mapping to every member
    holding it (almost always one, but see NumberPool's own docstring on a
    member legitimately holding more than one distinct number)."""
    seasons = [season.pk]
    previous = _previous_season(season)
    if previous is not None:
        seasons.append(previous.pk)

    taken: dict[int, list[Member]] = {}

    placed = TeamMembership.objects.filter(team__pool=pool, season_id__in=seasons).exclude(jersey_number=None).select_related("member")
    for membership in placed:
        taken.setdefault(membership.jersey_number, []).append(membership.member)

    pending = RegistrationDetails.objects.filter(requested_team__pool=pool, membership__season_id__in=seasons).exclude(requested_jersey_number=None).select_related("membership__member")
    for details in pending:
        taken.setdefault(details.requested_jersey_number, []).append(details.membership.member)

    return taken


def _age_gap_exempts(holder: Member, for_member: Member | None) -> bool:
    if for_member is None or holder.pk == for_member.pk:
        return False
    if holder.date_of_birth is None or for_member.date_of_birth is None:
        return False
    return abs(holder.date_of_birth - for_member.date_of_birth) >= _FIVE_YEARS


def is_number_available(pool, season: Season, number: int, *, for_member: Member | None = None) -> bool:
    """Whether ``number`` can be claimed in ``pool`` for ``season`` -- by
    ``for_member`` if given (used to apply the age-gap exception and to let a
    member keep their own number), or in the abstract otherwise."""
    if NumberReservation.objects.filter(pool=pool, number=number).exists():
        return False

    holders = numbers_taken(pool, season).get(number, [])
    for holder in holders:
        if for_member is not None and holder.pk == for_member.pk:
            continue
        if not _age_gap_exempts(holder, for_member):
            return False
    return True


def available_numbers(pool, season: Season, *, for_member: Member | None = None) -> list[int]:
    """``pool``'s full range, minus whatever is_number_available rejects."""
    return [number for number in range(pool.min_number, pool.max_number + 1) if is_number_available(pool, season, number, for_member=for_member)]


def member_current_number(member: Member, pool, season: Season) -> int | None:
    """The lowest jersey number ``member`` holds on any team in ``pool``, for
    ``season`` or the immediately preceding one -- a lapsed member
    re-registering still sees their old number. None if they hold none."""
    seasons = [season.pk]
    previous = _previous_season(season)
    if previous is not None:
        seasons.append(previous.pk)

    numbers = TeamMembership.objects.filter(member=member, team__pool=pool, season_id__in=seasons).exclude(jersey_number=None).order_by("jersey_number").values_list("jersey_number", flat=True)
    return numbers.first()
