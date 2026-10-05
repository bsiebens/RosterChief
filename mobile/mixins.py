"""Shared scaffolding for every Member-mode screen (the design handoff
README.md: "there is no parent app... every per-member screen carries a person
switcher at the top" + "the [Coach/Member] switcher only renders for an account
holding >=1 staff role"). One mixin so M1-M7's views (and the subagents building
them) don't each re-derive this.
"""

from django.conf import settings
from django.contrib.auth.mixins import AccessMixin
from django.http import Http404
from django.shortcuts import redirect
from django.utils.translation import gettext_lazy as _
from waffle import flag_is_active

from club.device import is_mobile_or_tablet
from club.services.access import get_current_season, has_management_access, teams_staffed_by
from controlpanel.messages import notify
from members.models import Member
from members.services.family import managed_people
from members.services.lookup import get_request_member
from notifications.services import unread_count

from .access import has_club_tie


class DesktopTemplateMixin:
    """Serves the desktop counterpart of a Member-mode template first, falling
    back to the mobile one -- so a screen without a desktop template yet (or a
    Coach-mode one, which never gets one) still just renders as before. See
    club.device.is_mobile_or_tablet, which already backs root()'s own device
    routing (club/views.py)."""

    def get_template_names(self):
        names = super().get_template_names()
        if is_mobile_or_tablet(self.request):
            return names
        desktop_names = [name.replace("mobile/", "mobile/desktop/", 1) for name in names if name.startswith("mobile/")]
        return desktop_names + names


class MemberAppMixin(AccessMixin):
    """The gate every signed-in member-app screen (Member mode, Coach mode,
    Shop, bug reports) builds on: a club subdomain, a signed-in account, and
    a real tie between that account and this club (mobile.access.has_club_tie).

    A session is valid on every club subdomain, so without the tie check any
    account from any club could open another club's app. No tie 404s rather
    than 403s -- the app simply doesn't exist for outsiders. An anonymous
    visitor is sent to login (``handle_no_permission``), same round trip
    LoginRequiredMixin gave these views before.

    Subclasses set up their own per-request state in ``prepare_member_app``,
    which only runs once the gate has passed. mobile/tests.py walks
    mobile/urls.py and fails for any route that isn't gated by this mixin or
    explicitly listed as exempt, so a new view can't forget it.
    """

    def dispatch(self, request, *args, **kwargs):
        if getattr(request, "club", None) is None:
            raise Http404("This page belongs to a club.")
        # Read by club/context_processors.py's branding() -- allauth's login/logout/
        # password-reset/MFA screens live under /accounts/, not /app/, so a path check
        # alone can't tell they were reached from the member app rather than the old
        # public site. Same pattern and same "sticks for the rest of the session"
        # reasoning as club/mixins.py's ClubStaffRequiredMixin sets management_context.
        # Set before the login redirect on purpose, so the login screen itself gets
        # the app's skin.
        request.session["mobile_context"] = True
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not has_club_tie(request.user, request.club):
            raise Http404("No membership in this club.")
        refusal = self.check_member_app(request)
        if refusal is not None:
            return refusal
        self.prepare_member_app(request)
        return super().dispatch(request, *args, **kwargs)

    def check_member_app(self, request):
        """Hook for a section's own gate (e.g. the shop's feature flag); runs
        only once the visitor has passed the gate above, so an anonymous or
        outside visitor learns nothing about it. Return a response to stop
        the request there, raise Http404, or return ``None`` to carry on."""
        return None

    def prepare_member_app(self, request):
        """Hook for per-request state; runs after the gate, before the handler."""


