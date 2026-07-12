import datetime
import uuid
from contextlib import contextmanager

from django.db import IntegrityError
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from members.models import Member

from .models import Club, ClubMembership, Season
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


class ClubMembershipModelTests(TestCase):
    def setUp(self):
        self.club = Club.objects.create(name="City Swim Club")
        self.member = Member.objects.create(
            first_name="Jane",
            last_name="Doe",
            email="jane@example.com",
        )

    def test_str_returns_club_and_member(self):
        membership = ClubMembership.objects.create(
            club=self.club,
            member=self.member,
            license="LIC-001",
        )

        self.assertEqual(str(membership), "City Swim Club - Jane Doe")

    def test_license_is_optional(self):
        membership = ClubMembership.objects.create(
            club=self.club,
            member=self.member,
        )

        self.assertEqual(membership.license, "")

    def test_pk_is_uuid(self):
        membership = ClubMembership.objects.create(
            club=self.club,
            member=self.member,
        )

        self.assertIsInstance(membership.pk, uuid.UUID)

    def test_same_member_can_join_different_clubs(self):
        other_club = Club.objects.create(name="Other Swim Club")

        first_membership = ClubMembership.objects.create(
            club=self.club,
            member=self.member,
            license="LIC-001",
        )
        second_membership = ClubMembership.objects.create(
            club=other_club,
            member=self.member,
            license="LIC-002",
        )

        self.assertEqual(first_membership.member, self.member)
        self.assertEqual(second_membership.member, self.member)
        self.assertEqual(self.member.member_of.count(), 2)

    def test_member_is_unique_per_club(self):
        ClubMembership.objects.create(
            club=self.club,
            member=self.member,
            license="LIC-001",
        )

        with self.assertRaises(IntegrityError):
            ClubMembership.objects.create(
                club=self.club,
                member=self.member,
                license="LIC-002",
            )

    def test_deleting_club_deletes_membership_but_keeps_member(self):
        ClubMembership.objects.create(
            club=self.club,
            member=self.member,
            license="LIC-001",
        )

        self.club.delete()

        self.assertFalse(ClubMembership.objects.exists())
        self.assertTrue(Member.objects.filter(pk=self.member.pk).exists())

    def test_deleting_member_deletes_membership_but_keeps_club(self):
        ClubMembership.objects.create(
            club=self.club,
            member=self.member,
            license="LIC-001",
        )

        self.member.delete()

        self.assertFalse(ClubMembership.objects.exists())
        self.assertTrue(Club.objects.filter(pk=self.club.pk).exists())

    def test_memberships_are_ordered_by_club_then_member_name(self):
        alpha_club = Club.objects.create(name="Alpha Club")
        zulu_club = Club.objects.create(name="Zulu Club")

        jane = Member.objects.create(first_name="Jane", last_name="Doe")
        alice = Member.objects.create(first_name="Alice", last_name="Smith")
        bob = Member.objects.create(first_name="Bob", last_name="Smith")

        ClubMembership.objects.create(club=zulu_club, member=bob)
        ClubMembership.objects.create(club=alpha_club, member=bob)
        ClubMembership.objects.create(club=alpha_club, member=alice)
        ClubMembership.objects.create(club=alpha_club, member=jane)

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
        membership = ClubMembership.objects.create(
            club=self.club,
            member=self.member,
            license="LIC-001",
        )

        self.assertEqual(list(self.club.members.all()), [membership])
        self.assertEqual(list(self.member.member_of.all()), [membership])

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
