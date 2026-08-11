from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.http import Http404
from waffle import flag_is_active

from members.models import Group

from .services.access import can_add_news, can_edit_news, can_publish_news, groups_manageable_by, has_management_access, is_club_admin, is_coach_manager, teams_managed_by


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


class FeatureRequiredMixin(ClubAdminRequiredMixin):
    """Gate for a whole management section (shop, forms, ...) this club doesn't
    have at all unless its waffle Flag (see the ``features`` app, set per-club
    from the control panel's Features page) is active for it. Checked before
    the admin-only test below and as a plain 404 rather than folded into
    ``test_func``'s 403: a club with the feature off doesn't have a permissions
    problem, the section just doesn't exist there, same reasoning as
    ``ClubStaffRequiredMixin`` 404ing the whole app off the base domain.

    Subclasses set ``feature_flag`` to the Flag's name, e.g. ``"shop"``.
    """

    feature_flag: str = ""

    def dispatch(self, request, *args, **kwargs):
        club = getattr(request, "club", None)
        if club is not None and not flag_is_active(request, self.feature_flag):
            raise Http404(f"The “{self.feature_flag}” feature isn't enabled for this club.")
        return super().dispatch(request, *args, **kwargs)


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


class EventManagerRequiredMixin(ClubStaffRequiredMixin):
    """Admin, a manager of at least one of this event's/series' *current*
    teams, or a member of at least one of its groups. ``self.get_teams()``
    must return the Team queryset/iterable the view acts on (e.g.
    ``self.get_object().teams.all()``) before ``test_func`` runs; override
    ``get_groups()`` the same way for a view whose object can carry groups
    (it defaults to none, so most subclasses only need get_teams()). Events/
    series aren't single-team/-group like a roster entry -- both are M2M, so
    authority is "belongs to at least one", not "belongs to the one". A
    club_wide event has no equivalent membership claim to check -- it's
    admin-only to create in the first place (EventForm), so the plain
    is_club_admin check below already covers it."""

    def get_teams(self):
        raise NotImplementedError("Subclasses must return the Teams this view acts on.")

    def get_groups(self):
        return Group.objects.none()

    def test_func(self):
        user, club = self.request.user, self.request.club
        if is_club_admin(user, club):
            return True
        if teams_managed_by(user, club).filter(pk__in=self.get_teams().values_list("pk", flat=True)).exists():
            return True
        return groups_manageable_by(user, club).filter(pk__in=self.get_groups().values_list("pk", flat=True)).exists()


class ManagementPositionRequiredMixin(ClubStaffRequiredMixin):
    """ADMIN, or anyone with a current-season *management*-position
    StaffAssignment on any team -- unlike ``TeamManagerRequiredMixin``, the
    entity here (Location, Opponent, ...) isn't scoped to one team, so "manager
    of this team" doesn't apply; any management position qualifies."""

    def test_func(self):
        return is_club_admin(self.request.user, self.request.club) or is_coach_manager(self.request.user, self.request.club)


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
