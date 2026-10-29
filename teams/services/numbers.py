"""Jersey-number-pool availability rules.

A number is unique *within a pool* (a club-defined grouping of one or more
Teams, see teams.models.NumberPool/Team.pool), not per team -- two teams
sharing a pool can never field the same number at once. "In use" counts both
a real, placed TeamMembership.jersey_number and a pending, not-yet-placed
registration.models.RegistrationDetails.requested_jersey_number, so a number
is blocked the moment someone registers for it, paid or not (see
RegistrationDetails' own docstring on why a request isn't a placement yet).

The one exception -- two different people sharing a number -- is an age gap
of at least the pool's own NumberPool.min_age_gap_years (5 by default, 0
turns sharing off entirely), sized in days (years * 365.25) rather than
naively diffing years, so it doesn't misfire around a birthday. A missing date_of_birth on either side is
treated conservatively as "not available": the gap can't be verified, so it
isn't assumed.
"""

import datetime
import itertools

from django.utils.translation import gettext_lazy as _

from club.models import Season
from members.models import Member
from registration.models import RegistrationDetails
from teams.models import NumberReservation, TeamMembership

#: NumberPool.min_age_gap_years' own default -- for has_unresolved_conflict
#: callers that have no pool at hand.
DEFAULT_MIN_AGE_GAP_YEARS = 5


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


def _age_gap_exempts(holder: Member, for_member: Member | None, min_age_gap_years: int) -> bool:
    if for_member is None or holder.pk == for_member.pk or min_age_gap_years <= 0:
        return False
    if holder.date_of_birth is None or for_member.date_of_birth is None:
        return False
    return abs(holder.date_of_birth - for_member.date_of_birth) >= datetime.timedelta(days=round(min_age_gap_years * 365.25))


def is_number_available(pool, season: Season, number: int, *, for_member: Member | None = None) -> bool:
    """Whether ``number`` can be claimed in ``pool`` for ``season`` -- by
    ``for_member`` if given (used to apply the age-gap exception and to let a
    member keep their own number), or in the abstract otherwise."""
    if NumberReservation.objects.filter(pool=pool, number=number).exists():
        return False

    return _holders_allow(numbers_taken(pool, season).get(number, []), pool, for_member)


def has_unresolved_conflict(holders: list[Member], *, min_age_gap_years: int = DEFAULT_MIN_AGE_GAP_YEARS) -> bool:
    """Whether ``holders`` (everyone currently sharing one number) includes a
    pair the age-gap exception doesn't cover -- the only way that happens is
    an admin's explicit override on TeamMembershipForm (see issue #6): a
    member management/ADMIN grant may knowingly place someone on a number
    is_number_available would otherwise reject, e.g. to carry over historical
    data from before numbers were tracked accurately. Two holders the age gap
    *does* cover is a legitimate, by-design share, not a conflict to flag --
    management.views.NumberListView uses this to tell the two apart on the
    Numbers page.

    The same member appearing more than once (on two teams sharing a pool, or
    placed on one and pending on another) is one player, not a clash with
    themselves, so a pair with the same pk is never a conflict."""
    return any(a.pk != b.pk and not _age_gap_exempts(a, b, min_age_gap_years) for a, b in itertools.combinations(holders, 2))


def available_numbers(pool, season: Season, *, for_member: Member | None = None) -> list[int]:
    """``pool``'s full range, minus whatever is_number_available rejects."""
    # Same rule as is_number_available, but the reservations and holders are read once for the whole range
    # rather than re-queried per number (a 1-99 pool was ~400 queries).
    reserved = set(NumberReservation.objects.filter(pool=pool).values_list("number", flat=True))
    taken = numbers_taken(pool, season)
    return [number for number in range(pool.min_number, pool.max_number + 1) if number not in reserved and _holders_allow(taken.get(number, []), pool, for_member)]


def _holders_allow(holders: list[Member], pool, for_member: Member | None) -> bool:
    return all((for_member is not None and holder.pk == for_member.pk) or _age_gap_exempts(holder, for_member, pool.min_age_gap_years) for holder in holders)


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


# --- the Numbers overview (management /numbers and the management API) ------------------------


