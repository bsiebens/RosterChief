"""Event lookups, authority checks and shapes shared by the management event routers (events, event_timeline,
event_officials, event_extras).

* ``_events`` -- the club's events with what the payloads read prefetched; ``_get_event`` -- one of them narrowed to the
  caller's teams (404 outside it); ``_get_manageable_event`` -- any event of the club, then the manager rule (403);
* ``_can_manage`` / ``_require_manage`` -- ``EventManagerRequiredMixin``'s rule for an event or series;
* ``_event_payload`` / ``_fresh_event_payload`` -- the management event JSON; ``_row`` -- one attendance row.
"""

from django.utils.translation import gettext_lazy as _

from api.core.errors import ApiError
from api.panel.guards import require
from api.protected.routers.coach_events import coach_event_out
from club.services.access import groups_manageable_by, is_club_admin, scoped_to_managed_teams, teams_managed_by
from events.models import Attendance, Event

NOT_FOUND = _("Not found.")


# --- who may do what ------------------------------------------------------------------------


def _manage_sets(request, club) -> tuple[bool, set, set]:
    """(admin, managed team ids, manageable group ids), worked out once per request."""
    cached = getattr(request, "_panel_event_sets", None)
    if cached is None:
        admin = is_club_admin(request.user, club)
        cached = (admin, set(teams_managed_by(request.user, club).values_list("pk", flat=True)), set(groups_manageable_by(request.user, club).values_list("pk", flat=True)))
        request._panel_event_sets = cached
    return cached


def _can_manage(request, club, obj) -> bool:
    """EventManagerRequiredMixin's rule for an event or series (``teams`` / ``groups`` prefetched)."""
    admin, team_pks, group_pks = _manage_sets(request, club)
    return admin or any(team.pk in team_pks for team in obj.teams.all()) or any(group.pk in group_pks for group in obj.groups.all())


def _require_manage(request, club, obj) -> None:
    require(request, club, lambda user, club: _can_manage(request, club, obj))


# --- querysets and shapes -------------------------------------------------------------------


def _events(club):
    return Event.objects.filter(club=club).select_related("location", "opponent", "series").prefetch_related("teams", "groups", "invited_members", "excluded_members")


def _get_event(request, club, event_id) -> Event:
    event = scoped_to_managed_teams(_events(club), request.user, club).filter(pk=event_id).first()
    if event is None:
        raise ApiError(404, "not_found", NOT_FOUND)
    return event


def _get_manageable_event(request, club, event_id) -> Event:
    """Like the web's write views: not scoped to the caller's teams (an unknown id is a 404), the manager rule decides (403)."""
    event = _events(club).filter(pk=event_id).first()
    if event is None:
        raise ApiError(404, "not_found", NOT_FOUND)
    _require_manage(request, club, event)
    return event


def _event_payload(request, club, event) -> dict:
    return {
        **coach_event_out(event, club),
        "group_ids": [group.pk for group in event.groups.all()],
        "club_wide": event.club_wide,
        "season_id": event.season_id,
        "detached": event.detached,
        "can_manage": _can_manage(request, club, event),
    }


def _fresh_event_payload(request, club, event_id) -> dict:
    return _event_payload(request, club, _events(club).get(pk=event_id))


# --- attendance -----------------------------------------------------------------------------


def _attendance_counts(event) -> dict:
    counts = dict.fromkeys(Attendance.AttendanceStatus.values, 0)
    for status in event.attendances.values_list("status", flat=True):
        counts[status] = counts.get(status, 0) + 1
    return counts


def _row(attendance, *, with_private: bool) -> dict:
    member = attendance.member
    return {
        "attendance_id": attendance.pk,
        "member_id": member.pk,
        "first_name": member.first_name,
        "last_name": member.last_name,
        "status": attendance.status,
        "note": attendance.note,
        "showed_up": attendance.showed_up,
        "answered_by_coach": attendance.answered_by_coach,
        "coach_note": attendance.coach_note if with_private else None,
    }
