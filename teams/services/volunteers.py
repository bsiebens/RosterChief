"""The volunteers overview and placing a registered volunteer -- shared by the management screens
(management.views.VolunteerListView / VolunteerPlaceView) and the management API (api/panel/routers/volunteers.py)."""

from itertools import groupby

from registration.models import RegistrationDetails
from teams.models import StaffAssignment


def volunteer_assignments(club, season):
    """Every ``season`` StaffAssignment of ``club``, by member then team."""
    return StaffAssignment.objects.filter(team__club=club, season=season).select_related("member", "team", "position").order_by("member__last_name", "member__first_name", "team__name", "position__name")


def volunteers_by_member(club, season) -> list[dict]:
    """``[{"member": Member, "assignments": [StaffAssignment, ...]}]`` for the current staff."""
    assignments = volunteer_assignments(club, season)
    return [{"member": member, "assignments": list(rows)} for member, rows in groupby(assignments, key=lambda assignment: assignment.member)]


def pending_volunteer_requests(club, season):
    """Volunteer registrations of ``season`` not yet placed into a real StaffAssignment."""
    return RegistrationDetails.objects.filter(
        entry_kind=RegistrationDetails.EntryKind.VOLUNTEER,
        resulting_staff_assignment__isnull=True,
        membership__club=club,
        membership__season=season,
    ).select_related("membership__member", "requested_team", "requested_position")


def placeable_request(club, season, pk):
    """The pending request ``pk`` or ``None`` (another club's, another season's or already placed)."""
    return pending_volunteer_requests(club, season).filter(pk=pk).first()


def already_placed(season, member, team) -> bool:
    return StaffAssignment.objects.filter(team=team, season=season, member=member).exists()


def place_volunteer(details: RegistrationDetails, form) -> StaffAssignment:
    """Save the (valid) ``VolunteerPlacementForm`` as the real StaffAssignment and stamp it back on ``details``
    so the request leaves the pending list."""
    assignment = form.save()
    details.resulting_staff_assignment = assignment
    details.save(update_fields=["resulting_staff_assignment"])
    return assignment
