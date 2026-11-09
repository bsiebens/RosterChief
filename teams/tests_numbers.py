"""Which jersey numbers a new player could wear (``teams.services.numbers.available_numbers`` / ``number_available``)."""

import datetime

from django.test import TestCase

from members.models import Member
from news.tests import NewsClubMixin
from teams.models import NumberPool, NumberReservation, TeamMembership
from teams.services import numbers


class AvailableNumbersTests(NewsClubMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.pool = NumberPool.objects.create(name="Juniors", min_number=1, max_number=6, min_age_gap_years=5)
        self.team.pool = self.pool
        self.team.save()
        self.other_team.pool = self.pool
        self.other_team.save()

    def holder(self, team, number, born):
        member = Member.objects.create(first_name="Holder", last_name=str(number), date_of_birth=born)
        return TeamMembership.objects.create(team=team, member=member, season=self.season, jersey_number=number)

    def free(self, team=None, born=None, **kw):
        return numbers.available_numbers(team or self.team, self.season, born, **kw)

    def test_the_range_of_the_pool(self):
        result = self.free()
        self.assertEqual(result["numbers"], [1, 2, 3, 4, 5, 6])
        self.assertEqual(result["taken"], [])
        self.assertEqual(result["pool"], {"name": "Juniors", "min": 1, "max": 6})

    def test_reserved_numbers_are_not_offered(self):
        NumberReservation.objects.create(pool=self.pool, number=2, note="Retired")
        result = self.free()
        self.assertEqual(result["numbers"], [1, 3, 4, 5, 6])
        self.assertEqual(result["taken"], [2])

    def test_a_number_held_on_the_same_team_is_never_shared(self):
        self.holder(self.team, 4, datetime.date(1990, 1, 1))
        self.assertNotIn(4, self.free(born=datetime.date(2015, 1, 1))["numbers"])

    def test_a_number_held_on_another_team_follows_the_age_gap(self):
        self.holder(self.other_team, 3, datetime.date(2000, 1, 1))
        self.assertIn(3, self.free(born=datetime.date(2015, 1, 1))["numbers"])  # 15 years apart
        self.assertNotIn(3, self.free(born=datetime.date(2001, 1, 1))["numbers"])  # 1 year apart
        self.assertNotIn(3, self.free()["numbers"])  # unknown date of birth: never shared

    def test_the_age_gap_is_off_when_it_is_zero(self):
        self.pool.min_age_gap_years = 0
        self.pool.save()
        self.holder(self.other_team, 3, datetime.date(1980, 1, 1))
        self.assertNotIn(3, self.free(born=datetime.date(2015, 1, 1))["numbers"])

    def test_numbers_of_other_seasons_do_not_count(self):
        past = type(self.season).objects.create(start_date=self.season.start_date - datetime.timedelta(days=800), end_date=self.season.start_date - datetime.timedelta(days=500))
        member = Member.objects.create(first_name="Old", last_name="Timer", date_of_birth=datetime.date(1990, 1, 1))
        TeamMembership.objects.create(team=self.team, member=member, season=past, jersey_number=5)
        self.assertIn(5, self.free()["numbers"])

    def test_numbers_asked_for_in_the_same_form_are_left_out(self):
        self.assertEqual(self.free(taken_in_batch=[1, "2"])["numbers"], [3, 4, 5, 6])

    def test_a_batch_spot_on_another_team_can_share_when_the_age_gap_allows(self):
        probe = numbers.probe_membership(self.other_team, self.season, datetime.date(2005, 1, 1), 3)
        self.assertIn(3, self.free(born=datetime.date(2015, 1, 1), batch=[probe])["numbers"])
        self.assertNotIn(3, self.free(born=datetime.date(2006, 1, 1), batch=[probe])["numbers"])
        same_team = numbers.probe_membership(self.team, self.season, datetime.date(2005, 1, 1), 3)
        self.assertNotIn(3, self.free(born=datetime.date(2015, 1, 1), batch=[same_team])["numbers"])

    def test_a_team_without_a_pool_has_no_range(self):
        self.team.pool = None
        self.team.save()
        self.holder(self.team, 77, datetime.date(1990, 1, 1))
        result = self.free(taken_in_batch=[5])
        self.assertEqual((result["pool"], result["numbers"], result["taken"]), (None, None, [5, 77]))

    def test_number_available_matches_the_list(self):
        NumberReservation.objects.create(pool=self.pool, number=2)
        self.holder(self.team, 4, datetime.date(1990, 1, 1))
        listed = set(self.free(born=datetime.date(2015, 1, 1))["numbers"])
        for n in range(0, 9):
            self.assertEqual(numbers.number_available(n, self.season, self.team, datetime.date(2015, 1, 1)), n in listed, n)
