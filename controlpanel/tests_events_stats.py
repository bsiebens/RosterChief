"""Events numbers on the club page's Events box and on the platform dashboard."""

import datetime

from django.test import TestCase
from django.utils import timezone
from django_tenants.utils import schema_context

from club.services import create_club
from controlpanel import stats
from events.models import Event, Season
from controlpanel.tests import make_user


def _stat(overview, label):
    group = next(g for g in overview["groups"] if g["key"] == "events")
    return next(s["value"] for s in group["stats"] if s["label"] == label)


class EventStatsTests(TestCase):
    def setUp(self):
        self.owner = make_user("owner@example.com")
        self.club = create_club("Events FC", self.owner)
        self.now = timezone.now()

    def event(self, days, kind=Event.EventKind.OTHER, **kw):
        with schema_context(self.club.schema_name):
            return Event.objects.create(title="e", kind=kind, start=self.now + datetime.timedelta(days=days), **kw)

    def season(self):
        today = timezone.localdate()
        with schema_context(self.club.schema_name):
            return Season.objects.create(start_date=today - datetime.timedelta(days=100), end_date=today + datetime.timedelta(days=100))

    def test_empty_club_reports_zero_and_no_season(self):
        o = stats.club_overview(self.club)
        group = next(g for g in o["groups"] if g["key"] == "events")
        self.assertTrue(group["available"])
        self.assertEqual((_stat(o, "Upcoming"), _stat(o, "In the next 7 days")), (0, 0))
        self.assertIsNone(_stat(o, "This season"))  # no season to count in: unknown, not zero
        self.assertIsNone(_stat(o, "Games played this season"))

    def test_counts(self):
        self.season()
        self.event(2)
        self.event(30)
        self.event(5, cancelled=True)  # cancelled events never count
        self.event(-3, kind=Event.EventKind.GAME)
        self.event(-4, kind=Event.EventKind.TRAINING)
        o = stats.club_overview(self.club)
        self.assertEqual(_stat(o, "Upcoming"), 2)
        self.assertEqual(_stat(o, "In the next 7 days"), 1)
        self.assertEqual(_stat(o, "This season"), 4)
        self.assertEqual(_stat(o, "Games played this season"), 1)

    def test_events_of_another_club_are_not_counted(self):
        other = create_club("Other FC", self.owner)
        with schema_context(other.schema_name):
            Event.objects.create(title="x", kind="other", start=self.now + datetime.timedelta(days=1))
        self.assertEqual(_stat(stats.club_overview(self.club), "Upcoming"), 0)

    def test_dashboard_totals_and_rows(self):
        self.event(1)
        self.event(2)
        other = create_club("Quiet FC", self.owner)
        snap = stats.snapshot(refresh=True)
        self.assertEqual(snap["totals"]["events_upcoming"]["value"], 2)
        rows = {r["name"]: r["events_upcoming"] for r in snap["clubs"]}
        self.assertEqual((rows["Events FC"], rows[other.name]), (2, 0))
