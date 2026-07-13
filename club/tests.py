import datetime
import uuid
from contextlib import contextmanager

from django.contrib import admin as django_admin
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db.models import ProtectedError
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from events.models import Event
from members.models import Family, FamilyMembership, Member
from teams.models import Position, StaffAssignment, Team, TeamMembership

from .models import Club, ClubMembership, ClubRole, Season
from .services.access import (
    COACH_MANAGER,
    can_edit_event,
    can_manage_shop,
    has_club_role,
    members_visible_to,
    roles_in_club,
    teams_managed_by,
    teams_staffed_by,
)
from .tenancy import (
    ClubTenantMiddleware,
    get_current_club,
    require_current_club,
    reset_current_club,
    set_current_club,
)


@contextmanager
def with_club(club):
    """Bind ``club`` as the active tenant for the duration of the block."""
    token = set_current_club(club)
    try:
        yield club
    finally:
        reset_current_club(token)


class ClubModelTests(TestCase):
    def test_str_returns_name(self):
        club = Club.objects.create(name="City Swim Club")

        self.assertEqual(str(club), "City Swim Club")

    def test_pk_is_uuid(self):
        club = Club.objects.create(name="City Swim Club")

        self.assertIsInstance(club.pk, uuid.UUID)

    def test_clubs_are_ordered_by_name(self):
        Club.objects.create(name="Zulu Club")
        Club.objects.create(name="Alpha Club")
        Club.objects.create(name="Middle Club")

        self.assertEqual(
            list(Club.objects.values_list("name", flat=True)),
            ["Alpha Club", "Middle Club", "Zulu Club"],
        )

    def test_verbose_names(self):
        self.assertEqual(Club._meta.verbose_name, "club")
        self.assertEqual(Club._meta.verbose_name_plural, "clubs")


def make_season(club, start_year=2026):
    return Season.objects.create(
        club=club,
        start_date=datetime.date(start_year, 8, 1),
        end_date=datetime.date(start_year + 1, 5, 31),
    )


