"""Shared scaffolding for every Member-mode screen (design_handoff_rosterchief_platform/
README.md: "there is no parent app... every per-member screen carries a person
switcher at the top" + "the [Coach/Member] switcher only renders for an account
holding >=1 staff role"). One mixin so M1-M7's views (and the subagents building
them) don't each re-derive this.
"""

from django.conf import settings
from django.http import Http404
from django.shortcuts import redirect
from django.utils.translation import gettext_lazy as _
from waffle import flag_is_active

from club.services.access import current_season, has_management_access, teams_staffed_by
from controlpanel.messages import notify
from members.models import FamilyMembership, Member
from members.views import ClubScopedPublicMixin
from notifications.models import Notification


class PersonScopeMixin(ClubScopedPublicMixin):
    """Resolves the signed-in account's own Member record plus every child
    they're a parent/guardian of *in this club* (mirrors members.views.MyFamilyView's
    own query -- kept separate rather than imported from there, since that view
    is public/unauthenticated-reachable and this one is always behind login).

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

    def dispatch(self, request, *args, **kwargs):
        # Read by club/context_processors.py's branding() -- allauth's login/logout/
        # password-reset/MFA screens live under /accounts/, not /app/, so a path check
        # alone can't tell they were reached from the member app rather than the old
        # public site. Same pattern and same "sticks for the rest of the session"
        # reasoning as club/mixins.py's ClubStaffRequiredMixin sets management_context.
        request.session["mobile_context"] = True
        self.me = Member.objects.filter(user=request.user).first() if request.user.is_authenticated else None
        self.managed_people = self._managed_people(request)
        self.scope_everyone, self.scope_person = self._resolve_scope(request)
        self.people_in_scope = self.managed_people if self.scope_everyone else ([self.scope_person] if self.scope_person else [])
        return super().dispatch(request, *args, **kwargs)

    def _managed_people(self, request):
        if self.me is None:
            return []
        children = list(
            Member.objects.filter(
                family_memberships__role=FamilyMembership.FamilyRole.CHILD,
                family_memberships__family__memberships__member=self.me,
                family_memberships__family__memberships__role__in=[FamilyMembership.FamilyRole.PARENT, FamilyMembership.FamilyRole.GUARDIAN],
                member_of__club=request.club,
            ).distinct()
        )
        return [self.me, *children]

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
        unread_notification_count = 0
        if self.managed_people:
            unread_notification_count = Notification.objects.filter(club=self.request.club, member__in=self.managed_people, read_at__isnull=True).count()

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
            season=current_season(self.request.club),
            vapid_public_key=settings.VAPID_PUBLIC_KEY,
            # Gates the Shop tab itself (base.html) -- same "just absent, not
            # disabled" treatment as the Coach/Member switcher above.
            shop_open=self.request.club.shop_open,
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
    base domain has no tenant to redirect within) and still 404s."""

    def dispatch(self, request, *args, **kwargs):
        if getattr(request, "club", None) is None:
            raise Http404("No club on this request.")
        if not request.club.shop_open:
            notify(request, f"w|{_('Shop closed')}|{_("The shop isn't taking orders right now.")}")
            return redirect("mobile:home")
        return super().dispatch(request, *args, **kwargs)
