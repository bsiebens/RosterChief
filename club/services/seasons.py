"""Generating a club's seasons ahead of time.

Season (club/models.py) has no stored notion of "when a season starts" -- that
lives on Club instead (season_start, season_duration_months), since different
clubs run their year on different cycles. Same shape as
events/services/recurrence.py's generate_occurrences: materialise missing rows
up to a horizon, get_or_create per row, safe to call repeatedly.
"""

import datetime

from dateutil.relativedelta import relativedelta
from django.db.models import ProtectedError
from django.db.models.deletion import Collector
from django.utils import timezone

from club.models import Season


def _initial_season_start(club, today):
    """The most recent occurrence of the club's configured season_start that is
    not later than ``today`` -- so a club with no seasons yet gets one covering
    "now" (or the most recently completed one), not an arbitrary future year."""
    anchor = club.season_start
    start = datetime.date(today.year, anchor.month, anchor.day)
    if start > today:
        start = datetime.date(today.year - 1, anchor.month, anchor.day)
    return start


def _season_end(start, club):
    return start + relativedelta(months=club.season_duration_months) - datetime.timedelta(days=1)


def generate_seasons(club, until):
    """Materialise seasons for ``club`` from wherever it last left off -- the day
    after its latest season's end_date, or its configured season_start if it has
    none yet -- through ``until``. get_or_create per row (matches the
    unique_season_dates_per_club constraint exactly), safe to call repeatedly.
    """
    latest = Season.objects.filter(club=club).order_by("-end_date").first()
    start = latest.end_date + datetime.timedelta(days=1) if latest else _initial_season_start(club, timezone.localdate())

    created = []
    while start <= until:
        end = _season_end(start, club)
        season, was_created = Season.objects.get_or_create(club=club, start_date=start, end_date=end)
        if was_created:
            _carry_guardians_into(club, season)
            created.append(season)
        start = end + datetime.timedelta(days=1)

    return created


def _carry_guardians_into(club, season):
    """Copy the previous season's guardians into a season that has just been created.

    The other half of members.services.family.carry_guardians_forward, which
    covers a guardian added *after* the later seasons already existed. Between
    them a parent keeps their tie to the club across every season boundary --
    without which they would quietly drop off the club while their child stayed
    enrolled, which is exactly the state a guardian exists to prevent.

    Copied from the immediately preceding season, not from "any season ever", so
    a guardian an admin deliberately removed stays removed rather than being
    resurrected from an older row.
    """
    from club.models import ClubMembership

    previous = Season.objects.filter(club=club, start_date__lt=season.start_date).order_by("-start_date").first()
    if previous is None:
        return

    guardians = ClubMembership.objects.filter(club=club, season=previous, kind=ClubMembership.Kind.GUARDIAN)
    ClubMembership.objects.bulk_create(
        [ClubMembership(club=club, member_id=guardian.member_id, season=season, kind=ClubMembership.Kind.GUARDIAN, status=guardian.status, signed_up_at=guardian.signed_up_at) for guardian in guardians],
        ignore_conflicts=True,
    )


def _expected_season_dates(club, until):
    """The (start_date, end_date) pairs generate_seasons would produce for
    ``club`` from scratch, ignoring whatever already exists -- used by
    resync_seasons to tell "matches the club's current settings" from "doesn't"."""
    start = _initial_season_start(club, timezone.localdate())
    expected = set()
    while start <= until:
        end = _season_end(start, club)
        expected.add((start, end))
        start = end + datetime.timedelta(days=1)
    return expected


def _is_referenced(season):
    """Whether deleting ``season`` would hit a PROTECT on any of its relations
    (ClubMembership, StaffAssignment, TeamMembership, Event, ...) without
    actually deleting anything."""
    collector = Collector(using=season._state.db)
    try:
        collector.collect([season])
    except ProtectedError:
        return True
    return False


def resync_seasons(club, until, *, commit=False):
    """Find seasons for ``club`` that don't match what its *current*
    season_start/season_duration_months would produce (e.g. left over from a
    different rule, or from before those settings were changed), within the
    same horizon generate_seasons would cover.

    A season is only ever removed if nothing references it through a PROTECTed
    relation -- a season already in use is reported as kept, never silently
    dropped. With commit=False (the default) nothing is deleted; the caller
    gets back what *would* happen.
    """
    expected = _expected_season_dates(club, until)

    removed, kept = [], []
    for season in Season.objects.filter(club=club):
        if (season.start_date, season.end_date) in expected:
            continue
        if _is_referenced(season):
            kept.append(season)
        else:
            removed.append(season)
            if commit:
                season.delete()

    return removed, kept