class ClubMembershipModelTests(TestCase):
    def setUp(self):
        self.club = Club.objects.create(name="City Swim Club")
        self.season = make_season(self.club)
        self.member = Member.objects.create(
            first_name="Jane",
            last_name="Doe",
            email="jane@example.com",
        )

    def test_str_returns_club_and_member(self):
        membership = ClubMembership.objects.create(club=self.club, season=self.season, member=self.member, license="LIC-001")

        self.assertEqual(str(membership), "City Swim Club - Jane Doe")

    def test_defaults_are_pending_and_unpaid(self):
        membership = ClubMembership.objects.create(club=self.club, season=self.season, member=self.member)

        self.assertEqual(membership.status, ClubMembership.StatusChoices.PENDING)
        self.assertEqual(membership.fee_status, ClubMembership.FeeStatus.UNPAID)
        self.assertEqual(membership.license, "")
        self.assertIsNone(membership.signed_up_at)

    def test_pk_is_uuid(self):
        membership = ClubMembership.objects.create(club=self.club, season=self.season, member=self.member)

        self.assertIsInstance(membership.pk, uuid.UUID)

    def test_club_is_filled_from_active_tenant(self):
        with with_club(self.club):
            membership = ClubMembership.objects.create(season=self.season, member=self.member)

        self.assertEqual(membership.club, self.club)

    def test_member_is_unique_per_club_and_season(self):
        ClubMembership.objects.create(club=self.club, season=self.season, member=self.member)

        with self.assertRaises(IntegrityError):
            ClubMembership.objects.create(club=self.club, season=self.season, member=self.member)

    def test_same_member_can_join_consecutive_seasons(self):
        next_season = make_season(self.club, start_year=2027)

        first = ClubMembership.objects.create(club=self.club, season=self.season, member=self.member)
        second = ClubMembership.objects.create(club=self.club, season=next_season, member=self.member)

        self.assertEqual(self.member.member_of.count(), 2)
        self.assertNotEqual(first.season, second.season)

    def test_same_member_can_join_different_clubs(self):
        other_club = Club.objects.create(name="Other Swim Club")
        other_season = make_season(other_club)

        ClubMembership.objects.create(club=self.club, season=self.season, member=self.member)
        ClubMembership.objects.create(club=other_club, season=other_season, member=self.member)

        self.assertEqual(self.member.member_of.count(), 2)

    def test_deleting_club_is_blocked_while_a_season_has_memberships(self):
        # Club -> Season is CASCADE, but ClubMembership -> Season is PROTECT, so
        # the club can't be deleted while one of its seasons is still referenced.
        ClubMembership.objects.create(club=self.club, season=self.season, member=self.member)

        with self.assertRaises(ProtectedError):
            self.club.delete()

        self.assertTrue(ClubMembership.objects.exists())

    def test_deleting_empty_club_cascades_to_its_seasons(self):
        self.club.delete()

        self.assertFalse(Club.objects.filter(pk=self.club.pk).exists())
        self.assertFalse(Season.objects.filter(pk=self.season.pk).exists())

    def test_deleting_member_deletes_membership_but_keeps_club(self):
        ClubMembership.objects.create(club=self.club, season=self.season, member=self.member)

        self.member.delete()

        self.assertFalse(ClubMembership.objects.exists())
        self.assertTrue(Club.objects.filter(pk=self.club.pk).exists())

    def test_season_is_protected_while_referenced(self):
        ClubMembership.objects.create(club=self.club, season=self.season, member=self.member)

        with self.assertRaises(ProtectedError):
            self.season.delete()

    def test_memberships_are_ordered_by_club_then_member_name(self):
        alpha_club = Club.objects.create(name="Alpha Club")
        zulu_club = Club.objects.create(name="Zulu Club")
        alpha_season = make_season(alpha_club)
        zulu_season = make_season(zulu_club)

        jane = Member.objects.create(first_name="Jane", last_name="Doe")
        alice = Member.objects.create(first_name="Alice", last_name="Smith")
        bob = Member.objects.create(first_name="Bob", last_name="Smith")

        ClubMembership.objects.create(club=zulu_club, season=zulu_season, member=bob)
        ClubMembership.objects.create(club=alpha_club, season=alpha_season, member=bob)
        ClubMembership.objects.create(club=alpha_club, season=alpha_season, member=alice)
        ClubMembership.objects.create(club=alpha_club, season=alpha_season, member=jane)

        self.assertEqual(
            [(membership.club.name, membership.member.last_name, membership.member.first_name) for membership in ClubMembership.objects.all()],
            [
                ("Alpha Club", "Doe", "Jane"),
                ("Alpha Club", "Smith", "Alice"),
                ("Alpha Club", "Smith", "Bob"),
                ("Zulu Club", "Smith", "Bob"),
            ],
        )

    def test_reverse_relations(self):
        membership = ClubMembership.objects.create(club=self.club, season=self.season, member=self.member)

        self.assertEqual(list(self.club.clubmemberships.all()), [membership])
        self.assertEqual(list(self.member.member_of.all()), [membership])
        self.assertEqual(list(self.season.memberships.all()), [membership])

    def test_verbose_names(self):
        self.assertEqual(ClubMembership._meta.verbose_name, "club membership")
        self.assertEqual(ClubMembership._meta.verbose_name_plural, "club memberships")


class ClubSlugTests(TestCase):
    def test_slug_is_derived_from_name(self):
        club = Club.objects.create(name="City Swim Club")

        self.assertEqual(club.slug, "city-swim-club")

    def test_explicit_slug_is_kept(self):
        club = Club.objects.create(name="City Swim Club", slug="ajax-united")

        self.assertEqual(club.slug, "ajax-united")

    def test_derived_slugs_are_made_unique(self):
        first = Club.objects.create(name="City Swim Club")
        second = Club.objects.create(name="City Swim Club")

        self.assertEqual(first.slug, "city-swim-club")
        self.assertEqual(second.slug, "city-swim-club-2")

    def test_slug_is_unique(self):
        Club.objects.create(name="First", slug="shared")

        with self.assertRaises(IntegrityError):
            Club.objects.create(name="Second", slug="shared")


