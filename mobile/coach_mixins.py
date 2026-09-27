"""Shared scaffolding for every Coach-mode screen (C1-C6) -- the dark-chrome
mirror of mobile/mixins.py's PersonScopeMixin. Scoped by *team*, not by
managed people: a coach acts on one team at a time (the header's team-picker
pill), switching which team is "active" rather than aggregating across
several people the way Member mode's person switcher does.
"""

from django.http import Http404

from club.services.access import get_current_season, teams_managed_by, teams_staffed_by
from members.services.lookup import get_request_member
from teams.models import StaffAssignment

from .mixins import MemberAppMixin

#: Session key remembering which team was last active, so navigating between
#: coach screens (or leaving and coming back) doesn't reset the picker to
#: whichever team happens to sort first.
ACTIVE_TEAM_SESSION_KEY = "coach_active_team_id"


class CoachScopeMixin(MemberAppMixin):
    """Resolves the signed-in account's Coach-mode standing: which teams
    they're on the staff of at all (``staffed_teams`` -- visibility, any
    position, see club.services.access.teams_staffed_by), which of those
    they actually manage (``managed_teams`` -- management position only,
    current season, teams_managed_by), and which one is currently "active"
    (the team-picker pill on C1's header).

    Built on MemberAppMixin (login + a tie to this club), and on top of
    that Coach mode 404s outright when ``staffed_teams`` is empty: an
    account that isn't on any team's staff this season has no Coach mode
    at all -- the Coach/Member switcher (has_coach_access) never offers it
    either.

    Actions gated on ``can_manage_active_team`` are hidden in templates, not
    disabled -- a coach on staff but without a management position (e.g. a
    physio) can see Coach mode but shouldn't see edit affordances they don't
    have the authority to use.

    ``active_team_role`` is ``self.me``'s own StaffAssignment.position on
    ``active_team`` this season (e.g. "Team manager", "Physio") -- the
    header shows this instead of a hardcoded "Head coach", since not every
    staffed team is one the account holder actually coaches.
    """

    #: Coach mode proper only exists for team staff. The evaluations screens
    #: (coach_evaluation_views.py) turn this off: their own gate is the
    #: club-wide can_manage_evaluations grant, which a technical director holds
    #: without staffing any team.
    require_staffed_team = True

    def prepare_member_app(self, request):
        super().prepare_member_app(request)
        self.me = get_request_member(request)
        self.staffed_teams = list(teams_staffed_by(request.user, request.club))
        if self.require_staffed_team and not self.staffed_teams:
            raise Http404("Not on the staff of a team in this club.")
        self.managed_teams = list(teams_managed_by(request.user, request.club))
        self.active_team = self._resolve_active_team(request)
        self.can_manage_active_team = self.active_team is not None and self.active_team in self.managed_teams
        self.active_team_role = self._resolve_active_team_role(request)

    def _resolve_active_team_role(self, request):
        if self.me is None or self.active_team is None:
            return None
        assignment = StaffAssignment.objects.filter(member=self.me, team=self.active_team, season=get_current_season(request)).select_related("position").first()
        return assignment.position if assignment is not None else None

    def _resolve_active_team(self, request):
        requested_id = request.GET.get("team")
        for team in self.staffed_teams:
            if requested_id and str(team.pk) == requested_id:
                request.session[ACTIVE_TEAM_SESSION_KEY] = str(team.pk)
                return team

        stored_id = request.session.get(ACTIVE_TEAM_SESSION_KEY)
        for team in self.staffed_teams:
            if stored_id and str(team.pk) == stored_id:
                return team

        return self.staffed_teams[0] if self.staffed_teams else None

    def get_context_data(self, **kwargs):
        kwargs.setdefault("active_tab", getattr(self, "active_tab", ""))
        kwargs.setdefault("screen_title", getattr(self, "screen_title", ""))

        return super().get_context_data(
            me=self.me,
            staffed_teams=self.staffed_teams,
            active_team=self.active_team,
            can_manage_active_team=self.can_manage_active_team,
            active_team_role=self.active_team_role,
            season=get_current_season(self.request),
            **kwargs,
        )
