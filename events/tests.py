from datetime import timedelta
from io import StringIO

from django.core.exceptions import ValidationError
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
    player_attendance_rankings,
    players_who_missed_recent_practices,
    propagate_series,
    record_check_in,
    team_attendance_rate,
    team_no_shows,
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

    def test_series_until_caps_expansion(self):
        series = self.make_series(rrule="FREQ=WEEKLY", until=self.anchor + timedelta(days=10))

        dts = occurrence_datetimes(series, self.anchor + timedelta(days=90))

        # Only the anchor and the first weekly repeat fall on/before `until`.
        self.assertEqual(dts, [self.anchor, self.anchor + timedelta(weeks=1)])
        self.assertTrue(all(dt <= series.until for dt in dts))


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

    def test_generation_stops_at_series_until(self):
        series = self.make_series(rrule="FREQ=WEEKLY", until=self.anchor + timedelta(days=10))

        generate_occurrences(series)

        self.assertEqual(series.occurrences.count(), 2)

    def test_gathering_and_deadline_come_from_offsets(self):
        series = self.make_series(gathering_offset=timedelta(minutes=30), deadline_offset=timedelta(days=1))

        generate_occurrences(series, self.anchor + timedelta(days=30))

        first = series.occurrences.order_by("start").first()
        self.assertEqual(first.gathering, self.anchor - timedelta(minutes=30))
        self.assertEqual(first.deadline, self.anchor - timedelta(days=1))

    def test_without_offsets_gathering_and_deadline_are_blank(self):
        series = self.make_series()

        generate_occurrences(series, self.anchor + timedelta(days=30))

        first = series.occurrences.order_by("start").first()
        self.assertIsNone(first.gathering)
        self.assertIsNone(first.deadline)


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

    def test_propagation_updates_timing_offsets(self):
        series = self.make_series()
        generate_occurrences(series, self.anchor + timedelta(days=30))
        series.gathering_offset = timedelta(minutes=45)
        series.save()

        propagate_series(series)

        first = series.occurrences.order_by("start").first()
        self.assertEqual(first.gathering, self.anchor - timedelta(minutes=45))


class ExtendSeriesCommandTests(RecurrenceTestBase):
    def test_command_generates_occurrences(self):
        series = self.make_series()
        out = StringIO()

        call_command("extend_event_series", stdout=out)

        self.assertEqual(series.occurrences.count(), 4)
        self.assertIn("Done.", out.getvalue())


class EventClubScopeTests(EventsTestBase):
    def setUp(self):
        super().setUp()
        self.other = Club.objects.create(name="Rival FC", slug="rival-fc")
        today = timezone.localdate()
        self.other_season = Season.objects.create(club=self.other, start_date=today - timedelta(days=30), end_date=today + timedelta(days=300))
        self.other_location = Location.objects.create(club=self.other, name="Arena", address="1 St", city="Town", zip_code="1000", country="BE")
        self.other_opponent = Opponent.objects.create(club=self.other, name="Rivals")
        self.other_team = Team.objects.create(club=self.other, name="First", short_name="1")

    def test_event_rejects_cross_club_season(self):
        event = Event(club=self.club, title="Match", start=self.future, season=self.other_season)
        with self.assertRaises(ValidationError) as ctx:
            event.full_clean()
        self.assertIn("season", ctx.exception.error_dict)

    def test_event_rejects_cross_club_location(self):
        event = Event(club=self.club, title="Match", start=self.future, location=self.other_location)
        with self.assertRaises(ValidationError) as ctx:
            event.full_clean()
        self.assertIn("location", ctx.exception.error_dict)

    def test_event_accepts_same_club_fields(self):
        Event(club=self.club, title="Match", start=self.future, season=self.season).full_clean()

    def test_event_rejects_cross_club_team(self):
        event = Event.objects.create(club=self.club, title="Match", start=self.future, season=self.season)
        with self.assertRaises(ValidationError):
            event.teams.add(self.other_team)

    def test_event_accepts_same_club_team(self):
        event = Event.objects.create(club=self.club, title="Match", start=self.future, season=self.season)
        event.teams.add(self.team)
        self.assertIn(self.team, event.teams.all())

    def test_series_rejects_cross_club_opponent(self):
        series = EventSeries(club=self.club, title="Weekly", rrule="FREQ=WEEKLY", dtstart=self.future, opponent=self.other_opponent)
        with self.assertRaises(ValidationError) as ctx:
            series.full_clean()
        self.assertIn("opponent", ctx.exception.error_dict)

    def test_series_rejects_cross_club_team(self):
        series = EventSeries.objects.create(club=self.club, title="Weekly", rrule="FREQ=WEEKLY", dtstart=self.future)
        with self.assertRaises(ValidationError):
            series.teams.add(self.other_team)


