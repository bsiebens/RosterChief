"""Shared scaffolding for every Coach-mode screen (C1-C6) -- the dark-chrome
mirror of mobile/mixins.py's PersonScopeMixin. Scoped by *team*, not by
managed people: a coach acts on one team at a time (the header's team-picker
pill), switching which team is "active" rather than aggregating across
several people the way Member mode's person switcher does.
"""

from club.services.access import current_season, teams_managed_by, teams_staffed_by
from members.models import Member
from members.views import ClubScopedPublicMixin

#: Session key remembering which team was last active, so navigating between
#: coach screens (or leaving and coming back) doesn't reset the picker to
#: whichever team happens to sort first.
ACTIVE_TEAM_SESSION_KEY = "coach_active_team_id"


class CoachScopeMixin(ClubScopedPublicMixin):
    """Resolves the signed-in account's Coach-mode standing: which teams
    they're on the staff of at all (``staffed_teams`` -- visibility, any
    position, see club.services.access.teams_staffed_by), which of those
    they actually manage (``managed_teams`` -- management position only,
    current season, teams_managed_by), and which one is currently "active"
    (the team-picker pill on C1's header).

    Every Coach view still needs its own ``LoginRequiredMixin`` (kept
    separate, same as PersonScopeMixin, so a view composes whichever other
    mixins it needs on top). Nothing here 404s when ``staffed_teams`` is
    empty -- each screen renders its own "not staffing a team yet" empty
    state instead, same judgment call as HomeView's "No one to show yet".

    Actions gated on ``can_manage_active_team`` are hidden in templates, not
    disabled -- a coach on staff but without a management position (e.g. a
    physio) can see Coach mode but shouldn't see edit affordances they don't
    have the authority to use.
    """

    def dispatch(self, request, *args, **kwargs):
        self.me = Member.objects.filter(user=request.user).first() if request.user.is_authenticated else None
        self.staffed_teams = list(teams_staffed_by(request.user, request.club)) if request.user.is_authenticated else []
        self.managed_teams = list(teams_managed_by(request.user, request.club)) if request.user.is_authenticated else []
        self.active_team = self._resolve_active_team(request)
        self.can_manage_active_team = self.active_team is not None and self.active_team in self.managed_teams
        return super().dispatch(request, *args, **kwargs)

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
            season=current_season(self.request.club),
            **kwargs,
        )