@override_settings(
    CLUBMANAGER_BASE_DOMAIN="clubmanager.app",
    ALLOWED_HOSTS=[".clubmanager.app", ".example.com", ".example.org"],
)
class ClubTenantMiddlewareTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        self.captured = {}
        self.middleware = ClubTenantMiddleware(self._capture)

    def _capture(self, request):
        # Runs inside the middleware, while the context var is set.
        self.captured["context_club"] = get_current_club()
        return "response"

    def _run(self, host):
        request = self.factory.get("/", HTTP_HOST=host)
        response = self.middleware(request)
        return request, response

    def test_subdomain_resolves_to_club(self):
        request, response = self._run("ajax-united.clubmanager.app")

        self.assertEqual(response, "response")
        self.assertEqual(request.club, self.club)
        self.assertEqual(self.captured["context_club"], self.club)

    def test_subdomain_resolution_ignores_port(self):
        request, _ = self._run("ajax-united.clubmanager.app:8000")

        self.assertEqual(request.club, self.club)

    def test_unknown_subdomain_sets_none(self):
        request, _ = self._run("unknown-club.clubmanager.app")

        self.assertIsNone(request.club)

    def test_bare_base_domain_has_no_club(self):
        request, _ = self._run("clubmanager.app")

        self.assertIsNone(request.club)

    def test_www_is_treated_as_no_club(self):
        request, _ = self._run("www.clubmanager.app")

        self.assertIsNone(request.club)

    def test_foreign_domain_has_no_club(self):
        request, _ = self._run("ajax-united.example.org")

        self.assertIsNone(request.club)

    def test_context_var_is_reset_after_request(self):
        self._run("ajax-united.clubmanager.app")

        self.assertIsNone(get_current_club())

    @override_settings(CLUBMANAGER_BASE_DOMAIN="")
    def test_generic_host_resolution_without_base_domain(self):
        request, _ = self._run("ajax-united.example.com")

        self.assertEqual(request.club, self.club)

    @override_settings(CLUBMANAGER_BASE_DOMAIN="")
    def test_two_label_host_has_no_club_without_base_domain(self):
        request, _ = self._run("example.com")

        self.assertIsNone(request.club)


class _FakeRequest:
    def __init__(self, host):
        self._host = host

    def get_host(self):
        return self._host


@override_settings(CLUBMANAGER_BASE_DOMAIN="clubmanager.app")
class GetSubdomainTests(TestCase):
    def subdomain(self, host):
        return ClubTenantMiddleware.get_subdomain(_FakeRequest(host))

    def test_empty_host_returns_none(self):
        self.assertIsNone(self.subdomain(""))

    def test_trailing_dot_is_stripped(self):
        self.assertEqual(self.subdomain("ajax-united.clubmanager.app."), "ajax-united")

    def test_nested_subdomain_uses_leftmost_label(self):
        self.assertEqual(self.subdomain("a.b.clubmanager.app"), "a")


class TenantContextTests(TestCase):
    def setUp(self):
        self.club = Club.objects.create(name="Ajax United", slug="ajax-united")

    def test_require_current_club_returns_active_club(self):
        with with_club(self.club):
            self.assertEqual(require_current_club(), self.club)

    def test_require_current_club_raises_without_context(self):
        with self.assertRaises(RuntimeError):
            require_current_club()

    def test_club_manager_current_returns_active_club(self):
        with with_club(self.club):
            self.assertEqual(Club.objects.current(), self.club)

    def test_club_manager_current_is_none_without_context(self):
        self.assertIsNone(Club.objects.current())


class TenantScopedModelTests(TestCase):
    def setUp(self):
        self.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        self.other = Club.objects.create(name="Rival FC", slug="rival-fc")
        self.dates = {
            "start_date": datetime.date(2026, 8, 1),
            "end_date": datetime.date(2027, 5, 31),
        }

    def test_save_keeps_explicit_club(self):
        season = Season.objects.create(club=self.club, **self.dates)

        self.assertEqual(season.club, self.club)

    def test_save_fills_club_from_context(self):
        with with_club(self.club):
            season = Season.objects.create(**self.dates)

        self.assertEqual(season.club, self.club)

    def test_save_without_club_or_context_raises(self):
        with self.assertRaises(RuntimeError):
            Season.objects.create(**self.dates)

    def test_for_club_filters_by_club(self):
        mine = Season.objects.create(club=self.club, **self.dates)
        Season.objects.create(club=self.other, **self.dates)

        self.assertEqual(list(Season.objects.for_club(self.club)), [mine])

    def test_current_club_filters_by_active_club(self):
        mine = Season.objects.create(club=self.club, **self.dates)
        Season.objects.create(club=self.other, **self.dates)

        with with_club(self.club):
            self.assertEqual(list(Season.objects.current_club()), [mine])

    def test_name_is_two_digit_year_range(self):
        season = Season.objects.create(club=self.club, **self.dates)

        self.assertEqual(season.name, "26-27")

    def test_name_zero_pads_years(self):
        season = Season.objects.create(
            club=self.club,
            start_date=datetime.date(2008, 8, 1),
            end_date=datetime.date(2009, 5, 31),
        )

        self.assertEqual(season.name, "08-09")

    def test_str_is_the_name(self):
        season = Season.objects.create(club=self.club, **self.dates)

        self.assertEqual(str(season), "26-27")


