import datetime
import uuid
from contextlib import contextmanager
from decimal import Decimal
from io import StringIO
from unittest import mock

from allauth.mfa.models import Authenticator
from dateutil.relativedelta import relativedelta
from django.contrib import admin as django_admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError
from django.db.models import ProtectedError
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from events.models import Event, Location
from members.models import Family, FamilyMembership, Member
from teams.models import Position, StaffAssignment, Team, TeamMembership
from teams.services import eligible_roster_members

from .models import Club, ClubMembership, ClubRole, DuesInvoice, FeePayment, MemberRequirementStatus, OnboardingRequirement, Season, Sponsor, club_logo_path
from .services.access import (
    COACH_MANAGER,
    can_edit_event,
    can_manage_members,
    can_manage_shop,
    has_club_role,
    has_management_access,
    is_club_admin,
    is_member_admin,
    is_platform_superuser,
    members_visible_to,
    roles_in_club,
    teams_managed_by,
    teams_staffed_by,
)
from .services.fees import mark_as_paid, open_dues_rows, record_payment, remaining_balance
from .services.invoicing import create_or_resend_invoice, invoice_pdf, invoices_due_for_reminder, recipient_for, resolve_document_address
from .services.onboarding import (
    annotate_onboarding_status,
    approve_all_clean,
    approve_one,
    blocked_member_ids_for_event,
    blocking_event_kinds,
    checklist_for,
    mark_bypassed,
    mark_complete,
    mark_incomplete,
)
from .services.seasons import _initial_season_start, _season_end, generate_seasons, resync_seasons
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
    # setUpTestData, not setUp: these three are read-only scaffolding, built once per class
    # instead of once per test. Django hands each test its own deep copy, and the database
    # is rolled back after every one, so the tests that archive or delete them stay isolated.
    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="City Swim Club")
        cls.season = make_season(cls.club)
        cls.member = Member.objects.create(
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
    ROSTERCHIEF_BASE_DOMAIN="rosterchief.app",
    ALLOWED_HOSTS=[".rosterchief.app", ".example.com", ".example.org"],
)
class ClubTenantMiddlewareTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")

    def setUp(self):
        self.factory = RequestFactory()
        # Bound to this instance's _capture, so it cannot be shared across tests.
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
        request, response = self._run("ajax-united.rosterchief.app")

        self.assertEqual(response, "response")
        self.assertEqual(request.club, self.club)
        self.assertEqual(self.captured["context_club"], self.club)

    def test_subdomain_resolution_ignores_port(self):
        request, _ = self._run("ajax-united.rosterchief.app:8000")

        self.assertEqual(request.club, self.club)

    def test_unknown_subdomain_sets_none(self):
        request, _ = self._run("unknown-club.rosterchief.app")

        self.assertIsNone(request.club)

    def test_bare_base_domain_has_no_club(self):
        request, _ = self._run("rosterchief.app")

        self.assertIsNone(request.club)

    def test_www_is_treated_as_no_club(self):
        request, _ = self._run("www.rosterchief.app")

        self.assertIsNone(request.club)

    def test_foreign_domain_has_no_club(self):
        request, _ = self._run("ajax-united.example.org")

        self.assertIsNone(request.club)

    def test_context_var_is_reset_after_request(self):
        self._run("ajax-united.rosterchief.app")

        self.assertIsNone(get_current_club())

    @override_settings(ROSTERCHIEF_BASE_DOMAIN="")
    def test_generic_host_resolution_without_base_domain(self):
        request, _ = self._run("ajax-united.example.com")

        self.assertEqual(request.club, self.club)

    @override_settings(ROSTERCHIEF_BASE_DOMAIN="")
    def test_two_label_host_has_no_club_without_base_domain(self):
        request, _ = self._run("example.com")

        self.assertIsNone(request.club)


class _FakeRequest:
    def __init__(self, host):
        self._host = host

    def get_host(self):
        return self._host


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app")
class GetSubdomainTests(TestCase):
    def subdomain(self, host):
        return ClubTenantMiddleware.get_subdomain(_FakeRequest(host))

    def test_empty_host_returns_none(self):
        self.assertIsNone(self.subdomain(""))

    def test_trailing_dot_is_stripped(self):
        self.assertEqual(self.subdomain("ajax-united.rosterchief.app."), "ajax-united")

    def test_nested_subdomain_uses_leftmost_label(self):
        self.assertEqual(self.subdomain("a.b.rosterchief.app"), "a")


class TenantContextTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")

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
    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        cls.other = Club.objects.create(name="Rival FC", slug="rival-fc")
        cls.dates = {
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
    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        cls.other = Club.objects.create(name="Rival FC", slug="rival-fc")
        cls.season = Season.objects.create(
            club=cls.club,
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
        # self.other, not self.club -- setUp's self.season (2026-08-01 to 2027-05-31)
        # would otherwise also cover "today" once real dates reach that window,
        # colliding with the one created here.
        today = timezone.now().date()
        current = Season.objects.create(
            club=self.other,
            start_date=today - datetime.timedelta(days=10),
            end_date=today + datetime.timedelta(days=10),
        )

        with with_club(self.other):
            self.assertEqual(Season.get_current(), current)

    def test_requires_an_active_club(self):
        with self.assertRaises(RuntimeError):
            Season.get_current(datetime.date(2026, 12, 25))


class SeasonNextAfterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        cls.current = Season.objects.create(club=cls.club, start_date=datetime.date(2026, 8, 1), end_date=datetime.date(2027, 5, 31))
        cls.next_season = Season.objects.create(club=cls.club, start_date=datetime.date(2027, 8, 1), end_date=datetime.date(2028, 5, 31))

    def test_returns_the_soonest_season_starting_after_the_date(self):
        self.assertEqual(Season.next_after(self.club, datetime.date(2026, 12, 25)), self.next_season)

    def test_returns_none_when_there_is_no_later_season(self):
        self.assertIsNone(Season.next_after(self.club, datetime.date(2027, 12, 25)))

    def test_is_scoped_to_the_given_club(self):
        other = Club.objects.create(name="Rival FC", slug="rival-fc")
        Season.objects.create(club=other, start_date=datetime.date(2027, 8, 1), end_date=datetime.date(2028, 5, 31))

        self.assertEqual(Season.next_after(other, datetime.date(2026, 12, 25)).club, other)


class SeasonBeforeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        cls.previous = Season.objects.create(club=cls.club, start_date=datetime.date(2025, 8, 1), end_date=datetime.date(2026, 5, 31))
        cls.current = Season.objects.create(club=cls.club, start_date=datetime.date(2026, 8, 1), end_date=datetime.date(2027, 5, 31))

    def test_returns_the_most_recent_season_starting_before_this_one(self):
        self.assertEqual(Season.before(self.club, self.current), self.previous)

    def test_returns_none_when_there_is_no_earlier_season(self):
        self.assertIsNone(Season.before(self.club, self.previous))

    def test_is_scoped_to_the_given_club(self):
        other = Club.objects.create(name="Rival FC", slug="rival-fc")
        other_current = Season.objects.create(club=other, start_date=datetime.date(2026, 8, 1), end_date=datetime.date(2027, 5, 31))

        self.assertIsNone(Season.before(other, other_current))


class SponsorModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")

    def test_str_returns_name(self):
        sponsor = Sponsor.objects.create(club=self.club, name="Acme Corp", start_date=datetime.date(2026, 1, 1))

        self.assertEqual(str(sponsor), "Acme Corp")

    def test_a_blank_end_date_is_valid(self):
        sponsor = Sponsor(club=self.club, name="Acme Corp", start_date=datetime.date(2026, 1, 1))
        sponsor.full_clean()

    def test_an_end_date_on_the_same_day_as_start_is_valid(self):
        sponsor = Sponsor(club=self.club, name="Acme Corp", start_date=datetime.date(2026, 1, 1), end_date=datetime.date(2026, 1, 1))
        sponsor.full_clean()

    def test_an_end_date_before_start_is_rejected(self):
        sponsor = Sponsor(club=self.club, name="Acme Corp", start_date=datetime.date(2026, 6, 1), end_date=datetime.date(2026, 1, 1))

        with self.assertRaises(ValidationError) as ctx:
            sponsor.full_clean()
        self.assertIn("end_date", ctx.exception.error_dict)

    def test_sponsors_are_ordered_by_name(self):
        Sponsor.objects.create(club=self.club, name="Zulu Corp", start_date=datetime.date(2026, 1, 1))
        Sponsor.objects.create(club=self.club, name="Acme Corp", start_date=datetime.date(2026, 1, 1))

        self.assertEqual(list(Sponsor.objects.values_list("name", flat=True)), ["Acme Corp", "Zulu Corp"])


class AdminRegistrationSmokeTests(TestCase):
    """Every registered model across all apps must have a working admin: load
    each changelist and add page to catch bad list_display / search_fields /
    fieldsets / autocomplete targets in any app's admin config."""

    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser(email="root@club.test", password="pw-secret-123")
        # Staff must hold a second factor (RequireMFAMiddleware), else they are
        # redirected to enrolment instead of reaching the admin.
        Authenticator.objects.create(user=cls.admin, type=Authenticator.Type.TOTP, data={"secret": "JBSWY3DPEHPK3PXP"})

    def setUp(self):
        # The test client is per-test, so the session it carries has to be too.
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


class ClubArchivingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")

    def test_a_new_club_is_active(self):
        self.assertFalse(self.club.is_archived)
        self.assertIn(self.club, Club.objects.active())
        self.assertNotIn(self.club, Club.objects.archived())

    def test_archive_and_restore(self):
        self.club.archive()

        self.assertTrue(self.club.is_archived)
        self.assertIn(self.club, Club.objects.archived())
        self.assertNotIn(self.club, Club.objects.active())

        self.club.restore()

        self.assertFalse(self.club.is_archived)
        self.assertIn(self.club, Club.objects.active())

    def test_archiving_twice_keeps_the_original_timestamp(self):
        self.club.archive()
        first = self.club.archived_at

        self.club.archive()

        self.assertEqual(self.club.archived_at, first)

    def test_restoring_an_active_club_is_a_no_op(self):
        self.club.restore()

        self.assertFalse(self.club.is_archived)

    def test_archiving_destroys_nothing(self):
        season = make_season(self.club)
        member = Member.objects.create(first_name="Jane", last_name="Doe")
        ClubMembership.objects.create(club=self.club, member=member, season=season)

        self.club.archive()

        self.assertTrue(ClubMembership.objects.filter(club=self.club).exists())
        self.assertTrue(Season.objects.filter(club=self.club).exists())


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=[".rosterchief.app"])
class ArchivedClubTenancyTests(TestCase):
    """An archived club's subdomain must stop resolving — that is what makes
    archiving a real deactivation rather than a cosmetic flag."""

    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")

    def setUp(self):
        self.middleware = ClubTenantMiddleware(lambda request: "response")

    def resolve(self):
        request = RequestFactory().get("/", HTTP_HOST="ajax-united.rosterchief.app")
        self.middleware(request)
        return request.club

    def test_active_club_resolves(self):
        self.assertEqual(self.resolve(), self.club)

    def test_archived_club_stops_resolving(self):
        self.club.archive()

        self.assertIsNone(self.resolve())

    def test_restored_club_resolves_again(self):
        self.club.archive()
        self.club.restore()

        self.assertEqual(self.resolve(), self.club)


class ClubRoleTests(TestCase):
    def test_str(self):
        club = Club.objects.create(name="Ajax United", slug="ajax-united")
        member = Member.objects.create(first_name="Jane", last_name="Doe")
        role = ClubRole.objects.create(club=club, member=member)

        self.assertEqual(str(role), f"{club} - {member}")


class ClubMembershipCleanTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        cls.other = Club.objects.create(name="Rival FC", slug="rival-fc")
        today = timezone.localdate()
        cls.season = Season.objects.create(club=cls.club, start_date=today, end_date=today + datetime.timedelta(days=300))
        cls.other_season = Season.objects.create(club=cls.other, start_date=today, end_date=today + datetime.timedelta(days=300))
        cls.member = Member.objects.create(first_name="Jane", last_name="Doe")

    def test_rejects_cross_club_season(self):
        membership = ClubMembership(club=self.club, member=self.member, season=self.other_season)
        with self.assertRaises(ValidationError) as ctx:
            membership.full_clean()
        self.assertIn("season", ctx.exception.error_dict)

    def test_accepts_same_club_season(self):
        ClubMembership(club=self.club, member=self.member, season=self.season).full_clean()


class GuardianMembershipTests(TestCase):
    """A parent attached to the club only through their child -- see
    ClubMembership.Kind. They hold the login and can be reached, but they are not
    a member: no fee, absent from every member list and count, and not eligible
    for a roster or staff spot."""

    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        today = timezone.localdate()
        cls.season = Season.objects.create(club=cls.club, start_date=today, end_date=today + datetime.timedelta(days=300))
        cls.child = Member.objects.create(first_name="Jamie", last_name="Doe")
        cls.parent = Member.objects.create(first_name="Taylor", last_name="Doe")
        ClubMembership.objects.create(club=cls.club, member=cls.child, season=cls.season, status=ClubMembership.StatusChoices.ACTIVE)
        cls.guardianship = ClubMembership.objects.create(club=cls.club, member=cls.parent, season=cls.season, kind=ClubMembership.Kind.GUARDIAN, status=ClubMembership.StatusChoices.ACTIVE)

        cls.admin_user = get_user_model().objects.create_user(email="guardian-admin@example.com", password="pw")
        admin_member = Member.objects.create(user=cls.admin_user, first_name="Ada", last_name="Admin")
        ClubRole.objects.create(club=cls.club, member=admin_member, role=ClubRole.Roles.ADMIN)

    def test_a_guardian_is_not_a_visible_member(self):
        visible = members_visible_to(self.admin_user, self.club)

        self.assertIn(self.child, visible)
        self.assertNotIn(self.parent, visible)

    def test_a_guardian_is_visible_when_explicitly_asked_for(self):
        # The group pickers and the person's own detail page ask for this: they're
        # about a person, not about the member list.
        visible = members_visible_to(self.admin_user, self.club, include_guardians=True)

        self.assertIn(self.parent, visible)

    def test_the_derived_member_role_does_not_leak_a_guardian_back_in(self):
        # An active membership of any kind grants a MEMBER ClubRole (club/signals.py),
        # so matching on "has any role in this club" would undo the exclusion.
        self.assertTrue(ClubRole.objects.filter(club=self.club, member=self.parent, role=ClubRole.Roles.MEMBER).exists())
        self.assertNotIn(self.parent, members_visible_to(self.admin_user, self.club))

    def test_a_parent_who_also_plays_stays_a_member(self):
        # Being a parent and being a member are independent; the family graph
        # records the first, `kind` the second.
        self.guardianship.kind = ClubMembership.Kind.MEMBER
        self.guardianship.save(update_fields=["kind"])

        self.assertIn(self.parent, members_visible_to(self.admin_user, self.club))

    def test_a_guardian_who_is_also_on_a_roster_stays_visible(self):
        # The guardian row alone would hide them; playing for a team must not be
        # undone by their also being someone's parent.
        team = Team.objects.create(club=self.club, name="First Team", short_name="1st")
        position = Position.objects.create(club=self.club, name="Forward", short_name="FW")
        TeamMembership.objects.create(team=team, member=self.parent, season=self.season, position=position)

        self.assertIn(self.parent, members_visible_to(self.admin_user, self.club))

    def test_a_guardian_is_not_eligible_for_a_roster_or_staff_spot(self):
        self.assertIn(self.child, eligible_roster_members(self.club))
        self.assertNotIn(self.parent, eligible_roster_members(self.club))

    def test_a_guardian_cannot_owe_a_fee(self):
        self.guardianship.fee_amount = Decimal("250.00")

        with self.assertRaises(ValidationError) as ctx:
            self.guardianship.full_clean()

        self.assertIn("fee_amount", ctx.exception.error_dict)

    def test_a_new_season_carries_guardians_forward(self):
        # Their tie to the club isn't seasonal -- without this a parent silently
        # drops off at the season boundary while their child stays enrolled.
        generate_seasons(self.club, until=self.season.end_date + datetime.timedelta(days=400))

        next_season = Season.objects.filter(club=self.club, start_date__gt=self.season.start_date).order_by("start_date").first()
        self.assertIsNotNone(next_season)
        self.assertTrue(ClubMembership.objects.filter(club=self.club, member=self.parent, season=next_season, kind=ClubMembership.Kind.GUARDIAN).exists())

    def test_a_guardian_removed_from_the_latest_season_is_not_resurrected(self):
        # Copied from the season immediately before, not from "any season ever",
        # so a deliberate removal stays removed.
        self.guardianship.delete()

        generate_seasons(self.club, until=self.season.end_date + datetime.timedelta(days=400))

        next_season = Season.objects.filter(club=self.club, start_date__gt=self.season.start_date).order_by("start_date").first()
        self.assertFalse(ClubMembership.objects.filter(club=self.club, member=self.parent, season=next_season).exists())


class AccessServiceTests(TestCase):
    # The clubs, season, teams and positions are pure scaffolding here -- every test
    # builds its *own* people and assignments on top of them -- so they are created once
    # per class rather than 28 times over.
    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        cls.other_club = Club.objects.create(name="Rival FC", slug="rival-fc")
        today = timezone.localdate()
        cls.season = Season.objects.create(club=cls.club, start_date=today, end_date=today + datetime.timedelta(days=300))
        cls.team = Team.objects.create(club=cls.club, name="First Team", short_name="1st")
        cls.second_team = Team.objects.create(club=cls.club, name="Second Team", short_name="2nd")
        cls.forward = Position.objects.create(club=cls.club, name="Forward", short_name="FW")
        # Management staff (coach / team manager) vs. non-management staff (e.g. physio).
        cls.coach_position = Position.objects.create(club=cls.club, name="Head Coach", short_name="HC", staff_position=True, management_position=True)
        cls.physio_position = Position.objects.create(club=cls.club, name="Physio", short_name="PH", staff_position=True, management_position=False)

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

    # --- platform superuser bypass ---
    def test_superuser_is_club_admin_everywhere_with_no_clubrole_at_all(self):
        user, _ = self.make_user_member("root@example.com")
        user.is_superuser = True
        user.save()

        self.assertTrue(is_club_admin(user, self.club))
        self.assertTrue(is_club_admin(user, self.other_club))
        self.assertTrue(has_management_access(user, self.club))
        self.assertTrue(is_platform_superuser(user))

    def test_a_plain_staff_flag_alone_is_not_the_superuser_bypass(self):
        user, _ = self.make_user_member("staffonly@example.com")
        user.is_staff = True
        user.save()

        self.assertFalse(is_club_admin(user, self.club))
        self.assertFalse(is_platform_superuser(user))

    def test_an_anonymous_user_is_never_the_superuser_bypass(self):
        self.assertFalse(is_platform_superuser(AnonymousUser()))

    # --- MEMBER_ADMIN / can_manage_members ---
    def test_member_admin_role_grants_can_manage_members_but_not_is_club_admin(self):
        user, member = self.make_user_member("memberadmin@example.com")
        self.grant(member, ClubRole.Roles.MEMBER_ADMIN)

        self.assertTrue(is_member_admin(user, self.club))
        self.assertTrue(can_manage_members(user, self.club))
        self.assertFalse(is_club_admin(user, self.club))

    def test_real_admin_also_satisfies_can_manage_members(self):
        user, member = self.make_user_member("admin@example.com")
        self.grant(member, ClubRole.Roles.ADMIN)

        self.assertTrue(can_manage_members(user, self.club))

    def test_editor_alone_does_not_satisfy_can_manage_members(self):
        user, member = self.make_user_member("editor@example.com")
        self.grant(member, ClubRole.Roles.EDITOR)

        self.assertFalse(can_manage_members(user, self.club))

    def test_member_admin_counts_as_management_access(self):
        user, member = self.make_user_member("memberadmin@example.com")
        self.grant(member, ClubRole.Roles.MEMBER_ADMIN)

        self.assertTrue(has_management_access(user, self.club))

    def test_member_admin_in_one_club_has_no_bearing_on_another(self):
        user, member = self.make_user_member("memberadmin@example.com")
        ClubRole.objects.create(club=self.club, member=member, role=ClubRole.Roles.MEMBER_ADMIN)

        self.assertFalse(can_manage_members(user, self.other_club))


class ClubRoleStatusSyncTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        today = timezone.localdate()
        cls.season = Season.objects.create(club=cls.club, start_date=today, end_date=today + datetime.timedelta(days=300))
        cls.member = Member.objects.create(first_name="Jane", last_name="Doe")

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


@override_settings(
    ROSTERCHIEF_BASE_DOMAIN="rosterchief.app",
    ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "testserver"],
)
class BrandingTests(TestCase):
    """The auth screens are shared; only the skin they inherit differs per tenant."""

    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")

    def login_page(self, host):
        return self.client.get(reverse("account_login"), HTTP_HOST=host)

    def test_the_base_domain_gets_the_platform_skin(self):
        response = self.login_page("rosterchief.app")

        self.assertTemplateUsed(response, "controlpanel/_auth_base.html")
        self.assertTemplateNotUsed(response, "_club_base.html")
        self.assertContains(response, "RosterChief")
        self.assertIsNone(response.context["club"])

    def test_a_club_subdomain_gets_the_club_skin(self):
        response = self.login_page("ajax-united.rosterchief.app")

        self.assertTemplateUsed(response, "_club_base.html")
        self.assertTemplateNotUsed(response, "controlpanel/_auth_base.html")
        self.assertContains(response, "Ajax United")
        self.assertEqual(response.context["club"], self.club)

    def test_an_archived_club_falls_back_to_the_platform_skin(self):
        # The subdomain stops resolving, so there is no club to brand with.
        self.club.archive()

        self.assertTemplateUsed(self.login_page("ajax-united.rosterchief.app"), "controlpanel/_auth_base.html")

    def test_a_club_without_a_logo_shows_its_initials_not_our_mark(self):
        response = self.login_page("ajax-united.rosterchief.app")

        self.assertContains(response, "AU")
        self.assertNotContains(response, "rosterchief-dark.svg")

    def test_a_club_logo_is_rendered_when_set(self):
        self.club.logo = "clubs/ajax-united/crest.png"
        self.club.save()

        self.assertContains(self.login_page("ajax-united.rosterchief.app"), "clubs/ajax-united/crest.png")

    def test_a_club_logo_gets_a_primary_coloured_ring(self):
        self.club.logo = "clubs/ajax-united/crest.png"
        self.club.save()

        self.assertContains(self.login_page("ajax-united.rosterchief.app"), "ring-primary")

    def test_a_club_colour_overrides_the_theme(self):
        self.club.primary_color = "#1e40af"
        self.club.save()

        self.assertContains(self.login_page("ajax-united.rosterchief.app"), "--color-primary: #1e40af")

    def test_no_colour_means_no_override(self):
        self.assertNotContains(self.login_page("ajax-united.rosterchief.app"), "--color-primary")

    def test_a_club_secondary_colour_overrides_the_theme(self):
        self.club.secondary_color = "#be185d"
        self.club.save()

        self.assertContains(self.login_page("ajax-united.rosterchief.app"), "--color-secondary: #be185d")

    def test_no_secondary_colour_means_no_override(self):
        self.assertNotContains(self.login_page("ajax-united.rosterchief.app"), "--color-secondary")