class PersonScopeMixin(DesktopTemplateMixin, MemberAppMixin):
    """Resolves the signed-in account's own Member record plus every child
    they're a parent/guardian of *in this club*.

    ``?as=<member-id>`` re-scopes the current screen to one managed person,
    same as the design doc's horizontally-scrolling chip row -- it re-scopes in
    place rather than navigating. ``?as=all`` (or, once there's more than one
    managed person, no ``?as=`` at all) scopes to *every* managed person --
    that's the default the moment there's actually something to aggregate; a
    member on their own, or a parent of exactly one child, has nothing to
    aggregate and just lands on that one record, same as before this existed.

    Screens that only ever operate on one person at a time keep using
    ``scope_person`` (``None`` in "everyone" mode); screens that can
    meaningfully show several people at once (Home's cards, Calendar's own
    "my schedule" listing) should use ``people_in_scope`` instead, which is
    always the right list to filter by regardless of which mode is active.
    """

    def prepare_member_app(self, request):
        super().prepare_member_app(request)
        self.me = get_request_member(request)
        self.managed_people = self._managed_people(request)
        self.scope_everyone, self.scope_person = self._resolve_scope(request)
        self.people_in_scope = self.managed_people if self.scope_everyone else ([self.scope_person] if self.scope_person else [])

    def _managed_people(self, request):
        return managed_people(self.me, request.club)

    def _resolve_scope(self, request) -> tuple[bool, Member | None]:
        """Returns ``(scope_everyone, scope_person)`` -- exactly one of the
        two is ever meaningful at a time (the other is ``False``/``None``)."""
        requested_id = request.GET.get("as")
        if requested_id and requested_id != "all":
            for person in self.managed_people:
                if str(person.pk) == requested_id:
                    return False, person
        if requested_id == "all":
            return True, None
        if len(self.managed_people) > 1:
            return True, None
        return False, (self.managed_people[0] if self.managed_people else None)

    def get_context_data(self, **kwargs):
        unread_notification_count = unread_count(self.request.club, self.managed_people)

        # Every screen sets these as class attributes (see e.g. HomeView.active_tab),
        # but only a subclass that explicitly forwards them into its own
        # get_context_data actually gets them into the template -- easy to forget
        # (most screens' own get_context_data never touch either one), so default
        # them here instead. setdefault, not an outright override: a screen with a
        # dynamic title (EventDetailView's event.title) already passes its own
        # screen_title through kwargs, and that must win.
        kwargs.setdefault("active_tab", getattr(self, "active_tab", ""))
        kwargs.setdefault("screen_title", getattr(self, "screen_title", ""))

        return super().get_context_data(
            me=self.me,
            managed_people=self.managed_people,
            scope_person=self.scope_person,
            scope_everyone=self.scope_everyone,
            has_staff_access=self.me is not None and has_management_access(self.request.user, self.request.club),
            # Narrower than has_staff_access above: an ADMIN/EDITOR with no
            # personal StaffAssignment satisfies that (desktop management
            # access), but the design doc is explicit the Coach/Member
            # switcher itself only appears for an account holding an actual
            # staff assignment -- see mobile/coach_mixins.py's CoachScopeMixin.
            has_coach_access=self.me is not None and teams_staffed_by(self.request.user, self.request.club).exists(),
            unread_notification_count=unread_notification_count,
            season=get_current_season(self.request),
            vapid_public_key=settings.VAPID_PUBLIC_KEY,
            # Gates the Shop tab itself (base.html) -- same "just absent, not
            # disabled" treatment as the Coach/Member switcher above.
            shop_open=shop_enabled(self.request),
            # Mirrors management.context_processors.feature_sections' own
            # officials_enabled -- mobile has no shared context-processor for
            # waffle flags, so every mobile screen that needs this reads it
            # from here instead (EventDetailView's own Officiating card,
            # CalendarView's official rows) rather than importing
            # flag_is_active repeatedly.
            officials_enabled=flag_is_active(self.request, "officials"),
            **kwargs,
        )


class ShopScopeMixin(PersonScopeMixin):
    """Every shop screen bounces to Home once ``shop_open`` is off -- there's no
    read-only "browse while closed" mode, matching the tab's own absence from
    the tab bar (base.html) and Product.is_public's own help text. A redirect
    with a flashed notice reads better here than a bare 404: someone who had
    the Shop tab open in a background tab, or followed an old link/bookmark,
    lands somewhere useful with an explanation instead of a dead end -- the
    notice is a normal Django message, so it's shown once and gone, same as
    every other flashed message in the app (see mobile/base.html's own
    comment on its messages block). Layered on top of PersonScopeMixin rather
    than duplicated onto each shop view.

    No club on the request at all is a different, more fundamental case (the
    base domain has no tenant to redirect within) and still 404s.

    Beyond the club's own open/closed switch, the shop is a licensed feature:
    without the "shop" waffle flag active for this club the screens 404, the
    same as every other disabled feature. Both checks run only after the
    member-app gate (``check_member_app``), so an anonymous visitor is sent to
    login first and learns nothing about the club's shop."""

    def check_member_app(self, request):
        refusal = super().check_member_app(request)
        if refusal is not None:
            return refusal
        if not shop_enabled(request):
            if not flag_is_active(request, SHOP_FLAG):
                raise Http404("The “shop” feature isn't enabled for this club.")
            notify(request, f"w|{_('Shop closed')}|{_("The shop isn't taking orders right now.")}")
            return redirect("mobile:home")
        return None


#: The waffle Flag licensing the shop for a club -- the same name
#: club.mixins.ShopManagerRequiredMixin gates the management side on.
SHOP_FLAG = "shop"


def shop_enabled(request) -> bool:
    """Whether the member-facing shop is reachable on this request's club: the
    feature is licensed for it (the "shop" flag) *and* the club has it open."""
    club = getattr(request, "club", None)
    return club is not None and club.shop_open and flag_is_active(request, SHOP_FLAG)