class SeasonGetCurrentTests(TestCase):
    def setUp(self):
        self.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        self.other = Club.objects.create(name="Rival FC", slug="rival-fc")
        self.season = Season.objects.create(
            club=self.club,
            start_date=datetime.date(2026, 8, 1),
            end_date=datetime.date(2027, 5, 31),
        )

    def test_returns_season_covering_the_given_date(self):
        with with_club(self.club):
            found = Season.get_current(datetime.date(2026, 12, 25))

        self.assertEqual(found, self.season)

    def test_includes_boundary_dates(self):
        with with_club(self.club):
            self.assertEqual(Season.get_current(datetime.date(2026, 8, 1)), self.season)
            self.assertEqual(Season.get_current(datetime.date(2027, 5, 31)), self.season)

    def test_returns_none_when_no_season_covers_the_date(self):
        with with_club(self.club):
            self.assertIsNone(Season.get_current(datetime.date(2027, 7, 1)))

    def test_is_scoped_to_the_active_club(self):
        # The other club's season covers the same date but must not leak.
        Season.objects.create(
            club=self.other,
            start_date=datetime.date(2026, 8, 1),
            end_date=datetime.date(2027, 5, 31),
        )

        with with_club(self.other):
            found = Season.get_current(datetime.date(2026, 12, 25))

        self.assertEqual(found.club, self.other)

    def test_defaults_to_today(self):
        today = timezone.now().date()
        current = Season.objects.create(
            club=self.club,
            start_date=today - datetime.timedelta(days=10),
            end_date=today + datetime.timedelta(days=10),
        )

        with with_club(self.club):
            self.assertEqual(Season.get_current(), current)

    def test_requires_an_active_club(self):
        with self.assertRaises(RuntimeError):
            Season.get_current(datetime.date(2026, 12, 25))


class AdminRegistrationSmokeTests(TestCase):
    """Every registered model across all apps must have a working admin: load
    each changelist and add page to catch bad list_display / search_fields /
    fieldsets / autocomplete targets in any app's admin config."""

    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(email="root@club.test", password="pw-secret-123")
        self.client.force_login(self.admin)

    def test_every_model_is_registered_in_admin(self):
        from django.apps import apps

        registered = set(django_admin.site._registry)
        # Concrete, non-auto-created models in these apps should all be registered.
        project_apps = {"authentication", "club", "members", "teams", "events", "formbuilder", "shop"}
        for model in apps.get_models():
            if model._meta.app_label not in project_apps or model._meta.auto_created:
                continue
            with self.subTest(model=model.__name__):
                self.assertIn(model, registered, f"{model.__name__} is not registered in the admin")

    def test_all_changelists_load(self):
        for model in django_admin.site._registry:
            url = reverse(f"admin:{model._meta.app_label}_{model._meta.model_name}_changelist")
            with self.subTest(model=model.__name__):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_all_add_pages_load(self):
        for model in django_admin.site._registry:
            url = reverse(f"admin:{model._meta.app_label}_{model._meta.model_name}_add")
            with self.subTest(model=model.__name__):
                self.assertEqual(self.client.get(url).status_code, 200)


class ClubRoleTests(TestCase):
    def test_str(self):
        club = Club.objects.create(name="Ajax United", slug="ajax-united")
        member = Member.objects.create(first_name="Jane", last_name="Doe")
        role = ClubRole.objects.create(club=club, member=member)

        self.assertEqual(str(role), f"{club} - {member}")


