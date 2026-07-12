from datetime import timedelta

from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone

from club.models import Club, Season
from members.models import Member
from teams.models import Position, Team, TeamMembership

from .models import Attendance, Event, Location, Opponent
from .services import effective_members


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
