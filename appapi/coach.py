"""Team scope for the coach endpoints (API.md §5.2).

Every coach route carries ``team_id`` in the path and is validated against
``teams_staffed_by`` -- a team the caller doesn't staff this season answers 404, the same as a
team that doesn't exist. Actions the web app limits to a management position
(``teams_managed_by``, the ``can_manage_active_team`` rule) additionally need ``manager=True``
and answer 403 ``manager_required`` for a coach without one. The per-team flags in ``GET /context``
(``staffed_teams``) say up front which is which, so the app can hide the buttons.
"""

import uuid
from dataclasses import dataclass

from django.utils.translation import gettext_lazy as _

from club.services.access import teams_managed_by, teams_staffed_by
from teams.models import StaffAssignment, Team

from .errors import ApiError
from .scope import ClubScope, club_scope


@dataclass
class CoachScope:
    club_scope: ClubScope
    team: Team
    is_manager: bool
    user: object

    @property
    def club(self):
        return self.club_scope.club

    def require_manager(self) -> None:
        if not self.is_manager:
            raise ApiError(403, "manager_required", _("Only the team's managers can do this."))


def coach_scope(request, team_id: uuid.UUID, *, manager: bool = False) -> CoachScope:
    """The club scope plus one staffed team, or 404. ``manager=True`` also requires a
    management position on that team (403 ``manager_required``)."""
    scope = club_scope(request)
    team = teams_staffed_by(request.user, scope.club).filter(pk=team_id).first()
    if team is None:
        raise ApiError(404, "not_found")
    coach = CoachScope(club_scope=scope, team=team, is_manager=teams_managed_by(request.user, scope.club).filter(pk=team.pk).exists(), user=request.user)
    if manager:
        coach.require_manager()
    return coach


def staffed_team_out(user, club, teams) -> list[dict]:
    """``staffed_teams`` of ``/context``: each team with the caller's role and what they may
    do there. The flags are separate on purpose, so a later rule that splits them (a
    coach who may publish line-ups but not edit the roster) needs no app release."""
    from club.services.access import current_season

    managed = set(teams_managed_by(user, club).values_list("pk", flat=True))
    positions = {assignment.team_id: assignment.position.name for assignment in StaffAssignment.objects.filter(member__user=user, team__in=teams, season=current_season(club)).select_related("position")}
    items = []
    for team in teams:
        is_manager = team.pk in managed
        items.append(
            {
                "id": team.pk,
                "name": team.name,
                "short_name": team.short_name,
                "role": "manager" if is_manager else "coach",
                "position": positions.get(team.pk, ""),
                "can_manage_attendance": is_manager,
                "can_edit_events": is_manager,
                "can_publish_lineup": is_manager,
                "can_edit_roster": is_manager,
            }
        )
    return items