class ClubMembershipCleanTests(TestCase):
    def setUp(self):
        self.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        self.other = Club.objects.create(name="Rival FC", slug="rival-fc")
        today = timezone.localdate()
        self.season = Season.objects.create(club=self.club, start_date=today, end_date=today + datetime.timedelta(days=300))
        self.other_season = Season.objects.create(club=self.other, start_date=today, end_date=today + datetime.timedelta(days=300))
        self.member = Member.objects.create(first_name="Jane", last_name="Doe")

    def test_rejects_cross_club_season(self):
        membership = ClubMembership(club=self.club, member=self.member, season=self.other_season)
        with self.assertRaises(ValidationError) as ctx:
            membership.full_clean()
        self.assertIn("season", ctx.exception.error_dict)

    def test_accepts_same_club_season(self):
        ClubMembership(club=self.club, member=self.member, season=self.season).full_clean()


class AccessServiceTests(TestCase):
    def setUp(self):
        self.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        self.other_club = Club.objects.create(name="Rival FC", slug="rival-fc")
        today = timezone.localdate()
        self.season = Season.objects.create(club=self.club, start_date=today, end_date=today + datetime.timedelta(days=300))
        self.team = Team.objects.create(club=self.club, name="First Team", short_name="1st")
        self.second_team = Team.objects.create(club=self.club, name="Second Team", short_name="2nd")
        self.forward = Position.objects.create(club=self.club, name="Forward", short_name="FW")
        # Management staff (coach / team manager) vs. non-management staff (e.g. physio).
        self.coach_position = Position.objects.create(club=self.club, name="Head Coach", short_name="HC", staff_position=True, management_position=True)
        self.physio_position = Position.objects.create(club=self.club, name="Physio", short_name="PH", staff_position=True, management_position=False)

    def make_user_member(self, email):
        user = get_user_model().objects.create_user(email=email, password="pw")
        member = Member.objects.create(user=user, first_name=email.split("@")[0].title(), last_name="Doe")
        return user, member

    def grant(self, member, role):
        return ClubRole.objects.create(club=self.club, member=member, role=role)

    def make_coach(self, member, team=None):
        return StaffAssignment.objects.create(team=team or self.team, member=member, season=self.season, position=self.coach_position)

    def make_support_staff(self, member, team=None):
        """Staff on the team, but in a non-management position."""
        return StaffAssignment.objects.create(team=team or self.team, member=member, season=self.season, position=self.physio_position)

    def make_event(self, **kwargs):
        kwargs.setdefault("club", self.club)
        kwargs.setdefault("title", "Match")
        kwargs.setdefault("start", timezone.now() + datetime.timedelta(days=1))
        return Event.objects.create(**kwargs)

    # --- has_club_role / roles_in_club ---
    def test_has_club_role_is_scoped_to_role_and_club(self):
        user, member = self.make_user_member("admin@example.com")
        self.grant(member, ClubRole.Roles.ADMIN)

        self.assertTrue(has_club_role(user, self.club, ClubRole.Roles.ADMIN))
        self.assertFalse(has_club_role(user, self.club, ClubRole.Roles.EDITOR))
        self.assertFalse(has_club_role(user, self.other_club, ClubRole.Roles.ADMIN))

    def test_roles_in_club_includes_derived_coach_manager(self):
        user, member = self.make_user_member("editor@example.com")
        self.grant(member, ClubRole.Roles.EDITOR)
        self.make_coach(member)

        self.assertEqual(roles_in_club(user, self.club), {ClubRole.Roles.EDITOR, COACH_MANAGER})

    def test_non_management_staff_gets_no_derived_role(self):
        user, member = self.make_user_member("physio@example.com")
        self.make_support_staff(member)

        self.assertEqual(roles_in_club(user, self.club), set())

    def test_roles_in_club_is_empty_for_outsider(self):
        user, _ = self.make_user_member("nobody@example.com")

        self.assertEqual(roles_in_club(user, self.club), set())

    # --- teams_managed_by ---
    def test_admin_manages_every_team(self):
        user, member = self.make_user_member("admin@example.com")
        self.grant(member, ClubRole.Roles.ADMIN)

        self.assertEqual(set(teams_managed_by(user, self.club)), {self.team, self.second_team})

    def test_coach_manages_only_their_team(self):
        user, member = self.make_user_member("coach@example.com")
        self.make_coach(member)

        self.assertEqual(list(teams_managed_by(user, self.club)), [self.team])

    def test_plain_member_manages_no_teams(self):
        user, _ = self.make_user_member("plain@example.com")

        self.assertEqual(list(teams_managed_by(user, self.club)), [])

    def test_non_management_staff_manages_no_teams(self):
        user, member = self.make_user_member("physio@example.com")
        self.make_support_staff(member)

        self.assertEqual(list(teams_managed_by(user, self.club)), [])

    def test_non_management_staff_does_not_inherit_a_managers_team(self):
        # The team has BOTH a manager and a non-management staffer. The staffer
        # must not pick up the team just because *someone else* manages it.
        _, manager = self.make_user_member("coach@example.com")
        self.make_coach(manager)
        physio_user, physio = self.make_user_member("physio@example.com")
        self.make_support_staff(physio)

        self.assertEqual(list(teams_managed_by(physio_user, self.club)), [])

    def make_past_season(self):
        return Season.objects.create(
            club=self.club,
            start_date=self.season.start_date - datetime.timedelta(days=400),
            end_date=self.season.start_date - datetime.timedelta(days=1),
        )

    def test_a_former_seasons_coach_no_longer_manages_the_team(self):
        # StaffAssignment is per-season: authority expires with it.
        user, member = self.make_user_member("coach@example.com")
        StaffAssignment.objects.create(team=self.team, member=member, season=self.make_past_season(), position=self.coach_position)

        self.assertEqual(list(teams_managed_by(user, self.club)), [])
        self.assertEqual(list(teams_staffed_by(user, self.club)), [])
        self.assertFalse(roles_in_club(user, self.club))

    def test_a_former_seasons_coach_cannot_edit_a_current_event(self):
        user, member = self.make_user_member("coach@example.com")
        StaffAssignment.objects.create(team=self.team, member=member, season=self.make_past_season(), position=self.coach_position)
        event = self.make_event()
        event.teams.add(self.team)

        self.assertFalse(can_edit_event(user, event))

    # --- members_visible_to ---
    def test_admin_sees_all_club_members(self):
        user, member = self.make_user_member("admin@example.com")
        self.grant(member, ClubRole.Roles.ADMIN)
        other = Member.objects.create(first_name="Other", last_name="Member")
        ClubMembership.objects.create(club=self.club, member=member, season=self.season)
        ClubMembership.objects.create(club=self.club, member=other, season=self.season)

        self.assertEqual(set(members_visible_to(user, self.club)), {member, other})

    def test_coach_sees_self_and_managed_roster(self):
        user, member = self.make_user_member("coach@example.com")
        self.make_coach(member)
        player = Member.objects.create(first_name="Player", last_name="One")
        TeamMembership.objects.create(team=self.team, member=player, season=self.season, position=self.forward)
        unrelated = Member.objects.create(first_name="Un", last_name="Related")

        visible = set(members_visible_to(user, self.club))

        self.assertEqual(visible, {member, player})
        self.assertNotIn(unrelated, visible)

    def test_parent_sees_self_and_children(self):
        user, parent = self.make_user_member("parent@example.com")
        child = Member.objects.create(first_name="Kid", last_name="Doe")
        family = Family.objects.create(name="Doe")
        FamilyMembership.objects.create(family=family, member=parent, role=FamilyMembership.FamilyRole.PARENT)
        FamilyMembership.objects.create(family=family, member=child, role=FamilyMembership.FamilyRole.CHILD)

        self.assertEqual(set(members_visible_to(user, self.club)), {parent, child})

    def test_user_without_a_member_sees_nobody(self):
        user = get_user_model().objects.create_user(email="ghost@example.com", password="pw")

        self.assertEqual(list(members_visible_to(user, self.club)), [])

    def test_non_management_staff_sees_the_roster_but_holds_no_authority(self):
        # A physio can see the team they work with, but manages nothing.
        user, member = self.make_user_member("physio@example.com")
        self.make_support_staff(member)
        player = Member.objects.create(first_name="Player", last_name="One")
        TeamMembership.objects.create(team=self.team, member=player, season=self.season, position=self.forward)

        self.assertEqual(set(members_visible_to(user, self.club)), {member, player})
        self.assertEqual(list(teams_managed_by(user, self.club)), [])
        self.assertEqual(list(teams_staffed_by(user, self.club)), [self.team])

    def test_manager_also_sees_the_teams_other_staff(self):
        user, manager = self.make_user_member("coach@example.com")
        self.make_coach(manager)
        _, physio = self.make_user_member("physio@example.com")
        self.make_support_staff(physio)

        self.assertIn(physio, set(members_visible_to(user, self.club)))

    def test_admin_sees_members_without_a_club_membership(self):
        user, admin = self.make_user_member("admin@example.com")
        self.grant(admin, ClubRole.Roles.ADMIN)
        _, coach = self.make_user_member("coach@example.com")
        self.make_coach(coach)  # staff, but no ClubMembership

        visible = set(members_visible_to(user, self.club))

        self.assertIn(coach, visible)
        self.assertIn(admin, visible)  # the admin sees themselves via their ClubRole

    def test_roster_visibility_is_scoped_to_the_current_season(self):
        user, member = self.make_user_member("coach@example.com")
        self.make_coach(member)
        old_season = Season.objects.create(
            club=self.club,
            start_date=self.season.start_date - datetime.timedelta(days=400),
            end_date=self.season.start_date - datetime.timedelta(days=1),
        )
        former_player = Member.objects.create(first_name="Former", last_name="Player")
        TeamMembership.objects.create(team=self.team, member=former_player, season=old_season, position=self.forward)

        self.assertNotIn(former_player, set(members_visible_to(user, self.club)))

    # --- can_edit_event ---
    def test_admin_can_edit_event(self):
        user, member = self.make_user_member("admin@example.com")
        self.grant(member, ClubRole.Roles.ADMIN)

        self.assertTrue(can_edit_event(user, self.make_event()))

    def test_editor_can_edit_event(self):
        user, member = self.make_user_member("editor@example.com")
        self.grant(member, ClubRole.Roles.EDITOR)

        self.assertTrue(can_edit_event(user, self.make_event()))

    def test_owner_can_edit_their_event(self):
        user, member = self.make_user_member("owner@example.com")

        self.assertTrue(can_edit_event(user, self.make_event(created_by=member)))

    def test_coach_can_edit_their_teams_event(self):
        user, member = self.make_user_member("coach@example.com")
        self.make_coach(member)
        event = self.make_event()
        event.teams.add(self.team)

        self.assertTrue(can_edit_event(user, event))

    def test_coach_cannot_edit_another_teams_event(self):
        user, member = self.make_user_member("coach@example.com")
        self.make_coach(member)
        event = self.make_event()
        event.teams.add(self.second_team)

        self.assertFalse(can_edit_event(user, event))

    def test_plain_member_cannot_edit_event(self):
        user, _ = self.make_user_member("plain@example.com")

        self.assertFalse(can_edit_event(user, self.make_event()))

    def test_non_management_staff_cannot_edit_their_teams_event(self):
        user, member = self.make_user_member("physio@example.com")
        self.make_support_staff(member)
        event = self.make_event()
        event.teams.add(self.team)

        self.assertFalse(can_edit_event(user, event))

    def test_non_management_staff_on_a_managed_team_cannot_edit_its_event(self):
        # Same escalation shape as teams_managed_by: a manager exists on the team,
        # but the physio must not inherit edit rights from them.
        _, manager = self.make_user_member("coach@example.com")
        self.make_coach(manager)
        physio_user, physio = self.make_user_member("physio@example.com")
        self.make_support_staff(physio)
        event = self.make_event()
        event.teams.add(self.team)

        self.assertFalse(can_edit_event(physio_user, event))

    # --- can_manage_shop ---
    def test_only_admin_can_manage_shop(self):
        admin_user, admin_member = self.make_user_member("admin@example.com")
        self.grant(admin_member, ClubRole.Roles.ADMIN)
        editor_user, editor_member = self.make_user_member("editor@example.com")
        self.grant(editor_member, ClubRole.Roles.EDITOR)

        self.assertTrue(can_manage_shop(admin_user, self.club))
        self.assertFalse(can_manage_shop(editor_user, self.club))


