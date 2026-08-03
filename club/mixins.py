from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.http import Http404

from .services.access import can_add_news, can_edit_news, can_publish_news, has_management_access, is_club_admin, teams_managed_by


class ClubStaffRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Gate for the club-facing management UI.

    Two rules, the mirror image of ``controlpanel.mixins.PlatformStaffRequiredMixin``:

    * **Club subdomain only.** This UI manages *one* club, so it doesn't exist on the
      base domain — same reasoning as the control panel refusing to exist on a club
      subdomain, just inverted.
    * **Staff only.** ADMIN/EDITOR, or a current-season ``StaffAssignment`` (coach,
      team manager, ...) — see ``has_management_access``. The plain MEMBER role every
      active player/club member holds automatically does *not* count: a club member
      with neither is a player/parent, and belongs in the separate app that serves
      them.
    """

    def dispatch(self, request, *args, **kwargs):
        if getattr(request, "club", None) is None:
            raise Http404("The management app is not available on the base domain.")
        return super().dispatch(request, *args, **kwargs)

    def test_func(self):
        return has_management_access(self.request.user, self.request.club)


class ClubAdminRequiredMixin(ClubStaffRequiredMixin):
    """ADMIN role only — club-wide settings that aren't scoped to a single team:
    seasons, positions, roles, shop configuration."""

    def test_func(self):
        return is_club_admin(self.request.user, self.request.club)


class TeamManagerRequiredMixin(ClubStaffRequiredMixin):
    """A manager of *this* team, or a club ADMIN. ``self.get_team()`` must return the
    ``Team`` the view acts on (e.g. from the URL's ``pk``) before ``test_func`` runs.
    """

    def get_team(self):
        raise NotImplementedError("Subclasses must return the Team this view acts on.")

    def test_func(self):
        user, club = self.request.user, self.request.club
        if is_club_admin(user, club):
            return True
        return teams_managed_by(user, club).filter(pk=self.get_team().pk).exists()


class NewsAuthorRequiredMixin(ClubStaffRequiredMixin):
    """ADMIN, EDITOR, or a current-season coach_manager -- who's trusted to
    author club content in the first place (creating a draft)."""

    def test_func(self):
        return can_add_news(self.request.user, self.request.club)


class NewsPublisherRequiredMixin(ClubStaffRequiredMixin):
    """ADMIN/EDITOR only -- the release-flow gate for pushing a news item live
    (or pulling it back)."""

    def test_func(self):
        return can_publish_news(self.request.user, self.request.club)


class NewsEditRequiredMixin(ClubStaffRequiredMixin):
    """Whoever may edit *this* news item right now: broad while it's a draft,
    editor/admin-only once published. ``self.get_news_item()`` must return the
    News the view acts on before ``test_func`` runs."""

    def get_news_item(self):
        raise NotImplementedError("Subclasses must return the News item this view acts on.")

    def test_func(self):
        return can_edit_news(self.request.user, self.get_news_item())