class TeamAttendanceStatsTests(EventsTestBase):
    """events.services.attendance's team+season-scoped stats -- the queries
    behind management.views.TeamDetailView's attendance panel."""

    def make_past_training(self, days_ago, **kwargs):
        # Past-start events are never auto-synced (see test_past_event_is_not_synced
        # above), so attendance rows have to be created by hand here.
        kwargs.setdefault("kind", Event.EventKind.TRAINING)
        event = self.make_event(start=timezone.now() - timedelta(days=days_ago), **kwargs)
        event.teams.add(self.team)
        return event

    def set_status(self, event, member, status):
        attendance, _created = Attendance.objects.update_or_create(event=event, member=member, defaults={"status": status})
        return attendance

    def test_record_check_in_sets_showed_up(self):
        event = self.make_past_training(1)
        attendance = self.set_status(event, self.alice, Attendance.AttendanceStatus.PRESENT)

        record_check_in(attendance, showed_up=False)

        attendance.refresh_from_db()
        self.assertFalse(attendance.showed_up)

    def test_team_attendance_rate_excludes_excused_and_no_response(self):
        event = self.make_past_training(1)
        self.set_status(event, self.alice, Attendance.AttendanceStatus.PRESENT)
        self.set_status(event, self.bob, Attendance.AttendanceStatus.ABSENT)

        self.assertEqual(team_attendance_rate(self.team, self.season), 50)

    def test_team_attendance_rate_is_none_with_no_past_events(self):
        self.assertIsNone(team_attendance_rate(self.team, self.season))

    def test_rankings_exclude_players_below_the_response_minimum(self):
        event = self.make_past_training(1)
        self.set_status(event, self.alice, Attendance.AttendanceStatus.PRESENT)
        self.set_status(event, self.bob, Attendance.AttendanceStatus.ABSENT)

        rankings = player_attendance_rankings(self.team, self.season, minimum_responses=2)

        self.assertEqual(rankings, [])

    def test_rankings_rank_best_first(self):
        e1 = self.make_past_training(10)
        e2 = self.make_past_training(3)
        self.set_status(e1, self.alice, Attendance.AttendanceStatus.PRESENT)
        self.set_status(e2, self.alice, Attendance.AttendanceStatus.PRESENT)
        self.set_status(e1, self.bob, Attendance.AttendanceStatus.PRESENT)
        self.set_status(e2, self.bob, Attendance.AttendanceStatus.ABSENT)

        rankings = player_attendance_rankings(self.team, self.season, minimum_responses=2)

        self.assertEqual([entry["member"] for entry in rankings], [self.alice, self.bob])
        self.assertEqual(rankings[0]["rate"], 100)
        self.assertEqual(rankings[1]["rate"], 50)

    def test_missed_recent_practices_needs_full_history(self):
        self.make_past_training(3)  # only one practice logged so far

        self.assertFalse(players_who_missed_recent_practices(self.team, self.season, count=2).exists())

    def test_missed_recent_practices_flags_absence_on_both(self):
        e1 = self.make_past_training(10)
        e2 = self.make_past_training(3)
        self.set_status(e1, self.alice, Attendance.AttendanceStatus.ABSENT)
        self.set_status(e2, self.alice, Attendance.AttendanceStatus.NO_RESPONSE)
        self.set_status(e1, self.bob, Attendance.AttendanceStatus.PRESENT)
        self.set_status(e2, self.bob, Attendance.AttendanceStatus.ABSENT)

        missed = players_who_missed_recent_practices(self.team, self.season, count=2)

        self.assertEqual(list(missed), [self.alice])

    def test_missed_recent_practices_excludes_an_excused_absence(self):
        e1 = self.make_past_training(10)
        e2 = self.make_past_training(3)
        self.set_status(e1, self.alice, Attendance.AttendanceStatus.EXCUSED)
        self.set_status(e2, self.alice, Attendance.AttendanceStatus.ABSENT)

        missed = players_who_missed_recent_practices(self.team, self.season, count=2)

        self.assertNotIn(self.alice, missed)

    def test_no_shows_requires_an_explicit_check_in(self):
        event = self.make_past_training(1)
        self.set_status(event, self.alice, Attendance.AttendanceStatus.PRESENT)

        # Nobody has been checked in at all -- must not read as a no-show.
        self.assertEqual(list(team_no_shows(self.team, self.season)), [])

    def test_no_shows_flags_a_present_rsvp_checked_in_as_absent(self):
        event = self.make_past_training(1)
        attendance = self.set_status(event, self.alice, Attendance.AttendanceStatus.PRESENT)
        record_check_in(attendance, showed_up=False)

        no_shows = team_no_shows(self.team, self.season)

        self.assertEqual(len(no_shows), 1)
        self.assertEqual(no_shows[0].member, self.alice)
        self.assertEqual(no_shows[0].event, event)

    def test_a_confirmed_check_in_is_not_a_no_show(self):
        event = self.make_past_training(1)
        attendance = self.set_status(event, self.alice, Attendance.AttendanceStatus.PRESENT)
        record_check_in(attendance, showed_up=True)

        self.assertEqual(list(team_no_shows(self.team, self.season)), [])