class ClubRoleStatusSyncTests(TestCase):
    def setUp(self):
        self.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        today = timezone.localdate()
        self.season = Season.objects.create(club=self.club, start_date=today, end_date=today + datetime.timedelta(days=300))
        self.member = Member.objects.create(first_name="Jane", last_name="Doe")

    def roles(self):
        return ClubRole.objects.filter(club=self.club, member=self.member)

    def make_membership(self, status=ClubMembership.StatusChoices.ACTIVE):
        return ClubMembership.objects.create(club=self.club, member=self.member, season=self.season, status=status)

    def test_active_membership_grants_member_role(self):
        self.make_membership()

        self.assertEqual(self.roles().get().role, ClubRole.Roles.MEMBER)

    def test_pending_membership_grants_no_role(self):
        self.make_membership(status=ClubMembership.StatusChoices.PENDING)

        self.assertFalse(self.roles().exists())

    def test_deactivating_membership_withdraws_member_role(self):
        membership = self.make_membership()
        self.assertTrue(self.roles().exists())

        membership.status = ClubMembership.StatusChoices.LAPSED
        membership.save()

        self.assertFalse(self.roles().exists())

    def test_deleting_membership_withdraws_member_role(self):
        membership = self.make_membership()

        membership.delete()

        self.assertFalse(self.roles().exists())

    def test_elevated_role_is_never_downgraded_or_removed(self):
        ClubRole.objects.create(club=self.club, member=self.member, role=ClubRole.Roles.ADMIN)

        membership = self.make_membership()
        self.assertEqual(self.roles().get().role, ClubRole.Roles.ADMIN)

        membership.status = ClubMembership.StatusChoices.CANCELLED
        membership.save()

        self.assertEqual(self.roles().get().role, ClubRole.Roles.ADMIN)

    def test_editor_role_survives_a_lapsed_membership(self):
        ClubRole.objects.create(club=self.club, member=self.member, role=ClubRole.Roles.EDITOR)
        membership = self.make_membership()

        membership.status = ClubMembership.StatusChoices.LAPSED
        membership.save()

        self.assertEqual(self.roles().get().role, ClubRole.Roles.EDITOR)

    def test_elevated_role_survives_membership_deletion(self):
        ClubRole.objects.create(club=self.club, member=self.member, role=ClubRole.Roles.ADMIN)
        membership = self.make_membership()

        membership.delete()

        self.assertEqual(self.roles().get().role, ClubRole.Roles.ADMIN)

    def test_elevated_role_survives_a_season_rollover(self):
        # Last season's membership lapses and the new season's is still pending:
        # the admin must not lose their role in the gap.
        ClubRole.objects.create(club=self.club, member=self.member, role=ClubRole.Roles.ADMIN)
        last_season = self.make_membership()
        next_season = Season.objects.create(
            club=self.club,
            start_date=self.season.end_date + datetime.timedelta(days=1),
            end_date=self.season.end_date + datetime.timedelta(days=300),
        )

        last_season.status = ClubMembership.StatusChoices.LAPSED
        last_season.save()
        ClubMembership.objects.create(club=self.club, member=self.member, season=next_season, status=ClubMembership.StatusChoices.PENDING)

        self.assertEqual(self.roles().get().role, ClubRole.Roles.ADMIN)

    def test_elevated_access_and_login_survive_a_lapsed_membership(self):
        # The whole point: a lapsed membership must not lock an admin out.
        user = get_user_model().objects.create_user(email="admin@example.com", password="pw")
        admin = Member.objects.create(user=user, first_name="Ada", last_name="Min")
        ClubRole.objects.create(club=self.club, member=admin, role=ClubRole.Roles.ADMIN)
        membership = ClubMembership.objects.create(club=self.club, member=admin, season=self.season, status=ClubMembership.StatusChoices.ACTIVE)

        membership.status = ClubMembership.StatusChoices.CANCELLED
        membership.save()

        self.assertTrue(user.is_active)  # can still log in
        self.assertIn(ClubRole.Roles.ADMIN, roles_in_club(user, self.club))
        self.assertTrue(has_club_role(user, self.club, ClubRole.Roles.ADMIN))
        self.assertTrue(can_manage_shop(user, self.club))
