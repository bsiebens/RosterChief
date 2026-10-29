"""Management referee staffing dashboard (API.md §17).

The JSON counterpart of the ``/manage/referees/management/`` screen
(management.views.RefereeManagementDashboardView): every upcoming home game the club arranges a referee
(or official) for, within a window (``range``) and narrowed by what is still missing (``need``), with who
is assigned, who could be, and the not-yet-confirmed sign-ups; plus season-wide status counts and the
referee workload. Assigning and unassigning go through ``/manage/events/{id}/referees|officials``
(event_officials router); the referee / official lists and levels are in the levels router, a member's
eligibility in the members router, and the referee-admin grants in the roles router.

Who may: ``can_manage_referees`` (club admin, member admin or a referee-admin grant), like the web page.
The query logic is ``events.services.staffing``, shared with the view.
"""

from ninja import Query, Router, Schema

from api.core.schemas import ErrorOut
from api.panel.guards import panel_club, require
from api.panel.schemas import referee_management as s
from club.services.access import can_manage_referees
from events.services.officials import officials_enabled_for
from events.services.staffing import DEFAULT_NEED, DEFAULT_RANGE, clean_need, clean_range, staffing_dashboard

router = Router(tags=["panel: referee management"])

ERRORS = {401: ErrorOut, 403: ErrorOut}


class DashboardQuery(Schema):
    range: str = DEFAULT_RANGE
    need: str = DEFAULT_NEED


def _ref(obj) -> dict:
    return {"id": obj.pk, "name": obj.name}


def _crew(game, *, kind: str) -> dict:
    """The panel of one crew on a game (``kind`` is ``referee`` or ``official``), from the attributes the service set."""
    rows = getattr(game, f"{kind}_rows")
    groups = getattr(game, "pending_signups" if kind == "referee" else "pending_official_signups")
    needed = getattr(game, "needs_referees" if kind == "referee" else "needs_officials")
    signups = {key: [{"member_id": signup.member_id, "name": str(signup.member), "status": signup.status, "responded_at": signup.responded_at} for signup in group] for key, group in groups.items()}
    return {
        "needed": needed,
        "max": game.max_referees if kind == "referee" else game.max_officials,
        "full": getattr(game, f"{kind}s_full"),
        "fees_pending": getattr(game, "fees_pending" if kind == "referee" else "official_fees_pending"),
        "assigned": [{"id": row.pk, "member_id": row.member_id, "name": row.display_name, "external": row.member_id is None, "assigned_by": str(row.assigned_by) if row.assigned_by_id else None} for row in rows],
        "candidates": [{"member_id": m.pk, "name": str(m), "expired": bool(getattr(m, "referee_expired", False)), "has_conflict": m.has_conflict, "conflict_titles": m.conflict_titles} for m in getattr(game, f"{kind}_candidates")],
        "signups": {"interested": [], "waiting": [], "declined": [], **signups},
    }


def _game_out(game, *, officials_enabled: bool) -> dict:
    return {
        "id": game.pk,
        "title": game.title,
        "kind": game.kind,
        "start": game.start,
        "location": game.location.name if game.location_id else None,
        "opponent": game.opponent.name if game.opponent_id else None,
        "teams": [_ref(team) for team in sorted(game.teams.all(), key=lambda t: t.name)],
        "referees": _crew(game, kind="referee"),
        "officials": _crew(game, kind="official") if officials_enabled else None,
    }


def _status(no_one, understaffed, fully_staffed, fees_pending) -> dict:
    return {"without_anyone": no_one, "understaffed": understaffed, "fully_staffed": fully_staffed, "fees_pending": fees_pending}


@router.get("/manage/referees/management", response={200: s.StaffingDashboardOut, **ERRORS}, summary="Referee staffing dashboard")
def referee_management(request, params: Query[DashboardQuery]):
    """Upcoming club-managed home games with their referee (and official) panels. `range` picks the window,
    `need` narrows it to games still short a referee / an official / either; unknown values fall back to the defaults.
    Club admin, member admin or referee admin."""
    club = panel_club(request)
    require(request, club, can_manage_referees)
    officials = officials_enabled_for(club)
    data = staffing_dashboard(club, range_choice=clean_range(params.range), need_choice=clean_need(params.need, officials), officials_enabled=officials)
    return {
        "range": data["range_choice"],
        "need": data["need_choice"],
        "officials_enabled": officials,
        "need_counts": data["need_counts"],
        "games": [_game_out(game, officials_enabled=officials) for game in data["games"]],
        "referee_status": _status(data["kpi_no_referee"], data["kpi_understaffed"], data["kpi_fully_staffed"], data["kpi_fees_pending"]),
        "official_status": _status(data["kpi_no_official"], data["kpi_officials_understaffed"], data["kpi_officials_fully_staffed"], data["kpi_official_fees_pending"]) if officials else None,
        "active_referees": data["kpi_active_referees"],
        "total_assignments": data["kpi_total_assignments"],
        "avg_games_per_referee": data["kpi_avg_games_per_referee"],
        "workload": [{"member_id": row["member__id"], "first_name": row["member__first_name"], "last_name": row["member__last_name"], "games": row["games"], "total_fees": row["total_fees"]} for row in data["workload"]],
    }