@override_settings(
    ROSTERCHIEF_BASE_DOMAIN="rosterchief.app",
    ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "testserver"],
)
class ManagementBrandingTests(TestCase):
    """allauth's password-change/MFA/logout screens live under /accounts/, outside
    /manage/, so branding() (this module) can't tell they were reached from the
    management app's own user menu by path alone -- it also checks the session flag
    ClubStaffRequiredMixin.dispatch sets (club/mixins.py). These are the tests for
    that flag, as distinct from BrandingTests above (which only covers the plain
    per-tenant split, never touching /manage/ at all)."""

    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        cls.season = Season.objects.create(club=cls.club, start_date=timezone.localdate() - datetime.timedelta(days=30), end_date=timezone.localdate() + datetime.timedelta(days=300))

        cls.staff_user = get_user_model().objects.create_user(email="staff@example.com", password="pw-secret-123")
        member = Member.objects.create(user=cls.staff_user, first_name="Ada", last_name="Admin")
        ClubMembership.objects.create(club=cls.club, member=member, season=cls.season, status=ClubMembership.StatusChoices.ACTIVE)
        ClubRole.objects.filter(club=cls.club, member=member).update(role=ClubRole.Roles.ADMIN)
        Authenticator.objects.create(user=cls.staff_user, type=Authenticator.Type.TOTP, data={"secret": "JBSWY3DPEHPK3PXP"})

    def test_the_change_password_screen_stays_club_branded_without_a_visit_to_manage(self):
        self.client.force_login(self.staff_user)

        response = self.client.get(reverse("account_change_password"), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertTemplateUsed(response, "_club_base.html")
        self.assertTemplateNotUsed(response, "management/_auth_base.html")

    def test_the_change_password_screen_gets_the_management_skin_after_visiting_manage(self):
        self.client.force_login(self.staff_user)
        self.client.get(reverse("management:home"), HTTP_HOST="ajax-united.rosterchief.app")

        response = self.client.get(reverse("account_change_password"), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertTemplateUsed(response, "management/_auth_base.html")
        self.assertContains(response, "Ajax United")

    def test_the_mfa_index_screen_gets_the_management_skin_after_visiting_manage(self):
        self.client.force_login(self.staff_user)
        self.client.get(reverse("management:home"), HTTP_HOST="ajax-united.rosterchief.app")

        response = self.client.get(reverse("mfa_index"), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertTemplateUsed(response, "management/_auth_base.html")


@override_settings(
    ROSTERCHIEF_BASE_DOMAIN="rosterchief.app",
    ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "testserver"],
)
class Custom403PageTests(TestCase):
    """Django's default 403 handler picks up templates/403.html automatically --
    branded per tenant (base_template, same as maintenance.html) so a permission
    error still looks like the app, not a bare Django error page. A club subdomain
    itself splits further: a /manage/ URL gets the management app's own skin
    (management/_auth_base.html) rather than the club's public one, matching every
    other allauth-adjacent screen reached from inside the management app -- see
    club/context_processors.py's MANAGEMENT_BASE_TEMPLATE."""

    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")

    def test_a_manage_url_403_gets_the_management_skin(self):
        member = get_user_model().objects.create_user(email="member-403@example.com", password="pw-secret-123")
        self.client.force_login(member)

        response = self.client.get(reverse("management:position_list"), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.status_code, 403)
        self.assertContains(response, "Access denied", status_code=403)
        self.assertContains(response, "Ajax United", status_code=403)
        self.assertTemplateUsed(response, "management/_auth_base.html")

    def test_the_base_domain_403_gets_the_platform_skin(self):
        self.client.force_login(get_user_model().objects.create_user(email="platform-403@example.com", password="pw-secret-123"))

        response = self.client.get(reverse("controlpanel:dashboard"))

        self.assertEqual(response.status_code, 403)
        self.assertContains(response, "Access denied", status_code=403)
        self.assertTemplateUsed(response, "controlpanel/_auth_base.html")
        self.assertContains(response, "RosterChief", status_code=403)


class ClubBrandingModelTests(TestCase):
    def test_initials_use_the_first_two_words(self):
        self.assertEqual(Club(name="Ajax United Football Club").initials, "AU")
        self.assertEqual(Club(name="Ajax").initials, "A")

    def test_text_on_a_pale_colour_is_black_and_on_a_dark_one_white(self):
        # A club picking pale yellow must not get white-on-yellow buttons.
        self.assertEqual(Club(primary_color="#fef08a").primary_content_color, "#000000")
        self.assertEqual(Club(primary_color="#1e40af").primary_content_color, "#ffffff")

    def test_no_colour_means_no_contrast_colour(self):
        self.assertEqual(Club(primary_color="").primary_content_color, "")

    def test_contrast_color_filter_matches_the_same_algorithm(self):
        # HTML emails apply this to a literal fallback background (e.g.
        # club.secondary_color|default:"#ec4899") rather than a club's own
        # colour, so it can't be exercised through primary_content_color --
        # but it must still agree with it for a colour a club actually set.
        from club.templatetags.club_email import contrast_color

        self.assertEqual(contrast_color("#ec4899"), Club(secondary_color="#ec4899").secondary_content_color)
        self.assertEqual(contrast_color("#fef08a"), "#000000")
        self.assertEqual(contrast_color("#1e40af"), "#ffffff")

    def test_a_colour_must_be_a_hex_value(self):
        club = Club(name="Ajax United", primary_color="blue")

        with self.assertRaises(ValidationError):
            club.full_clean()

    def test_logos_are_stored_per_club(self):
        self.assertEqual(club_logo_path(Club(slug="ajax-united"), "crest.png"), "clubs/ajax-united/crest.png")


@override_settings(
    ROSTERCHIEF_BASE_DOMAIN="rosterchief.app",
    ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "testserver"],
)
class RootViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        cls.user = get_user_model().objects.create_user(email="member@example.com", password="pw-secret-123")

    def test_the_base_domain_hands_off_to_the_control_panel(self):
        response = self.client.get("/", HTTP_HOST="rosterchief.app")

        self.assertRedirects(response, reverse("controlpanel:dashboard"), fetch_redirect_response=False)

    def test_a_club_subdomain_lands_on_the_club_home(self):
        self.client.force_login(self.user)

        response = self.client.get("/", HTTP_HOST="ajax-united.rosterchief.app")

        self.assertTemplateUsed(response, "club/home.html")
        self.assertContains(response, "Ajax United")

    def test_the_club_home_requires_a_login(self):
        response = self.client.get("/", HTTP_HOST="ajax-united.rosterchief.app")

        self.assertRedirects(response, f"{reverse('account_login')}?next=/", fetch_redirect_response=False)


class FeeServiceTests(TestCase):
    """club.services.fees -- record_payment/mark_as_paid/remaining_balance, the
    service layer behind the Memberships page's per-row payment actions."""

    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        cls.season = make_season(cls.club)
        cls.member = Member.objects.create(first_name="Jane", last_name="Doe")
        cls.membership = ClubMembership.objects.create(
            club=cls.club, member=cls.member, season=cls.season, status=ClubMembership.StatusChoices.PENDING, fee_amount=Decimal("150.00")
        )

    def roles(self):
        return ClubRole.objects.filter(club=self.club, member=self.member)

    def test_remaining_balance_starts_at_the_full_fee(self):
        self.assertEqual(remaining_balance(self.membership), Decimal("150.00"))

    def test_remaining_balance_is_never_negative(self):
        record_payment(self.membership, amount=Decimal("200.00"))

        self.assertEqual(remaining_balance(self.membership), Decimal("0.00"))

    def test_a_partial_payment_creates_a_record_and_updates_the_running_total(self):
        payment = record_payment(self.membership, amount=Decimal("50.00"), method=FeePayment.Method.CASH, reference="R1", note="first installment")

        self.assertEqual(payment.membership, self.membership)
        self.assertEqual(payment.amount, Decimal("50.00"))
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.amount_paid, Decimal("50.00"))
        self.assertEqual(self.membership.fee_status, ClubMembership.FeeStatus.PARTIALLY_PAID)
        # Not yet settled -- status doesn't change on a partial payment.
        self.assertEqual(self.membership.status, ClubMembership.StatusChoices.PENDING)

    def test_multiple_partial_payments_accumulate(self):
        record_payment(self.membership, amount=Decimal("50.00"))
        record_payment(self.membership, amount=Decimal("60.00"))

        self.membership.refresh_from_db()
        self.assertEqual(self.membership.amount_paid, Decimal("110.00"))
        self.assertEqual(self.membership.fee_status, ClubMembership.FeeStatus.PARTIALLY_PAID)
        self.assertEqual(FeePayment.objects.filter(membership=self.membership).count(), 2)

    def test_reaching_the_full_amount_settles_the_fee_but_leaves_status_pending(self):
        # Paying in full only ever settles fee_status now -- activation is
        # exclusively club.services.onboarding.approve_one/approve_all_clean's call
        # (see OnboardingRequirement's docstring), so a membership can be fully paid
        # and still sit PENDING until an admin actually approves it.
        record_payment(self.membership, amount=Decimal("100.00"))
        record_payment(self.membership, amount=Decimal("50.00"))

        self.membership.refresh_from_db()
        self.assertEqual(self.membership.fee_status, ClubMembership.FeeStatus.PAID)
        self.assertEqual(self.membership.status, ClubMembership.StatusChoices.PENDING)
        self.assertIsNone(self.membership.activated_at)
        self.assertFalse(self.roles().filter(role=ClubRole.Roles.MEMBER).exists())

    def test_settling_in_full_never_touches_activated_at(self):
        earlier = datetime.date(2026, 1, 1)
        self.membership.activated_at = earlier
        self.membership.save()

        record_payment(self.membership, amount=Decimal("150.00"))

        self.membership.refresh_from_db()
        self.assertEqual(self.membership.activated_at, earlier)

    def test_a_waived_membership_is_untouched_by_a_payment(self):
        self.membership.fee_status = ClubMembership.FeeStatus.WAIVED
        self.membership.save()

        record_payment(self.membership, amount=Decimal("50.00"))

        self.membership.refresh_from_db()
        self.assertEqual(self.membership.fee_status, ClubMembership.FeeStatus.WAIVED)

    def test_mark_as_paid_records_the_exact_remaining_balance(self):
        record_payment(self.membership, amount=Decimal("100.00"))

        mark_as_paid(self.membership)

        self.membership.refresh_from_db()
        self.assertEqual(self.membership.fee_status, ClubMembership.FeeStatus.PAID)
        payment = FeePayment.objects.get(membership=self.membership, amount=Decimal("50.00"))
        self.assertEqual(payment.note, "Marked as paid")

    def test_mark_as_paid_with_no_fee_amount_set_skips_creating_a_zero_payment(self):
        # FeePayment.amount has a MinValueValidator(0.01) -- a $0 "payment" isn't a
        # real transaction, so this must flip the flags directly instead.
        unpriced = ClubMembership.objects.create(club=self.club, member=Member.objects.create(first_name="No", last_name="Price"), season=self.season, status=ClubMembership.StatusChoices.PENDING)

        mark_as_paid(unpriced)

        unpriced.refresh_from_db()
        self.assertEqual(unpriced.fee_status, ClubMembership.FeeStatus.PAID)
        self.assertEqual(unpriced.status, ClubMembership.StatusChoices.PENDING)
        self.assertFalse(FeePayment.objects.filter(membership=unpriced).exists())

    def test_recorded_by_is_stored_on_the_payment(self):
        user = get_user_model().objects.create_user(email="admin-fees@example.com", password="pw-secret-123")

        payment = record_payment(self.membership, amount=Decimal("50.00"), recorded_by=user)

        self.assertEqual(payment.recorded_by, user)


