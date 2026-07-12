from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone

from club.models import Club, Season
from members.models import Member
from teams.models import Position, Team, TeamMembership

from .models import Attendance, Event, EventSeries, Location, Opponent
from .services import (
    cancel_occurrence,
    detach_occurrence,
    effective_members,
    generate_occurrences,
    occurrence_datetimes,
    propagate_series,
)


class EventsTestBase(TestCase):
    def setUp(self):
        self.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        today = timezone.localdate()
        self.season = Season.objects.create(
            club=self.club,
            start_date=today - timedelta(days=30),
            end_date=today + timedelta(days=300),
        )
        self.team = Team.objects.create(club=self.club, name="First Team", short_name="1st")
        self.position = Position.objects.create(club=self.club, name="Forward", short_name="FW")
        self.alice = Member.objects.create(first_name="Alice", last_name="Ash")
        self.bob = Member.objects.create(first_name="Bob", last_name="Birch")
        TeamMembership.objects.create(team=self.team, member=self.alice, season=self.season, position=self.position)
        TeamMembership.objects.create(team=self.team, member=self.bob, season=self.season, position=self.position)
        self.future = timezone.now() + timedelta(days=7)

    def make_event(self, **kwargs):
        kwargs.setdefault("club", self.club)
        kwargs.setdefault("title", "Training")
        kwargs.setdefault("start", self.future)
        if "season" not in kwargs:
            kwargs["season"] = self.season
        return Event.objects.create(**kwargs)

    def attendee_ids(self, event):
        return set(event.attendances.values_list("member_id", flat=True))


class EventModelTests(EventsTestBase):
    def test_str_methods(self):
        opponent = Opponent.objects.create(club=self.club, name="Rivals FC")
        location = Location.objects.create(club=self.club, name="Arena", address="1 St", city="Town", zip_code="1000", country="BE")
        event = self.make_event(title="Big Match")
        attendance = Attendance.objects.create(event=event, member=self.alice)

        self.assertEqual(str(opponent), "Rivals FC")
        self.assertEqual(str(location), "Arena")
        self.assertEqual(str(event), "Big Match")
        self.assertEqual(str(attendance), "Big Match - Alice Ash")

    def test_attendance_is_unique_per_event_and_member(self):
        event = self.make_event()
        Attendance.objects.create(event=event, member=self.alice)

        with self.assertRaises(IntegrityError):
            Attendance.objects.create(event=event, member=self.alice)


class EffectiveMembersTests(EventsTestBase):
    def test_union_of_team_invited_minus_excluded(self):
        carol = Member.objects.create(first_name="Carol", last_name="Cedar")
        event = self.make_event()
        event.teams.set([self.team])
        event.invited_members.set([carol])
        event.excluded_members.set([self.bob])

        self.assertEqual(
            set(effective_members(event).values_list("id", flat=True)),
            {self.alice.id, carol.id},
        )

    def test_season_is_derived_from_start_date(self):
        event = self.make_event(season=None)
        event.teams.set([self.team])

        self.assertEqual(self.attendee_ids(event), {self.alice.id, self.bob.id})

    def test_no_covering_season_yields_no_team_members(self):
        event = self.make_event(season=None, start=timezone.now() + timedelta(days=5000))
        event.teams.set([self.team])

        self.assertEqual(event.attendances.count(), 0)


class AttendanceSyncTests(EventsTestBase):
    def test_setting_teams_creates_attendance_for_roster(self):
        event = self.make_event()
        event.teams.set([self.team])

        self.assertEqual(self.attendee_ids(event), {self.alice.id, self.bob.id})

    def test_invited_member_gets_attendance(self):
        carol = Member.objects.create(first_name="Carol", last_name="Cedar")
        event = self.make_event()
        event.invited_members.set([carol])

        self.assertEqual(self.attendee_ids(event), {carol.id})

    def test_reverse_invited_relation_syncs(self):
        carol = Member.objects.create(first_name="Carol", last_name="Cedar")
        event = self.make_event()
        carol.invited_to_events.add(event)

        self.assertIn(carol.id, self.attendee_ids(event))

    def test_excluding_member_removes_attendance(self):
        event = self.make_event()
        event.teams.set([self.team])
        event.excluded_members.set([self.alice])

        self.assertEqual(self.attendee_ids(event), {self.bob.id})

    def test_hard_reconcile_removes_even_responded_rows(self):
        event = self.make_event()
        event.teams.set([self.team])
        attendance = event.attendances.get(member=self.alice)
        attendance.status = Attendance.AttendanceStatus.PRESENT
        attendance.save()

        event.excluded_members.set([self.alice])

        self.assertFalse(event.attendances.filter(member=self.alice).exists())

    def test_past_event_is_not_synced(self):
        event = self.make_event(start=timezone.now() - timedelta(days=1))
        event.teams.set([self.team])

        self.assertEqual(event.attendances.count(), 0)


class RosterChangeSyncTests(EventsTestBase):
    def test_adding_roster_member_syncs_future_events(self):
        event = self.make_event()
        event.teams.set([self.team])
        dave = Member.objects.create(first_name="Dave", last_name="Dogwood")

        TeamMembership.objects.create(team=self.team, member=dave, season=self.season, position=self.position)

        self.assertIn(dave.id, self.attendee_ids(event))

    def test_removing_roster_member_syncs_future_events(self):
        event = self.make_event()
        event.teams.set([self.team])

        TeamMembership.objects.get(team=self.team, member=self.alice).delete()

        self.assertNotIn(self.alice.id, self.attendee_ids(event))

    def test_roster_change_leaves_past_events_untouched(self):
        past = self.make_event(start=timezone.now() - timedelta(days=1))
        past.teams.add(self.team)
        Attendance.objects.create(event=past, member=self.alice)

        TeamMembership.objects.get(team=self.team, member=self.alice).delete()

        self.assertTrue(past.attendances.filter(member=self.alice).exists())


