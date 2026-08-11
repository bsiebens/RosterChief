from django.utils import timezone

from club.models import ClubMembership, Season
from members.models import Member


def eligible_roster_members(club):
    """Members eligible to be added to a team's roster or staff: active (paid) for
    the club this season or next, regardless of which season's roster/staff list is
    currently being edited (the team page's season switcher can point at either)."""
    today = timezone.localdate()
    eligible_seasons = [season for season in (Season.covering(club, today), Season.next_after(club, today)) if season is not None]
    return Member.objects.filter(
        member_of__club=club,
        member_of__season__in=eligible_seasons,
        member_of__status=ClubMembership.StatusChoices.ACTIVE,
        # Guardians are attached to the club only as a parent of a member, so they
        # are not eligible for a roster spot *or* a staff one. A parent who
        # volunteers as a coach is a member of the club and marked as one.
        member_of__kind=ClubMembership.Kind.MEMBER,
    ).distinct()