class OpenDuesRowsTests(TestCase):
    """club.services.fees.open_dues_rows -- the shared source behind mobile's
    Home dues card and its Payments & dues screen (mobile/views.py's HomeView
    and PaymentsView)."""

    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        cls.season = make_season(cls.club)
        cls.member = Member.objects.create(first_name="Jane", last_name="Doe")

    def test_returns_nothing_without_a_season(self):
        ClubMembership.objects.create(club=self.club, member=self.member, season=self.season, fee_amount=Decimal("150.00"))

        self.assertEqual(open_dues_rows(self.club, [self.member], None), [])

    def test_returns_nothing_without_any_people(self):
        self.assertEqual(open_dues_rows(self.club, [], self.season), [])

    def test_a_membership_with_a_remaining_balance_is_included(self):
        membership = ClubMembership.objects.create(club=self.club, member=self.member, season=self.season, fee_amount=Decimal("150.00"))

        rows = open_dues_rows(self.club, [self.member], self.season)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["membership"], membership)
        self.assertEqual(rows[0]["balance"], Decimal("150.00"))
        self.assertIsNone(rows[0]["invoice"])

    def test_a_fully_paid_membership_is_excluded(self):
        membership = ClubMembership.objects.create(club=self.club, member=self.member, season=self.season, fee_amount=Decimal("150.00"))
        record_payment(membership, amount=Decimal("150.00"))

        self.assertEqual(open_dues_rows(self.club, [self.member], self.season), [])

    def test_a_waived_membership_is_excluded_even_with_an_unpaid_balance(self):
        ClubMembership.objects.create(club=self.club, member=self.member, season=self.season, fee_amount=Decimal("150.00"), fee_status=ClubMembership.FeeStatus.WAIVED)

        self.assertEqual(open_dues_rows(self.club, [self.member], self.season), [])

    def test_a_membership_with_no_fee_priced_is_excluded(self):
        ClubMembership.objects.create(club=self.club, member=self.member, season=self.season)

        self.assertEqual(open_dues_rows(self.club, [self.member], self.season), [])

    def test_the_linked_invoice_is_included_when_one_exists(self):
        membership = ClubMembership.objects.create(club=self.club, member=self.member, season=self.season, fee_amount=Decimal("150.00"))
        invoice = DuesInvoice.objects.create(club=self.club, membership=membership, amount=Decimal("150.00"), due_date=timezone.now().date(), sent_at=timezone.now())

        rows = open_dues_rows(self.club, [self.member], self.season)

        self.assertEqual(rows[0]["invoice"], invoice)

    def test_only_includes_the_given_people(self):
        other_member = Member.objects.create(first_name="Tom", last_name="Roe")
        ClubMembership.objects.create(club=self.club, member=self.member, season=self.season, fee_amount=Decimal("150.00"))
        ClubMembership.objects.create(club=self.club, member=other_member, season=self.season, fee_amount=Decimal("150.00"))

        rows = open_dues_rows(self.club, [self.member], self.season)

        self.assertEqual([row["membership"].member for row in rows], [self.member])


