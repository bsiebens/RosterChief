"""Management volunteers (``/manage/volunteers``): who holds a staff position this season, and the
volunteer registrations still waiting for one.

Same rule as management.views (``MemberAdminRequiredMixin``): ``can_manage_members``. Placing uses
``VolunteerPlacementForm`` and teams.services.volunteers; everything else about a staff assignment is
managed on the team's own staff endpoints.
"""

import uuid

from django.utils.translation import gettext_lazy as _
from ninja import Router

from api.core.errors import ApiError
from api.core.forms import form_error, post_data
from api.core.schemas import ErrorOut
from api.panel.guards import panel_club, require
from api.panel.schemas import volunteers as s
from club.services.access import can_manage_members, current_season
from management.forms import VolunteerPlacementForm
from teams.services.volunteers import already_placed, pending_volunteer_requests, place_volunteer, placeable_request, volunteers_by_member

router = Router(tags=["panel: volunteers"])

PREFIX = "/manage/volunteers"
ERRORS = {401: ErrorOut, 403: ErrorOut, 404: ErrorOut}


def _gate(request):
    club = panel_club(request)
    require(request, club, can_manage_members)
    return club


def _ref(obj) -> dict | None:
    return {"id": obj.pk, "name": obj.name} if obj is not None else None


def _assignment_out(assignment) -> dict:
    return {"id": assignment.pk, "team": _ref(assignment.team), "position": _ref(assignment.position)}


@router.get(PREFIX, response={200: s.VolunteersOut, **ERRORS}, summary="Volunteers and open volunteer registrations")
def list_volunteers(request):
    """Current-season staff by member, and pending registrations. Both are empty without a current season. Club admins and member admins."""
    club = _gate(request)
    season = current_season(club)
    if season is None:
        return {"season": None, "volunteers": [], "pending": []}
    volunteers = [{"member_id": row["member"].pk, "first_name": row["member"].first_name, "last_name": row["member"].last_name, "assignments": [_assignment_out(a) for a in row["assignments"]]} for row in volunteers_by_member(club, season)]
    pending = [
        {
            "id": details.pk,
            "member_id": details.membership.member_id,
            "first_name": details.membership.member.first_name,
            "last_name": details.membership.member.last_name,
            "requested_team": _ref(details.requested_team),
            "requested_position": _ref(details.requested_position),
        }
        for details in pending_volunteer_requests(club, season)
    ]
    return {
        "season": {"id": season.pk, "name": season.name, "start_date": season.start_date, "end_date": season.end_date, "is_current": True},
        "volunteers": volunteers,
        "pending": pending,
    }


@router.post(PREFIX + "/{uuid:request_id}/place", response={201: s.AssignmentOut, 409: ErrorOut, 422: ErrorOut, **ERRORS}, summary="Place a registered volunteer on a team")
def place(request, request_id: uuid.UUID, payload: s.PlaceIn):
    """Creates the staff assignment (team and a staff position) and takes the registration off the pending list. 409 `already_placed` when the person already holds a position on that team this season. Club admins and member admins."""
    club = _gate(request)
    season = current_season(club)
    details = placeable_request(club, season, request_id) if season is not None else None
    if details is None:
        raise ApiError(404, "not_found", _("Not found."))
    member = details.membership.member
    form = VolunteerPlacementForm(post_data(payload.dict(), single={"team_id": "team", "position_id": "position"}), club=club, season=season, member=member)
    if not form.is_valid():
        raise form_error(form, {"team": "team_id", "position": "position_id"})
    if already_placed(season, member, form.cleaned_data["team"]):
        raise ApiError(409, "already_placed", _("This person already holds a position on that team."))
    return 201, _assignment_out(place_volunteer(details, form))
