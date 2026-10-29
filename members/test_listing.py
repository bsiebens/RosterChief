from datetime import date

from django.test import TestCase

from authentication.models import User
from club.models import Club, ClubMembership, ClubRole, Season
from members.models import Member
from members.services.listing import filter_members, normalize_kind


class FilterMembersTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        cls.season = Season.objects.create(club=cls.club, start_date=date(2026, 8, 1), end_date=date(2027, 5, 31))
        cls.user = User.objects.create_user(email="admin@example.com", password="x")
        admin = Member.objects.create(user=cls.user, first_name="Ada", last_name="Admin")
        ClubRole.objects.create(club=cls.club, member=admin, role=ClubRole.Roles.ADMIN)
        cls.active = cls.make("Anna", "Active", ClubMembership.StatusChoices.ACTIVE)
        cls.lapsed = cls.make("Lars", "Lapsed", ClubMembership.StatusChoices.LAPSED)
        cls.guardian = cls.make("Greta", "Guardian", ClubMembership.StatusChoices.ACTIVE, kind=ClubMembership.Kind.GUARDIAN)

    @classmethod
    def make(cls, first, last, status, **extra):
        member = Member.objects.create(first_name=first, last_name=last, email=f"{first.lower()}@example.com")
        ClubMembership.objects.create(club=cls.club, member=member, season=cls.season, status=status, **extra)
        return member

    def names(self, **filters):
        return {m.first_name for m in filter_members(self.user, self.club, self.season, **filters)}

    def test_default_hides_lapsed_people_and_guardians(self):
        self.assertIn("Anna", self.names())
        self.assertNotIn("Lars", self.names())
        self.assertNotIn("Greta", self.names())

    def test_status_all_brings_back_lapsed(self):
        self.assertIn("Lars", self.names(status="all"))

    def test_status_filters_to_one_status(self):
        self.assertEqual(self.names(status=ClubMembership.StatusChoices.LAPSED), {"Lars"})

    def test_kind_guardian_shows_only_guardians_and_both_shows_everyone(self):
        self.assertEqual(self.names(kind="guardian"), {"Greta"})
        self.assertIn("Greta", self.names(kind="both"))
        self.assertIn("Anna", self.names(kind="both"))

    def test_unknown_kind_falls_back_to_member(self):
        self.assertEqual(normalize_kind("bogus"), "member")
        self.assertNotIn("Greta", self.names(kind="bogus"))

    def test_fee_status_filter(self):
        ClubMembership.objects.filter(member=self.active).update(fee_status=ClubMembership.FeeStatus.PAID)
        self.assertEqual(self.names(fee_status=ClubMembership.FeeStatus.PAID), {"Anna"})

    def test_search_matches_name_and_email(self):
        self.assertEqual(self.names(q=" anna "), {"Anna"})
        self.assertEqual(self.names(q="active"), {"Anna"})
        self.assertEqual(self.names(q="nobody"), set())

    def test_unrostered_keeps_members_without_a_team(self):
        self.assertIn("Anna", self.names(unrostered=True))