class RecurrenceTestBase(EventsTestBase):
    def setUp(self):
        super().setUp()
        self.anchor = (timezone.now() + timedelta(days=1)).replace(microsecond=0)

    def make_series(self, **kwargs):
        kwargs.setdefault("club", self.club)
        kwargs.setdefault("title", "Weekly Training")
        kwargs.setdefault("kind", Event.EventKind.TRAINING)
        kwargs.setdefault("rrule", "FREQ=WEEKLY;COUNT=4")
        kwargs.setdefault("dtstart", self.anchor)
        kwargs.setdefault("duration", timedelta(hours=2))
        series = EventSeries.objects.create(**kwargs)
        series.teams.set([self.team])
        return series


class OccurrenceExpansionTests(RecurrenceTestBase):
    def test_str(self):
        self.assertEqual(str(self.make_series()), "Weekly Training")

    def test_weekly_expansion(self):
        series = self.make_series()

        dts = occurrence_datetimes(series, self.anchor + timedelta(days=30))

        self.assertEqual(dts[0], self.anchor)
        self.assertEqual(dts[1], self.anchor + timedelta(weeks=1))
        self.assertEqual(len(dts), 4)

    def test_until_bounds_expansion(self):
        series = self.make_series()

        dts = occurrence_datetimes(series, self.anchor + timedelta(days=10))

        self.assertEqual(len(dts), 2)

    def test_excluded_dates_are_skipped(self):
        series = self.make_series()
        skipped = self.anchor + timedelta(weeks=1)
        series.excluded_dates = [skipped.isoformat()]
        series.save()

        dts = occurrence_datetimes(series, self.anchor + timedelta(days=30))

        self.assertNotIn(skipped, dts)
        self.assertEqual(len(dts), 3)


class GenerateOccurrencesTests(RecurrenceTestBase):
    def test_materialises_occurrences_with_template_and_attendance(self):
        series = self.make_series()

        created = generate_occurrences(series, self.anchor + timedelta(days=30))

        self.assertEqual(len(created), 4)
        first = series.occurrences.order_by("start").first()
        self.assertEqual(first.start, self.anchor)
        self.assertEqual(first.end, self.anchor + timedelta(hours=2))
        self.assertEqual(first.title, "Weekly Training")
        self.assertEqual(first.kind, Event.EventKind.TRAINING)
        # Audience copied from the series, so attendance follows the roster.
        self.assertEqual(set(first.attendances.values_list("member_id", flat=True)), {self.alice.id, self.bob.id})
        series.refresh_from_db()
        self.assertIsNotNone(series.generated_until)

    def test_generation_is_idempotent(self):
        series = self.make_series()
        until = self.anchor + timedelta(days=30)

        generate_occurrences(series, until)
        generate_occurrences(series, until)

        self.assertEqual(series.occurrences.count(), 4)


class SingleOccurrenceTests(RecurrenceTestBase):
    def test_cancel_deletes_and_prevents_regeneration(self):
        series = self.make_series()
        until = self.anchor + timedelta(days=30)
        generate_occurrences(series, until)
        target = series.occurrences.order_by("start")[1]
        target_start = target.start

        cancel_occurrence(target)
        self.assertFalse(series.occurrences.filter(start=target_start).exists())

        generate_occurrences(series, until)
        self.assertFalse(series.occurrences.filter(start=target_start).exists())
        self.assertEqual(series.occurrences.count(), 3)

    def test_cancel_soft_marks_cancelled(self):
        series = self.make_series()
        generate_occurrences(series, self.anchor + timedelta(days=30))
        target = series.occurrences.order_by("start").first()

        cancel_occurrence(target, hard_delete=False)

        target.refresh_from_db()
        self.assertTrue(target.cancelled)
        self.assertIn(target.start.isoformat(), series.excluded_dates)

    def test_detached_occurrence_is_left_untouched_by_propagation(self):
        series = self.make_series()
        generate_occurrences(series, self.anchor + timedelta(days=30))
        detached = series.occurrences.order_by("start").first()
        detach_occurrence(detached)

        series.title = "Renamed"
        series.save()
        propagate_series(series)

        detached.refresh_from_db()
        self.assertEqual(detached.title, "Weekly Training")
        other = series.occurrences.exclude(pk=detached.pk).order_by("start").first()
        self.assertEqual(other.title, "Renamed")

    def test_propagation_updates_audience_and_attendance(self):
        series = self.make_series()
        generate_occurrences(series, self.anchor + timedelta(days=30))
        carol = Member.objects.create(first_name="Carol", last_name="Cedar")
        series.invited_members.set([carol])

        propagate_series(series)

        event = series.occurrences.order_by("start").first()
        self.assertIn(carol.id, set(event.attendances.values_list("member_id", flat=True)))


class ExtendSeriesCommandTests(RecurrenceTestBase):
    def test_command_generates_occurrences(self):
        series = self.make_series()
        out = StringIO()

        call_command("extend_event_series", stdout=out)

        self.assertEqual(series.occurrences.count(), 4)
        self.assertIn("Done.", out.getvalue())