def number_holder(member, team) -> dict:
    """One holder of a number -- structured for the PDF export's own name/team/birth year columns."""
    return {"member": member, "team": team.short_name or team.name, "birth_year": member.date_of_birth.year if member.date_of_birth else None}


def number_tile(number, state, *, people=(), note="", reservation=None) -> dict:
    people = list(people)
    holders = [f"{person['member']} ({person['team']})" for person in people]
    return {"number": number, "state": state, "people": people, "holders": holders, "note": note, "reservation": reservation}


def build_number_tiles(pool, season) -> list[dict]:
    """One pool's full number range for ``season`` -- five states: ``reserved``, ``taken``,
    ``conflict`` (an admin's override left an unresolved clash), ``pending`` (requested at
    registration, not yet placed), ``previous`` (held last season) and ``available``."""
    previous = Season.objects.filter(club=pool.club, start_date__lt=season.start_date).order_by("-start_date").first()

    reservations = {reservation.number: reservation for reservation in NumberReservation.objects.filter(pool=pool).select_related("reserved_by")}
    # Kept as memberships (not pre-formatted people, unlike the previous-season/pending dicts below) until
    # after the conflict check -- has_unresolved_conflict needs the actual Member rows to weigh the age-gap
    # exception, not display text.
    placed_this_season = {}
    for membership in TeamMembership.objects.filter(team__pool=pool, season=season).exclude(jersey_number=None).select_related("member", "team"):
        placed_this_season.setdefault(membership.jersey_number, []).append(membership)
    placed_previous_season = {}
    if previous is not None:
        for membership in TeamMembership.objects.filter(team__pool=pool, season=previous).exclude(jersey_number=None).select_related("member", "team"):
            placed_previous_season.setdefault(membership.jersey_number, []).append(number_holder(membership.member, membership.team))
    pending_this_season = {}
    for details in RegistrationDetails.objects.filter(requested_team__pool=pool, membership__season=season).exclude(requested_jersey_number=None).select_related("membership__member", "requested_team"):
        pending_this_season.setdefault(details.requested_jersey_number, []).append(number_holder(details.membership.member, details.requested_team))

    tiles = []
    for number in range(pool.min_number, pool.max_number + 1):
        reservation = reservations.get(number)
        if reservation is not None:
            tiles.append(number_tile(number, "reserved", note=reservation.note, reservation=reservation))
        elif number in placed_this_season:
            memberships = placed_this_season[number]
            # A genuine conflict (issue #6) only ever comes from an admin's override_conflict on
            # TeamMembershipForm -- is_number_available already blocks everything else, age-gap-exempt
            # shares included.
            state = "conflict" if has_unresolved_conflict([membership.member for membership in memberships], min_age_gap_years=pool.min_age_gap_years) else "taken"
            tiles.append(number_tile(number, state, people=[number_holder(membership.member, membership.team) for membership in memberships]))
        elif number in pending_this_season:
            tiles.append(number_tile(number, "pending", people=pending_this_season[number]))
        elif number in placed_previous_season:
            tiles.append(number_tile(number, "previous", people=placed_previous_season[number]))
        else:
            tiles.append(number_tile(number, "available"))
    return tiles


class NumberReservationError(Exception):
    """A reservation that can't be made; ``code`` is a stable machine string, ``str()`` the localised message."""

    def __init__(self, code: str, message):
        super().__init__(str(message))
        self.code = code


def reserve_number(club, pool, number: int, *, note: str, user) -> NumberReservation:
    """Block ``number`` in ``pool`` by hand (e.g. a retired shirt)."""
    if not (pool.min_number <= number <= pool.max_number):
        raise NumberReservationError("invalid_number", _("That number is not valid for this pool."))
    if NumberReservation.objects.filter(pool=pool, number=number).exists():
        raise NumberReservationError("already_reserved", _("#%(number)s is already reserved.") % {"number": number})
    return NumberReservation.objects.create(club=club, pool=pool, number=number, note=note, reserved_by=user)


def can_release_reservation(user, club, reservation) -> bool:
    """Whoever reserved the number themselves, or a member admin releasing someone else's."""
    from club.services.access import can_manage_members

    return reservation.reserved_by_id == user.id or can_manage_members(user, club)