class RecipientForTests(TestCase):
    """club.services.invoicing.recipient_for -- the member's own email, else the
    first parent/guardian who has one, else nobody reachable at all."""

    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        cls.season = make_season(cls.club)

    def test_the_members_own_email_wins(self):
        member = Member.objects.create(first_name="Jane", last_name="Doe", email="jane@example.com")

        email, used_guardian = recipient_for(member)

        self.assertEqual(email, "jane@example.com")
        self.assertFalse(used_guardian)

    def test_falls_back_to_a_guardians_email_when_the_member_has_none(self):
        family = Family.objects.create()
        member = Member.objects.create(first_name="Jane", last_name="Doe")
        parent = Member.objects.create(first_name="Pat", last_name="Doe", email="pat@example.com")
        FamilyMembership.objects.create(family=family, member=member, role=FamilyMembership.FamilyRole.CHILD)
        FamilyMembership.objects.create(family=family, member=parent, role=FamilyMembership.FamilyRole.PARENT)

        email, used_guardian = recipient_for(member)

        self.assertEqual(email, "pat@example.com")
        self.assertTrue(used_guardian)

    def test_empty_when_nobody_is_reachable(self):
        family = Family.objects.create()
        member = Member.objects.create(first_name="Jane", last_name="Doe")
        parent = Member.objects.create(first_name="Pat", last_name="Doe")
        FamilyMembership.objects.create(family=family, member=member, role=FamilyMembership.FamilyRole.CHILD)
        FamilyMembership.objects.create(family=family, member=parent, role=FamilyMembership.FamilyRole.PARENT)

        email, used_guardian = recipient_for(member)

        self.assertEqual(email, "")
        self.assertFalse(used_guardian)


class CreateOrResendInvoiceTests(TestCase):
    """club.services.invoicing.create_or_resend_invoice -- one invoice per
    membership, numbered once, re-snapshotted on every send."""

    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        cls.season = make_season(cls.club)
        cls.member = Member.objects.create(first_name="Jane", last_name="Doe", email="jane@example.com")
        cls.membership = ClubMembership.objects.create(club=cls.club, member=cls.member, season=cls.season, status=ClubMembership.StatusChoices.PENDING, fee_amount=Decimal("150.00"))

    def test_amount_is_the_remaining_balance_not_the_full_fee(self):
        record_payment(self.membership, amount=Decimal("50.00"))

        invoice = create_or_resend_invoice(self.membership, due_in_days=14, recipient_email="jane@example.com", sent_to_guardian=False)

        self.assertEqual(invoice.amount, Decimal("100.00"))

    def test_due_date_is_today_plus_due_in_days(self):
        invoice = create_or_resend_invoice(self.membership, due_in_days=10, recipient_email="jane@example.com", sent_to_guardian=False)

        self.assertEqual(invoice.due_date, timezone.now().date() + datetime.timedelta(days=10))

    def test_a_number_is_allocated_once(self):
        invoice = create_or_resend_invoice(self.membership, due_in_days=14, recipient_email="jane@example.com", sent_to_guardian=False)
        first_number = invoice.number

        resent = create_or_resend_invoice(self.membership, due_in_days=30, recipient_email="jane@example.com", sent_to_guardian=False)

        self.assertEqual(resent.pk, invoice.pk)
        self.assertEqual(resent.number, first_number)
        self.assertEqual(resent.due_date, timezone.now().date() + datetime.timedelta(days=30))

    def test_a_membership_can_only_ever_have_one_invoice_row(self):
        create_or_resend_invoice(self.membership, due_in_days=14, recipient_email="jane@example.com", sent_to_guardian=False)
        create_or_resend_invoice(self.membership, due_in_days=14, recipient_email="jane@example.com", sent_to_guardian=False)

        self.assertEqual(DuesInvoice.objects.filter(membership=self.membership).count(), 1)


class InvoicePdfTests(TestCase):
    """club.services.invoicing.invoice_pdf -- same header convention as
    management/event_referee_form_pdf.html: legal name, and the club's home
    location, never any other one."""

    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united", legal_name="Ajax United VZW")
        cls.season = make_season(cls.club)
        cls.member = Member.objects.create(first_name="Jane", last_name="Doe", email="jane@example.com")
        cls.membership = ClubMembership.objects.create(club=cls.club, member=cls.member, season=cls.season, status=ClubMembership.StatusChoices.PENDING, fee_amount=Decimal("150.00"))
        cls.invoice = create_or_resend_invoice(cls.membership, due_in_days=14, recipient_email="jane@example.com", sent_to_guardian=False)

    def render(self):
        with mock.patch("club.services.invoicing.render_pdf", side_effect=lambda html: html) as renderer:
            invoice_pdf(self.invoice)
        return renderer.call_args[0][0]

    def test_the_header_uses_the_legal_name(self):
        html = self.render()

        self.assertIn("Ajax United VZW", html)

    def test_the_header_uses_the_clubs_home_location(self):
        Location.objects.create(club=self.club, name="Sports Hall", address="Sportlaan 1", zip_code="1000", city="Brussels", is_home=True)
        Location.objects.create(club=self.club, name="Away ground", address="Elsewhere 2", zip_code="2000", city="Antwerp", is_home=False)

        html = self.render()

        self.assertIn("Sportlaan 1", html)
        self.assertNotIn("Elsewhere 2", html)

    def test_renders_fine_with_no_home_location_set(self):
        self.assertEqual(Location.objects.filter(club=self.club, is_home=True).count(), 0)

        html = self.render()

        self.assertIn(self.invoice.number, html)

    def test_the_legal_address_takes_precedence_over_the_home_location(self):
        Location.objects.create(club=self.club, name="Sports Hall", address="Sportlaan 1", zip_code="1000", city="Brussels", is_home=True)
        self.club.legal_address = "Registered Office 5"
        self.club.legal_zip_code = "9000"
        self.club.legal_city = "Ghent"
        self.club.save(update_fields=["legal_address", "legal_zip_code", "legal_city"])

        html = self.render()

        self.assertIn("Registered Office 5", html)
        self.assertNotIn("Sportlaan 1", html)


