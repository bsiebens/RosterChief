from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.http import Http404
from waffle import flag_is_active

from members.models import Group

from .services.access import can_add_news, can_edit_news, can_manage_forms, can_manage_members, can_manage_shop, can_publish_news, groups_manageable_by, has_management_access, is_club_admin, is_coach_manager, teams_managed_by


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
        # Read by club/context_processors.py's branding() -- allauth's password-change/MFA/
        # logout screens live under /accounts/, not /manage/, so a path check alone can't
        # tell they were reached from the management app's own user menu. This sticks for
        # the rest of the session (nothing clears it back to False on a public-site visit),
        # which is the right default for the common case of one person, one role.
        request.session["management_context"] = True
        return super().dispatch(request, *args, **kwargs)

    def test_func(self):
        return has_management_access(self.request.user, self.request.club)


class ClubAdminRequiredMixin(ClubStaffRequiredMixin):
    """ADMIN role only (a platform superuser always passes too, see
    is_club_admin) — genuinely admin-only ground: Finance/Shop, Club identity,
    Sponsors, seasons, and granting/revoking ClubRole itself. Everything a
    MEMBER_ADMIN may also touch uses MemberAdminRequiredMixin below instead."""

    def test_func(self):
        return is_club_admin(self.request.user, self.request.club)


class MemberAdminRequiredMixin(ClubStaffRequiredMixin):
    """ADMIN, a platform superuser, or MEMBER_ADMIN specifically -- full read/write
    on people: members, families, groups, parent claims, member import, teams
    (roster/staff/CRUD), referee levels, referee management, and onboarding
    requirements. Deliberately does NOT cover Finance/Shop, Club identity,
    Sponsors, or role-granting (role_list/role_create/role_revoke stay
    ClubAdminRequiredMixin) -- a MEMBER_ADMIN must never be able to grant
    themselves, or anyone else, real ADMIN."""

    def test_func(self):
        return can_manage_members(self.request.user, self.request.club)


class FeatureFlagMixin:
    """404s the whole view when ``self.feature_flag`` isn't active for this
    club -- a club with the feature off doesn't have a permissions problem,
    the section just doesn't exist there, same reasoning ``ClubStaffRequiredMixin``
    gives for 404ing the whole app off the base domain. A plain mixin, not
    itself a permission gate, so it layers onto whichever ``ClubStaffRequiredMixin``-
    derived tier a given view actually needs -- see ``FeatureRequiredMixin``
    just below for the admin-only-by-default combination every original
    feature section (shop, forms) uses, and e.g. the "officials" section's
    own three tiers (management/views.py) for a feature that -- unlike shop/
    forms -- needs more than one.

    Subclasses set ``feature_flag`` to the Flag's name, e.g. ``"shop"``.
    """

    feature_flag: str = ""

    def dispatch(self, request, *args, **kwargs):
        club = getattr(request, "club", None)
        if club is not None and not flag_is_active(request, self.feature_flag):
            raise Http404(f"The “{self.feature_flag}” feature isn't enabled for this club.")
        return super().dispatch(request, *args, **kwargs)


class FeatureRequiredMixin(FeatureFlagMixin, ClubAdminRequiredMixin):
    """Gate for a whole management section (shop, forms, ...) this club doesn't
    have at all unless its waffle Flag (see the ``features`` app, set per-club
    from the control panel's Features page) is active for it. Admin-only by
    default (via ``ClubAdminRequiredMixin``) -- subclasses like
    ``ShopManagerRequiredMixin`` below override ``test_func`` to widen that.
    """


class ShopManagerRequiredMixin(FeatureRequiredMixin):
    """The shop section's own gate -- same "shop" waffle-flag 404 as
    FeatureRequiredMixin (the section doesn't exist at all for a club that
    hasn't got it turned on), but ADMIN *or* a ShopManager grant, not
    ADMIN-only like the plain FeatureRequiredMixin every other feature section
    uses. See can_manage_shop / club.models.ShopManager."""

    feature_flag = "shop"

    def test_func(self):
        return can_manage_shop(self.request.user, self.request.club)


class FormManagerRequiredMixin(FeatureRequiredMixin):
    """The forms section's own gate -- same "formbuilder" waffle-flag 404 as
    FeatureRequiredMixin, but ADMIN/EDITOR/coach-manager, not ADMIN-only --
    same role set as can_add_news (ARCHITECTURE.md's RBAC table already
    assigns EDITOR ownership of formbuilder content). Gates every management
    view for Form/Field/FormSend, including response viewing/export -- see
    can_manage_forms's own docstring for why this is one function, not
    News's add/publish/edit split. A non-admin who can reach this (an
    EDITOR or coach-manager) has no nav-sidebar entry for it, same
    already-accepted gap ShopManager (a non-admin ShopManager grant) has for
    Products -- the nav sidebar's Settings/Finance sections are wrapped in
    an admin-only check regardless of the underlying view's own, broader
    gate; not something this feature needs to be the one to fix."""

    feature_flag = "formbuilder"

    def test_func(self):
        return can_manage_forms(self.request.user, self.request.club)


#: The "officials" section's own gate -- unlike shop/forms (one permission
#: tier each), officials needs the same three-tier split the pre-existing
#: referee system already has (RefereeListView/RefereeLevelListView are any
#: staff; RefereeLevelCreateView/UpdateView/DeleteView are MEMBER_ADMIN;
#: EventRefereeAssignView/RemoveView/FeeUpdateView/FormPdfView are ADMIN-only)
#: -- each layers FeatureFlagMixin onto the matching existing permission
#: tier rather than introducing a fourth, officials-specific one.
OFFICIALS_FLAG = "officials"


class OfficialsStaffRequiredMixin(FeatureFlagMixin, ClubStaffRequiredMixin):
    """Officials list/read views -- any staff, once "officials" is on for
    this club. Mirrors RefereeListView/RefereeLevelListView's own plain
    ClubStaffRequiredMixin."""

    feature_flag = OFFICIALS_FLAG


class OfficialsManagerRequiredMixin(FeatureFlagMixin, MemberAdminRequiredMixin):
    """Officials level CRUD -- MEMBER_ADMIN or ADMIN, once "officials" is on.
    Mirrors RefereeLevelCreateView/UpdateView/DeleteView's own
    MemberAdminRequiredMixin."""

    feature_flag = OFFICIALS_FLAG


class OfficialsAdminRequiredMixin(FeatureFlagMixin, ClubAdminRequiredMixin):
    """Officials assignment/fee/PDF -- ADMIN only, once "officials" is on.
    Mirrors EventRefereeAssignView/RemoveView/FeeUpdateView/FormPdfView's own
    ClubAdminRequiredMixin."""

    feature_flag = OFFICIALS_FLAG


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
