from datetime import date

from django.test import TestCase

from club.models import Club, ClubMembership, Season
from club.services.fees import mark_many_as_paid, season_memberships
from members.models import Member


class SeasonMembershipsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        cls.other = Club.objects.create(name="Rival FC", slug="rival-fc")
        cls.season = Season.objects.create(club=cls.club, start_date=date(2026, 8, 1), end_date=date(2027, 5, 31))
        cls.unpaid = cls.make("Una", "Unpaid", ClubMembership.FeeStatus.UNPAID, fee_amount=50)
        cls.partial = cls.make("Pat", "Partial", ClubMembership.FeeStatus.PARTIALLY_PAID, fee_amount=50)
        cls.paid = cls.make("Pia", "Paid", ClubMembership.FeeStatus.PAID)
        cls.cancelled = cls.make("Cal", "Cancelled", ClubMembership.FeeStatus.UNPAID, status=ClubMembership.StatusChoices.CANCELLED)
        cls.guardian = cls.make("Greta", "Guardian", ClubMembership.FeeStatus.UNPAID, kind=ClubMembership.Kind.GUARDIAN)

    @classmethod
    def make(cls, first, last, fee_status, **extra):
        member = Member.objects.create(first_name=first, last_name=last)
        return ClubMembership.objects.create(club=cls.club, member=member, season=cls.season, fee_status=fee_status, **extra)

    def names(self, **filters):
        return [m.member.first_name for m in season_memberships(self.club, self.season, **filters)]

    def test_default_is_who_still_owes_excluding_cancelled_and_guardians(self):
        self.assertEqual(self.names(), ["Pat", "Una"])

    def test_all_and_single_fee_status(self):
        self.assertEqual(set(self.names(fee_status="all")), {"Una", "Pat", "Pia", "Cal"})
        self.assertEqual(self.names(fee_status=ClubMembership.FeeStatus.PAID), ["Pia"])

    def test_status_filter(self):
        self.assertEqual(self.names(fee_status="all", status=ClubMembership.StatusChoices.CANCELLED), ["Cal"])

    def test_search(self):
        self.assertEqual(self.names(q="pat"), ["Pat"])

    def test_no_season_is_empty(self):
        self.assertFalse(season_memberships(self.club, None).exists())

    def test_mark_many_as_paid_settles_own_club_only(self):
        foreign_member = Member.objects.create(first_name="Fay", last_name="Foreign")
        foreign_season = Season.objects.create(club=self.other, start_date=date(2026, 8, 1), end_date=date(2027, 5, 31))
        foreign = ClubMembership.objects.create(club=self.other, member=foreign_member, season=foreign_season, fee_amount=40)

        count = mark_many_as_paid(self.club, [self.unpaid.pk, self.partial.pk, foreign.pk])

        self.assertEqual(count, 2)
        self.unpaid.refresh_from_db()
        foreign.refresh_from_db()
        self.assertEqual(self.unpaid.fee_status, ClubMembership.FeeStatus.PAID)
        self.assertNotEqual(foreign.fee_status, ClubMembership.FeeStatus.PAID)