class ResolveDocumentAddressTests(TestCase):
    """club.services.invoicing.resolve_document_address -- the club's own
    legal_address when set, else its home Location, else None."""

    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")

    def test_returns_none_with_neither_set(self):
        self.assertIsNone(resolve_document_address(self.club))

    def test_falls_back_to_the_home_location(self):
        home = Location.objects.create(club=self.club, name="Sports Hall", address="Sportlaan 1", zip_code="1000", city="Brussels", is_home=True)

        self.assertEqual(resolve_document_address(self.club), home)

    def test_legal_address_wins_over_the_home_location(self):
        Location.objects.create(club=self.club, name="Sports Hall", address="Sportlaan 1", zip_code="1000", city="Brussels", is_home=True)
        self.club.legal_address = "Registered Office 5"
        self.club.legal_zip_code = "9000"
        self.club.legal_city = "Ghent"
        self.club.save(update_fields=["legal_address", "legal_zip_code", "legal_city"])

        address = resolve_document_address(self.club)

        self.assertEqual(address.address, "Registered Office 5")
        self.assertEqual(address.zip_code, "9000")
        self.assertEqual(address.city, "Ghent")


class InvoicesDueForReminderTests(TestCase):
    """club.services.invoicing.invoices_due_for_reminder -- sent, unpaid, past
    their own due date; never paid, waived, or not yet due."""

    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        cls.season = make_season(cls.club)

    def make_invoice(self, *, fee_status, due_date, sent_at=None):
        member = Member.objects.create(first_name="Member", last_name=fee_status)
        membership = ClubMembership.objects.create(club=self.club, member=member, season=self.season, status=ClubMembership.StatusChoices.PENDING, fee_amount=Decimal("100.00"), fee_status=fee_status)
        return DuesInvoice.objects.create(club=self.club, membership=membership, amount=Decimal("100.00"), due_date=due_date, sent_at=sent_at or timezone.now())

    def test_includes_an_overdue_unpaid_invoice(self):
        overdue = self.make_invoice(fee_status=ClubMembership.FeeStatus.UNPAID, due_date=timezone.now().date() - datetime.timedelta(days=1))

        self.assertIn(overdue, invoices_due_for_reminder(self.club))

    def test_excludes_a_paid_invoice(self):
        paid = self.make_invoice(fee_status=ClubMembership.FeeStatus.PAID, due_date=timezone.now().date() - datetime.timedelta(days=1))

        self.assertNotIn(paid, invoices_due_for_reminder(self.club))

    def test_excludes_a_waived_invoice(self):
        waived = self.make_invoice(fee_status=ClubMembership.FeeStatus.WAIVED, due_date=timezone.now().date() - datetime.timedelta(days=1))

        self.assertNotIn(waived, invoices_due_for_reminder(self.club))

    def test_excludes_one_not_yet_due(self):
        not_due = self.make_invoice(fee_status=ClubMembership.FeeStatus.UNPAID, due_date=timezone.now().date() + datetime.timedelta(days=5))

        self.assertNotIn(not_due, invoices_due_for_reminder(self.club))


class SeasonStartEndTests(TestCase):
    """club.services.seasons._initial_season_start / _season_end -- the
    per-club rules generate_seasons chains off, now that a club's own
    season_start/season_duration_months drive them instead of a fixed Aug-May
    window."""

    # Every test here reassigns a field on its own copy of the club without saving it;
    # setUpTestData's per-test deep copy is what keeps that from leaking sideways.
    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")

    def test_a_date_past_this_years_anchor_uses_this_year(self):
        self.club.season_start = datetime.date(2000, 8, 1)

        start = _initial_season_start(self.club, datetime.date(2026, 8, 15))

        self.assertEqual(start, datetime.date(2026, 8, 1))

    def test_a_date_before_this_years_anchor_uses_last_year(self):
        self.club.season_start = datetime.date(2000, 8, 1)

        start = _initial_season_start(self.club, datetime.date(2027, 2, 1))

        self.assertEqual(start, datetime.date(2026, 8, 1))

    def test_the_anchor_date_itself_uses_this_year(self):
        self.club.season_start = datetime.date(2000, 8, 1)

        start = _initial_season_start(self.club, datetime.date(2026, 8, 1))

        self.assertEqual(start, datetime.date(2026, 8, 1))

    def test_season_end_is_the_day_before_the_start_plus_the_duration(self):
        self.club.season_duration_months = 12

        end = _season_end(datetime.date(2026, 8, 1), self.club)

        self.assertEqual(end, datetime.date(2027, 7, 31))

    def test_a_shorter_duration_produces_a_shorter_season(self):
        self.club.season_duration_months = 6

        end = _season_end(datetime.date(2026, 8, 1), self.club)

        self.assertEqual(end, datetime.date(2027, 1, 31))


class GenerateSeasonsTests(TestCase):
    """club.services.seasons.generate_seasons -- the service behind the
    generate_seasons management command."""

    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")

    def test_generates_a_season_covering_today(self):
        today = timezone.localdate()

        generate_seasons(self.club, today)

        self.assertTrue(Season.objects.filter(club=self.club, start_date__lte=today, end_date__gte=today).exists())

    def test_generates_every_window_through_the_horizon(self):
        today = timezone.localdate()
        until = today + relativedelta(years=2)

        created = generate_seasons(self.club, until)

        self.assertGreaterEqual(len(created), 2)
        for season in created:
            self.assertLessEqual(season.start_date, until)

    def test_is_idempotent_on_a_second_run(self):
        until = timezone.localdate() + relativedelta(years=2)
        generate_seasons(self.club, until)
        count_after_first = Season.objects.filter(club=self.club).count()

        second_run = generate_seasons(self.club, until)

        self.assertEqual(second_run, [])
        self.assertEqual(Season.objects.filter(club=self.club).count(), count_after_first)

    def test_a_new_season_starts_the_day_after_the_last_one_ends(self):
        today = timezone.localdate()
        generate_seasons(self.club, today)
        latest = Season.objects.filter(club=self.club).order_by("-end_date").first()

        generate_seasons(self.club, latest.end_date + relativedelta(months=self.club.season_duration_months))

        next_season = Season.objects.filter(club=self.club, start_date=latest.end_date + datetime.timedelta(days=1)).first()
        self.assertIsNotNone(next_season)

    def test_changing_the_duration_only_affects_the_next_generated_season(self):
        today = timezone.localdate()
        generate_seasons(self.club, today)
        first = Season.objects.filter(club=self.club).order_by("-end_date").first()

        self.club.season_duration_months = 6
        self.club.save()
        generate_seasons(self.club, first.end_date + relativedelta(months=6))

        first_end_before = first.end_date
        first.refresh_from_db()
        self.assertEqual(first.end_date, first_end_before)  # existing season untouched
        second = Season.objects.get(club=self.club, start_date=first.end_date + datetime.timedelta(days=1))
        self.assertEqual(second.end_date, second.start_date + relativedelta(months=6) - datetime.timedelta(days=1))

    def test_does_not_disturb_a_pre_existing_irregular_season(self):
        odd = Season.objects.create(club=self.club, start_date=datetime.date(2020, 3, 1), end_date=datetime.date(2020, 9, 1))

        generate_seasons(self.club, timezone.localdate())

        odd.refresh_from_db()
        self.assertEqual(odd.start_date, datetime.date(2020, 3, 1))
        self.assertEqual(odd.end_date, datetime.date(2020, 9, 1))


class ResyncSeasonsTests(TestCase):
    """club.services.seasons.resync_seasons -- cleaning up seasons that don't
    match a club's current settings (e.g. left over from a since-changed
    season_start/season_duration_months)."""

    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        cls.until = timezone.localdate() + relativedelta(years=2)

    def test_a_wrong_and_unreferenced_season_is_reported_as_removable(self):
        wrong = Season.objects.create(club=self.club, start_date=datetime.date(2020, 3, 1), end_date=datetime.date(2020, 9, 1))

        removed, kept = resync_seasons(self.club, self.until)

        self.assertIn(wrong, removed)
        self.assertEqual(kept, [])

    def test_commit_actually_deletes_a_wrong_unreferenced_season(self):
        wrong = Season.objects.create(club=self.club, start_date=datetime.date(2020, 3, 1), end_date=datetime.date(2020, 9, 1))

        resync_seasons(self.club, self.until, commit=True)

        self.assertFalse(Season.objects.filter(pk=wrong.pk).exists())

    def test_without_commit_nothing_is_actually_deleted(self):
        wrong = Season.objects.create(club=self.club, start_date=datetime.date(2020, 3, 1), end_date=datetime.date(2020, 9, 1))

        resync_seasons(self.club, self.until, commit=False)

        self.assertTrue(Season.objects.filter(pk=wrong.pk).exists())

    def test_a_wrong_but_referenced_season_is_kept_not_removed(self):
        wrong = Season.objects.create(club=self.club, start_date=datetime.date(2020, 3, 1), end_date=datetime.date(2020, 9, 1))
        member = Member.objects.create(first_name="Jane", last_name="Doe")
        ClubMembership.objects.create(club=self.club, member=member, season=wrong)

        removed, kept = resync_seasons(self.club, self.until, commit=True)

        self.assertEqual(removed, [])
        self.assertIn(wrong, kept)
        self.assertTrue(Season.objects.filter(pk=wrong.pk).exists())

    def test_a_season_matching_current_settings_is_left_alone(self):
        generate_seasons(self.club, timezone.localdate())

        removed, kept = resync_seasons(self.club, self.until)

        self.assertEqual(removed, [])
        self.assertEqual(kept, [])

    def test_a_season_referenced_only_via_staff_assignment_is_kept_not_removed(self):
        # Season is PROTECTed by more than just ClubMembership -- a season kept
        # alive only through a StaffAssignment must not be silently deleted either.
        wrong = Season.objects.create(club=self.club, start_date=datetime.date(2020, 3, 1), end_date=datetime.date(2020, 9, 1))
        team = Team.objects.create(club=self.club, name="First Team", short_name="1st")
        position = Position.objects.create(club=self.club, name="Head Coach", short_name="HC", staff_position=True)
        member = Member.objects.create(first_name="Jane", last_name="Doe")
        StaffAssignment.objects.create(team=team, member=member, season=wrong, position=position)

        removed, kept = resync_seasons(self.club, self.until, commit=True)

        self.assertEqual(removed, [])
        self.assertIn(wrong, kept)
        self.assertTrue(Season.objects.filter(pk=wrong.pk).exists())


class GenerateSeasonsCommandTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")

    def test_default_years_is_two(self):
        call_command("generate_seasons", stdout=StringIO())

        today = timezone.localdate()
        until = today + relativedelta(years=2)
        seasons = Season.objects.filter(club=self.club).order_by("start_date")
        self.assertTrue(seasons.exists())
        for season in seasons:
            self.assertLessEqual(season.start_date, until)
        self.assertTrue(seasons.filter(start_date__lte=today, end_date__gte=today).exists())

    def test_years_argument_controls_the_horizon(self):
        call_command("generate_seasons", "--years", "1", stdout=StringIO())

        until = timezone.localdate() + relativedelta(years=1)
        for season in Season.objects.filter(club=self.club):
            self.assertLessEqual(season.start_date, until)

    def test_archived_clubs_are_skipped(self):
        self.club.archive()

        call_command("generate_seasons", stdout=StringIO())

        self.assertFalse(Season.objects.filter(club=self.club).exists())

    def test_second_run_creates_nothing_new(self):
        call_command("generate_seasons", stdout=StringIO())
        count_after_first = Season.objects.filter(club=self.club).count()

        out = StringIO()
        call_command("generate_seasons", stdout=out)

        self.assertEqual(Season.objects.filter(club=self.club).count(), count_after_first)
        self.assertIn("Generated 0 season", out.getvalue())

    def test_resync_without_commit_reports_but_does_not_delete(self):
        wrong = Season.objects.create(club=self.club, start_date=datetime.date(2020, 3, 1), end_date=datetime.date(2020, 9, 1))

        out = StringIO()
        call_command("generate_seasons", "--resync", stdout=out)

        self.assertTrue(Season.objects.filter(pk=wrong.pk).exists())
        self.assertIn("Would remove", out.getvalue())

    def test_resync_with_commit_deletes_the_wrong_season(self):
        wrong = Season.objects.create(club=self.club, start_date=datetime.date(2020, 3, 1), end_date=datetime.date(2020, 9, 1))

        call_command("generate_seasons", "--resync", "--commit", stdout=StringIO())

        self.assertFalse(Season.objects.filter(pk=wrong.pk).exists())



