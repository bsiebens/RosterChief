"""events.services.recurrence: RRULE building/parsing, expansion, generation, the extend command and task."""

import datetime
import io

from django.core.management import call_command
from django.db import connection
from django.test import TestCase
from django.utils import timezone
from django_tenants.utils import schema_context

from club.models import Club, Domain
from club.services import create_club
from controlpanel.tests import make_user
from events.models import Event, EventSeries, Season
from events.services import recurrence as rec
from events.tasks import extend_event_series


def local(y, m, d, hh=19, mm=0):
    return timezone.make_aware(datetime.datetime(y, m, d, hh, mm))


class RRuleTextTests(TestCase):
    def test_build_and_parse_round_trip(self):
        cases = [
            (("weekly", 1, ["WE", "MO"]), "FREQ=WEEKLY;INTERVAL=1;BYDAY=MO,WE"),
            (("weekly", 2, ["FR"], 10), "FREQ=WEEKLY;INTERVAL=2;BYDAY=FR;COUNT=10"),
            (("daily", 3), "FREQ=DAILY;INTERVAL=3"),
            (("monthly", 1, ["MO"]), "FREQ=MONTHLY;INTERVAL=1"),  # weekdays ignored for monthly
        ]
        for args, expected in cases:
            rule = rec.build_rrule(*args)
            self.assertEqual(rule, expected)
            parsed = rec.parse_rrule(rule)
            self.assertEqual(rec.build_rrule(parsed["frequency"], parsed["interval"], parsed["weekdays"], parsed["count"]), rule)
        self.assertEqual(rec.parse_rrule("FREQ=WEEKLY;INTERVAL=1;BYDAY=MO,WE"), {"frequency": "weekly", "interval": 1, "weekdays": ["MO", "WE"], "count": None})
        with self.assertRaises(ValueError):
            rec.build_rrule("yearly", 1)

    def test_parse_rejects_foreign_rules(self):
        for raw in ("FREQ=WEEKLY;BYDAY=MO", "FREQ=YEARLY;INTERVAL=1", "FREQ=MONTHLY;INTERVAL=1;BYMONTHDAY=1", "FREQ=WEEKLY;INTERVAL=1", "FREQ=DAILY;INTERVAL=1;BYDAY=MO", ""):
            self.assertIsNone(rec.parse_rrule(raw), raw)

    def test_describe(self):
        self.assertEqual(rec.describe_rrule("FREQ=WEEKLY;INTERVAL=1;BYDAY=MO,WE"), "Every week on Mon, Wed")
        self.assertEqual(rec.describe_rrule("FREQ=WEEKLY;INTERVAL=2;BYDAY=FR;COUNT=5"), "Every 2 weeks on Fri, 5 times")
        self.assertEqual(rec.describe_rrule("FREQ=DAILY;INTERVAL=1"), "Every day")
        self.assertEqual(rec.describe_rrule("FREQ=MONTHLY;INTERVAL=3"), "Every 3 months")
        self.assertEqual(rec.describe_rrule("FREQ=YEARLY;BYMONTH=3"), "FREQ=YEARLY;BYMONTH=3")
        series = EventSeries(rrule="FREQ=DAILY;INTERVAL=1", dtstart=local(2026, 1, 1), until=rec.end_of_day(datetime.date(2026, 6, 30)))
        self.assertEqual(rec.describe_recurrence(series), "Every day, until 30/06/2026")
        self.assertEqual(rec.recurrence_values(series)["until"], datetime.date(2026, 6, 30))
        self.assertFalse(rec.recurrence_values(EventSeries(rrule="FREQ=YEARLY", dtstart=local(2026, 1, 1)))["friendly"])


class RecurrenceDbTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        connection.set_schema_to_public()

    @classmethod
    def setUpTestData(cls):
        cls.owner = make_user("owner@rec.test")
        public = Club(schema_name="public", name="Platform", owner=cls.owner)
        public.auto_create_schema = False
        public.save()
        Domain.objects.create(domain="testserver", tenant=public, is_primary=True)
        cls.club = create_club("Recurrence FC", cls.owner)
        with schema_context(cls.club.schema_name):
            Season.objects.create(start_date=datetime.date(2026, 7, 1), end_date=datetime.date(2027, 6, 30))

    def setUp(self):
        self._ctx = schema_context(self.club.schema_name)
        self._ctx.__enter__()
        self.addCleanup(self._ctx.__exit__, None, None, None)

    def series(self, rrule="FREQ=WEEKLY;INTERVAL=1;BYDAY=MO,WE", dtstart=None, **kw):
        return EventSeries.objects.create(rrule=rrule, dtstart=dtstart or local(2026, 10, 19), title="Training", kind="training", duration=datetime.timedelta(minutes=90), gathering_offset=datetime.timedelta(minutes=15), **kw)

    def test_expansion_keeps_local_time_over_dst_and_skips_exdates(self):
        s = self.series(excluded_dates=[rec.iso(local(2026, 10, 21))])
        starts = rec.occurrence_datetimes(s, local(2026, 11, 2, 23))
        self.assertEqual([timezone.localtime(d).date() for d in starts], [datetime.date(2026, 10, 19), datetime.date(2026, 10, 26), datetime.date(2026, 10, 28), datetime.date(2026, 11, 2)])
        self.assertTrue(all(timezone.localtime(d).time() == datetime.time(19, 0) for d in starts))  # DST ended on Oct 25

    def test_until_caps_and_count(self):
        s = self.series(until=rec.end_of_day(datetime.date(2026, 10, 26)))
        self.assertEqual(len(rec.occurrence_datetimes(s, local(2027, 1, 1))), 3)
        s2 = self.series(rrule="FREQ=DAILY;INTERVAL=2;COUNT=4")
        self.assertEqual(len(rec.occurrence_datetimes(s2, local(2027, 1, 1))), 4)

    def test_generate_is_idempotent_and_resumes(self):
        s = self.series()
        created = rec.generate_occurrences(s, until=local(2026, 11, 1, 23))
        self.assertEqual(len(created), 4)  # 19, 21, 26, 28 Oct
        e = created[0]
        self.assertEqual((e.title, e.kind, e.end - e.start, e.start - e.gathering, e.series_id), ("Training", "training", datetime.timedelta(minutes=90), datetime.timedelta(minutes=15), s.pk))
        self.assertIsNotNone(e.season_id)
        self.assertEqual(rec.generate_occurrences(s, until=local(2026, 11, 1, 23)), [])
        more = rec.generate_occurrences(s, until=local(2026, 11, 8, 23))
        self.assertEqual([timezone.localtime(x.start).date() for x in more], [datetime.date(2026, 11, 2), datetime.date(2026, 11, 4)])
        s.refresh_from_db()
        self.assertEqual(s.generated_until, local(2026, 11, 8, 23))

    def test_count_rule_never_exceeds_count(self):
        s = self.series(rrule="FREQ=WEEKLY;INTERVAL=1;BYDAY=MO;COUNT=3")
        rec.generate_occurrences(s, until=local(2026, 10, 27))
        rec.generate_occurrences(s, until=local(2027, 3, 1))
        self.assertEqual(s.occurrences.count(), 3)

    def test_cancelled_or_deleted_slot_is_not_regenerated(self):
        s = self.series()
        created = rec.generate_occurrences(s, until=local(2026, 11, 1, 23))
        rec.cancel_occurrence(created[1])
        rec.cancel_occurrence(created[2], hard_delete=False)
        s.refresh_from_db()
        self.assertEqual(len(s.excluded_dates), 2)
        rec.generate_occurrences(s, until=local(2026, 11, 1, 23))
        self.assertEqual(s.occurrences.count(), 3)
        self.assertTrue(Event.objects.get(pk=created[2].pk).cancelled)

    def test_propagate_skips_detached_and_past(self):
        s = self.series(dtstart=timezone.now() - datetime.timedelta(days=14))
        rec.generate_occurrences(s, until=timezone.now() + datetime.timedelta(days=14))
        future = list(s.occurrences.filter(start__gte=timezone.now()).order_by("start"))
        rec.detach_occurrence(future[0])
        s.title = "Renamed"
        s.save()
        updated = rec.propagate_series(s)
        self.assertEqual(len(updated), len(future) - 1)
        self.assertEqual(Event.objects.get(pk=future[0].pk).title, "Training")
        self.assertFalse(s.occurrences.filter(start__lt=timezone.now(), title="Renamed").exists())

    def test_split_series(self):
        s = self.series(rrule="FREQ=WEEKLY;INTERVAL=1;BYDAY=MO;COUNT=6")
        rec.generate_occurrences(s, until=local(2027, 1, 1))
        third = s.occurrences.order_by("start")[2]
        new = rec.split_series(s, third.start)
        self.assertEqual(s.occurrences.count(), 2)
        self.assertEqual(new.occurrences.count(), 4)
        self.assertIn("COUNT=4", new.rrule)
        self.assertEqual(new.dtstart, third.start)
        rec.generate_occurrences(s, until=local(2027, 1, 1))
        rec.generate_occurrences(new, until=local(2027, 1, 1))
        self.assertEqual(Event.objects.count(), 6)

    def test_extend_command_and_task(self):
        s = self.series(dtstart=timezone.now() + datetime.timedelta(days=1))
        out = io.StringIO()
        call_command("extend_event_series", stdout=out)
        n = s.occurrences.count()
        self.assertGreater(n, 20)  # ~90 days of two per week
        self.assertFalse(s.occurrences.filter(start__gt=rec.horizon()).exists())
        self.assertIn(f"Generated {n} occurrence(s) across 1 series.", out.getvalue())
        self.assertEqual(extend_event_series(), "generated 0 occurrence(s)")
        s.occurrences.order_by("-start").first().delete()  # not regenerated: before generated_until
        self.assertEqual(extend_event_series(), "generated 0 occurrence(s)")
