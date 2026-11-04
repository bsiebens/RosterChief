"""Season calendar and the periodic task that creates missing seasons."""

import datetime

from django.db import connection
from django.test import TestCase
from django_tenants.utils import schema_context

from club.models import Club
from club.services import create_club
from controlpanel.tests import make_user
from events.models import Season
from events.seasons import add_months, ensure_seasons, season_end
from events.tasks import create_missing_seasons

D = datetime.date


class SeasonMathTests(TestCase):
    def test_add_months_clamps_and_season_end(self):
        self.assertEqual(add_months(D(2026, 1, 31), 1), D(2026, 2, 28))
        self.assertEqual(season_end(D(2026, 7, 1), 12), D(2027, 6, 30))
        self.assertEqual(season_end(D(2026, 9, 1), 10), D(2027, 6, 30))


class CreateMissingSeasonsTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        connection.set_schema_to_public()

    @classmethod
    def setUpTestData(cls):
        owner = make_user("seasons@owner.test")
        public = Club(schema_name="public", name="Platform", owner=owner)
        public.auto_create_schema = False
        public.save()
        cls.club = create_club("Season FC", owner)

    def tearDown(self):
        connection.set_schema_to_public()

    def run_ensure(self, today, **kw):
        with schema_context(self.club.schema_name):
            created = ensure_seasons(today, **{"start_month": 7, "start_day": 1, "duration_months": 12, **kw})
            return [(s.start_date, s.end_date) for s in created], list(Season.objects.order_by("start_date").values_list("start_date", "end_date"))

    def test_empty_club_gets_current_and_next_season(self):
        _, seasons = self.run_ensure(D(2026, 11, 3))
        self.assertEqual(seasons, [(D(2026, 7, 1), D(2027, 6, 30)), (D(2027, 7, 1), D(2028, 6, 30))])

    def test_before_the_start_date_uses_last_years_window(self):
        _, seasons = self.run_ensure(D(2026, 3, 1))
        self.assertEqual(seasons[0], (D(2025, 7, 1), D(2026, 6, 30)))

    def test_idempotent_and_chains_from_existing(self):
        self.run_ensure(D(2026, 11, 3))
        created, seasons = self.run_ensure(D(2026, 11, 3))
        self.assertEqual(created, [])
        created, seasons = self.run_ensure(D(2027, 8, 1))  # a new season has become current: add the one after it
        self.assertEqual(created, [(D(2028, 7, 1), D(2029, 6, 30))])
        self.assertEqual(len(seasons), 3)

    def test_custom_duration(self):
        _, seasons = self.run_ensure(D(2026, 11, 3), start_month=9, duration_months=10)
        self.assertEqual(seasons[0], (D(2026, 9, 1), D(2027, 6, 30)))
        self.assertEqual(seasons[1][0], D(2027, 7, 1))

    def test_task_creates_seasons_for_clubs_and_is_idempotent(self):
        today = D(2026, 11, 9)
        self.assertIn("created", create_missing_seasons(today=today))
        with schema_context(self.club.schema_name):
            self.assertEqual(Season.objects.count(), 2)
        self.assertIn("created 0", create_missing_seasons(today=today))