class OnboardingRequirementTests(TestCase):
    """club.services.onboarding -- deliberately orthogonal to status/fee_status (see
    OnboardingRequirement's docstring): a fully paid, active membership can still
    have open requirements, and neither field moves when one is marked complete."""

    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        cls.season = make_season(cls.club)
        cls.member = Member.objects.create(first_name="Jane", last_name="Doe")
        cls.membership = ClubMembership.objects.create(
            club=cls.club, member=cls.member, season=cls.season, status=ClubMembership.StatusChoices.ACTIVE, fee_status=ClubMembership.FeeStatus.PAID
        )
        cls.staff = get_user_model().objects.create_user(email="staff@example.com", password="pw-secret-123")
        cls.photo = OnboardingRequirement.objects.create(club=cls.club, name="Photo")
        cls.medical = OnboardingRequirement.objects.create(club=cls.club, name="Medical certificate", requires_document=True)

    def test_a_membership_with_no_status_rows_has_every_requirement_open(self):
        self.assertEqual(self.membership.open_requirement_count, 2)
        self.assertFalse(self.membership.onboarding_complete)

    def test_marking_one_complete_leaves_the_other_open(self):
        mark_complete(self.membership, self.photo, user=self.staff)

        self.assertEqual(self.membership.open_requirement_count, 1)
        self.assertFalse(self.membership.onboarding_complete)

    def test_completing_every_requirement_clears_the_membership(self):
        mark_complete(self.membership, self.photo, user=self.staff)
        mark_complete(self.membership, self.medical, user=self.staff)

        self.assertTrue(self.membership.onboarding_complete)

    def test_marking_complete_never_touches_status_or_fee_status(self):
        # The whole point: a document upload must never re-derive membership state --
        # club.services.fees owns status/fee_status exclusively.
        unpaid = ClubMembership.objects.create(club=self.club, member=Member.objects.create(first_name="Tom", last_name="Roe"), season=self.season, status=ClubMembership.StatusChoices.PENDING)

        mark_complete(unpaid, self.photo, user=self.staff)
        mark_complete(unpaid, self.medical, user=self.staff)
        unpaid.refresh_from_db()

        self.assertTrue(unpaid.onboarding_complete)
        self.assertEqual(unpaid.status, ClubMembership.StatusChoices.PENDING)
        self.assertEqual(unpaid.fee_status, ClubMembership.FeeStatus.UNPAID)

    def test_mark_complete_records_who_and_when(self):
        status = mark_complete(self.membership, self.medical, user=self.staff, note="emailed 12 Aug")

        self.assertTrue(status.is_complete)
        self.assertEqual(status.completed_by, self.staff)
        self.assertIsNotNone(status.completed_at)
        self.assertEqual(status.note, "emailed 12 Aug")

    def test_mark_complete_is_idempotent_per_requirement(self):
        mark_complete(self.membership, self.photo, user=self.staff)
        mark_complete(self.membership, self.photo, user=self.staff)

        self.assertEqual(MemberRequirementStatus.objects.filter(membership=self.membership, requirement=self.photo).count(), 1)

    def test_mark_incomplete_undoes_it_without_deleting_the_row(self):
        mark_complete(self.membership, self.photo, user=self.staff, note="handed in at practice")
        status = mark_incomplete(self.membership, self.photo)

        self.assertFalse(status.is_complete)
        self.assertIsNone(status.completed_at)
        self.assertIsNone(status.completed_by)
        # The note (and any document) survive the toggle -- it's evidence something
        # was received once, even if it needs redoing.
        self.assertEqual(status.note, "handed in at practice")

    def test_an_inactive_requirement_does_not_block_onboarding(self):
        self.medical.is_active = False
        self.medical.save()

        mark_complete(self.membership, self.photo, user=self.staff)

        self.assertTrue(self.membership.onboarding_complete)

    def test_checklist_for_pairs_every_active_requirement_with_its_status_or_none(self):
        mark_complete(self.membership, self.photo, user=self.staff)

        checklist = checklist_for(self.membership)
        by_requirement = dict(checklist)

        self.assertEqual(len(checklist), 2)
        self.assertTrue(by_requirement[self.photo].is_complete)
        self.assertIsNone(by_requirement[self.medical])

    def test_a_second_clubs_requirement_never_applies_here(self):
        other_club = Club.objects.create(name="Rival FC", slug="rival-fc")
        OnboardingRequirement.objects.create(club=other_club, name="Waiver")

        self.assertEqual(self.membership.open_requirement_count, 2)  # not 3

    def test_annotate_onboarding_status_matches_the_per_row_property_across_a_list(self):
        second = ClubMembership.objects.create(club=self.club, member=Member.objects.create(first_name="Sam", last_name="Lee"), season=self.season, status=ClubMembership.StatusChoices.ACTIVE)
        mark_complete(self.membership, self.photo, user=self.staff)

        annotated = annotate_onboarding_status(ClubMembership.objects.filter(club=self.club))
        by_pk = {membership.pk: membership.onboarding_open for membership in annotated}

        self.assertEqual(by_pk[self.membership.pk], 1)
        self.assertEqual(by_pk[second.pk], 2)

    def test_annotate_onboarding_status_costs_a_fixed_number_of_queries_regardless_of_list_size(self):
        # One for the queryset itself, one for the club's required requirements, one for
        # every membership's completed statuses -- flat regardless of how many rows.
        for i in range(5):
            ClubMembership.objects.create(club=self.club, member=Member.objects.create(first_name=f"M{i}", last_name="Roe"), season=self.season)

        with self.assertNumQueries(3):
            annotate_onboarding_status(ClubMembership.objects.filter(club=self.club))

    # --- mark_bypassed ---
    def test_mark_bypassed_resolves_the_item_without_marking_it_complete(self):
        status = mark_bypassed(self.membership, self.photo, user=self.staff, note="already has a recent one on file")

        self.assertFalse(status.is_complete)
        self.assertTrue(status.is_bypassed)
        self.assertEqual(status.note, "already has a recent one on file")
        self.assertEqual(self.membership.open_requirement_count, 1)

    def test_mark_complete_clears_a_prior_bypass(self):
        mark_bypassed(self.membership, self.photo, user=self.staff, note="not needed")
        status = mark_complete(self.membership, self.photo, user=self.staff)

        self.assertTrue(status.is_complete)
        self.assertFalse(status.is_bypassed)

    def test_mark_bypassed_clears_a_prior_completion(self):
        mark_complete(self.membership, self.photo, user=self.staff)
        status = mark_bypassed(self.membership, self.photo, user=self.staff, note="turns out not needed")

        self.assertFalse(status.is_complete)
        self.assertTrue(status.is_bypassed)

    def test_mark_incomplete_also_clears_a_bypass(self):
        mark_bypassed(self.membership, self.photo, user=self.staff, note="not needed")
        status = mark_incomplete(self.membership, self.photo)

        self.assertFalse(status.is_complete)
        self.assertFalse(status.is_bypassed)
        self.assertEqual(self.membership.open_requirement_count, 2)

    # --- blocking_event_kinds ---
    def test_blocking_event_kinds_is_empty_when_nothing_blocks_anything(self):
        self.assertEqual(blocking_event_kinds(self.membership), set())

    def test_blocking_event_kinds_collects_kinds_from_every_open_requirement(self):
        self.medical.blocked_event_kinds = ["game", "tournament"]
        self.medical.save()
        self.photo.blocked_event_kinds = ["game"]
        self.photo.save()

        self.assertEqual(blocking_event_kinds(self.membership), {"game", "tournament"})

    def test_blocking_event_kinds_ignores_a_resolved_requirement(self):
        self.medical.blocked_event_kinds = ["game"]
        self.medical.save()
        mark_complete(self.membership, self.medical, user=self.staff)

        self.assertEqual(blocking_event_kinds(self.membership), set())

    def test_blocking_event_kinds_ignores_a_bypassed_requirement(self):
        self.medical.blocked_event_kinds = ["game"]
        self.medical.save()
        mark_bypassed(self.membership, self.medical, user=self.staff, note="waived")

        self.assertEqual(blocking_event_kinds(self.membership), set())

    # --- blocked_member_ids_for_event ---
    def test_blocked_member_ids_for_event_is_empty_when_nothing_is_configured_to_block(self):
        self.assertEqual(blocked_member_ids_for_event(self.club, self.season, "game"), set())

    def test_blocked_member_ids_for_event_flags_a_member_with_an_open_blocking_requirement(self):
        self.medical.blocked_event_kinds = ["game"]
        self.medical.save()

        self.assertEqual(blocked_member_ids_for_event(self.club, self.season, "game"), {self.member.pk})

    def test_blocked_member_ids_for_event_is_kind_specific(self):
        self.medical.blocked_event_kinds = ["game"]
        self.medical.save()

        self.assertEqual(blocked_member_ids_for_event(self.club, self.season, "training"), set())

    def test_blocked_member_ids_for_event_excludes_a_member_who_resolved_it(self):
        self.medical.blocked_event_kinds = ["game"]
        self.medical.save()
        mark_complete(self.membership, self.medical, user=self.staff)

        self.assertEqual(blocked_member_ids_for_event(self.club, self.season, "game"), set())

    def test_blocked_member_ids_for_event_excludes_a_bypassed_requirement_too(self):
        self.medical.blocked_event_kinds = ["game"]
        self.medical.save()
        mark_bypassed(self.membership, self.medical, user=self.staff, note="waived")

        self.assertEqual(blocked_member_ids_for_event(self.club, self.season, "game"), set())

    # --- approve_all_clean ---
    def test_approve_all_clean_activates_a_pending_paid_up_fully_checked_member(self):
        pending = ClubMembership.objects.create(club=self.club, member=Member.objects.create(first_name="Tom", last_name="Roe"), season=self.season, status=ClubMembership.StatusChoices.PENDING, fee_status=ClubMembership.FeeStatus.PAID)
        mark_complete(pending, self.photo, user=self.staff)
        mark_bypassed(pending, self.medical, user=self.staff, note="waived")

        activated = approve_all_clean(self.club, self.season)

        pending.refresh_from_db()
        self.assertEqual(activated, 1)
        self.assertEqual(pending.status, ClubMembership.StatusChoices.ACTIVE)

    def test_approve_all_clean_skips_a_pending_member_with_an_open_requirement(self):
        pending = ClubMembership.objects.create(club=self.club, member=Member.objects.create(first_name="Tom", last_name="Roe"), season=self.season, status=ClubMembership.StatusChoices.PENDING, fee_status=ClubMembership.FeeStatus.PAID)
        mark_complete(pending, self.photo, user=self.staff)
        # self.medical left open.

        activated = approve_all_clean(self.club, self.season)

        pending.refresh_from_db()
        self.assertEqual(activated, 0)
        self.assertEqual(pending.status, ClubMembership.StatusChoices.PENDING)

    def test_approve_all_clean_skips_a_pending_member_who_has_not_paid(self):
        pending = ClubMembership.objects.create(club=self.club, member=Member.objects.create(first_name="Tom", last_name="Roe"), season=self.season, status=ClubMembership.StatusChoices.PENDING, fee_status=ClubMembership.FeeStatus.UNPAID)
        mark_complete(pending, self.photo, user=self.staff)
        mark_complete(pending, self.medical, user=self.staff)

        activated = approve_all_clean(self.club, self.season)

        pending.refresh_from_db()
        self.assertEqual(activated, 0)
        self.assertEqual(pending.status, ClubMembership.StatusChoices.PENDING)

    def test_approve_all_clean_never_touches_an_already_active_membership(self):
        # self.membership is already ACTIVE/PAID with two open requirements --
        # approve_all_clean only ever moves PENDING -> ACTIVE, it doesn't re-check
        # or deactivate anyone already active.
        activated = approve_all_clean(self.club, self.season)

        self.membership.refresh_from_db()
        self.assertEqual(activated, 0)
        self.assertEqual(self.membership.status, ClubMembership.StatusChoices.ACTIVE)

    def test_approve_all_clean_ignores_a_guardian_kind_membership(self):
        guardian_member = Member.objects.create(first_name="Pat", last_name="Guardian")
        ClubMembership.objects.create(club=self.club, member=guardian_member, season=self.season, kind=ClubMembership.Kind.GUARDIAN, status=ClubMembership.StatusChoices.PENDING, fee_status=ClubMembership.FeeStatus.PAID)

        activated = approve_all_clean(self.club, self.season)

        self.assertEqual(activated, 0)

    def test_approve_all_clean_stamps_activated_at(self):
        pending = ClubMembership.objects.create(club=self.club, member=Member.objects.create(first_name="Tom", last_name="Roe"), season=self.season, status=ClubMembership.StatusChoices.PENDING, fee_status=ClubMembership.FeeStatus.PAID)
        mark_complete(pending, self.photo, user=self.staff)
        mark_complete(pending, self.medical, user=self.staff)

        approve_all_clean(self.club, self.season)

        pending.refresh_from_db()
        self.assertEqual(pending.activated_at, timezone.localdate())

    # --- approve_one ---
    def test_approve_one_activates_a_clean_pending_membership_and_stamps_activated_at(self):
        pending = ClubMembership.objects.create(club=self.club, member=Member.objects.create(first_name="Tom", last_name="Roe"), season=self.season, status=ClubMembership.StatusChoices.PENDING, fee_status=ClubMembership.FeeStatus.PAID)
        mark_complete(pending, self.photo, user=self.staff)
        mark_complete(pending, self.medical, user=self.staff)

        activated = approve_one(pending)

        pending.refresh_from_db()
        self.assertTrue(activated)
        self.assertEqual(pending.status, ClubMembership.StatusChoices.ACTIVE)
        self.assertEqual(pending.activated_at, timezone.localdate())

    def test_approve_one_refuses_a_paid_but_unchecked_membership(self):
        # Fully paid is not enough on its own -- the whole point of this change is
        # that fee_status alone never activates; the checklist must be resolved too.
        pending = ClubMembership.objects.create(club=self.club, member=Member.objects.create(first_name="Tom", last_name="Roe"), season=self.season, status=ClubMembership.StatusChoices.PENDING, fee_status=ClubMembership.FeeStatus.PAID)
        mark_complete(pending, self.photo, user=self.staff)
        # self.medical left open.

        activated = approve_one(pending)

        pending.refresh_from_db()
        self.assertFalse(activated)
        self.assertEqual(pending.status, ClubMembership.StatusChoices.PENDING)
        self.assertIsNone(pending.activated_at)
