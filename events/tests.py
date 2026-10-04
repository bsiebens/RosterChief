import itertools
import json
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from zoneinfo import ZoneInfo

import requests
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from waffle import get_waffle_flag_model

from club.models import Club, ClubMembership, ClubRole, OnboardingRequirement, Season
from club.services.onboarding import mark_bypassed, mark_complete
from features.jobs import JOB_REGISTRY
from features.models import JobRun, JobToggle, Maintenance
from members.models import Group, GroupMembership, Member
from notifications.models import Notification
from teams.models import OfficialLevel, OfficialProfile, Position, RefereeLevel, RefereeProfile, StaffAssignment, Team, TeamMembership

from .admin import EventAdminForm
from .competition.hockey import CEHL, DFEL, IJNL, RBIHF
from .models import Attendance, Competition, Event, EventOfficial, EventReferee, EventSeries, EventTask, EventTaskClaim, GameMoment, GameTimeline, Lineup, LineupSelection, Location, OfficialSignup, Opponent, RefereeSignup
from .services import (
    blocked_upcoming_events_for_member,
    cancel_occurrence,
    detach_occurrence,
    dfel_import,
    effective_members,
    game_moments,
    game_push,
    game_timeline,
    generate_occurrences,
    ijnl_import,
    import_sources,
    occurrence_datetimes,
    player_attendance_rankings,
    players_who_missed_recent_practices,
    propagate_series,
    rbihf_standings,
    record_check_in,
    team_attendance_rate,
    team_no_shows,
    timeline_parsers,
)
from .services.attendance import member_attendance_counts
from .services.calendar import add_months, agenda_groups, month_bounds, month_grid, season_grid, week_bounds, week_grid
from .services.competitions import CompetitionFetchError, fetch_game_info
from .services.lineup import cancel_scheduled_publish, notify_dropout, publish_lineup, schedule_lineup_publish, selected_members_by_position, toggle_selection
from .services.officials import (
    OfficialAssignmentError,
    accept_official_signup,
    add_external_official,
    assign_official,
    decline_official_signup,
    eligible_officials,
    needs_official_management,
    set_official_fee,
    sync_official_invites,
)
from .services.rbihf_import import RBIHFImportError, apply_plan, build_plan, extract_team_id, parse_fixtures, suggested_location, suggested_opponent
from .services.referees import (
    RefereeAssignmentError,
    accept_referee_signup,
    add_external_referee,
    assign_referee,
    conflicting_events,
    decline_referee_signup,
    eligible_referees,
    needs_referee_management,
    remove_referee,
    set_referee_fee,
    sync_referee_invites,
)
from .services.tasks import TaskClaimError, claim_task, unclaim_task


class EventsTestBase(TestCase):
    # One club, one current season, one two-player roster: shared by every events test
    # and never edited in place. Tests that do change these rows (deleting Alice, flipping
    # the team to federation-managed) are safe -- setUpTestData hands each test its own
    # copy of the objects, and the surrounding transaction rolls the rows back.
    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        today = timezone.localdate()
        cls.season = Season.objects.create(
            club=cls.club,
            start_date=today - timedelta(days=30),
            end_date=today + timedelta(days=300),
        )
        cls.team = Team.objects.create(club=cls.club, name="First Team", short_name="1st")
        cls.position = Position.objects.create(club=cls.club, name="Forward", short_name="FW")
        cls.alice = Member.objects.create(first_name="Alice", last_name="Ash")
        cls.bob = Member.objects.create(first_name="Bob", last_name="Birch")
        TeamMembership.objects.create(team=cls.team, member=cls.alice, season=cls.season, position=cls.position)
        TeamMembership.objects.create(team=cls.team, member=cls.bob, season=cls.season, position=cls.position)
        cls.future = timezone.now() + timedelta(days=7)

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

    def test_is_home_game(self):
        home_ground = Location.objects.create(club=self.club, name="Home Ground", address="1 St", city="Town", zip_code="1000", country="BE", is_home=True)
        away_ground = Location.objects.create(club=self.club, name="Away Ground", address="2 St", city="Town", zip_code="1000", country="BE")

        home_game = self.make_event(kind=Event.EventKind.GAME, location=home_ground)
        away_game = self.make_event(kind=Event.EventKind.GAME, location=away_ground)
        game_with_no_location = self.make_event(kind=Event.EventKind.GAME)
        training_at_home_ground = self.make_event(kind=Event.EventKind.TRAINING, location=home_ground)

        self.assertTrue(home_game.is_home_game)
        self.assertFalse(away_game.is_home_game)
        self.assertFalse(game_with_no_location.is_home_game)
        self.assertFalse(training_at_home_ground.is_home_game)

    def test_is_home_fixture(self):
        # is_home_fixture is the broader, referee/official-tools-only check --
        # unlike is_home_game, it also covers a home Tournament (which has no
        # single opponent/score of its own, so is_home_game itself deliberately
        # stays Game-only).
        home_ground = Location.objects.create(club=self.club, name="Home Ground", address="1 St", city="Town", zip_code="1000", country="BE", is_home=True)
        away_ground = Location.objects.create(club=self.club, name="Away Ground", address="2 St", city="Town", zip_code="1000", country="BE")

        home_game = self.make_event(kind=Event.EventKind.GAME, location=home_ground)
        home_tournament = self.make_event(kind=Event.EventKind.TOURNAMENT, location=home_ground)
        away_tournament = self.make_event(kind=Event.EventKind.TOURNAMENT, location=away_ground)
        home_training = self.make_event(kind=Event.EventKind.TRAINING, location=home_ground)

        self.assertTrue(home_game.is_home_fixture)
        self.assertTrue(home_tournament.is_home_fixture)
        self.assertFalse(away_tournament.is_home_fixture)
        self.assertFalse(home_training.is_home_fixture)

    def test_a_game_with_no_end_gets_a_two_hour_default_on_save(self):
        game = self.make_event(kind=Event.EventKind.GAME, start=self.future)

        self.assertEqual(game.end, self.future + timedelta(hours=2))

    def test_an_explicit_end_is_not_overridden(self):
        explicit_end = self.future + timedelta(hours=5)

        game = self.make_event(kind=Event.EventKind.GAME, start=self.future, end=explicit_end)

        self.assertEqual(game.end, explicit_end)

    def test_a_non_game_event_is_left_with_no_end(self):
        training = self.make_event(kind=Event.EventKind.TRAINING, start=self.future)

        self.assertIsNone(training.end)

    def test_re_saving_a_game_does_not_move_its_end(self):
        game = self.make_event(kind=Event.EventKind.GAME, start=self.future)
        original_end = game.end

        game.title = "Renamed"
        game.save()

        self.assertEqual(game.end, original_end)


class CompetitionModelTests(EventsTestBase):
    def test_sport_type_defaults_to_other(self):
        competition = Competition.objects.create(name="Local League", module="events.competition.other")

        self.assertEqual(competition.sport_type, Club.SportType.OTHER)

    def test_the_seeded_hockey_competitions_are_backfilled_as_ice_hockey(self):
        # Migration 0019's data migration backfills the two competitions seeded
        # by 0017 (both real ice hockey leagues) rather than leaving them at the
        # generic "other" default.
        for name in ["RBIHF", "CEHL"]:
            with self.subTest(name=name):
                self.assertEqual(Competition.objects.get(name=name).sport_type, Club.SportType.ICE_HOCKEY)

    def test_the_officials_flag_exists_but_is_not_enabled_for_anyone_yet(self):
        # Migration 0031's data migration makes sure the "officials" flag row
        # exists (so a club admin has something to turn on from the control
        # panel's Features page) without switching it on for anyone -- see
        # that migration's own docstring.
        Flag = get_waffle_flag_model()
        flag = Flag.objects.get(name="officials")
        self.assertIsNone(flag.everyone)
        self.assertFalse(flag.clubs.exists())


class EventAdminFormCompetitionTests(EventsTestBase):
    """`competition` is a plain CharField, but the admin should only ever offer
    competitions this club is actually allowed to use -- see events/admin.py."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        Flag = get_waffle_flag_model()
        cls.active_flag = Flag.objects.create(name="active-competition")
        cls.active_flag.clubs.add(cls.club)
        cls.inactive_flag = Flag.objects.create(name="inactive-competition")
        cls.active_competition = Competition.objects.create(name="Active Cup", module="events.competition.active", flag=cls.active_flag)
        cls.inactive_competition = Competition.objects.create(name="Inactive Cup", module="events.competition.inactive", flag=cls.inactive_flag)
        cls.flagless_competition = Competition.objects.create(name="Flagless Cup", module="events.competition.flagless")

    def test_a_flagless_competition_never_appears(self):
        form = EventAdminForm(instance=Event())
        choices = dict(form.fields["competition"].choices)

        self.assertNotIn("Flagless Cup", choices)

    def test_a_brand_new_event_offers_every_flagged_competition_unfiltered(self):
        # The club is unknown until the event is actually saved (see
        # EventClubScopeTests for the same "club_id is None pre-save" timing issue
        # elsewhere), so a new event can't be filtered by club yet -- it falls back
        # to showing everything with a flag rather than crashing or showing nothing.
        form = EventAdminForm(instance=Event())
        choices = dict(form.fields["competition"].choices)

        self.assertIn("Active Cup", choices)
        self.assertIn("Inactive Cup", choices)

    def test_an_existing_event_only_offers_competitions_active_for_its_club(self):
        event = self.make_event(kind=Event.EventKind.GAME)
        form = EventAdminForm(instance=event)
        choices = dict(form.fields["competition"].choices)

        self.assertIn("Active Cup", choices)
        self.assertNotIn("Inactive Cup", choices)
        self.assertNotIn("Flagless Cup", choices)


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

    def test_group_members_are_included(self):
        carol = Member.objects.create(first_name="Carol", last_name="Cedar")
        group = Group.objects.create(club=self.club, name="Committee")
        GroupMembership.objects.create(group=group, member=carol)
        event = self.make_event()
        event.groups.set([group])

        self.assertEqual(self.attendee_ids(event), {carol.id})

    def test_teams_and_groups_combine(self):
        carol = Member.objects.create(first_name="Carol", last_name="Cedar")
        group = Group.objects.create(club=self.club, name="Committee")
        GroupMembership.objects.create(group=group, member=carol)
        event = self.make_event()
        event.teams.set([self.team])
        event.groups.set([group])

        self.assertEqual(self.attendee_ids(event), {self.alice.id, self.bob.id, carol.id})

    def test_club_wide_includes_every_active_member_this_season(self):
        carol = Member.objects.create(first_name="Carol", last_name="Cedar")
        ClubMembership.objects.create(club=self.club, member=carol, season=self.season, status=ClubMembership.StatusChoices.ACTIVE)
        lapsed = Member.objects.create(first_name="Len", last_name="Lapsed")
        ClubMembership.objects.create(club=self.club, member=lapsed, season=self.season, status=ClubMembership.StatusChoices.LAPSED)
        event = self.make_event(club_wide=True)

        self.assertEqual(self.attendee_ids(event), {carol.id})

    def test_club_wide_ignores_teams_and_groups(self):
        # Teams/groups can be left populated on the row without affecting the
        # audience -- validation (EventForm) is what keeps editors from doing
        # this in the UI, but effective_members itself just ignores them.
        carol = Member.objects.create(first_name="Carol", last_name="Cedar")
        ClubMembership.objects.create(club=self.club, member=carol, season=self.season, status=ClubMembership.StatusChoices.ACTIVE)
        event = self.make_event(club_wide=True)
        event.teams.set([self.team])

        self.assertEqual(self.attendee_ids(event), {carol.id})

    def test_club_wide_still_honours_invited_and_excluded(self):
        carol = Member.objects.create(first_name="Carol", last_name="Cedar")
        ClubMembership.objects.create(club=self.club, member=carol, season=self.season, status=ClubMembership.StatusChoices.ACTIVE)
        dave = Member.objects.create(first_name="Dave", last_name="Dogwood")
        event = self.make_event(club_wide=True)
        event.invited_members.set([dave])
        event.excluded_members.set([carol])

        self.assertEqual(self.attendee_ids(event), {dave.id})

    # --- onboarding-requirement gating (club.services.onboarding.blocked_member_ids_for_event) ---
    def make_membership(self, member, **kwargs):
        kwargs.setdefault("status", ClubMembership.StatusChoices.ACTIVE)
        return ClubMembership.objects.create(club=self.club, member=member, season=self.season, **kwargs)

    def test_an_open_blocking_requirement_excludes_the_member_for_that_kind(self):
        self.make_membership(self.alice)
        OnboardingRequirement.objects.create(club=self.club, name="Medical certificate", blocked_event_kinds=["game"])
        event = self.make_event(kind=Event.EventKind.GAME)
        event.teams.set([self.team])

        self.assertEqual(self.attendee_ids(event), {self.bob.id})

    def test_the_same_open_requirement_does_not_block_an_unlisted_kind(self):
        self.make_membership(self.alice)
        OnboardingRequirement.objects.create(club=self.club, name="Medical certificate", blocked_event_kinds=["game"])
        event = self.make_event(kind=Event.EventKind.TRAINING)
        event.teams.set([self.team])

        self.assertEqual(self.attendee_ids(event), {self.alice.id, self.bob.id})

    def test_a_completed_blocking_requirement_stops_excluding_the_member(self):
        membership = self.make_membership(self.alice)
        staff = get_user_model().objects.create_user(email="staff@example.com", password="pw-secret-123")
        requirement = OnboardingRequirement.objects.create(club=self.club, name="Medical certificate", blocked_event_kinds=["game"])
        mark_complete(membership, requirement, user=staff)
        event = self.make_event(kind=Event.EventKind.GAME)
        event.teams.set([self.team])

        self.assertEqual(self.attendee_ids(event), {self.alice.id, self.bob.id})

    def test_a_bypassed_blocking_requirement_also_stops_excluding_the_member(self):
        membership = self.make_membership(self.alice)
        staff = get_user_model().objects.create_user(email="staff@example.com", password="pw-secret-123")
        requirement = OnboardingRequirement.objects.create(club=self.club, name="Medical certificate", blocked_event_kinds=["game"])
        mark_bypassed(membership, requirement, user=staff, note="waived")
        event = self.make_event(kind=Event.EventKind.GAME)
        event.teams.set([self.team])

        self.assertEqual(self.attendee_ids(event), {self.alice.id, self.bob.id})

    def test_an_explicit_invite_overrides_the_block(self):
        self.make_membership(self.alice)
        OnboardingRequirement.objects.create(club=self.club, name="Medical certificate", blocked_event_kinds=["game"])
        event = self.make_event(kind=Event.EventKind.GAME)
        event.teams.set([self.team])
        event.invited_members.set([self.alice])

        self.assertEqual(self.attendee_ids(event), {self.alice.id, self.bob.id})

    def test_a_member_with_no_club_membership_at_all_is_unaffected_either_way(self):
        # Alice/Bob have a TeamMembership but no ClubMembership in the base fixture --
        # blocked_member_ids_for_event has nothing to look up for them, and they were
        # never excluded by it to begin with (this is really a "doesn't crash" check).
        OnboardingRequirement.objects.create(club=self.club, name="Medical certificate", blocked_event_kinds=["game"])
        event = self.make_event(kind=Event.EventKind.GAME)
        event.teams.set([self.team])

        self.assertEqual(self.attendee_ids(event), {self.alice.id, self.bob.id})

    # --- blocked_upcoming_events_for_member (the read-side "why can't I sign up" mirror) ---

    def test_blocked_event_is_reported_with_its_blocking_requirement(self):
        self.make_membership(self.alice)
        requirement = OnboardingRequirement.objects.create(club=self.club, name="Medical certificate", blocked_event_kinds=["game"])
        event = self.make_event(kind=Event.EventKind.GAME)
        event.teams.set([self.team])

        results = blocked_upcoming_events_for_member(self.alice, self.club)

        self.assertEqual(len(results), 1)
        blocked_event, requirements = results[0]
        self.assertEqual(blocked_event, event)
        self.assertEqual(requirements, [requirement])

    def test_an_unblocked_member_reports_nothing(self):
        # Bob has no ClubMembership row at all in the base fixture -- nothing
        # for open_requirements_blocking to find, same as blocked_member_ids_
        # for_event's own "no club membership" case above.
        OnboardingRequirement.objects.create(club=self.club, name="Medical certificate", blocked_event_kinds=["game"])
        event = self.make_event(kind=Event.EventKind.GAME)
        event.teams.set([self.team])

        self.assertEqual(blocked_upcoming_events_for_member(self.bob, self.club), [])

    def test_a_kind_the_requirement_does_not_block_reports_nothing(self):
        self.make_membership(self.alice)
        OnboardingRequirement.objects.create(club=self.club, name="Medical certificate", blocked_event_kinds=["game"])
        event = self.make_event(kind=Event.EventKind.TRAINING)
        event.teams.set([self.team])

        self.assertEqual(blocked_upcoming_events_for_member(self.alice, self.club), [])

    def test_a_resolved_requirement_reports_nothing(self):
        membership = self.make_membership(self.alice)
        staff = get_user_model().objects.create_user(email="staff2@example.com", password="pw-secret-123")
        requirement = OnboardingRequirement.objects.create(club=self.club, name="Medical certificate", blocked_event_kinds=["game"])
        mark_complete(membership, requirement, user=staff)
        event = self.make_event(kind=Event.EventKind.GAME)
        event.teams.set([self.team])

        self.assertEqual(blocked_upcoming_events_for_member(self.alice, self.club), [])

    def test_an_explicit_invite_is_not_reported_as_blocked(self):
        # invited_members bypasses the block entirely -- Alice already has a
        # normal Attendance row for this event, so there's nothing to explain.
        self.make_membership(self.alice)
        OnboardingRequirement.objects.create(club=self.club, name="Medical certificate", blocked_event_kinds=["game"])
        event = self.make_event(kind=Event.EventKind.GAME)
        event.teams.set([self.team])
        event.invited_members.set([self.alice])

        self.assertEqual(blocked_upcoming_events_for_member(self.alice, self.club), [])

    def test_a_past_blocked_event_is_not_reported(self):
        self.make_membership(self.alice)
        OnboardingRequirement.objects.create(club=self.club, name="Medical certificate", blocked_event_kinds=["game"])
        event = self.make_event(kind=Event.EventKind.GAME, start=timezone.now() - timedelta(days=1))
        event.teams.set([self.team])

        self.assertEqual(blocked_upcoming_events_for_member(self.alice, self.club), [])


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


class LineupServiceTests(EventsTestBase):
    """events.services.lineup -- toggle_selection/publish_lineup/
    selected_members_by_position, the write+read path behind coach mode's C3
    screen (mobile/coach_views.py) and the published member-side view
    (mobile/views.py's EventDetailView)."""

    def make_game_with_lineup(self, **event_kwargs):
        event_kwargs.setdefault("kind", Event.EventKind.GAME)
        event = self.make_event(**event_kwargs)
        event.teams.add(self.team)
        lineup = Lineup.objects.create(event=event, team=self.team)
        return event, lineup

    def test_toggle_selection_selects_an_unselected_member(self):
        _event, lineup = self.make_game_with_lineup()

        now_selected = toggle_selection(lineup, self.alice)

        self.assertTrue(now_selected)
        self.assertTrue(LineupSelection.objects.filter(lineup=lineup, member=self.alice).exists())

    def test_toggle_selection_deselects_a_selected_member(self):
        _event, lineup = self.make_game_with_lineup()
        LineupSelection.objects.create(lineup=lineup, member=self.alice)

        now_selected = toggle_selection(lineup, self.alice)

        self.assertFalse(now_selected)
        self.assertFalse(LineupSelection.objects.filter(lineup=lineup, member=self.alice).exists())

    def test_publish_sets_published_at(self):
        _event, lineup = self.make_game_with_lineup()

        publish_lineup(lineup)

        lineup.refresh_from_db()
        self.assertIsNotNone(lineup.published_at)

    def test_publish_clears_a_pending_schedule(self):
        _event, lineup = self.make_game_with_lineup()
        lineup.scheduled_publish_at = timezone.now() + timedelta(days=1)
        lineup.save()

        publish_lineup(lineup)

        lineup.refresh_from_db()
        self.assertIsNone(lineup.scheduled_publish_at)

    def test_schedule_lineup_publish_sets_the_time(self):
        _event, lineup = self.make_game_with_lineup()
        when = timezone.now() + timedelta(days=1)

        schedule_lineup_publish(lineup, when)

        lineup.refresh_from_db()
        self.assertEqual(lineup.scheduled_publish_at, when)
        self.assertIsNone(lineup.published_at)

    def test_cancel_scheduled_publish_clears_the_time(self):
        _event, lineup = self.make_game_with_lineup()
        lineup.scheduled_publish_at = timezone.now() + timedelta(days=1)
        lineup.save()

        cancel_scheduled_publish(lineup)

        lineup.refresh_from_db()
        self.assertIsNone(lineup.scheduled_publish_at)

    def test_publish_selects_picked_members_and_not_selects_the_rest(self):
        event, lineup = self.make_game_with_lineup()
        LineupSelection.objects.create(lineup=lineup, member=self.alice)
        Attendance.objects.update_or_create(event=event, member=self.alice, defaults={"status": Attendance.AttendanceStatus.PRESENT})
        Attendance.objects.update_or_create(event=event, member=self.bob, defaults={"status": Attendance.AttendanceStatus.PRESENT})

        publish_lineup(lineup)

        self.assertEqual(Attendance.objects.get(event=event, member=self.alice).status, Attendance.AttendanceStatus.SELECTED)
        self.assertEqual(Attendance.objects.get(event=event, member=self.bob).status, Attendance.AttendanceStatus.NOT_SELECTED)

    def test_publish_leaves_unavailable_members_untouched(self):
        event, lineup = self.make_game_with_lineup()
        Attendance.objects.update_or_create(event=event, member=self.bob, defaults={"status": Attendance.AttendanceStatus.ABSENT})

        publish_lineup(lineup)

        self.assertEqual(Attendance.objects.get(event=event, member=self.bob).status, Attendance.AttendanceStatus.ABSENT)

    def test_selected_members_by_position_groups_by_the_teams_roster_position(self):
        _event, lineup = self.make_game_with_lineup()
        winger = Position.objects.create(club=self.club, name="Winger", short_name="W", ordering=1)
        defense = Position.objects.create(club=self.club, name="Defense", short_name="D", ordering=2)
        TeamMembership.objects.filter(team=self.team, member=self.alice).update(position=winger)
        TeamMembership.objects.filter(team=self.team, member=self.bob).update(position=defense)
        LineupSelection.objects.create(lineup=lineup, member=self.alice)
        LineupSelection.objects.create(lineup=lineup, member=self.bob)

        categories = selected_members_by_position(lineup)

        self.assertEqual([c["label"] for c in categories], ["Winger", "Defense"])
        self.assertEqual(categories[0]["members"], [self.alice])
        self.assertEqual(categories[1]["members"], [self.bob])

    def test_selected_members_by_position_falls_back_to_no_position_bucket(self):
        _event, lineup = self.make_game_with_lineup()
        guest = Member.objects.create(first_name="Gia", last_name="Guest")
        LineupSelection.objects.create(lineup=lineup, member=guest)

        categories = selected_members_by_position(lineup)

        self.assertEqual(len(categories), 1)
        self.assertEqual(categories[0]["label"], "No position set")
        self.assertEqual(categories[0]["members"], [guest])

    def test_publish_notifies_only_selected_members(self):
        event, lineup = self.make_game_with_lineup()
        LineupSelection.objects.create(lineup=lineup, member=self.alice)
        Attendance.objects.update_or_create(event=event, member=self.alice, defaults={"status": Attendance.AttendanceStatus.PRESENT})
        Attendance.objects.update_or_create(event=event, member=self.bob, defaults={"status": Attendance.AttendanceStatus.PRESENT})

        publish_lineup(lineup)

        notified_member_ids = set(Notification.objects.filter(member__in=[self.alice, self.bob]).values_list("member_id", flat=True))
        self.assertEqual(notified_member_ids, {self.alice.pk})

    def test_republish_does_not_renotify_a_player_still_selected(self):
        event, lineup = self.make_game_with_lineup()
        LineupSelection.objects.create(lineup=lineup, member=self.alice)
        Attendance.objects.update_or_create(event=event, member=self.alice, defaults={"status": Attendance.AttendanceStatus.PRESENT})
        publish_lineup(lineup)
        Notification.objects.filter(member=self.alice).delete()

        publish_lineup(lineup)

        self.assertFalse(Notification.objects.filter(member=self.alice).exists())

    def test_republish_notifies_a_newly_added_player(self):
        event, lineup = self.make_game_with_lineup()
        Attendance.objects.update_or_create(event=event, member=self.alice, defaults={"status": Attendance.AttendanceStatus.PRESENT})
        publish_lineup(lineup)
        Notification.objects.filter(member=self.alice).delete()

        LineupSelection.objects.create(lineup=lineup, member=self.alice)
        publish_lineup(lineup)

        notification = Notification.objects.get(member=self.alice)
        self.assertIn("in the line-up", notification.body)

    def test_republish_notifies_a_dropped_player_with_a_different_message(self):
        event, lineup = self.make_game_with_lineup()
        LineupSelection.objects.create(lineup=lineup, member=self.alice)
        Attendance.objects.update_or_create(event=event, member=self.alice, defaults={"status": Attendance.AttendanceStatus.PRESENT})
        publish_lineup(lineup)
        Notification.objects.filter(member=self.alice).delete()

        LineupSelection.objects.filter(lineup=lineup, member=self.alice).delete()
        publish_lineup(lineup)

        notification = Notification.objects.get(member=self.alice)
        self.assertIn("not in it this time", notification.body)
        self.assertEqual(Attendance.objects.get(event=event, member=self.alice).status, Attendance.AttendanceStatus.NOT_SELECTED)

    def test_notify_dropout_notifies_the_teams_managers(self):
        event, _lineup = self.make_game_with_lineup()
        manager = Member.objects.create(first_name="Cara", last_name="Coach")
        management_position = Position.objects.create(club=self.club, name="Head coach", short_name="HC", staff_position=True, management_position=True)
        StaffAssignment.objects.create(team=self.team, member=manager, season=self.season, position=management_position)

        notify_dropout(event, self.alice, "Twisted an ankle")

        notification = Notification.objects.get(member=manager)
        self.assertIn(self.alice.get_full_name(), notification.body)
        self.assertIn("Twisted an ankle", notification.body)

    def test_notify_dropout_does_not_notify_non_management_staff(self):
        event, _lineup = self.make_game_with_lineup()
        physio = Member.objects.create(first_name="Pat", last_name="Physio")
        physio_position = Position.objects.create(club=self.club, name="Physio", short_name="PH", staff_position=True, management_position=False)
        StaffAssignment.objects.create(team=self.team, member=physio, season=self.season, position=physio_position)

        notify_dropout(event, self.alice, "Twisted an ankle")

        self.assertFalse(Notification.objects.filter(member=physio).exists())


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

    def test_adding_a_group_member_syncs_future_events(self):
        group = Group.objects.create(club=self.club, name="Committee")
        event = self.make_event()
        event.groups.set([group])
        carol = Member.objects.create(first_name="Carol", last_name="Cedar")

        GroupMembership.objects.create(group=group, member=carol)

        self.assertIn(carol.id, self.attendee_ids(event))

    def test_removing_a_group_member_syncs_future_events(self):
        group = Group.objects.create(club=self.club, name="Committee")
        carol = Member.objects.create(first_name="Carol", last_name="Cedar")
        membership = GroupMembership.objects.create(group=group, member=carol)
        event = self.make_event()
        event.groups.set([group])

        membership.delete()

        self.assertNotIn(carol.id, self.attendee_ids(event))

    def test_a_new_active_club_membership_syncs_future_club_wide_events(self):
        event = self.make_event(club_wide=True)
        carol = Member.objects.create(first_name="Carol", last_name="Cedar")

        ClubMembership.objects.create(club=self.club, member=carol, season=self.season, status=ClubMembership.StatusChoices.ACTIVE)

        self.assertIn(carol.id, self.attendee_ids(event))

    def test_a_club_membership_lapsing_syncs_future_club_wide_events(self):
        carol = Member.objects.create(first_name="Carol", last_name="Cedar")
        membership = ClubMembership.objects.create(club=self.club, member=carol, season=self.season, status=ClubMembership.StatusChoices.ACTIVE)
        event = self.make_event(club_wide=True)

        membership.status = ClubMembership.StatusChoices.LAPSED
        membership.save()

        self.assertNotIn(carol.id, self.attendee_ids(event))

    def test_roster_change_leaves_past_events_untouched(self):
        past = self.make_event(start=timezone.now() - timedelta(days=1))
        past.teams.add(self.team)
        Attendance.objects.create(event=past, member=self.alice)

        TeamMembership.objects.get(team=self.team, member=self.alice).delete()

        self.assertTrue(past.attendances.filter(member=self.alice).exists())

    def test_joining_a_team_notifies_the_member_of_new_events(self):
        event = self.make_event()
        event.teams.set([self.team])
        dave = Member.objects.create(first_name="Dave", last_name="Dogwood")

        TeamMembership.objects.create(team=self.team, member=dave, season=self.season, position=self.position)

        notification = Notification.objects.get(member=dave)
        self.assertEqual(notification.title, "New events on your calendar")
        self.assertIn("1 new event", notification.body)

    def test_joining_a_team_with_several_upcoming_events_sends_one_summary_notification(self):
        first = self.make_event(title="First")
        first.teams.set([self.team])
        second = self.make_event(title="Second", start=self.future + timedelta(days=1))
        second.teams.set([self.team])
        dave = Member.objects.create(first_name="Dave", last_name="Dogwood")

        TeamMembership.objects.create(team=self.team, member=dave, season=self.season, position=self.position)

        self.assertEqual(Notification.objects.filter(member=dave).count(), 1)
        notification = Notification.objects.get(member=dave)
        self.assertIn("2 new events", notification.body)

    def test_joining_a_team_with_no_upcoming_events_does_not_notify(self):
        dave = Member.objects.create(first_name="Dave", last_name="Dogwood")

        TeamMembership.objects.create(team=self.team, member=dave, season=self.season, position=self.position)

        self.assertFalse(Notification.objects.filter(member=dave).exists())

    def test_leaving_a_team_does_not_notify(self):
        event = self.make_event()
        event.teams.set([self.team])

        TeamMembership.objects.get(team=self.team, member=self.alice).delete()

        self.assertFalse(Notification.objects.filter(member=self.alice).exists())

    def test_editing_a_membership_field_does_not_notify(self):
        event = self.make_event()
        event.teams.set([self.team])
        membership = TeamMembership.objects.get(team=self.team, member=self.alice)

        membership.jersey_number = 42
        membership.save()

        self.assertFalse(Notification.objects.filter(member=self.alice).exists())

    def test_joining_a_group_notifies_the_member_of_new_events(self):
        group = Group.objects.create(club=self.club, name="Committee")
        event = self.make_event()
        event.groups.set([group])
        carol = Member.objects.create(first_name="Carol", last_name="Cedar")

        GroupMembership.objects.create(group=group, member=carol)

        notification = Notification.objects.get(member=carol)
        self.assertIn("1 new event", notification.body)


class RecurrenceTestBase(EventsTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.anchor = (timezone.now() + timedelta(days=1)).replace(microsecond=0)

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

    def test_a_later_run_only_adds_whats_new_since_the_last_occurrence(self):
        # No COUNT -- this is the open-ended shape the resume path exists for.
        series = self.make_series(rrule="FREQ=WEEKLY")

        first_batch = generate_occurrences(series, self.anchor + timedelta(days=20))
        self.assertEqual(len(first_batch), 3)  # anchor, +1w, +2w

        second_batch = generate_occurrences(series, self.anchor + timedelta(days=40))

        self.assertEqual(len(second_batch), 3)  # +3w, +4w, +5w -- not the first 3 again
        self.assertEqual(series.occurrences.count(), 6)
        self.assertEqual(len(set(series.occurrences.values_list("start", flat=True))), 6)  # no duplicates

    def test_resume_stays_cheap_regardless_of_how_much_history_already_exists(self):
        series = self.make_series(rrule="FREQ=WEEKLY")
        # 200 weeks of "existing" history, well past what a from-scratch walk
        # to `until` below would ever touch -- created directly, bypassing
        # generate_occurrences, purely to simulate an old series.
        Event.objects.bulk_create([Event(club=self.club, series=series, start=self.anchor + timedelta(weeks=week), title="Weekly Training", kind=Event.EventKind.TRAINING) for week in range(200)])

        # A generous ceiling, not an exact count -- apply_template's own m2m/
        # attendance-sync bookkeeping costs a real handful of queries per
        # *newly created* event regardless of this optimization. The point
        # here is that cost tracks len(created) (3 -- weeks 199 already
        # exists, 200/201/202 are new), not the 200 weeks of pre-existing
        # history: a regression back to re-walking all of it and re-creating
        # (or re-diffing against) every prior week would blow well past this.
        with CaptureQueriesContext(connection) as queries:
            created = generate_occurrences(series, self.anchor + timedelta(weeks=202))
        self.assertLess(len(queries), 150)

        self.assertEqual(len(created), 3)  # weeks 200, 201, 202 -- not a re-walk of 0-199

    def test_a_count_bounded_rule_is_never_resumed_and_still_stops_at_count(self):
        series = self.make_series(rrule="FREQ=WEEKLY;COUNT=4")

        generate_occurrences(series, self.anchor + timedelta(days=10))  # partial window
        generate_occurrences(series, self.anchor + timedelta(days=90))  # much further out

        self.assertEqual(series.occurrences.count(), 4)

    def test_resume_preserves_interval_spacing_for_a_biweekly_rule(self):
        series = self.make_series(rrule="FREQ=WEEKLY;INTERVAL=2")

        generate_occurrences(series, self.anchor + timedelta(days=20))
        generate_occurrences(series, self.anchor + timedelta(days=60))

        starts = list(series.occurrences.order_by("start").values_list("start", flat=True))
        gaps = {b - a for a, b in itertools.pairwise(starts)}
        self.assertEqual(gaps, {timedelta(weeks=2)})


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
        self.assertIn("Generated 4 occurrence(s)", out.getvalue())


class EventClubScopeTests(EventsTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.other = Club.objects.create(name="Rival FC", slug="rival-fc")
        today = timezone.localdate()
        cls.other_season = Season.objects.create(club=cls.other, start_date=today - timedelta(days=30), end_date=today + timedelta(days=300))
        cls.other_location = Location.objects.create(club=cls.other, name="Arena", address="1 St", city="Town", zip_code="1000", country="BE")
        cls.other_opponent = Opponent.objects.create(club=cls.other, name="Rivals")
        cls.other_team = Team.objects.create(club=cls.other, name="First", short_name="1")

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

    def test_member_attendance_counts_no_shows_matches_team_no_shows_definition(self):
        event = self.make_past_training(1)
        attendance = self.set_status(event, self.alice, Attendance.AttendanceStatus.PRESENT)
        record_check_in(attendance, showed_up=False)

        counts = member_attendance_counts(self.alice, self.season)

        self.assertEqual(counts["no_shows"], 1)
        # Still counted as present -- present/absent read the RSVP only, not showed_up.
        self.assertEqual(counts["present"], 1)

    def test_member_attendance_counts_no_shows_ignores_an_unchecked_present_rsvp(self):
        event = self.make_past_training(1)
        self.set_status(event, self.alice, Attendance.AttendanceStatus.PRESENT)

        counts = member_attendance_counts(self.alice, self.season)

        self.assertEqual(counts["no_shows"], 0)

    def test_member_attendance_counts_no_shows_ignores_a_confirmed_check_in(self):
        event = self.make_past_training(1)
        attendance = self.set_status(event, self.alice, Attendance.AttendanceStatus.PRESENT)
        record_check_in(attendance, showed_up=True)

        counts = member_attendance_counts(self.alice, self.season)

        self.assertEqual(counts["no_shows"], 0)


RBIHF_TEAM_ID = "4460"
RBIHF_TEAM_NAME = "Sportoase Antwerp Phantoms"


def rbihf_sample_html(rows):
    """A trimmed stand-in for an RBIHF team page (https://www.rbihf.be/league/team/<id>)
    -- structure confirmed against the real page while designing this feature.
    ``rows`` is a list of dicts: game_id, date ("YYYY-MM-DD"), hour ("HH:MM"),
    venue, home_id, home_name, visit_id, visit_name."""
    row_html = "".join(
        f'<tr><td class="game-nr"><a href="/game/{r["game_id"]}" title="Game {r["game_id"]}">{r["game_id"]}</a></td>'
        f'<td class="date">{r["date"]}</td><td class="hour">{r["hour"]}</td><td>{r["venue"]}</td>'
        f'<td><a href="/league/team/{r["home_id"]}" title="{r["home_name"]}">{r["home_name"]}</a></td>'
        f'<td><a href="/league/team/{r["visit_id"]}" title="{r["visit_name"]}">{r["visit_name"]}</a></td></tr>'
        for r in rows
    )
    return f"""<html><body>
    <div class="block"><div class="block-header"><h2>{RBIHF_TEAM_NAME}</h2></div></div>
    <div class="block"><div class="block-header"><h2 id="games-upcoming">Upcoming games</h2></div>
    <div class="block-content"><table>
    <tr><th class="game-nr">#</th><th class="date">Date</th><th class="hour">Hour</th><th>Location</th><th>Home</th><th>Visit</th></tr>
    {row_html}
    </table></div></div>
    </body></html>"""


class RBIHFParseFixturesTests(TestCase):
    """Pure parsing, no network, no DB -- events.services.rbihf_import.parse_fixtures."""

    def test_parses_team_name_and_fixtures(self):
        html = rbihf_sample_html(
            [
                {"game_id": "5002", "date": "2026-09-12", "hour": "12:15", "venue": "Deurne", "home_id": RBIHF_TEAM_ID, "home_name": RBIHF_TEAM_NAME, "visit_id": "4464", "visit_name": "Amsterdam Tigers"},
                {"game_id": "5010", "date": "2026-09-20", "hour": "18:30", "venue": "Antwerp Ice Rink", "home_id": "4500", "home_name": "Brussels Bears", "visit_id": RBIHF_TEAM_ID, "visit_name": RBIHF_TEAM_NAME},
            ]
        )

        team_name, fixtures = parse_fixtures(html, RBIHF_TEAM_ID)

        self.assertEqual(team_name, RBIHF_TEAM_NAME)
        self.assertEqual(len(fixtures), 2)

    def test_resolves_home_fixture_correctly(self):
        html = rbihf_sample_html([{"game_id": "5002", "date": "2026-09-12", "hour": "12:15", "venue": "Deurne", "home_id": RBIHF_TEAM_ID, "home_name": RBIHF_TEAM_NAME, "visit_id": "4464", "visit_name": "Amsterdam Tigers"}])

        _team_name, fixtures = parse_fixtures(html, RBIHF_TEAM_ID)

        self.assertTrue(fixtures[0].is_home)
        self.assertEqual(fixtures[0].opponent_name, "Amsterdam Tigers")
        self.assertEqual(fixtures[0].venue_text, "Deurne")
        self.assertEqual(fixtures[0].external_game_id, "5002")

    def test_resolves_away_fixture_correctly(self):
        html = rbihf_sample_html([{"game_id": "5010", "date": "2026-09-20", "hour": "18:30", "venue": "Antwerp Ice Rink", "home_id": "4500", "home_name": "Brussels Bears", "visit_id": RBIHF_TEAM_ID, "visit_name": RBIHF_TEAM_NAME}])

        _team_name, fixtures = parse_fixtures(html, RBIHF_TEAM_ID)

        self.assertFalse(fixtures[0].is_home)
        self.assertEqual(fixtures[0].opponent_name, "Brussels Bears")

    def test_a_row_where_neither_side_matches_is_skipped(self):
        html = rbihf_sample_html([{"game_id": "9999", "date": "2026-09-20", "hour": "18:30", "venue": "Elsewhere", "home_id": "1", "home_name": "Team One", "visit_id": "2", "visit_name": "Team Two"}])

        _team_name, fixtures = parse_fixtures(html, RBIHF_TEAM_ID)

        self.assertEqual(fixtures, [])

    def test_missing_games_table_raises(self):
        with self.assertRaises(RBIHFImportError):
            parse_fixtures("<html><body><h2>Some Team</h2></body></html>", RBIHF_TEAM_ID)


class RBIHFExtractTeamIdTests(TestCase):
    def test_accepts_the_documented_shape(self):
        self.assertEqual(extract_team_id("https://www.rbihf.be/league/team/4460"), "4460")

    def test_accepts_without_www(self):
        self.assertEqual(extract_team_id("https://rbihf.be/league/team/4460"), "4460")

    def test_rejects_a_different_host(self):
        with self.assertRaises(RBIHFImportError):
            extract_team_id("https://evil.example.com/league/team/4460")

    def test_rejects_a_different_path(self):
        with self.assertRaises(RBIHFImportError):
            extract_team_id("https://www.rbihf.be/leagues")


class RBIHFImportPlanTests(EventsTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.home_location = Location.objects.create(club=cls.club, name="Home Arena", address="1 St", city="Antwerp", zip_code="1000", country="BE", is_home=True)
        cls.away_location = Location.objects.create(club=cls.club, name="Deurne Ice Hall", address="2 St", city="Deurne", zip_code="2100", country="BE")

    def home_fixture_row(self, game_id="5002", date=None):
        # A fixed future offset, not a hardcoded calendar date: build_plan's own
        # to_delete logic (events.services.rbihf_import) compares a fixture's
        # start against timezone.now(), so a literal date eventually becomes
        # "today" (or the past) and silently flips these tests' own "this is a
        # future fixture" premise depending on what day -- and time -- they
        # happen to run.
        date = date or (timezone.now() + timedelta(days=7)).strftime("%Y-%m-%d")
        return {"game_id": game_id, "date": date, "hour": "12:15", "venue": "Deurne", "home_id": RBIHF_TEAM_ID, "home_name": RBIHF_TEAM_NAME, "visit_id": "4464", "visit_name": "Amsterdam Tigers"}

    # -- suggested_location --------------------------------------------------

    def test_suggested_location_for_a_home_fixture_is_the_clubs_home_location(self):
        _team_name, fixtures = parse_fixtures(rbihf_sample_html([self.home_fixture_row()]), RBIHF_TEAM_ID)

        self.assertEqual(suggested_location(self.club, fixtures[0]), self.home_location)

    def test_suggested_location_for_an_away_fixture_matches_by_city(self):
        row = {"game_id": "5010", "date": "2026-09-20", "hour": "18:30", "venue": "Deurne", "home_id": "4500", "home_name": "Brussels Bears", "visit_id": RBIHF_TEAM_ID, "visit_name": RBIHF_TEAM_NAME}
        _team_name, fixtures = parse_fixtures(rbihf_sample_html([row]), RBIHF_TEAM_ID)

        self.assertEqual(suggested_location(self.club, fixtures[0]), self.away_location)

    def test_suggested_location_is_none_when_nothing_matches(self):
        row = {"game_id": "5010", "date": "2026-09-20", "hour": "18:30", "venue": "Nowhere Familiar", "home_id": "4500", "home_name": "Brussels Bears", "visit_id": RBIHF_TEAM_ID, "visit_name": RBIHF_TEAM_NAME}
        _team_name, fixtures = parse_fixtures(rbihf_sample_html([row]), RBIHF_TEAM_ID)

        self.assertIsNone(suggested_location(self.club, fixtures[0]))

    # -- suggested_opponent ---------------------------------------------------

    def test_suggested_opponent_matches_case_insensitively(self):
        opponent = Opponent.objects.create(club=self.club, name="AMSTERDAM TIGERS")
        _team_name, fixtures = parse_fixtures(rbihf_sample_html([self.home_fixture_row()]), RBIHF_TEAM_ID)

        self.assertEqual(suggested_opponent(self.club, fixtures[0]), opponent)

    def test_suggested_opponent_is_none_when_nothing_matches(self):
        _team_name, fixtures = parse_fixtures(rbihf_sample_html([self.home_fixture_row()]), RBIHF_TEAM_ID)

        self.assertIsNone(suggested_opponent(self.club, fixtures[0]))

    # -- build_plan ------------------------------------------------------------

    def test_a_new_fixture_is_planned_as_a_create(self):
        html = rbihf_sample_html([self.home_fixture_row()])

        plan = build_plan(self.club, self.team, RBIHF_TEAM_ID, html)

        self.assertEqual(len(plan.to_create), 1)
        self.assertEqual(plan.to_create[0].fixture.external_game_id, "5002")
        self.assertEqual(plan.to_create[0].suggested_location, self.home_location)

    def test_an_identical_existing_fixture_is_unchanged(self):
        html = rbihf_sample_html([self.home_fixture_row()])
        plan = build_plan(self.club, self.team, RBIHF_TEAM_ID, html)
        apply_plan(plan, {})

        plan_again = build_plan(self.club, self.team, RBIHF_TEAM_ID, html)

        self.assertEqual(plan_again.to_create, [])
        self.assertEqual(plan_again.to_update, [])
        self.assertEqual(plan_again.unchanged_count, 1)

    def test_a_changed_fixture_is_planned_as_an_update_with_a_diff(self):
        html = rbihf_sample_html([self.home_fixture_row()])
        plan = build_plan(self.club, self.team, RBIHF_TEAM_ID, html)
        apply_plan(plan, {})

        changed_row = self.home_fixture_row()
        changed_row["hour"] = "20:00"  # same game id, different time
        changed_html = rbihf_sample_html([changed_row])

        plan_again = build_plan(self.club, self.team, RBIHF_TEAM_ID, changed_html)

        self.assertEqual(len(plan_again.to_update), 1)
        self.assertIn("start", plan_again.to_update[0].changes)

    def test_a_future_fixture_no_longer_listed_is_planned_for_deletion(self):
        html = rbihf_sample_html([self.home_fixture_row()])
        plan = build_plan(self.club, self.team, RBIHF_TEAM_ID, html)
        apply_plan(plan, {})

        plan_again = build_plan(self.club, self.team, RBIHF_TEAM_ID, rbihf_sample_html([]))

        self.assertEqual(len(plan_again.to_delete), 1)
        self.assertEqual(plan_again.to_delete[0].external_game_id, "5002")

    def test_a_past_fixture_no_longer_listed_is_not_deleted(self):
        # "Upcoming games" naturally stops listing a game once it's happened --
        # that's not a signal it was cancelled.
        past_row = self.home_fixture_row(date="2020-01-01")
        html = rbihf_sample_html([past_row])
        plan = build_plan(self.club, self.team, RBIHF_TEAM_ID, html)
        apply_plan(plan, {})

        plan_again = build_plan(self.club, self.team, RBIHF_TEAM_ID, rbihf_sample_html([]))

        self.assertEqual(plan_again.to_delete, [])

    def test_a_different_competitions_fixture_for_the_same_team_is_not_deleted(self):
        # Two RBIHF pages for the one team (Division 1, Cup) -- importing
        # "Cup" must not wipe out "Division 1"'s already-imported games just
        # because they're absent from the Cup page's own listing.
        division_html = rbihf_sample_html([self.home_fixture_row(game_id="5002")])
        division_plan = build_plan(self.club, self.team, RBIHF_TEAM_ID, division_html, "Division 1")
        apply_plan(division_plan, {})

        cup_team_id = "9999"
        cup_row = self.home_fixture_row(game_id="7001")
        cup_row["home_id"] = cup_team_id
        cup_html = rbihf_sample_html([cup_row])
        cup_plan = build_plan(self.club, self.team, cup_team_id, cup_html, "Cup")

        self.assertEqual(cup_plan.to_delete, [])
        self.assertEqual(len(cup_plan.to_create), 1)

        apply_plan(cup_plan, {})

        self.assertTrue(Event.objects.filter(club=self.club, external_game_id="5002").exists())
        division_event = Event.objects.get(club=self.club, external_game_id="5002")
        self.assertEqual(division_event.external_source_id, RBIHF_TEAM_ID)
        self.assertEqual(division_event.competition_label, "Division 1")
        cup_event = Event.objects.get(club=self.club, external_game_id="7001")
        self.assertEqual(cup_event.external_source_id, cup_team_id)
        self.assertEqual(cup_event.competition_label, "Cup")

    def test_a_legacy_event_with_no_source_id_is_still_matched_by_game_id_not_duplicated(self):
        html = rbihf_sample_html([self.home_fixture_row()])
        plan = build_plan(self.club, self.team, RBIHF_TEAM_ID, html)
        apply_plan(plan, {})
        # Simulate a row imported before external_source_id existed.
        Event.objects.filter(club=self.club, external_game_id="5002").update(external_source_id="")

        plan_again = build_plan(self.club, self.team, RBIHF_TEAM_ID, html, "Division 1")

        self.assertEqual(plan_again.to_create, [])
        self.assertEqual(len(plan_again.to_update), 1)
        apply_plan(plan_again, {})

        self.assertEqual(Event.objects.filter(club=self.club, external_game_id="5002").count(), 1)
        healed = Event.objects.get(club=self.club, external_game_id="5002")
        self.assertEqual(healed.external_source_id, RBIHF_TEAM_ID)
        self.assertEqual(healed.competition_label, "Division 1")

    def test_a_future_fixture_missing_from_a_differently_sourced_competition_is_protected_from_deletion(self):
        # A known, different source id is never a delete candidate, even when
        # the other competition's own scrape has moved on to only listing
        # unrelated games.
        cup_team_id = "9999"
        cup_row = self.home_fixture_row(game_id="7001")
        cup_row["home_id"] = cup_team_id
        cup_plan = build_plan(self.club, self.team, cup_team_id, rbihf_sample_html([cup_row]), "Cup")
        apply_plan(cup_plan, {})

        division_plan_again = build_plan(self.club, self.team, RBIHF_TEAM_ID, rbihf_sample_html([]), "Division 1")

        self.assertEqual(division_plan_again.to_delete, [])

    def test_a_fixture_for_a_different_team_is_not_touched(self):
        other_team = Team.objects.create(club=self.club, name="Second Team", short_name="2nd")
        html = rbihf_sample_html([self.home_fixture_row()])
        plan = build_plan(self.club, other_team, RBIHF_TEAM_ID, html)
        apply_plan(plan, {})

        # Re-running for *our* team should see no existing RBIHF events at all
        # for it -- the one that exists belongs to other_team.
        plan_for_our_team = build_plan(self.club, self.team, RBIHF_TEAM_ID, html)

        self.assertEqual(len(plan_for_our_team.to_create), 1)

    # -- apply_plan --------------------------------------------------------

    def test_apply_plan_creates_events_with_the_expected_fields(self):
        html = rbihf_sample_html([self.home_fixture_row()])
        plan = build_plan(self.club, self.team, RBIHF_TEAM_ID, html)

        result = apply_plan(plan, {"5002": str(self.home_location.pk)})

        self.assertEqual(result, {"created": 1, "updated": 0, "deleted": 0})
        event = Event.objects.get(club=self.club, external_game_id="5002")
        self.assertEqual(event.kind, Event.EventKind.GAME)
        self.assertEqual(event.competition, "RBIHF")
        self.assertEqual(event.opponent.name, "Amsterdam Tigers")
        self.assertEqual(event.location, self.home_location)
        self.assertIn(self.team, event.teams.all())
        self.assertTrue(event.is_home_game)

    def test_apply_plan_ignores_a_location_id_from_another_club(self):
        other_club = Club.objects.create(name="Rival FC", slug="rival-fc")
        foreign_location = Location.objects.create(club=other_club, name="Not ours", address="x", city="x", zip_code="x", country="BE")
        html = rbihf_sample_html([self.home_fixture_row()])
        plan = build_plan(self.club, self.team, RBIHF_TEAM_ID, html)

        apply_plan(plan, {"5002": str(foreign_location.pk)})

        event = Event.objects.get(club=self.club, external_game_id="5002")
        self.assertIsNone(event.location)

    def test_apply_plan_uses_an_explicitly_chosen_opponent_instead_of_the_scraped_name(self):
        renamed = Opponent.objects.create(club=self.club, name="Amsterdam Tigers HC")
        html = rbihf_sample_html([self.home_fixture_row()])
        plan = build_plan(self.club, self.team, RBIHF_TEAM_ID, html)

        apply_plan(plan, {}, {"5002": str(renamed.pk)})

        event = Event.objects.get(club=self.club, external_game_id="5002")
        self.assertEqual(event.opponent, renamed)
        self.assertEqual(Opponent.objects.filter(club=self.club, name="Amsterdam Tigers").count(), 0)

    def test_apply_plan_ignores_an_opponent_id_from_another_club(self):
        other_club = Club.objects.create(name="Rival FC", slug="rival-fc")
        foreign_opponent = Opponent.objects.create(club=other_club, name="Not ours")
        html = rbihf_sample_html([self.home_fixture_row()])
        plan = build_plan(self.club, self.team, RBIHF_TEAM_ID, html)

        apply_plan(plan, {}, {"5002": str(foreign_opponent.pk)})

        event = Event.objects.get(club=self.club, external_game_id="5002")
        # Falls back to find-or-create by the scraped name, not the foreign row.
        self.assertEqual(event.opponent.name, "Amsterdam Tigers")

    def test_apply_plan_preserves_a_previously_chosen_opponent_on_update(self):
        renamed = Opponent.objects.create(club=self.club, name="Amsterdam Tigers HC")
        html = rbihf_sample_html([self.home_fixture_row()])
        plan = build_plan(self.club, self.team, RBIHF_TEAM_ID, html)
        apply_plan(plan, {}, {"5002": str(renamed.pk)})

        # Re-run with a changed start time (something else triggers the update).
        # build_plan should suggest (pre-select) the event's existing opponent
        # rather than re-guessing from the scraped name -- a real <select>
        # always resubmits whichever option is pre-selected, so that's what's
        # passed to apply_plan here too, not a blank dict.
        changed_row = self.home_fixture_row()
        changed_row["hour"] = "20:00"
        plan_again = build_plan(self.club, self.team, RBIHF_TEAM_ID, rbihf_sample_html([changed_row]))
        self.assertEqual(plan_again.to_update[0].suggested_opponent, renamed)
        apply_plan(plan_again, {}, {"5002": str(renamed.pk)})

        event = Event.objects.get(club=self.club, external_game_id="5002")
        self.assertEqual(event.opponent, renamed)

    def test_apply_plan_updates_only_start_opponent_and_location(self):
        html = rbihf_sample_html([self.home_fixture_row()])
        plan = build_plan(self.club, self.team, RBIHF_TEAM_ID, html)
        apply_plan(plan, {})
        event = Event.objects.get(club=self.club, external_game_id="5002")
        event.end = self.future
        event.gathering = self.future
        event.score_for = 3
        event.save()

        changed_row = self.home_fixture_row()
        changed_row["hour"] = "20:00"
        plan_again = build_plan(self.club, self.team, RBIHF_TEAM_ID, rbihf_sample_html([changed_row]))
        apply_plan(plan_again, {})

        event.refresh_from_db()
        self.assertEqual(timezone.localtime(event.start).strftime("%H:%M"), "20:00")
        self.assertIsNotNone(event.end)
        self.assertIsNotNone(event.gathering)
        self.assertEqual(event.score_for, 3)

    def test_apply_plan_deletes_only_whats_in_to_delete(self):
        html = rbihf_sample_html([self.home_fixture_row()])
        plan = build_plan(self.club, self.team, RBIHF_TEAM_ID, html)
        apply_plan(plan, {})

        plan_again = build_plan(self.club, self.team, RBIHF_TEAM_ID, rbihf_sample_html([]))
        result = apply_plan(plan_again, {})

        self.assertEqual(result, {"created": 0, "updated": 0, "deleted": 1})
        self.assertFalse(Event.objects.filter(club=self.club, external_game_id="5002").exists())


DFEL_TEAM_ID = "70779"
DFEL_DIVISION_ID = "21691"
DFEL_SOURCE_ID = "70779/21691"
DFEL_TEAM_NAME = "Sharks Woman Mechelen"
DFEL_TEAM_PAGE_HTML = """<html><body><div id="team"></div><script>
var cfg = JSON.parse('{\\u0022domNode\\u0022: \\u0022#team\\u0022, \\u0022divisionId\\u0022: \\u002221691\\u0022, \\u0022apiKey\\u0022: \\u0022abc123def456\\u0022, \\u0022sport\\u0022: \\u0022icehockey\\u0022}');
</script></body></html>"""


def dfel_row(game_id, *, days=7, home_id=DFEL_TEAM_ID, home_name=DFEL_TEAM_NAME, away_id="70757", away_name="FASS Berlin", home_score=0, away_score=0, status=0, ended=False, city="Dinslaken", longname="Dinslaken - Eishalle"):
    """One hockeydata Schedule row, trimmed to the fields dfel_import reads --
    shape confirmed against a live response while designing this feature.
    ``days`` is relative to today (at a fixed 18:00 UTC, so two calls for the
    same ``days`` agree to the millisecond), so "upcoming" stays stable
    whenever this runs."""
    start = (timezone.now() + timedelta(days=days)).astimezone(UTC).replace(hour=18, minute=0, second=0, microsecond=0)
    address = json.dumps({"headline1": "Eishalle", "street1": "Some Straße 1", "zipcode": "46535", "city": city, "country": "GER"}) if city is not None else ""
    return {
        "id": game_id,
        "divisionId": int(DFEL_DIVISION_ID),
        "scheduledGameStart": timezone.localtime(start).strftime("%Y-%m-%dT%H:%M:%S"),
        "gameUtcTimestamp": int(start.timestamp() * 1000),
        "homeTeamId": int(home_id),
        "homeTeamLongName": home_name,
        "awayTeamId": int(away_id),
        "awayTeamLongName": away_name,
        "homeTeamScore": home_score,
        "awayTeamScore": away_score,
        "gameStatus": status,
        "gameHasEnded": ended,
        "labels": ["LIVE"] if status == 1 else [],
        "location": {"id": 1, "longname": longname, "shortname": "Eishalle", "address": address},
        "dateIsToBeDetermined": False,
    }


def dfel_schedule(rows):
    return json.dumps({"statusId": 1, "statusMsg": "Ok", "data": {"rows": rows}})


def dfel_response(text, status_code=200):
    return mock.Mock(status_code=status_code, text=text)


class DFELExtractIdsTests(TestCase):
    def test_a_team_page_url_yields_team_and_division(self):
        for url in ["https://ehv-nrw.de/leagues/team/70779/21691/", "https://www.ehv-nrw.de/leagues/team/70779/21691", "  https://ehv-nrw.de/leagues/team/70779/21691/  "]:
            with self.subTest(url=url):
                self.assertEqual(dfel_import.extract_ids(url), ("70779", "21691"))

    def test_a_non_matching_url_is_rejected(self):
        for url in ["https://ehv-nrw.de/leagues/team/70779/", "https://evil.example.com/leagues/team/70779/21691/", "https://www.rbihf.be/league/team/4460", ""]:
            with self.subTest(url=url), self.assertRaises(dfel_import.DFELImportError):
                dfel_import.extract_ids(url)

    def test_source_id_joins_team_and_division(self):
        self.assertEqual(dfel_import.source_id("70779", "21691"), DFEL_SOURCE_ID)


class DFELFetchScheduleTests(TestCase):
    def test_the_api_key_is_read_from_the_escaped_widget_config(self):
        self.assertEqual(dfel_import.extract_api_key(DFEL_TEAM_PAGE_HTML), "abc123def456")

    def test_the_api_key_is_also_found_with_plain_quotes(self):
        self.assertEqual(dfel_import.extract_api_key('{"apiKey": "f4b71c87"}'), "f4b71c87")

    def test_a_page_without_an_api_key_is_an_error(self):
        with self.assertRaises(dfel_import.DFELImportError):
            dfel_import.extract_api_key("<html></html>")

    def test_fetch_schedule_unwraps_the_jsonp_callback(self):
        schedule = dfel_schedule([dfel_row("g1")])
        with mock.patch("events.services.dfel_import.requests.get", side_effect=[dfel_response(DFEL_TEAM_PAGE_HTML), dfel_response(f"rosterchief({schedule});")]) as mock_get:
            raw_json = dfel_import.fetch_schedule(DFEL_TEAM_ID, DFEL_DIVISION_ID)

        self.assertEqual(json.loads(raw_json), json.loads(schedule))
        page_call, schedule_call = mock_get.call_args_list
        self.assertEqual(page_call.args[0], "https://ehv-nrw.de/leagues/team/70779/21691/")
        self.assertEqual(schedule_call.args[0], dfel_import.SCHEDULE_URL)
        self.assertEqual(schedule_call.kwargs["params"]["apiKey"], "abc123def456")
        self.assertEqual(schedule_call.kwargs["params"]["divisionId"], DFEL_DIVISION_ID)

    def test_a_network_error_is_a_dfel_import_error(self):
        with mock.patch("events.services.dfel_import.requests.get", side_effect=requests.ConnectionError("boom")), self.assertRaises(dfel_import.DFELImportError):
            dfel_import.fetch_schedule(DFEL_TEAM_ID, DFEL_DIVISION_ID)

    def test_an_http_error_is_a_dfel_import_error(self):
        with mock.patch("events.services.dfel_import.requests.get", side_effect=[dfel_response(DFEL_TEAM_PAGE_HTML), dfel_response("nope", status_code=500)]), self.assertRaises(dfel_import.DFELImportError):
            dfel_import.fetch_schedule(DFEL_TEAM_ID, DFEL_DIVISION_ID)

    def test_invalid_json_is_a_dfel_import_error(self):
        with mock.patch("events.services.dfel_import.requests.get", side_effect=[dfel_response(DFEL_TEAM_PAGE_HTML), dfel_response("rosterchief(<html>oops</html>)")]), self.assertRaises(dfel_import.DFELImportError):
            dfel_import.fetch_schedule(DFEL_TEAM_ID, DFEL_DIVISION_ID)

    def test_a_non_ok_status_is_a_dfel_import_error(self):
        body = json.dumps({"statusId": 0, "statusMsg": "Invalid apiKey"})
        with mock.patch("events.services.dfel_import.requests.get", side_effect=[dfel_response(DFEL_TEAM_PAGE_HTML), dfel_response(f"rosterchief({body})")]):
            with self.assertRaisesMessage(dfel_import.DFELImportError, "Invalid apiKey"):
                dfel_import.fetch_schedule(DFEL_TEAM_ID, DFEL_DIVISION_ID)


class DFELParseFixturesTests(TestCase):
    def test_home_and_away_games_are_detected_with_the_right_opponent(self):
        rows = [dfel_row("home"), dfel_row("away", days=14, home_id="70774", home_name='KEC "Die Haie"', away_id=DFEL_TEAM_ID, away_name=DFEL_TEAM_NAME, city="Köln")]
        team_name, fixtures = dfel_import.parse_fixtures(dfel_schedule(rows), DFEL_TEAM_ID)

        self.assertEqual(team_name, DFEL_TEAM_NAME)
        home, away = fixtures
        self.assertEqual((home.external_game_id, home.is_home, home.opponent_name, home.venue_text), ("home", True, "FASS Berlin", "Dinslaken"))
        self.assertEqual((away.external_game_id, away.is_home, away.opponent_name, away.venue_text), ("away", False, 'KEC "Die Haie"', "Köln"))

    def test_start_is_an_aware_utc_datetime_from_the_timestamp(self):
        row = dfel_row("g1")
        _team_name, [fixture] = dfel_import.parse_fixtures(dfel_schedule([row]), DFEL_TEAM_ID)

        self.assertEqual(fixture.start, datetime.fromtimestamp(row["gameUtcTimestamp"] / 1000, tz=UTC))
        self.assertEqual(fixture.start.utcoffset(), timedelta(0))

    def test_past_games_are_skipped(self):
        _team_name, fixtures = dfel_import.parse_fixtures(dfel_schedule([dfel_row("past", days=-3), dfel_row("future")]), DFEL_TEAM_ID)

        self.assertEqual([fixture.external_game_id for fixture in fixtures], ["future"])

    def test_other_teams_games_are_skipped(self):
        rows = [dfel_row("ours"), dfel_row("theirs", home_id="70774", home_name="KEC", away_id="70757", away_name="FASS Berlin")]
        _team_name, fixtures = dfel_import.parse_fixtures(dfel_schedule(rows), DFEL_TEAM_ID)

        self.assertEqual([fixture.external_game_id for fixture in fixtures], ["ours"])

    def test_the_venue_falls_back_to_the_location_name_without_an_address(self):
        _team_name, [fixture] = dfel_import.parse_fixtures(dfel_schedule([dfel_row("g1", city=None, longname="Dinslaken - Eishalle")]), DFEL_TEAM_ID)

        self.assertEqual(fixture.venue_text, "Dinslaken - Eishalle")

    def test_the_team_name_comes_from_a_past_game_when_nothing_is_upcoming(self):
        team_name, fixtures = dfel_import.parse_fixtures(dfel_schedule([dfel_row("past", days=-3)]), DFEL_TEAM_ID)

        self.assertEqual((team_name, fixtures), (DFEL_TEAM_NAME, []))

    def test_a_team_not_in_the_division_is_an_error(self):
        with self.assertRaises(dfel_import.DFELImportError):
            dfel_import.parse_fixtures(dfel_schedule([dfel_row("g1")]), "99999")

    def test_find_game_by_id(self):
        raw_json = dfel_schedule([dfel_row("a"), dfel_row("b")])

        self.assertEqual(dfel_import.find_game(raw_json, "b")["id"], "b")
        self.assertIsNone(dfel_import.find_game(raw_json, "zzz"))


class DFELImportPlanTests(EventsTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.home_location = Location.objects.create(club=cls.club, name="Home Arena", address="1 St", city="Mechelen", zip_code="2800", country="BE", is_home=True)
        cls.away_location = Location.objects.create(club=cls.club, name="Eishalle", address="2 St", city="Köln", zip_code="50679", country="DE")

    def away_row(self, game_id="a1", days=10):
        return dfel_row(game_id, days=days, home_id="70774", home_name="KEC", away_id=DFEL_TEAM_ID, away_name=DFEL_TEAM_NAME, city="köln")

    def test_new_fixtures_are_planned_as_creates_with_suggested_locations(self):
        plan = dfel_import.build_plan(self.club, self.team, DFEL_SOURCE_ID, dfel_schedule([dfel_row("h1"), self.away_row()]))

        self.assertEqual(plan.competition, "DFEL")
        self.assertEqual(plan.source_id, DFEL_SOURCE_ID)
        self.assertEqual(plan.scraped_team_name, DFEL_TEAM_NAME)
        self.assertEqual([(p.fixture.external_game_id, p.suggested_location) for p in plan.to_create], [("h1", self.home_location), ("a1", self.away_location)])

    def test_apply_plan_creates_dfel_events(self):
        plan = dfel_import.build_plan(self.club, self.team, DFEL_SOURCE_ID, dfel_schedule([dfel_row("h1")]), "DFEL2")
        result = dfel_import.apply_plan(plan, {"h1": str(self.home_location.pk)})

        self.assertEqual(result, {"created": 1, "updated": 0, "deleted": 0})
        event = Event.objects.get(external_game_id="h1")
        self.assertEqual((event.competition, event.external_source_id, event.competition_label), ("DFEL", DFEL_SOURCE_ID, "DFEL2"))
        self.assertEqual(event.kind, Event.EventKind.GAME)
        self.assertEqual(event.opponent.name, "FASS Berlin")
        self.assertEqual(event.location, self.home_location)
        self.assertEqual(list(event.teams.all()), [self.team])
        self.assertTrue(event.title.startswith("1st "))

    def test_a_reimport_updates_a_moved_game_and_leaves_the_rest_unchanged(self):
        dfel_import.apply_plan(dfel_import.build_plan(self.club, self.team, DFEL_SOURCE_ID, dfel_schedule([dfel_row("h1"), dfel_row("h2", days=21)])), {})

        plan = dfel_import.build_plan(self.club, self.team, DFEL_SOURCE_ID, dfel_schedule([dfel_row("h1", days=8), dfel_row("h2", days=21)]))

        self.assertEqual([p.fixture.external_game_id for p in plan.to_update], ["h1"])
        self.assertIn("start", plan.to_update[0].changes)
        self.assertEqual(plan.unchanged_count, 1)
        self.assertEqual(plan.to_create, [])

    def test_a_future_game_no_longer_listed_is_planned_for_deletion(self):
        dfel_import.apply_plan(dfel_import.build_plan(self.club, self.team, DFEL_SOURCE_ID, dfel_schedule([dfel_row("h1"), dfel_row("h2")])), {})

        plan = dfel_import.build_plan(self.club, self.team, DFEL_SOURCE_ID, dfel_schedule([dfel_row("h1")]))

        self.assertEqual([event.external_game_id for event in plan.to_delete], ["h2"])

    def test_a_dfel_import_never_deletes_rbihf_events(self):
        rbihf_event = self.make_event(kind=Event.EventKind.GAME, competition="RBIHF", external_game_id="5002", external_source_id=RBIHF_TEAM_ID)
        rbihf_event.teams.add(self.team)

        plan = dfel_import.build_plan(self.club, self.team, DFEL_SOURCE_ID, dfel_schedule([dfel_row("h1")]))

        self.assertEqual(plan.to_delete, [])

    def test_a_dfel_import_never_deletes_another_divisions_events(self):
        other_division = dfel_import.source_id(DFEL_TEAM_ID, "99999")
        dfel_import.apply_plan(dfel_import.build_plan(self.club, self.team, other_division, dfel_schedule([dfel_row("cup1")])), {})

        plan = dfel_import.build_plan(self.club, self.team, DFEL_SOURCE_ID, dfel_schedule([dfel_row("h1")]))

        self.assertEqual(plan.to_delete, [])
        self.assertEqual([p.fixture.external_game_id for p in plan.to_create], ["h1"])

    def test_an_rbihf_import_never_deletes_dfel_events(self):
        dfel_import.apply_plan(dfel_import.build_plan(self.club, self.team, DFEL_SOURCE_ID, dfel_schedule([dfel_row("h1")])), {})

        plan = build_plan(self.club, self.team, RBIHF_TEAM_ID, rbihf_sample_html([]))

        self.assertEqual(plan.to_delete, [])


class DFELUpdateGameInformationTests(EventsTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.home_location = Location.objects.create(club=cls.club, name="Home Arena", address="1 St", city="Mechelen", zip_code="2800", country="BE", is_home=True)
        cls.competition = Competition.objects.get(name="DFEL")

    def setUp(self):
        # The flag's per-club membership is cached (features.models) -- same leak concern as PollLiveGameResultsTests.
        cache.clear()
        self.addCleanup(cache.clear)

    def make_game(self, **kwargs):
        kwargs.setdefault("kind", Event.EventKind.GAME)
        kwargs.setdefault("competition", "DFEL")
        kwargs.setdefault("external_game_id", "g1")
        kwargs.setdefault("external_source_id", DFEL_SOURCE_ID)
        return self.make_event(**kwargs)

    def patch_fetch(self, rows):
        return mock.patch("events.services.dfel_import.requests.get", side_effect=[dfel_response(DFEL_TEAM_PAGE_HTML), dfel_response(f"rosterchief({dfel_schedule(rows)})")])

    def test_the_seeded_competition_is_flagged_ice_hockey(self):
        self.assertEqual(self.competition.module, "events.competition.hockey")
        self.assertEqual(self.competition.sport_type, Club.SportType.ICE_HOCKEY)
        self.assertEqual(self.competition.flag.name, "DFEL")

    def test_a_home_game_maps_home_score_to_score_for(self):
        game = self.make_game(location=self.home_location)
        with self.patch_fetch([dfel_row("g1", days=0, home_score=4, away_score=1, status=1)]):
            DFEL().update_game_information(game)

        game.refresh_from_db()
        self.assertEqual((game.score_for, game.score_against, game.is_live), (4, 1, True))

    def test_an_away_game_maps_away_score_to_score_for(self):
        game = self.make_game()
        with self.patch_fetch([dfel_row("g1", days=0, home_id="70774", away_id=DFEL_TEAM_ID, home_score=4, away_score=1, status=1)]):
            DFEL().update_game_information(game)

        game.refresh_from_db()
        self.assertEqual((game.score_for, game.score_against), (1, 4))

    def test_an_ended_game_is_not_live(self):
        game = self.make_game(is_live=True)
        with self.patch_fetch([dfel_row("g1", days=0, home_score=2, away_score=2, status=1, ended=True)]):
            DFEL().update_game_information(game)

        game.refresh_from_db()
        self.assertFalse(game.is_live)

    def test_a_game_missing_from_the_schedule_is_an_error(self):
        game = self.make_game(external_game_id="gone")
        with self.patch_fetch([dfel_row("g1")]), self.assertRaises(ValueError):
            DFEL().update_game_information(game)

    def test_a_missing_source_or_game_id_is_an_error_without_fetching(self):
        for kwargs in [{"external_source_id": ""}, {"external_source_id": "70779"}, {"external_game_id": ""}]:
            with self.subTest(**kwargs), mock.patch("events.services.dfel_import.requests.get") as mock_get, self.assertRaises(ValueError):
                DFEL().update_game_information(self.make_game(**kwargs))
            mock_get.assert_not_called()

    def test_fetch_game_info_is_gated_on_the_dfel_flag(self):
        game = self.make_game(location=self.home_location)
        with mock.patch("events.services.dfel_import.requests.get") as mock_get:
            self.assertFalse(fetch_game_info(game))
        mock_get.assert_not_called()

        self.competition.flag.clubs.add(self.club)
        with self.patch_fetch([dfel_row("g1", days=0, home_score=3, away_score=0, status=1)]):
            self.assertTrue(fetch_game_info(game))

        game.refresh_from_db()
        self.assertEqual((game.score_for, game.score_against, game.is_live), (3, 0, True))

    def test_fetch_game_info_reports_a_fetch_failure(self):
        self.competition.flag.clubs.add(self.club)
        game = self.make_game(external_game_id="gone")
        with self.patch_fetch([dfel_row("g1")]), self.assertRaises(CompetitionFetchError):
            fetch_game_info(game)


IJNL_TEAM_ID = "64414"
IJNL_SEASON = "2026/27"
IJNL_SOURCE_ID = f"{IJNL_TEAM_ID}/{IJNL_SEASON}"
IJNL_TEAM_NAME = "Sharks Ladies Mechelen"


def ijnl_game(game_id, *, days=7, time_text="16:00", home_id=IJNL_TEAM_ID, home_name=IJNL_TEAM_NAME, away_id="32609", away_name="Amsterdam Tigers Vrouwen", undated=False, time_unknown=False, state="gepland"):
    """One game in an ijshockey.nl team ``/data`` response, ``days`` from now."""
    return {
        "gameGuid": game_id,
        "datum": None if undated else (timezone.now() + timedelta(days=days)).strftime("%Y-%m-%d"),
        "tijd": None if time_unknown else time_text,
        "datumOnbekend": undated,
        "tijdOnbekend": time_unknown,
        "staat": state,
        "competitie": {"league": "Vrouwen", "onderdeelId": 21808, "onderdeel": "Vrouwen Eredivisie"},
        "thuis": {"teamId": int(home_id), "naam": home_name, "score": None},
        "uit": {"teamId": int(away_id), "naam": away_name, "score": None},
        "locatie": None,
    }


def ijnl_team_data(programma, uitslagen=(), season=IJNL_SEASON):
    return json.dumps(
        {
            "team": {"teamId": int(IJNL_TEAM_ID), "naam": IJNL_TEAM_NAME, "korteNaam": IJNL_TEAM_NAME, "club": IJNL_TEAM_NAME},
            "seizoen": season,
            "onderdelen": [{"onderdeelId": 21808, "onderdeel": "Vrouwen Eredivisie"}],
            "programma": list(programma),
            "uitslagen": list(uitslagen),
        }
    )


def ijnl_game_data(state="live", home="2", away="1"):
    """An ijshockey.nl game ``/data?part=live`` response."""
    scoreboard = f'<section class="mp-scoreboard"><div class="mp-result"><span class="mp-status">Periode 2</span><div class="mp-score" aria-label="{home} tegen {away}"><span>{home}</span><span>{away}</span></div></div></section>'
    return json.dumps({"title": "Sharks - Tigers", "state": state, "refreshSeconds": 60, "sections": {"scoreboard": scoreboard}})


def ijnl_response(text, status_code=200):
    return mock.Mock(status_code=status_code, text=text)


class IJNLExtractIdsTests(TestCase):
    def test_a_team_page_url_yields_team_and_season(self):
        for url in [
            "https://www.ijshockey.nl/competities/team/64414?seizoen=2026%2F27",
            "https://ijshockey.nl/competities/team/64414/?seizoen=2026/27",
            "  https://www.ijshockey.nl/competities/team/64414?seizoen=2026%2F27  ",
        ]:
            with self.subTest(url=url):
                self.assertEqual(ijnl_import.extract_ids(url), (IJNL_TEAM_ID, IJNL_SEASON))

    def test_a_url_without_a_season_yields_a_blank_season(self):
        self.assertEqual(ijnl_import.extract_ids("https://www.ijshockey.nl/competities/team/64414"), (IJNL_TEAM_ID, ""))

    def test_a_non_matching_url_is_rejected(self):
        for url in [
            "https://www.ijshockey.nl/competities/wedstrijd/49a66dbb-eaa2-4527-9c8b-31ee324d736b",
            "https://evil.example.com/competities/team/64414",
            "https://www.ijshockey.nl.evil.example.com/competities/team/64414",
            "https://www.ijshockey.nl/competities/team/64414?seizoen=next",
            "",
        ]:
            with self.subTest(url=url), self.assertRaises(ijnl_import.IJNLImportError):
                ijnl_import.extract_ids(url)

    def test_source_id_and_canonical_url(self):
        self.assertEqual(ijnl_import.source_id(IJNL_TEAM_ID, IJNL_SEASON), IJNL_SOURCE_ID)
        self.assertEqual(ijnl_import.team_page_url(IJNL_TEAM_ID, IJNL_SEASON), "https://www.ijshockey.nl/competities/team/64414?seizoen=2026%2F27")


class IJNLFetchTeamDataTests(TestCase):
    def test_fetch_asks_for_the_season(self):
        with mock.patch("events.services.ijnl_import.requests.get", return_value=ijnl_response(ijnl_team_data([]))) as mock_get:
            raw_json = ijnl_import.fetch_team_data(IJNL_TEAM_ID, IJNL_SEASON)

        self.assertEqual(ijnl_import.season_of(raw_json), IJNL_SEASON)
        self.assertEqual(mock_get.call_args.args[0], "https://www.ijshockey.nl/competities/team/64414/data")
        self.assertEqual(mock_get.call_args.kwargs["params"], {"seizoen": IJNL_SEASON})

    def test_a_network_or_http_error_is_an_ijnl_import_error(self):
        for side_effect in [requests.ConnectionError("boom"), [ijnl_response("nope", status_code=500)]]:
            with self.subTest(side_effect=side_effect), mock.patch("events.services.ijnl_import.requests.get", side_effect=side_effect), self.assertRaises(ijnl_import.IJNLImportError):
                ijnl_import.fetch_team_data(IJNL_TEAM_ID, IJNL_SEASON)

    def test_an_unexpected_payload_is_an_ijnl_import_error(self):
        for body in ["<html>oops</html>", json.dumps({"team": {}}), json.dumps([])]:
            with self.subTest(body=body), mock.patch("events.services.ijnl_import.requests.get", return_value=ijnl_response(body)), self.assertRaises(ijnl_import.IJNLImportError):
                ijnl_import.fetch_team_data(IJNL_TEAM_ID, IJNL_SEASON)


class IJNLParseFixturesTests(TestCase):
    def test_home_and_away_games_are_detected_with_the_right_opponent(self):
        games = [ijnl_game("home"), ijnl_game("away", days=14, home_id="32610", home_name="Dordrecht Lady Lions", away_id=IJNL_TEAM_ID, away_name=IJNL_TEAM_NAME)]
        team_name, fixtures, undated = ijnl_import.parse_fixtures(ijnl_team_data(games), IJNL_TEAM_ID)

        self.assertEqual((team_name, undated), (IJNL_TEAM_NAME, []))
        home, away = fixtures
        self.assertEqual((home.external_game_id, home.is_home, home.opponent_name), ("home", True, "Amsterdam Tigers Vrouwen"))
        self.assertEqual((away.external_game_id, away.is_home, away.opponent_name), ("away", False, "Dordrecht Lady Lions"))

    def test_start_is_dutch_wall_clock_time(self):
        game = ijnl_game("g1", time_text="16:45")
        _team_name, [fixture], _undated = ijnl_import.parse_fixtures(ijnl_team_data([game]), IJNL_TEAM_ID)

        local = fixture.start.astimezone(ZoneInfo("Europe/Amsterdam"))
        self.assertEqual((local.strftime("%Y-%m-%d"), local.hour, local.minute), (game["datum"], 16, 45))

    def test_an_unknown_hour_falls_back_to_noon_like_the_site(self):
        _team_name, [fixture], _undated = ijnl_import.parse_fixtures(ijnl_team_data([ijnl_game("g1", time_unknown=True)]), IJNL_TEAM_ID)

        self.assertEqual(fixture.start.astimezone(ZoneInfo("Europe/Amsterdam")).hour, 12)

    def test_undated_games_are_reported_separately(self):
        _team_name, fixtures, undated = ijnl_import.parse_fixtures(ijnl_team_data([ijnl_game("g1"), ijnl_game("tbd", undated=True)]), IJNL_TEAM_ID)

        self.assertEqual(([fixture.external_game_id for fixture in fixtures], undated), (["g1"], ["tbd"]))

    def test_played_games_are_skipped(self):
        _team_name, fixtures, _undated = ijnl_import.parse_fixtures(ijnl_team_data([ijnl_game("future")], uitslagen=[ijnl_game("past", days=-3, state="definitief")]), IJNL_TEAM_ID)

        self.assertEqual([fixture.external_game_id for fixture in fixtures], ["future"])

    def test_parse_score_reads_the_scoreboard(self):
        self.assertEqual(ijnl_import.parse_score(json.loads(ijnl_game_data(home="4", away="0"))), (4, 0))
        self.assertEqual(ijnl_import.parse_score(json.loads(ijnl_game_data(state="gepland", home="-", away="-"))), (None, None))
        self.assertEqual(ijnl_import.parse_score({"state": "gepland"}), (None, None))


class IJNLImportPlanTests(EventsTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.home_location = Location.objects.create(club=cls.club, name="Home Arena", address="1 St", city="Mechelen", zip_code="2800", country="BE", is_home=True)

    def plan(self, games, source_id=IJNL_SOURCE_ID, label=""):
        return ijnl_import.build_plan(self.club, self.team, source_id, ijnl_team_data(games), label)

    def test_apply_plan_creates_ijnl_events(self):
        plan = self.plan([ijnl_game("h1")], label="Eredivisie")

        self.assertEqual((plan.competition, plan.source_id, plan.scraped_team_name), ("IJNL", IJNL_SOURCE_ID, IJNL_TEAM_NAME))
        self.assertEqual(plan.to_create[0].suggested_location, self.home_location)
        result = ijnl_import.apply_plan(plan, {"h1": str(self.home_location.pk)})

        self.assertEqual(result, {"created": 1, "updated": 0, "deleted": 0})
        event = Event.objects.get(external_game_id="h1")
        self.assertEqual((event.competition, event.external_source_id, event.competition_label), ("IJNL", IJNL_SOURCE_ID, "Eredivisie"))
        self.assertEqual(event.opponent.name, "Amsterdam Tigers Vrouwen")
        self.assertEqual(list(event.teams.all()), [self.team])

    def test_a_reimport_updates_a_moved_game_and_deletes_a_dropped_one(self):
        ijnl_import.apply_plan(self.plan([ijnl_game("h1"), ijnl_game("h2", days=21)]), {})

        plan = self.plan([ijnl_game("h1", days=8)])

        self.assertEqual([p.fixture.external_game_id for p in plan.to_update], ["h1"])
        self.assertIn("start", plan.to_update[0].changes)
        self.assertEqual([event.external_game_id for event in plan.to_delete], ["h2"])

    def test_a_game_that_lost_its_date_is_kept_not_deleted(self):
        ijnl_import.apply_plan(self.plan([ijnl_game("h1"), ijnl_game("h2")]), {})

        plan = self.plan([ijnl_game("h1"), ijnl_game("h2", undated=True)])

        self.assertEqual((plan.to_delete, plan.to_create, plan.to_update), ([], [], []))

    def test_another_seasons_import_never_deletes_these_events(self):
        ijnl_import.apply_plan(self.plan([ijnl_game("h1")]), {})

        plan = self.plan([ijnl_game("next1")], source_id=ijnl_import.source_id(IJNL_TEAM_ID, "2027/28"))

        self.assertEqual(plan.to_delete, [])


class IJNLUpdateGameInformationTests(EventsTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.home_location = Location.objects.create(club=cls.club, name="Home Arena", address="1 St", city="Mechelen", zip_code="2800", country="BE", is_home=True)
        cls.competition = Competition.objects.get(name="IJNL")

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def make_game(self, **kwargs):
        kwargs.setdefault("kind", Event.EventKind.GAME)
        kwargs.setdefault("competition", "IJNL")
        kwargs.setdefault("external_game_id", "49a66dbb-eaa2-4527-9c8b-31ee324d736b")
        kwargs.setdefault("external_source_id", IJNL_SOURCE_ID)
        return self.make_event(**kwargs)

    def patch_fetch(self, body):
        return mock.patch("events.services.ijnl_import.requests.get", return_value=ijnl_response(body))

    def test_the_seeded_competition_is_flagged_ice_hockey(self):
        self.assertEqual(self.competition.module, "events.competition.hockey")
        self.assertEqual(self.competition.sport_type, Club.SportType.ICE_HOCKEY)
        self.assertEqual(self.competition.flag.name, "IJNL")

    def test_a_live_home_game_maps_home_score_to_score_for(self):
        game = self.make_game(location=self.home_location)
        with self.patch_fetch(ijnl_game_data(state="live", home="3", away="1")) as mock_get:
            IJNL().update_game_information(game)

        game.refresh_from_db()
        self.assertEqual((game.score_for, game.score_against, game.is_live), (3, 1, True))
        self.assertEqual(mock_get.call_args.args[0], "https://www.ijshockey.nl/competities/wedstrijd/49a66dbb-eaa2-4527-9c8b-31ee324d736b/data")
        self.assertEqual(mock_get.call_args.kwargs["params"], {"part": "live"})

    def test_a_finished_away_game_maps_away_score_to_score_for(self):
        game = self.make_game(is_live=True)
        with self.patch_fetch(ijnl_game_data(state="definitief", home="4", away="0")):
            IJNL().update_game_information(game)

        game.refresh_from_db()
        self.assertEqual((game.score_for, game.score_against, game.is_live), (0, 4, False))

    def test_a_missing_game_id_is_an_error_without_fetching(self):
        with mock.patch("events.services.ijnl_import.requests.get") as mock_get, self.assertRaises(ValueError):
            IJNL().update_game_information(self.make_game(external_game_id=""))
        mock_get.assert_not_called()

    def test_fetch_game_info_is_gated_on_the_ijnl_flag(self):
        game = self.make_game(location=self.home_location)
        with mock.patch("events.services.ijnl_import.requests.get") as mock_get:
            self.assertFalse(fetch_game_info(game))
        mock_get.assert_not_called()

        self.competition.flag.clubs.add(self.club)
        with self.patch_fetch(ijnl_game_data(state="live", home="1", away="0")):
            self.assertTrue(fetch_game_info(game))

        game.refresh_from_db()
        self.assertEqual((game.score_for, game.score_against), (1, 0))


class EventRefereeModelTests(EventsTestBase):
    def test_str(self):
        event = self.make_event(title="Cup Final")
        referee = Member.objects.create(first_name="Ref", last_name="Eree")
        assignment = EventReferee.objects.create(event=event, member=referee, assigned_by=self.alice)

        self.assertEqual(str(assignment), "Cup Final - Ref Eree")

    def test_member_unique_per_event(self):
        event = self.make_event()
        referee = Member.objects.create(first_name="Ref", last_name="Eree")
        EventReferee.objects.create(event=event, member=referee, assigned_by=self.alice)

        with self.assertRaises(IntegrityError):
            EventReferee.objects.create(event=event, member=referee, assigned_by=self.alice)

    def test_max_referees_defaults_to_two(self):
        event = self.make_event()
        self.assertEqual(event.max_referees, 2)

    def test_deleting_the_assigner_keeps_the_assignment(self):
        event = self.make_event()
        referee = Member.objects.create(first_name="Ref", last_name="Eree")
        assignment = EventReferee.objects.create(event=event, member=referee, assigned_by=self.alice)

        self.alice.delete()
        assignment.refresh_from_db()

        self.assertIsNone(assignment.assigned_by)
        self.assertEqual(EventReferee.objects.filter(pk=assignment.pk).count(), 1)


class RefereeServiceTests(EventsTestBase):
    """events.services.referees -- eligibility (level + validity, derived via
    RefereeProfile.eligible_teams), conflict detection (a soft warning, never
    a block), and the max_referees hard ceiling."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.home_ground = Location.objects.create(club=cls.club, name="Home Ground", address="1 St", city="Town", zip_code="1000", country="BE", is_home=True)
        cls.away_ground = Location.objects.create(club=cls.club, name="Away Ground", address="2 St", city="Town", zip_code="1000", country="BE")

        cls.level = RefereeLevel.objects.create(club=cls.club, name="Regional")
        cls.level.teams.add(cls.team)

        cls.referee = Member.objects.create(first_name="Ref", last_name="Eree")
        cls.referee_profile = RefereeProfile.objects.create(club=cls.club, member=cls.referee, level=cls.level, valid_until=timezone.localdate() + timedelta(days=30))

    def make_eligible_profile(self, member, level=None):
        return RefereeProfile.objects.create(club=self.club, member=member, level=level or self.level, valid_until=timezone.localdate() + timedelta(days=30))

    def make_home_game(self, **kwargs):
        kwargs.setdefault("kind", Event.EventKind.GAME)
        kwargs.setdefault("location", self.home_ground)
        kwargs.setdefault("teams", None)
        teams = kwargs.pop("teams")
        event = self.make_event(**kwargs)
        event.teams.add(teams or self.team)
        return event

    def test_eligible_referees_returns_qualified_members_for_a_home_game(self):
        game = self.make_home_game()

        self.assertEqual(set(eligible_referees(game)), {self.referee})

    def test_eligible_referees_empty_for_an_away_game(self):
        game = self.make_home_game(location=self.away_ground)

        self.assertEqual(set(eligible_referees(game)), set())

    def test_needs_referee_management_true_for_a_club_managed_home_game(self):
        game = self.make_home_game()
        self.assertTrue(needs_referee_management(game))

    def test_needs_referee_management_false_for_an_away_game(self):
        game = self.make_home_game(location=self.away_ground)
        self.assertFalse(needs_referee_management(game))

    def test_needs_referee_management_true_for_a_club_managed_home_tournament(self):
        tournament = self.make_home_game(kind=Event.EventKind.TOURNAMENT)
        self.assertTrue(needs_referee_management(tournament))

    def test_needs_referee_management_false_for_a_federation_managed_team(self):
        self.team.referee_management = Team.RefereeManagement.FEDERATION
        self.team.save(update_fields=["referee_management"])
        game = self.make_home_game()

        self.assertFalse(needs_referee_management(game))

    def test_eligible_referees_empty_for_a_federation_managed_team(self):
        self.team.referee_management = Team.RefereeManagement.FEDERATION
        self.team.save(update_fields=["referee_management"])
        game = self.make_home_game()

        self.assertEqual(set(eligible_referees(game)), set())

    def test_assign_referee_rejects_a_federation_managed_team(self):
        self.team.referee_management = Team.RefereeManagement.FEDERATION
        self.team.save(update_fields=["referee_management"])
        game = self.make_home_game()

        with self.assertRaises(RefereeAssignmentError):
            assign_referee(game, self.referee, assigned_by=self.alice)

    def test_eligible_referees_empty_for_a_non_game_kind(self):
        practice = self.make_event(kind=Event.EventKind.TRAINING, location=self.home_ground)
        practice.teams.add(self.team)

        self.assertEqual(set(eligible_referees(practice)), set())

    def test_eligible_referees_excludes_someone_already_assigned(self):
        game = self.make_home_game()
        EventReferee.objects.create(event=game, member=self.referee, assigned_by=self.alice)

        self.assertEqual(set(eligible_referees(game)), set())

    def test_eligible_referees_ignores_someone_not_eligible_for_this_team(self):
        other_team = Team.objects.create(club=self.club, name="Second Team", short_name="2nd")
        unrelated_referee = Member.objects.create(first_name="Not", last_name="Eligible")
        RefereeProfile.objects.create(club=self.club, member=unrelated_referee)  # no level set

        game = self.make_home_game(teams=other_team)

        self.assertEqual(set(eligible_referees(game)), set())

    def test_eligible_referees_includes_a_higher_level_via_inheritance(self):
        # self.level ("Regional") qualifies for self.team; a referee holding a
        # higher level that inherits from Regional (without self.team added
        # directly) must still show up -- see RefereeLevel.eligible_team_ids.
        national = RefereeLevel.objects.create(club=self.club, name="National", inherits_from=self.level)
        national_referee = Member.objects.create(first_name="Nat", last_name="Ional")
        self.make_eligible_profile(national_referee, level=national)

        game = self.make_home_game()

        self.assertIn(national_referee, set(eligible_referees(game)))

    def test_eligible_referees_ignores_an_expired_profile(self):
        expired_referee = Member.objects.create(first_name="Ex", last_name="Pired")
        RefereeProfile.objects.create(club=self.club, member=expired_referee, level=self.level, valid_until=timezone.localdate() - timedelta(days=1))

        game = self.make_home_game()

        self.assertNotIn(expired_referee, set(eligible_referees(game)))

    def test_conflicting_events_finds_an_overlapping_expected_attendance(self):
        # Alice is on self.team's roster (EventsTestBase.setUp), so she's part
        # of any event's effective audience that includes self.team.
        game = self.make_home_game(start=self.future)
        other_training = self.make_event(title="Other training", start=self.future, end=self.future + timedelta(hours=1))
        other_training.teams.add(self.team)

        conflicts = conflicting_events(self.alice, game)

        self.assertIn(other_training, conflicts)

    def test_conflicting_events_empty_when_nothing_overlaps(self):
        game = self.make_home_game(start=self.future)
        later = self.make_event(title="Much later", start=self.future + timedelta(days=5))
        later.teams.add(self.team)

        self.assertEqual(conflicting_events(self.alice, game), [])

    def test_conflicting_events_assumes_a_duration_when_end_is_blank(self):
        # Neither event has an explicit `end` -- the 2-hour assumed window
        # (events.services.referees.ASSUMED_EVENT_DURATION) is what makes these
        # two (30 minutes apart) overlap.
        game = self.make_home_game(start=self.future)
        nearby = self.make_event(title="Nearby", start=self.future + timedelta(minutes=30))
        nearby.teams.add(self.team)

        self.assertIn(nearby, conflicting_events(self.alice, game))

    def test_conflicting_events_does_not_block_assignment(self):
        # Soft warning only -- assign_referee succeeds regardless of conflicts.
        game = self.make_home_game(start=self.future)
        other_training = self.make_event(title="Other training", start=self.future, end=self.future + timedelta(hours=1))
        other_training.teams.add(self.team)
        self.assertTrue(conflicting_events(self.alice, game))

        self.make_eligible_profile(self.alice)
        assign_referee(game, self.alice, assigned_by=self.bob)

        self.assertTrue(EventReferee.objects.filter(event=game, member=self.alice).exists())

    def test_assign_referee_creates_the_row(self):
        game = self.make_home_game()

        assignment = assign_referee(game, self.referee, assigned_by=self.alice)

        self.assertEqual(assignment.event, game)
        self.assertEqual(assignment.member, self.referee)
        self.assertEqual(assignment.assigned_by, self.alice)

    def test_assign_referee_settles_an_open_invite_as_accepted(self):
        game = self.make_home_game()
        RefereeSignup.objects.get_or_create(event=game, member=self.referee)  # usually already invited on creation

        assign_referee(game, self.referee, assigned_by=self.alice)

        signup = RefereeSignup.objects.get(event=game, member=self.referee)
        self.assertEqual(signup.status, RefereeSignup.Status.ACCEPTED)
        self.assertIsNotNone(signup.responded_at)

    def test_assign_referee_creates_an_accepted_signup_when_never_invited(self):
        # So the game shows up among the referee's own duties in the app.
        game = self.make_home_game()

        assign_referee(game, self.referee, assigned_by=self.alice)

        self.assertEqual(RefereeSignup.objects.get(event=game, member=self.referee).status, RefereeSignup.Status.ACCEPTED)

    def test_assign_referee_rejects_an_away_game(self):
        game = self.make_home_game(location=self.away_ground)

        with self.assertRaises(RefereeAssignmentError):
            assign_referee(game, self.referee, assigned_by=self.alice)

    def test_assign_referee_rejects_a_non_game(self):
        practice = self.make_event(kind=Event.EventKind.TRAINING, location=self.home_ground)
        practice.teams.add(self.team)

        with self.assertRaises(RefereeAssignmentError):
            assign_referee(practice, self.referee, assigned_by=self.alice)

    def test_assign_referee_rejects_once_at_capacity(self):
        game = self.make_home_game(max_referees=1)
        assign_referee(game, self.referee, assigned_by=self.alice)
        second_referee = Member.objects.create(first_name="Second", last_name="Ref")
        self.make_eligible_profile(second_referee)

        with self.assertRaises(RefereeAssignmentError):
            assign_referee(game, second_referee, assigned_by=self.alice)

        self.assertEqual(game.referees.count(), 1)

    def test_assign_referee_rejects_the_same_member_twice(self):
        game = self.make_home_game()
        assign_referee(game, self.referee, assigned_by=self.alice)

        with self.assertRaises(RefereeAssignmentError):
            assign_referee(game, self.referee, assigned_by=self.alice)

    def test_remove_referee_deletes_the_assignment(self):
        game = self.make_home_game()
        assignment = assign_referee(game, self.referee, assigned_by=self.alice)

        remove_referee(assignment)

        self.assertFalse(EventReferee.objects.filter(event=game, member=self.referee).exists())

    def test_add_external_referee_creates_a_memberless_row(self):
        game = self.make_home_game()

        assignment = add_external_referee(game, "Guest Referee", assigned_by=self.alice)

        self.assertIsNone(assignment.member)
        self.assertEqual(assignment.external_name, "Guest Referee")
        self.assertTrue(assignment.is_external)
        self.assertEqual(assignment.display_name, "Guest Referee")

    def test_add_external_referee_strips_the_name(self):
        game = self.make_home_game()
        assignment = add_external_referee(game, "  Guest Referee  ", assigned_by=self.alice)
        self.assertEqual(assignment.external_name, "Guest Referee")

    def test_add_external_referee_rejects_a_blank_name(self):
        game = self.make_home_game()
        with self.assertRaises(RefereeAssignmentError):
            add_external_referee(game, "   ", assigned_by=self.alice)

    def test_add_external_referee_counts_against_capacity(self):
        game = self.make_home_game(max_referees=1)
        add_external_referee(game, "Guest Referee", assigned_by=self.alice)

        with self.assertRaises(RefereeAssignmentError):
            assign_referee(game, self.referee, assigned_by=self.alice)

    def test_add_external_referee_rejects_an_away_game(self):
        game = self.make_home_game(location=self.away_ground)
        with self.assertRaises(RefereeAssignmentError):
            add_external_referee(game, "Guest Referee", assigned_by=self.alice)

    def test_set_referee_fee_computes_totals(self):
        game = self.make_home_game()
        assignment = assign_referee(game, self.referee, assigned_by=self.alice)

        set_referee_fee(assignment, fee=Decimal("25.00"), km=Decimal("40"), km_rate=Decimal("0.35"))

        assignment.refresh_from_db()
        self.assertEqual(assignment.km_total, Decimal("14.00"))
        self.assertEqual(assignment.total_payable, Decimal("39.00"))

    def test_set_referee_fee_without_km_has_no_km_total(self):
        game = self.make_home_game()
        assignment = assign_referee(game, self.referee, assigned_by=self.alice)

        set_referee_fee(assignment, fee=Decimal("25.00"))

        assignment.refresh_from_db()
        self.assertEqual(assignment.km_total, Decimal("0"))
        self.assertEqual(assignment.total_payable, Decimal("25.00"))

    def test_no_fee_drops_the_fee_and_travel_and_is_not_pending(self):
        game = self.make_home_game()
        assignment = assign_referee(game, self.referee, assigned_by=self.alice)
        self.assertTrue(assignment.fee_pending)

        set_referee_fee(assignment, fee=Decimal("25.00"), km=Decimal("40"), km_rate=Decimal("0.35"), no_fee=True)

        assignment.refresh_from_db()
        self.assertEqual((assignment.no_fee, assignment.fee, assignment.km, assignment.km_rate, assignment.total_payable), (True, Decimal("0.00"), None, None, Decimal("0")))
        self.assertFalse(assignment.fee_pending)

        set_referee_fee(assignment, fee=Decimal("20.00"))

        assignment.refresh_from_db()
        self.assertEqual((assignment.no_fee, assignment.fee), (False, Decimal("20.00")))


class RefereeSignupServiceTests(EventsTestBase):
    """events.services.referees.sync_referee_invites/accept_referee_signup/
    decline_referee_signup -- the self-service sign-up flow, layered on top
    of the same assign_referee/eligible_referees the admin flow uses."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.home_ground = Location.objects.create(club=cls.club, name="Home Ground", address="1 St", city="Town", zip_code="1000", country="BE", is_home=True)
        cls.level = RefereeLevel.objects.create(club=cls.club, name="Regional")
        cls.level.teams.add(cls.team)
        cls.referee = Member.objects.create(first_name="Ref", last_name="Eree")
        cls.referee_profile = RefereeProfile.objects.create(club=cls.club, member=cls.referee, level=cls.level, valid_until=timezone.localdate() + timedelta(days=30))

    def make_home_game(self, **kwargs):
        kwargs.setdefault("kind", Event.EventKind.GAME)
        kwargs.setdefault("location", self.home_ground)
        event = self.make_event(**kwargs)
        event.teams.add(self.team)
        return event

    def test_creating_a_home_game_invites_eligible_referees(self):
        # sync_referee_invites is wired from events/signals.py's post_save(Event)
        # -- make_home_game's own .save() (via make_event) already triggers it.
        game = self.make_home_game()

        self.assertTrue(RefereeSignup.objects.filter(event=game, member=self.referee, status=RefereeSignup.Status.INVITED).exists())

    def test_creating_a_home_game_notifies_eligible_referees(self):
        self.make_home_game()

        self.assertTrue(Notification.objects.filter(member=self.referee, title="Referee needed").exists())

    def test_sync_does_not_reinvite_someone_who_already_responded(self):
        game = self.make_home_game()
        signup = RefereeSignup.objects.get(event=game, member=self.referee)
        signup.status = RefereeSignup.Status.DECLINED
        signup.responded_at = timezone.now()
        signup.save()

        sync_referee_invites(game)

        signup.refresh_from_db()
        self.assertEqual(signup.status, RefereeSignup.Status.DECLINED)

    def test_sync_is_a_noop_for_an_away_game(self):
        away_ground = Location.objects.create(club=self.club, name="Away Ground", address="2 St", city="Town", zip_code="1000", country="BE")
        game = self.make_home_game(location=away_ground)

        self.assertFalse(RefereeSignup.objects.filter(event=game).exists())

    def test_accept_creates_a_real_event_referee_assignment(self):
        game = self.make_home_game()
        signup = RefereeSignup.objects.get(event=game, member=self.referee)

        accept_referee_signup(signup)

        assignment = EventReferee.objects.get(event=game, member=self.referee)
        self.assertIsNone(assignment.assigned_by)
        signup.refresh_from_db()
        self.assertEqual(signup.status, RefereeSignup.Status.ACCEPTED)
        self.assertIsNotNone(signup.responded_at)

    def test_accept_raises_when_the_game_is_already_full(self):
        game = self.make_home_game(max_referees=1)
        add_external_referee(game, "Guest Referee", assigned_by=None)
        signup = RefereeSignup.objects.get(event=game, member=self.referee)

        with self.assertRaises(RefereeAssignmentError):
            accept_referee_signup(signup)

        signup.refresh_from_db()
        self.assertEqual(signup.status, RefereeSignup.Status.INVITED)

    def test_decline_marks_the_signup_declined(self):
        game = self.make_home_game()
        signup = RefereeSignup.objects.get(event=game, member=self.referee)

        decline_referee_signup(signup)

        signup.refresh_from_db()
        self.assertEqual(signup.status, RefereeSignup.Status.DECLINED)
        self.assertIsNotNone(signup.responded_at)

    def test_decline_after_accepting_removes_the_self_service_assignment(self):
        game = self.make_home_game()
        signup = RefereeSignup.objects.get(event=game, member=self.referee)
        accept_referee_signup(signup)

        decline_referee_signup(signup)

        self.assertFalse(EventReferee.objects.filter(event=game, member=self.referee).exists())

    def test_decline_after_a_staff_assignment_removes_it_too(self):
        # A staff pick settles the invite as accepted (assign_referee), so a
        # later "no" from the member has to undo it -- otherwise they'd stay
        # assigned while their own answer says they can't make it.
        game = self.make_home_game()
        assign_referee(game, self.referee, assigned_by=self.alice)
        signup = RefereeSignup.objects.get(event=game, member=self.referee)

        decline_referee_signup(signup)

        self.assertFalse(EventReferee.objects.filter(event=game, member=self.referee).exists())
        self.assertEqual(RefereeSignup.objects.get(pk=signup.pk).status, RefereeSignup.Status.DECLINED)

    def test_decline_after_accepting_notifies_the_teams_managers(self):
        game = self.make_home_game()
        signup = RefereeSignup.objects.get(event=game, member=self.referee)
        accept_referee_signup(signup)
        manager = Member.objects.create(first_name="Cara", last_name="Coach")
        management_position = Position.objects.create(club=self.club, name="Head coach", short_name="HC", staff_position=True, management_position=True)
        StaffAssignment.objects.create(team=self.team, member=manager, season=self.season, position=management_position)

        decline_referee_signup(signup)

        notification = Notification.objects.get(member=manager, title="Referee dropped out")
        self.assertIn(self.referee.get_full_name(), notification.body)

    def test_declining_an_unanswered_invite_does_not_notify_managers(self):
        game = self.make_home_game()
        signup = RefereeSignup.objects.get(event=game, member=self.referee)
        manager = Member.objects.create(first_name="Cara", last_name="Coach")
        management_position = Position.objects.create(club=self.club, name="Head coach", short_name="HC", staff_position=True, management_position=True)
        StaffAssignment.objects.create(team=self.team, member=manager, season=self.season, position=management_position)

        decline_referee_signup(signup)

        self.assertFalse(Notification.objects.filter(member=manager, title="Referee dropped out").exists())


class OfficialsTestBase(EventsTestBase):
    """Shared scaffolding for every officials test: the "officials" waffle
    flag active for self.club (sync_official_invites -- the one code path
    that runs with no request, off every event save/team-change signal --
    checks this itself; every other officials function is flag-agnostic,
    same as events.services.officials' own module docstring explains)."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # get_or_create, not create -- migration 0031 already seeds this flag
        # (unenabled) into every database, this test DB included.
        flag, _created = get_waffle_flag_model().objects.get_or_create(name="officials")
        flag.clubs.add(cls.club)


class EventOfficialModelTests(OfficialsTestBase):
    def test_str(self):
        event = self.make_event(title="Cup Final")
        official = Member.objects.create(first_name="Off", last_name="Icial")
        assignment = EventOfficial.objects.create(event=event, member=official, assigned_by=self.alice)

        self.assertEqual(str(assignment), "Cup Final - Off Icial")

    def test_member_unique_per_event(self):
        event = self.make_event()
        official = Member.objects.create(first_name="Off", last_name="Icial")
        EventOfficial.objects.create(event=event, member=official, assigned_by=self.alice)

        with self.assertRaises(IntegrityError):
            EventOfficial.objects.create(event=event, member=official, assigned_by=self.alice)

    def test_member_xor_external_name_is_enforced(self):
        event = self.make_event()

        with self.assertRaises(IntegrityError):
            EventOfficial.objects.create(event=event, assigned_by=self.alice)

    def test_max_officials_defaults_to_one(self):
        event = self.make_event()
        self.assertEqual(event.max_officials, 1)


class OfficialServiceTests(OfficialsTestBase):
    """events.services.officials -- mirrors RefereeServiceTests' own coverage
    of the shared eligibility/capacity logic (see that class for the
    exhaustive case-by-case breakdown); this class re-confirms it against
    the separate official model/table/flag, not every edge case twice."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.home_ground = Location.objects.create(club=cls.club, name="Home Ground", address="1 St", city="Town", zip_code="1000", country="BE", is_home=True)
        cls.away_ground = Location.objects.create(club=cls.club, name="Away Ground", address="2 St", city="Town", zip_code="1000", country="BE")

        cls.level = OfficialLevel.objects.create(club=cls.club, name="Regional")
        cls.level.teams.add(cls.team)

        cls.official = Member.objects.create(first_name="Off", last_name="Icial")
        cls.official_profile = OfficialProfile.objects.create(club=cls.club, member=cls.official, level=cls.level, valid_until=timezone.localdate() + timedelta(days=30))

    def make_home_game(self, **kwargs):
        kwargs.setdefault("kind", Event.EventKind.GAME)
        kwargs.setdefault("location", self.home_ground)
        kwargs.setdefault("teams", None)
        teams = kwargs.pop("teams")
        event = self.make_event(**kwargs)
        event.teams.add(teams or self.team)
        return event

    def test_eligible_officials_returns_qualified_members_for_a_home_game(self):
        game = self.make_home_game()
        self.assertEqual(set(eligible_officials(game)), {self.official})

    def test_needs_official_management_true_for_a_club_managed_home_tournament(self):
        tournament = self.make_home_game(kind=Event.EventKind.TOURNAMENT)
        self.assertTrue(needs_official_management(tournament))

    def test_needs_official_management_false_for_a_federation_managed_team(self):
        self.team.official_management = Team.OfficialManagement.FEDERATION
        self.team.save(update_fields=["official_management"])
        game = self.make_home_game()

        self.assertFalse(needs_official_management(game))

    def test_federation_managed_for_officials_does_not_affect_referees(self):
        # official_management and referee_management are independent fields
        # -- confirmed via AskUserQuestion during planning.
        self.team.official_management = Team.OfficialManagement.FEDERATION
        self.team.save(update_fields=["official_management"])
        game = self.make_home_game()

        self.assertFalse(needs_official_management(game))
        self.assertTrue(needs_referee_management(game))

    def test_eligible_officials_includes_a_higher_level_via_inheritance(self):
        national = OfficialLevel.objects.create(club=self.club, name="National", inherits_from=self.level)
        national_official = Member.objects.create(first_name="Nat", last_name="Ional")
        OfficialProfile.objects.create(club=self.club, member=national_official, level=national, valid_until=timezone.localdate() + timedelta(days=30))

        game = self.make_home_game()

        self.assertIn(national_official, set(eligible_officials(game)))

    def test_assign_official_creates_the_row(self):
        game = self.make_home_game()

        assignment = assign_official(game, self.official, assigned_by=self.alice)

        self.assertEqual(assignment.event, game)
        self.assertEqual(assignment.member, self.official)

    def test_assign_official_settles_an_open_invite_as_accepted(self):
        game = self.make_home_game()
        OfficialSignup.objects.get_or_create(event=game, member=self.official)  # usually already invited on creation

        assign_official(game, self.official, assigned_by=self.alice)

        self.assertEqual(OfficialSignup.objects.get(event=game, member=self.official).status, OfficialSignup.Status.ACCEPTED)

    def test_assign_official_rejects_once_at_capacity(self):
        game = self.make_home_game(max_officials=1)
        assign_official(game, self.official, assigned_by=self.alice)
        second = Member.objects.create(first_name="Second", last_name="Off")
        OfficialProfile.objects.create(club=self.club, member=second, level=self.level, valid_until=timezone.localdate() + timedelta(days=30))

        with self.assertRaises(OfficialAssignmentError):
            assign_official(game, second, assigned_by=self.alice)

        self.assertEqual(game.officials.count(), 1)

    def test_assign_official_rejects_the_same_member_twice(self):
        game = self.make_home_game()
        assign_official(game, self.official, assigned_by=self.alice)

        with self.assertRaises(OfficialAssignmentError):
            assign_official(game, self.official, assigned_by=self.alice)

    def test_add_external_official_creates_a_memberless_row(self):
        game = self.make_home_game()

        assignment = add_external_official(game, "Guest Official", assigned_by=self.alice)

        self.assertIsNone(assignment.member)
        self.assertTrue(assignment.is_external)
        self.assertEqual(assignment.display_name, "Guest Official")

    def test_add_external_official_rejects_a_blank_name(self):
        game = self.make_home_game()
        with self.assertRaises(OfficialAssignmentError):
            add_external_official(game, "   ", assigned_by=self.alice)

    def test_set_official_fee_computes_totals(self):
        game = self.make_home_game()
        assignment = assign_official(game, self.official, assigned_by=self.alice)

        set_official_fee(assignment, fee=Decimal("25.00"), km=Decimal("40"), km_rate=Decimal("0.35"))

        assignment.refresh_from_db()
        self.assertEqual(assignment.km_total, Decimal("14.00"))
        self.assertEqual(assignment.total_payable, Decimal("39.00"))

    def test_set_official_fee_with_no_fee_owes_nothing(self):
        game = self.make_home_game()
        assignment = assign_official(game, self.official, assigned_by=self.alice)

        set_official_fee(assignment, fee=Decimal("25.00"), km=Decimal("40"), km_rate=Decimal("0.35"), no_fee=True)

        assignment.refresh_from_db()
        self.assertEqual((assignment.no_fee, assignment.total_payable, assignment.fee_pending), (True, Decimal("0"), False))


class OfficialSignupServiceTests(OfficialsTestBase):
    """events.services.officials.sync_official_invites/accept_official_signup/
    decline_official_signup -- mirrors RefereeSignupServiceTests, plus the
    officials-specific "off unless the flag is on" behaviour."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.home_ground = Location.objects.create(club=cls.club, name="Home Ground", address="1 St", city="Town", zip_code="1000", country="BE", is_home=True)
        cls.level = OfficialLevel.objects.create(club=cls.club, name="Regional")
        cls.level.teams.add(cls.team)
        cls.official = Member.objects.create(first_name="Off", last_name="Icial")
        cls.official_profile = OfficialProfile.objects.create(club=cls.club, member=cls.official, level=cls.level, valid_until=timezone.localdate() + timedelta(days=30))

    def make_home_game(self, **kwargs):
        kwargs.setdefault("kind", Event.EventKind.GAME)
        kwargs.setdefault("location", self.home_ground)
        event = self.make_event(**kwargs)
        event.teams.add(self.team)
        return event

    def test_creating_a_home_game_invites_eligible_officials(self):
        game = self.make_home_game()
        self.assertTrue(OfficialSignup.objects.filter(event=game, member=self.official, status=OfficialSignup.Status.INVITED).exists())

    def test_accept_creates_a_real_event_official_assignment(self):
        game = self.make_home_game()
        signup = OfficialSignup.objects.get(event=game, member=self.official)

        accept_official_signup(signup)

        assignment = EventOfficial.objects.get(event=game, member=self.official)
        self.assertIsNone(assignment.assigned_by)
        signup.refresh_from_db()
        self.assertEqual(signup.status, OfficialSignup.Status.ACCEPTED)

    def test_decline_after_accepting_removes_the_self_service_assignment(self):
        game = self.make_home_game()
        signup = OfficialSignup.objects.get(event=game, member=self.official)
        accept_official_signup(signup)

        decline_official_signup(signup)

        self.assertFalse(EventOfficial.objects.filter(event=game, member=self.official).exists())

    def test_decline_after_a_staff_assignment_removes_it_too(self):
        game = self.make_home_game()
        assign_official(game, self.official, assigned_by=self.alice)
        signup = OfficialSignup.objects.get(event=game, member=self.official)

        decline_official_signup(signup)

        self.assertFalse(EventOfficial.objects.filter(event=game, member=self.official).exists())
        self.assertEqual(OfficialSignup.objects.get(pk=signup.pk).status, OfficialSignup.Status.DECLINED)

    def test_decline_after_accepting_notifies_the_teams_managers(self):
        game = self.make_home_game()
        signup = OfficialSignup.objects.get(event=game, member=self.official)
        accept_official_signup(signup)
        manager = Member.objects.create(first_name="Cara", last_name="Coach")
        management_position = Position.objects.create(club=self.club, name="Head coach", short_name="HC", staff_position=True, management_position=True)
        StaffAssignment.objects.create(team=self.team, member=manager, season=self.season, position=management_position)

        decline_official_signup(signup)

        notification = Notification.objects.get(member=manager, title="Official dropped out")
        self.assertIn(self.official.get_full_name(), notification.body)

    def test_sync_does_not_reinvite_someone_who_already_responded(self):
        game = self.make_home_game()
        signup = OfficialSignup.objects.get(event=game, member=self.official)
        signup.status = OfficialSignup.Status.DECLINED
        signup.responded_at = timezone.now()
        signup.save()

        sync_official_invites(game)

        signup.refresh_from_db()
        self.assertEqual(signup.status, OfficialSignup.Status.DECLINED)

    def test_sync_does_nothing_when_the_flag_is_off_for_this_club(self):
        get_waffle_flag_model().objects.filter(name="officials").first().clubs.remove(self.club)

        game = self.make_home_game()

        self.assertFalse(OfficialSignup.objects.filter(event=game).exists())

    def test_sync_does_nothing_when_no_officials_flag_exists_at_all(self):
        # A brand-new install/club with the Flag row never created at all --
        # officials_enabled_for must not blow up on a missing Flag.
        get_waffle_flag_model().objects.filter(name="officials").delete()

        game = self.make_home_game()

        self.assertFalse(OfficialSignup.objects.filter(event=game).exists())


class EventTaskModelTests(EventsTestBase):
    def test_str(self):
        event = self.make_event(title="Home Opener")
        task = EventTask.objects.create(event=event, title="Bring fruit")

        self.assertEqual(str(task), "Home Opener - Bring fruit")

    def test_needed_quantity_defaults_to_one(self):
        event = self.make_event()
        task = EventTask.objects.create(event=event, title="Bring fruit")

        self.assertEqual(task.needed_quantity, 1)

    def test_claim_unique_per_member_per_task(self):
        event = self.make_event()
        task = EventTask.objects.create(event=event, title="Bring fruit")
        EventTaskClaim.objects.create(task=task, member=self.alice)

        with self.assertRaises(IntegrityError):
            EventTaskClaim.objects.create(task=task, member=self.alice)

    def test_deleting_the_event_deletes_its_tasks(self):
        event = self.make_event()
        task = EventTask.objects.create(event=event, title="Bring fruit")

        event.delete()

        self.assertFalse(EventTask.objects.filter(pk=task.pk).exists())


class ClaimTaskServiceTests(EventsTestBase):
    """events.services.tasks.claim_task/unclaim_task -- the row-locked
    capacity check against EventTask.needed_quantity, same shape as
    events.services.referees/officials' own _lock_and_check_capacity."""

    def make_task(self, needed_quantity=1):
        event = self.make_event()
        return EventTask.objects.create(event=event, title="Bring fruit", needed_quantity=needed_quantity)

    def test_claim_creates_the_row(self):
        task = self.make_task()

        claim = claim_task(task, self.alice)

        self.assertEqual(claim.task, task)
        self.assertEqual(claim.member, self.alice)

    def test_claim_rejects_the_same_member_twice(self):
        task = self.make_task()
        claim_task(task, self.alice)

        with self.assertRaises(TaskClaimError):
            claim_task(task, self.alice)

    def test_claim_rejects_once_at_capacity(self):
        task = self.make_task(needed_quantity=1)
        claim_task(task, self.alice)

        with self.assertRaises(TaskClaimError):
            claim_task(task, self.bob)

        self.assertEqual(task.claims.count(), 1)

    def test_a_second_slot_can_be_claimed_when_quantity_allows_it(self):
        task = self.make_task(needed_quantity=2)
        claim_task(task, self.alice)

        claim_task(task, self.bob)

        self.assertEqual(task.claims.count(), 2)

    def test_unclaim_deletes_the_row(self):
        task = self.make_task()
        claim = claim_task(task, self.alice)

        unclaim_task(claim)

        self.assertFalse(EventTaskClaim.objects.filter(pk=claim.pk).exists())

    def test_unclaim_frees_the_slot_for_someone_else(self):
        task = self.make_task(needed_quantity=1)
        claim = claim_task(task, self.alice)
        unclaim_task(claim)

        claim_task(task, self.bob)

        self.assertEqual(task.claims.count(), 1)


class CalendarGridTests(EventsTestBase):
    """events.services.calendar -- date-range math and grid layout behind the
    Events page's Week/Month/Season views."""

    def at(self, day, hour, minute=0):
        return timezone.make_aware(datetime.combine(day, time(hour, minute)))

    def test_week_bounds_returns_monday_to_sunday(self):
        start, end = week_bounds(date(2026, 8, 19))  # a Wednesday

        self.assertEqual(start, date(2026, 8, 17))
        self.assertEqual(end, date(2026, 8, 23))

    def test_month_bounds_returns_first_and_last_day(self):
        start, end = month_bounds(date(2026, 2, 10))

        self.assertEqual(start, date(2026, 2, 1))
        self.assertEqual(end, date(2026, 2, 28))

    def test_add_months_rolls_over_the_year(self):
        self.assertEqual(add_months(date(2026, 11, 15), 2), date(2027, 1, 1))

    def _item(self, day):
        return SimpleNamespace(start=self.at(day, 12))

    def test_agenda_groups_splits_into_this_week_next_week_and_later(self):
        # 2026-08-19 is a Wednesday -- this week runs Mon 17..Sun 23, next
        # week Mon 24..Sun 30, matching test_week_bounds_returns_monday_to_
        # sunday above.
        this_week_item = self._item(date(2026, 8, 21))
        next_week_item = self._item(date(2026, 8, 27))
        later_item = self._item(date(2026, 9, 15))
        items = [this_week_item, next_week_item, later_item]

        this_week, next_week, later_months = agenda_groups(items, today=date(2026, 8, 19))

        self.assertEqual(this_week, [this_week_item])
        self.assertEqual(next_week, [next_week_item])
        self.assertEqual(len(later_months), 1)
        self.assertEqual(later_months[0]["month_start"], date(2026, 9, 1))
        self.assertEqual(later_months[0]["items"], [later_item])

    def test_agenda_groups_later_items_split_across_separate_months(self):
        september_item = self._item(date(2026, 9, 15))
        october_item = self._item(date(2026, 10, 15))

        _this_week, _next_week, later_months = agenda_groups([september_item, october_item], today=date(2026, 8, 19))

        self.assertEqual([month["month_start"] for month in later_months], [date(2026, 9, 1), date(2026, 10, 1)])

    def test_agenda_groups_show_past_skips_week_labels(self):
        past_item = self._item(date(2026, 8, 1))

        this_week, next_week, months = agenda_groups([past_item], show_past=True)

        self.assertEqual(this_week, [])
        self.assertEqual(next_week, [])
        self.assertEqual(months, [{"month_start": date(2026, 8, 1), "items": [past_item]}])

    def test_agenda_groups_empty_list(self):
        self.assertEqual(agenda_groups([]), ([], [], []))

    def test_agenda_groups_start_of_accessor_for_non_event_items(self):
        row = {"event": self._item(date(2026, 8, 21))}

        this_week, _next_week, _later = agenda_groups([row], start_of=lambda row: row["event"].start, today=date(2026, 8, 19))

        self.assertEqual(this_week, [row])

    def test_week_grid_always_covers_the_full_day(self):
        monday = date(2026, 8, 17)
        event = self.make_event(start=self.at(monday, 10), end=self.at(monday, 11, 30))

        grid = week_grid([event], monday)

        self.assertEqual(grid["day_start_hour"], 0)
        self.assertEqual(grid["day_end_hour"], 24)
        self.assertEqual(grid["hours"], list(range(0, 25)))
        span = 24
        block = grid["days"][0]["blocks"][0]
        self.assertAlmostEqual(block["top_pct"], 100 * 10 / span, places=2)
        self.assertAlmostEqual(block["height_pct"], 100 * 1.5 / span, places=2)

    def test_week_grid_still_covers_an_event_starting_at_midnight_or_ending_near_it(self):
        monday = date(2026, 8, 17)
        event = self.make_event(start=self.at(monday, 0), end=self.at(monday, 23, 45))

        grid = week_grid([event], monday)

        self.assertEqual(grid["day_start_hour"], 0)
        self.assertEqual(grid["day_end_hour"], 24)

    def test_week_grid_first_event_hour_is_the_earliest_events_start_hour(self):
        monday = date(2026, 8, 17)
        morning = self.make_event(title="Morning", start=self.at(monday, 9), end=self.at(monday, 10))
        evening = self.make_event(title="Evening", start=self.at(monday + timedelta(days=2), 18), end=self.at(monday + timedelta(days=2), 19))

        grid = week_grid([morning, evening], monday)

        self.assertEqual(grid["first_event_hour"], 9)

    def test_week_grid_first_event_hour_is_none_when_the_week_has_no_events(self):
        monday = date(2026, 8, 17)

        grid = week_grid([], monday)

        self.assertIsNone(grid["first_event_hour"])

    def test_week_grid_excludes_events_outside_the_week(self):
        monday = date(2026, 8, 17)
        event = self.make_event(start=self.at(monday + timedelta(days=7), 10))

        grid = week_grid([event], monday)

        self.assertFalse(any(day["blocks"] for day in grid["days"]))

    def test_week_grid_splits_overlapping_events_into_columns(self):
        monday = date(2026, 8, 17)
        first = self.make_event(title="Training A", start=self.at(monday, 10), end=self.at(monday, 11))
        second = self.make_event(title="Training B", start=self.at(monday, 10, 30), end=self.at(monday, 11, 30))

        grid = week_grid([first, second], monday)

        blocks = grid["days"][0]["blocks"]
        self.assertEqual(len(blocks), 2)
        self.assertEqual({block["width_pct"] for block in blocks}, {50.0})
        self.assertEqual({block["left_pct"] for block in blocks}, {0.0, 50.0})

    def test_week_grid_gives_non_overlapping_events_full_width(self):
        monday = date(2026, 8, 17)
        first = self.make_event(title="Morning", start=self.at(monday, 9), end=self.at(monday, 10))
        second = self.make_event(title="Evening", start=self.at(monday, 18), end=self.at(monday, 19))

        grid = week_grid([first, second], monday)

        blocks = grid["days"][0]["blocks"]
        self.assertTrue(all(block["width_pct"] == 100.0 for block in blocks))

    def test_week_grid_block_carries_the_gathering_strips_own_position(self):
        monday = date(2026, 8, 17)
        event = self.make_event(start=self.at(monday, 10), end=self.at(monday, 11), gathering=self.at(monday, 9, 30))

        grid = week_grid([event], monday)

        block = grid["days"][0]["blocks"][0]
        span = 24
        self.assertAlmostEqual(block["gathering_top_pct"], 100 * 9.5 / span, places=2)
        self.assertAlmostEqual(block["gathering_height_pct"], 100 * 0.5 / span, places=2)

    def test_week_grid_floors_a_very_short_gathering_strips_own_height(self):
        # A 5-minute gap would otherwise render as an unreadable sliver --
        # too short even for its own time label.
        monday = date(2026, 8, 17)
        event = self.make_event(start=self.at(monday, 10), end=self.at(monday, 11), gathering=self.at(monday, 9, 55))

        grid = week_grid([event], monday)

        block = grid["days"][0]["blocks"][0]
        self.assertEqual(block["gathering_height_pct"], 2.0)

    def test_week_grid_omits_the_gathering_strip_with_no_gathering_time(self):
        monday = date(2026, 8, 17)
        event = self.make_event(start=self.at(monday, 10), end=self.at(monday, 11))

        grid = week_grid([event], monday)

        block = grid["days"][0]["blocks"][0]
        self.assertNotIn("gathering_top_pct", block)

    def test_week_grid_omits_the_gathering_strip_crossing_into_the_previous_day(self):
        monday = date(2026, 8, 17)
        event = self.make_event(start=self.at(monday, 0, 15), end=self.at(monday, 1), gathering=self.at(monday - timedelta(days=1), 23, 45))

        grid = week_grid([event], monday)

        block = grid["days"][0]["blocks"][0]
        self.assertNotIn("gathering_top_pct", block)

    def test_week_grid_omits_the_gathering_strip_when_its_not_actually_earlier(self):
        # Defensive -- nothing currently stops a single (non-recurring) event's
        # gathering time from being entered after its own start.
        monday = date(2026, 8, 17)
        event = self.make_event(start=self.at(monday, 10), end=self.at(monday, 11), gathering=self.at(monday, 10, 30))

        grid = week_grid([event], monday)

        block = grid["days"][0]["blocks"][0]
        self.assertNotIn("gathering_top_pct", block)

    def test_month_grid_always_spans_full_weeks_of_seven_days(self):
        grid = month_grid([], date(2026, 2, 1))  # Feb 2026 starts on a Sunday

        self.assertTrue(all(len(week) == 7 for week in grid["weeks"]))
        self.assertEqual(grid["weeks"][0][0]["date"].weekday(), 0)  # Monday

    def test_month_grid_flags_days_outside_the_month(self):
        grid = month_grid([], date(2026, 2, 1))

        self.assertFalse(grid["weeks"][0][0]["in_month"])  # January spillover
        in_month_cell = next(cell for week in grid["weeks"] for cell in week if cell["date"] == date(2026, 2, 1))
        self.assertTrue(in_month_cell["in_month"])

    def test_month_grid_buckets_events_under_their_start_day(self):
        event = self.make_event(start=self.at(date(2026, 2, 12), 14))

        grid = month_grid([event], date(2026, 2, 1))

        cell = next(cell for week in grid["weeks"] for cell in week if cell["date"] == date(2026, 2, 12))
        self.assertEqual(cell["events"], [event])

    def test_season_grid_spans_every_month_of_the_season(self):
        season = Season.objects.create(club=self.club, start_date=date(2026, 8, 1), end_date=date(2027, 7, 31))

        months = season_grid([], season)

        self.assertEqual(len(months), 12)
        self.assertEqual(months[0]["label"], date(2026, 8, 1))
        self.assertEqual(months[-1]["label"], date(2027, 7, 1))

    def test_season_grid_cells_carry_a_count_not_the_event_list(self):
        season = Season.objects.create(club=self.club, start_date=date(2026, 8, 1), end_date=date(2027, 7, 31))
        event = self.make_event(start=self.at(date(2026, 9, 5), 10))

        months = season_grid([event], season)

        september = next(month for month in months if month["label"] == date(2026, 9, 1))
        cell = next(cell for week in september["weeks"] for cell in week if cell["date"] == date(2026, 9, 5))
        self.assertEqual(cell["count"], 1)
        self.assertNotIn("events", cell)


class SendDeadlineRemindersTests(EventsTestBase):
    """events.tasks.send_deadline_reminders -- one reminder push per event,
    a week before whichever cutoff matters (its own deadline, or its start
    when no deadline is set), to whoever's still NO_RESPONSE. This is the
    periodic sweep that also catches a recurring series' occurrences, which
    never go through notify_new_event's on-creation path at all."""

    def setUp(self):
        # Maintenance.save() writes through to the cache (see that model's own
        # docstring) -- a DB rollback between tests doesn't clear it, so the
        # maintenance-mode test below would otherwise leak into every test that
        # happens to run after it.
        cache.clear()
        self.addCleanup(cache.clear)

    def make_event_with_roster(self, **kwargs):
        event = self.make_event(**kwargs)
        event.teams.add(self.team)
        return event

    def test_reminds_within_the_window_before_the_deadline(self):
        event = self.make_event_with_roster(start=timezone.now() + timedelta(days=10), deadline=timezone.now() + timedelta(days=6))

        result = call_command("send_deadline_reminders")

        self.assertTrue(Notification.objects.filter(member=self.alice, title=event.title).exists())
        self.assertTrue(Notification.objects.filter(member=self.bob, title=event.title).exists())
        self.assertIn("Reminded 2 member(s) across 1 event(s)", result)

    def test_falls_back_to_the_event_start_when_no_deadline_is_set(self):
        event = self.make_event_with_roster(start=timezone.now() + timedelta(days=6), deadline=None)

        call_command("send_deadline_reminders")

        self.assertTrue(Notification.objects.filter(member=self.alice, title=event.title).exists())

    def test_does_not_remind_before_the_window_opens(self):
        self.make_event_with_roster(start=timezone.now() + timedelta(days=30), deadline=timezone.now() + timedelta(days=20))

        call_command("send_deadline_reminders")

        self.assertFalse(Notification.objects.exists())

    def test_does_not_remind_after_the_deadline_has_passed(self):
        # Deadline already gone, but the event itself hasn't started yet -- the
        # window is closed, not "still open and overdue".
        self.make_event_with_roster(start=timezone.now() + timedelta(days=2), deadline=timezone.now() - timedelta(hours=1))

        call_command("send_deadline_reminders")

        self.assertFalse(Notification.objects.exists())

    def test_only_notifies_members_who_have_not_answered(self):
        event = self.make_event_with_roster(start=timezone.now() + timedelta(days=10), deadline=timezone.now() + timedelta(days=6))
        Attendance.objects.filter(event=event, member=self.alice).update(status=Attendance.AttendanceStatus.PRESENT)

        call_command("send_deadline_reminders")

        self.assertFalse(Notification.objects.filter(member=self.alice).exists())
        self.assertTrue(Notification.objects.filter(member=self.bob).exists())

    def test_does_not_notify_the_same_event_twice(self):
        self.make_event_with_roster(start=timezone.now() + timedelta(days=10), deadline=timezone.now() + timedelta(days=6))

        call_command("send_deadline_reminders")
        Notification.objects.all().delete()
        call_command("send_deadline_reminders")

        self.assertFalse(Notification.objects.exists())

    def test_marks_the_event_processed_even_when_nobody_needed_notifying(self):
        event = self.make_event_with_roster(start=timezone.now() + timedelta(days=10), deadline=timezone.now() + timedelta(days=6))
        Attendance.objects.filter(event=event).update(status=Attendance.AttendanceStatus.PRESENT)

        call_command("send_deadline_reminders")

        event.refresh_from_db()
        self.assertIsNotNone(event.deadline_reminder_sent_at)

    def test_skips_a_cancelled_event(self):
        self.make_event_with_roster(start=timezone.now() + timedelta(days=10), deadline=timezone.now() + timedelta(days=6), cancelled=True)

        call_command("send_deadline_reminders")

        self.assertFalse(Notification.objects.exists())

    def test_raises_during_maintenance_instead_of_silently_skipping(self):
        Maintenance.start(user=None)

        with self.assertRaises(CommandError):
            call_command("send_deadline_reminders")

    def test_raises_when_paused_from_the_control_panel(self):
        JobToggle.set_enabled("events.tasks.send_deadline_reminders", False)

        with self.assertRaises(CommandError):
            call_command("send_deadline_reminders")

    def test_writes_a_successful_job_run(self):
        call_command("send_deadline_reminders")

        run = JobRun.objects.get(name="events.tasks.send_deadline_reminders")
        self.assertEqual(run.status, JobRun.Status.SUCCESS)
        self.assertIn("Reminded", run.detail)

    def test_writes_a_failed_job_run_during_maintenance(self):
        Maintenance.start(user=None)

        with self.assertRaises(CommandError):
            call_command("send_deadline_reminders")

        run = JobRun.objects.get(name="events.tasks.send_deadline_reminders")
        self.assertEqual(run.status, JobRun.Status.FAILURE)


class PublishScheduledLineupsTests(EventsTestBase):
    """events.tasks.publish_scheduled_lineups -- the periodic sweep behind a
    coach's "schedule for later" Publish option (mobile/coach_views.py's
    CoachLineupPublishView, events.services.lineup.schedule_lineup_publish)."""

    def setUp(self):
        # Same Maintenance-cache leak concern as SendDeadlineRemindersTests above.
        cache.clear()
        self.addCleanup(cache.clear)

    def make_game_with_lineup(self, **event_kwargs):
        event_kwargs.setdefault("kind", Event.EventKind.GAME)
        event = self.make_event(**event_kwargs)
        event.teams.add(self.team)
        return event, Lineup.objects.create(event=event, team=self.team)

    def test_publishes_a_lineup_whose_time_has_arrived(self):
        _event, lineup = self.make_game_with_lineup()
        lineup.scheduled_publish_at = timezone.now() - timedelta(minutes=1)
        lineup.save()

        result = call_command("publish_scheduled_lineups")

        lineup.refresh_from_db()
        self.assertIsNotNone(lineup.published_at)
        self.assertIsNone(lineup.scheduled_publish_at)
        self.assertIn("Published 1 scheduled line-up(s)", result)

    def test_leaves_a_not_yet_due_schedule_alone(self):
        _event, lineup = self.make_game_with_lineup()
        lineup.scheduled_publish_at = timezone.now() + timedelta(days=1)
        lineup.save()

        call_command("publish_scheduled_lineups")

        lineup.refresh_from_db()
        self.assertIsNone(lineup.published_at)
        self.assertIsNotNone(lineup.scheduled_publish_at)

    def test_ignores_a_lineup_with_no_schedule_at_all(self):
        _event, lineup = self.make_game_with_lineup()

        result = call_command("publish_scheduled_lineups")

        lineup.refresh_from_db()
        self.assertIsNone(lineup.published_at)
        self.assertIn("Published 0 scheduled line-up(s)", result)

    def test_already_published_lineup_is_not_touched_again(self):
        _event, lineup = self.make_game_with_lineup()
        published_at = timezone.now() - timedelta(days=1)
        lineup.published_at = published_at
        lineup.scheduled_publish_at = timezone.now() - timedelta(minutes=1)
        lineup.save()

        call_command("publish_scheduled_lineups")

        lineup.refresh_from_db()
        self.assertEqual(lineup.published_at, published_at)

    def test_raises_during_maintenance_instead_of_silently_skipping(self):
        Maintenance.start(user=None)

        with self.assertRaises(CommandError):
            call_command("publish_scheduled_lineups")

    def test_raises_when_paused_from_the_control_panel(self):
        JobToggle.set_enabled("events.tasks.publish_scheduled_lineups", False)

        with self.assertRaises(CommandError):
            call_command("publish_scheduled_lineups")

    def test_writes_a_successful_job_run(self):
        call_command("publish_scheduled_lineups")

        run = JobRun.objects.get(name="events.tasks.publish_scheduled_lineups")
        self.assertEqual(run.status, JobRun.Status.SUCCESS)
        self.assertIn("Published", run.detail)


class PollLiveGameResultsTests(EventsTestBase):
    """events.management.commands.poll_live_game_results -- the per-minute sweep
    that keeps a game's score/live status fresh from its competition's data
    source, from shortly before kickoff through a while after the final
    whistle. See events.services.competitions.fetch_game_info for the actual
    per-club gating and events.competition.hockey for what a real fetch does."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.competition = Competition.objects.get(name="RBIHF")
        cls.competition.flag.clubs.add(cls.club)

    def setUp(self):
        # Same Maintenance-cache leak concern as the other job test classes above.
        cache.clear()
        self.addCleanup(cache.clear)

    def make_game(self, **kwargs):
        kwargs.setdefault("kind", Event.EventKind.GAME)
        kwargs.setdefault("competition", "RBIHF")
        kwargs.setdefault("external_game_id", "1234")
        return self.make_event(**kwargs)

    def mock_response(self, *, live, score_a=0, score_b=0):
        response = mock.Mock(status_code=200)
        response.json.return_value = {"live": live, "scoreA": score_a, "scoreB": score_b}
        return response

    def test_a_game_starting_soon_is_polled_and_updated(self):
        game = self.make_game(start=timezone.now() + timedelta(minutes=10))

        # No location is set on these test games, so is_home_game is False and
        # RBIHF.update_game_information takes the away branch (score_for comes
        # from scoreB, not scoreA) -- see events/competition/hockey.py.
        with mock.patch("events.competition.hockey.requests.get", return_value=self.mock_response(live=True, score_b=2)) as mock_get:
            result = call_command("poll_live_game_results")

        mock_get.assert_called_once()
        game.refresh_from_db()
        self.assertTrue(game.is_live)
        self.assertIn("Checked 1 game(s); fetched 1, 0 failed", result)

    def test_ignores_a_game_starting_too_far_ahead(self):
        self.make_game(start=timezone.now() + timedelta(hours=2))

        with mock.patch("events.competition.hockey.requests.get") as mock_get:
            call_command("poll_live_game_results")

        mock_get.assert_not_called()

    def test_polls_a_game_still_within_the_trailing_hour(self):
        game = self.make_game(start=timezone.now() - timedelta(hours=3), end=timezone.now() - timedelta(minutes=30))

        with mock.patch("events.competition.hockey.requests.get", return_value=self.mock_response(live=False, score_b=3)):
            call_command("poll_live_game_results")

        game.refresh_from_db()
        self.assertEqual(game.score_for, 3)

    def test_ignores_a_game_past_its_trailing_hour(self):
        self.make_game(start=timezone.now() - timedelta(hours=4), end=timezone.now() - timedelta(hours=2))

        with mock.patch("events.competition.hockey.requests.get") as mock_get:
            call_command("poll_live_game_results")

        mock_get.assert_not_called()

    def test_ignores_a_game_with_no_competition_configured(self):
        self.make_game(start=timezone.now() + timedelta(minutes=5), competition="")

        with mock.patch("events.competition.hockey.requests.get") as mock_get:
            call_command("poll_live_game_results")

        mock_get.assert_not_called()

    def test_stops_polling_once_a_live_game_finishes(self):
        game = self.make_game(start=timezone.now() - timedelta(minutes=5))

        with mock.patch("events.competition.hockey.requests.get", return_value=self.mock_response(live=True)):
            call_command("poll_live_game_results")
        game.refresh_from_db()
        self.assertTrue(game.is_live)
        self.assertIsNone(game.live_score_polling_done_at)

        with mock.patch("events.competition.hockey.requests.get", return_value=self.mock_response(live=False, score_a=1, score_b=4)):
            call_command("poll_live_game_results")
        game.refresh_from_db()
        self.assertFalse(game.is_live)
        self.assertEqual(game.score_for, 4)
        self.assertIsNotNone(game.live_score_polling_done_at)

        with mock.patch("events.competition.hockey.requests.get") as mock_get:
            call_command("poll_live_game_results")
        mock_get.assert_not_called()

    def test_a_fetch_failure_does_not_stop_the_rest_of_the_sweep(self):
        # Event's default ordering is -start, so the later-starting game is
        # fetched first -- pin both starts explicitly so the side_effect list
        # below lines up with a known call order rather than one that's only
        # deterministic by accident.
        now = timezone.now()
        self.make_game(title="Boom", start=now + timedelta(minutes=6))
        ok_game = self.make_game(title="Fine", external_game_id="5678", start=now + timedelta(minutes=5))

        with mock.patch("events.competition.hockey.requests.get", side_effect=[ConnectionError("boom"), self.mock_response(live=True)]):
            result = call_command("poll_live_game_results")

        ok_game.refresh_from_db()
        self.assertTrue(ok_game.is_live)
        self.assertIn("Checked 2 game(s); fetched 1, 1 failed", result)

    def test_raises_during_maintenance_instead_of_silently_skipping(self):
        Maintenance.start(user=None)

        with self.assertRaises(CommandError):
            call_command("poll_live_game_results")

    def test_raises_when_paused_from_the_control_panel(self):
        JobToggle.set_enabled("events.tasks.poll_live_game_results", False)

        with self.assertRaises(CommandError):
            call_command("poll_live_game_results")

    def test_writes_a_successful_job_run(self):
        call_command("poll_live_game_results")

        run = JobRun.objects.get(name="events.tasks.poll_live_game_results")
        self.assertEqual(run.status, JobRun.Status.SUCCESS)
        self.assertIn("Checked 0 game(s)", run.detail)


class BackfillGameTitlesMigrationTests(TestCase):
    """events.migrations.0033_backfill_game_titles_home_away -- run directly against
    the real app registry (a data-only migration touching no removed fields, so the
    live models work fine here), not through Django's migration test harness."""

    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United")
        cls.team = Team.objects.create(club=cls.club, name="First Team", short_name="1st")
        cls.opponent = Opponent.objects.create(club=cls.club, name="Rivals FC")
        cls.home_ground = Location.objects.create(club=cls.club, name="Home Ground", address="1 St", city="Town", zip_code="1000", country="BE", is_home=True)
        cls.away_ground = Location.objects.create(club=cls.club, name="Their Rink", address="2 St", city="Elsewhere", zip_code="2000", country="BE", is_home=False)

    def game(self, **kwargs):
        kwargs.setdefault("club", self.club)
        kwargs.setdefault("kind", Event.EventKind.GAME)
        kwargs.setdefault("start", timezone.now() + timedelta(days=1))
        kwargs.setdefault("opponent", self.opponent)
        event = Event.objects.create(**kwargs)
        event.teams.add(self.team)
        return event

    def run_migration(self):
        from importlib import import_module

        from django.apps import apps

        migration = import_module("events.migrations.0033_backfill_game_titles_home_away")
        migration.backfill_game_titles(apps, None)

    def test_a_home_games_title_is_left_as_is(self):
        game = self.game(title=f"{self.team.short_name} vs {self.opponent}", location=self.home_ground)

        self.run_migration()

        game.refresh_from_db()
        self.assertEqual(game.title, f"{self.team.short_name} vs {self.opponent}")

    def test_an_away_games_title_is_flipped(self):
        game = self.game(title=f"{self.team.short_name} vs {self.opponent}", location=self.away_ground)

        self.run_migration()

        game.refresh_from_db()
        self.assertEqual(game.title, f"{self.opponent} vs {self.team.short_name}")

    def test_a_game_with_no_location_is_left_as_is(self):
        game = self.game(title=f"{self.team.short_name} vs {self.opponent}")

        self.run_migration()

        game.refresh_from_db()
        self.assertEqual(game.title, f"{self.team.short_name} vs {self.opponent}")

    def test_a_training_is_untouched(self):
        event = Event.objects.create(club=self.club, kind=Event.EventKind.TRAINING, title="Practice", start=timezone.now() + timedelta(days=1))

        self.run_migration()

        event.refresh_from_db()
        self.assertEqual(event.title, "Practice")


class HockeyCompetitionRequestTimeoutTests(EventsTestBase):
    """RBIHF/CEHL live-score lookups must bound their HTTP calls, and a timeout
    has to come back out of fetch_game_info as an ordinary CompetitionFetchError."""

    def setUp(self):
        # Flag membership is cached per club (features.models) -- keep it from leaking between tests.
        cache.clear()
        self.addCleanup(cache.clear)

    def enable(self, name):
        competition = Competition.objects.get(name=name)
        if competition.flag is None:
            competition.flag = get_waffle_flag_model().objects.create(name=f"{name}-timeout-test")
            competition.save(update_fields=["flag"])
        competition.flag.clubs.add(self.club)

    def make_game(self, competition):
        return self.make_event(kind=Event.EventKind.GAME, competition=competition, external_game_id="1234")

    def test_every_request_passes_a_timeout(self):
        for name in ["RBIHF", "CEHL"]:
            with self.subTest(competition=name):
                self.enable(name)
                response = mock.Mock(status_code=500)
                with mock.patch("events.competition.hockey.requests.get", return_value=response) as mock_get:
                    fetch_game_info(self.make_game(name))
                self.assertTrue(mock_get.call_args_list)
                for call in mock_get.call_args_list:
                    self.assertEqual(call.kwargs.get("timeout"), dfel_import.REQUEST_TIMEOUT_SECONDS)

    def test_a_timeout_surfaces_as_a_competition_fetch_error(self):
        for name in ["RBIHF", "CEHL"]:
            with self.subTest(competition=name):
                self.enable(name)
                game = self.make_game(name)
                with mock.patch("events.competition.hockey.requests.get", side_effect=requests.Timeout("read timed out")), self.assertRaises(CompetitionFetchError):
                    fetch_game_info(game)


class ImportSourcesTestBase(EventsTestBase):
    """Shared scaffolding for events.services.import_sources and the weekly
    check_fixture_sources job: RBIHF/DFEL team page URLs to store on a team, and
    the RBIHF/DFEL flags reachable by name."""

    RBIHF_URL = f"https://www.rbihf.be/league/team/{RBIHF_TEAM_ID}"
    DFEL_URL = f"https://ehv-nrw.de/leagues/team/{DFEL_TEAM_ID}/{DFEL_DIVISION_ID}/"

    def setUp(self):
        # Flag._get_club_ids caches per flag name -- a rollback between tests doesn't clear it.
        cache.clear()
        self.addCleanup(cache.clear)

    def enable_flag(self, name, club=None):
        flag, _created = get_waffle_flag_model().objects.get_or_create(name=name)
        flag.clubs.add(club or self.club)
        cache.clear()
        return flag

    def fixture_row(self, game_id="5002", *, days=7, hour="12:15", visit_name="Amsterdam Tigers"):
        date = (timezone.now() + timedelta(days=days)).strftime("%Y-%m-%d")
        return {"game_id": game_id, "date": date, "hour": hour, "venue": "Deurne", "home_id": RBIHF_TEAM_ID, "home_name": RBIHF_TEAM_NAME, "visit_id": "4464", "visit_name": visit_name}

    def import_rows(self, rows, team=None, label=""):
        """What a confirmed one-off import leaves behind: the games, stamped with this source."""
        apply_plan(build_plan(self.club, team or self.team, RBIHF_TEAM_ID, rbihf_sample_html(rows), label), {})

    def store(self, team=None, *, source=import_sources.SOURCE_RBIHF, url=None, season=None, label=""):
        url = url or (self.RBIHF_URL if source == import_sources.SOURCE_RBIHF else self.DFEL_URL)
        return import_sources.record_source(team or self.team, source=source, url=url, season=season or self.season, competition_label=label)

    def patch_fetch(self, payload):
        """Patches the network half of check_entry -- ``payload`` is the RBIHF HTML
        returned for every entry, an exception to raise, or a callable(entry)."""
        if isinstance(payload, Exception) or callable(payload):
            return mock.patch("events.services.import_sources.fetch", side_effect=payload)
        return mock.patch("events.services.import_sources.fetch", return_value=payload)


class RecordSourceTests(ImportSourcesTestBase):
    def test_replaces_the_same_season_source_and_label_case_insensitively(self):
        self.store(label="Division 1", url="https://www.rbihf.be/league/team/1111")
        self.store(label="  division 1 ", url="https://www.rbihf.be/league/team/2222")

        self.team.refresh_from_db()
        self.assertEqual(len(self.team.import_sources), 1)
        self.assertEqual(self.team.import_sources[0]["url"], "https://www.rbihf.be/league/team/2222")
        self.assertEqual(self.team.import_sources[0]["competition_label"], "division 1")

    def test_appends_a_different_label_source_or_season(self):
        next_season = Season.objects.create(club=self.club, start_date=self.season.end_date + timedelta(days=1), end_date=self.season.end_date + timedelta(days=365))

        self.store(label="Division 1")
        self.store(label="Cup")
        self.store(source=import_sources.SOURCE_DFEL, label="Division 1")
        self.store(label="Division 1", season=next_season)

        self.team.refresh_from_db()
        self.assertEqual(len(self.team.import_sources), 4)

    def test_rejects_an_unknown_source(self):
        with self.assertRaises(ValueError):
            import_sources.record_source(self.team, source="NOPE", url="https://example.com", season=self.season)

    def test_sources_for_filters_by_season(self):
        other_season = Season.objects.create(club=self.club, start_date=self.season.start_date - timedelta(days=365), end_date=self.season.start_date - timedelta(days=1))
        self.store(label="Division 1")
        self.store(label="Division 1", season=other_season)

        self.team.refresh_from_db()
        self.assertEqual(len(import_sources.sources_for(self.team)), 2)
        current = import_sources.sources_for(self.team, self.season)
        self.assertEqual(len(current), 1)
        self.assertEqual(current[0]["season_id"], str(self.season.pk))


class CheckEntryTests(ImportSourcesTestBase):
    def check(self, payload):
        entry = self.store()
        with self.patch_fetch(payload):
            return import_sources.check_entry(self.club, self.team, entry)

    def test_a_moved_hour_is_reported_as_rescheduled(self):
        self.import_rows([self.fixture_row()])

        result = self.check(rbihf_sample_html([self.fixture_row(hour="20:00")]))

        self.assertEqual([game_id for game_id, _old, _new in result.rescheduled], ["5002"])
        self.assertTrue(result.has_changes)
        self.assertEqual(result.as_dict(), {"rescheduled": 1, "new_games": 0, "removed_games": 0, "error": ""})

    def test_a_new_game_is_reported(self):
        self.import_rows([self.fixture_row()])

        result = self.check(rbihf_sample_html([self.fixture_row(), self.fixture_row("5003", days=14)]))

        self.assertEqual([game_id for game_id, _start, _opponent in result.new_games], ["5003"])
        self.assertEqual(result.rescheduled, [])

    def test_a_removed_future_game_is_reported(self):
        self.import_rows([self.fixture_row(), self.fixture_row("5003", days=14)])

        result = self.check(rbihf_sample_html([self.fixture_row()]))

        self.assertEqual([game_id for game_id, _start, _title in result.removed_games], ["5003"])

    def test_an_opponent_only_change_is_not_reported(self):
        self.import_rows([self.fixture_row()])

        result = self.check(rbihf_sample_html([self.fixture_row(visit_name="Amsterdam Lions")]))

        self.assertFalse(result.has_changes)

    def test_a_fetch_error_is_captured_not_raised(self):
        result = self.check(import_sources.ImportSourceError("RBIHF is down"))

        self.assertEqual(result.error, "RBIHF is down")
        self.assertTrue(result.has_changes)
        self.assertEqual(result.as_dict()["error"], "RBIHF is down")


class CheckFixtureSourcesCommandTests(ImportSourcesTestBase):
    """events.tasks.check_fixture_sources -- the weekly sweep over every active club's
    current-season import sources, alerting only that club's admins, once."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # With logins -- notifications.services.recipient_emails only mails a member's own address when they have one.
        users = get_user_model().objects
        cls.admin = Member.objects.create(first_name="Ada", last_name="Admin", email="ada@example.com", user=users.create_user(email="ada@example.com", password="pw-secret-123"))
        cls.second_admin = Member.objects.create(first_name="Abe", last_name="Admin", email="abe@example.com", user=users.create_user(email="abe@example.com", password="pw-secret-123"))
        ClubRole.objects.create(club=cls.club, member=cls.admin, role=ClubRole.Roles.ADMIN)
        ClubRole.objects.create(club=cls.club, member=cls.second_admin, role=ClubRole.Roles.ADMIN)
        cls.editor = Member.objects.create(first_name="Ed", last_name="Editor", email="ed@example.com")
        ClubRole.objects.create(club=cls.club, member=cls.editor, role=ClubRole.Roles.EDITOR)
        cls.second_team = Team.objects.create(club=cls.club, name="Second Team", short_name="2nd")

    def setUp(self):
        super().setUp()
        self.enable_flag("RBIHF")

    def run_job(self):
        return call_command("check_fixture_sources", stdout=StringIO())

    def stored(self, team=None):
        team = team or self.team
        team.refresh_from_db()
        return team.import_sources

    def test_is_registered_as_a_weekly_job(self):
        meta = JOB_REGISTRY["events.tasks.check_fixture_sources"]

        self.assertEqual(meta["command"], "check_fixture_sources")
        self.assertEqual(meta["trigger"], {"trigger": "cron", "day_of_week": "mon", "hour": 6, "minute": 15})

    def test_notifies_only_the_clubs_admins_once_with_a_summary(self):
        self.import_rows([self.fixture_row(), self.fixture_row("5003", days=14)], label="Division 1")
        self.store(label="Division 1")
        self.store(self.second_team, label="Cup")
        html = rbihf_sample_html([self.fixture_row(hour="20:00"), self.fixture_row("5004", days=21)])

        with self.patch_fetch(html):
            result = self.run_job()

        notifications = Notification.objects.filter(club=self.club)
        self.assertEqual(set(notifications.values_list("member_id", flat=True)), {self.admin.pk, self.second_admin.pk})
        self.assertEqual(notifications.filter(member=self.admin).count(), 1)
        body = notifications.get(member=self.admin).body
        self.assertIn("First Team · RBIHF · Division 1: 1 game moved to another date or time, 1 new game, 1 game removed.", body)
        self.assertIn("Second Team · RBIHF · Cup: 2 new games.", body)
        self.assertIn("import button", body)
        self.assertIn("Checked 2 fixture source(s): 2 with changes", result)
        self.assertEqual(sorted(address for message in mail.outbox for address in message.to), ["abe@example.com", "ada@example.com"])

    def test_no_notification_when_nothing_changed(self):
        self.import_rows([self.fixture_row()])
        self.store()

        with self.patch_fetch(rbihf_sample_html([self.fixture_row(visit_name="Renamed Opponent")])):
            self.run_job()

        self.assertFalse(Notification.objects.exists())
        self.assertEqual(self.stored()[0]["last_check"], {"rescheduled": 0, "new_games": 0, "removed_games": 0, "error": ""})

    def test_writes_bookkeeping_on_the_entry(self):
        self.store()

        with self.patch_fetch(rbihf_sample_html([self.fixture_row()])):
            self.run_job()

        entry = self.stored()[0]
        self.assertEqual(entry["last_check"]["new_games"], 1)
        self.assertTrue(entry["last_checked_at"])
        self.assertEqual(entry["url"], self.RBIHF_URL)

    def test_a_source_whose_flag_is_off_is_skipped(self):
        self.store(source=import_sources.SOURCE_DFEL)  # DFEL flag never enabled for this club

        with self.patch_fetch(import_sources.ImportSourceError("should not be fetched")) as fetch:
            self.run_job()

        fetch.assert_not_called()
        self.assertNotIn("last_checked_at", self.stored()[0])
        self.assertFalse(Notification.objects.exists())

    def test_other_seasons_entries_are_skipped(self):
        old_season = Season.objects.create(club=self.club, start_date=self.season.start_date - timedelta(days=365), end_date=self.season.start_date - timedelta(days=1))
        self.store(season=old_season)

        with self.patch_fetch(import_sources.ImportSourceError("should not be fetched")) as fetch:
            self.run_job()

        fetch.assert_not_called()
        self.assertFalse(Notification.objects.exists())

    def test_archived_clubs_are_skipped(self):
        self.store()
        self.club.archive()

        with self.patch_fetch(import_sources.ImportSourceError("should not be fetched")) as fetch:
            self.run_job()

        fetch.assert_not_called()

    def test_a_fetch_error_is_alerted(self):
        self.store()

        with self.patch_fetch(import_sources.ImportSourceError("Could not reach rbihf.be")):
            self.run_job()

        body = Notification.objects.get(member=self.admin).body
        self.assertIn("First Team · RBIHF: the fixture list could not be fetched (Could not reach rbihf.be).", body)
        self.assertEqual(self.stored()[0]["last_check"]["error"], "Could not reach rbihf.be")

    def test_one_entry_blowing_up_does_not_stop_the_others(self):
        self.store()
        self.store(self.second_team)
        calls = []

        def fetch(entry):
            calls.append(entry)
            if len(calls) == 1:
                raise RuntimeError("parser bug")
            return rbihf_sample_html([self.fixture_row()])

        with self.patch_fetch(fetch):
            self.run_job()

        self.assertEqual(len(calls), 2)
        first, second = self.stored(self.team)[0], self.stored(self.second_team)[0]
        self.assertTrue(first["last_check"]["error"])
        self.assertEqual(second["last_check"]["new_games"], 1)
        self.assertEqual(Notification.objects.filter(member=self.admin).count(), 1)

    def test_one_notification_per_club(self):
        today = timezone.localdate()
        other_club = Club.objects.create(name="Other Club", slug="other-club")
        other_season = Season.objects.create(club=other_club, start_date=today - timedelta(days=10), end_date=today + timedelta(days=300))
        other_team = Team.objects.create(club=other_club, name="Other First", short_name="OF")
        other_admin = Member.objects.create(first_name="Oz", last_name="Other", email="oz@example.com")
        ClubRole.objects.create(club=other_club, member=other_admin, role=ClubRole.Roles.ADMIN)
        self.enable_flag("RBIHF", other_club)
        import_sources.record_source(other_team, source=import_sources.SOURCE_RBIHF, url=self.RBIHF_URL, season=other_season)
        self.store()

        with self.patch_fetch(rbihf_sample_html([self.fixture_row()])):
            self.run_job()

        self.assertEqual(Notification.objects.filter(club=self.club, member=self.admin).count(), 1)
        self.assertEqual(list(Notification.objects.filter(club=other_club).values_list("member_id", flat=True)), [other_admin.pk])


class IJNLImportSourceTests(ImportSourcesTestBase):
    IJNL_URL = "https://www.ijshockey.nl/competities/team/64414?seizoen=2026%2F27"

    def test_a_stored_ijnl_entry_is_fetched_and_planned_for_its_season(self):
        entry = self.store(source=import_sources.SOURCE_IJNL, url=self.IJNL_URL)
        with mock.patch("events.services.ijnl_import.requests.get", return_value=ijnl_response(ijnl_team_data([ijnl_game("h1")]))) as mock_get:
            raw = import_sources.fetch(entry)

        self.assertEqual(mock_get.call_args.kwargs["params"], {"seizoen": IJNL_SEASON})
        plan = import_sources.build_plan_for(self.club, self.team, entry, raw)
        self.assertEqual((plan.competition, plan.source_id), ("IJNL", IJNL_SOURCE_ID))

    def test_a_stored_entry_without_a_season_is_an_error(self):
        entry = self.store(source=import_sources.SOURCE_IJNL, url="https://www.ijshockey.nl/competities/team/64414")

        with self.assertRaises(import_sources.ImportSourceError):
            import_sources.source_id_for(entry)


def rbihf_league_html(played=(), upcoming=(), rule="Won: 2 points | Draw: 1 point | Lost: 0 points", friendly_played=()):
    """A cut-down RBIHF league page: a ranked "Regular Competition" tab (type-513)
    and an unranked "Friendly Games" tab (type-498). ``played`` rows are
    (game_id, home_id, home_name, away_id, away_name, score); ``upcoming`` rows
    the same without the score."""

    def team_cell(team_id, name):
        return f'<td><a href="/league/team/{team_id}" title="{name}">{name}</a></td>'

    def played_table(rows):
        body = "".join(
            f'<tr><td class="game-nr"><a href="/game/{game_id}">{game_id}</a></td><td class="date">27 Sep 2026</td><td class="hour">20:00</td><td>Deurne</td>{team_cell(home_id, home)}{team_cell(away_id, away)}<td class="score">{score}</td></tr>'
            for game_id, home_id, home, away_id, away, score in rows
        )
        return f"<h2>Played games</h2><table><tr><th>#</th><th>Date</th><th>Hour</th><th>Location</th><th>Home</th><th>Visit</th><th>Score</th></tr>{body}</table>"

    def upcoming_table(rows):
        body = "".join(f'<tr><td><a href="/game/{game_id}">{game_id}</a></td><td>04 Oct 2026</td><td>18:00</td><td>Mechelen</td>{team_cell(home_id, home)}{team_cell(away_id, away)}</tr>' for game_id, home_id, home, away_id, away in rows)
        return f"<h2>Upcoming games</h2><table><tr><th>#</th><th>Date</th><th>Hour</th><th>Location</th><th>Home</th><th>Visit</th></tr>{body}</table>"

    return f"""<html><head><title>RBIHF :: Under 18</title></head><body>
    <ul><li><a href="#type-all">Summary</a></li><li><a href="#type-498">Friendly Games</a></li><li><a href="#type-513">Regular Competition</a></li></ul>
    <div id="type-all" class="tab">{played_table(played)}</div>
    <div id="type-498" class="tab">{played_table(friendly_played)}</div>
    <div id="type-513" class="tab">{upcoming_table(upcoming)}{played_table(played)}
      <div class="node page ranking"><div class="content"><img src="/images/league/ranking/513" /></div><div class="meta">{rule}<br /><a href="/league/u18/stats">More statistics</a>.</div></div>
    </div></body></html>"""


RBIHF_OT_RULE = "Won: 3 points | Won after overtime or penaltyshots: 2 points | Lost after overtime or penaltyshots: 1 point | Lost: 0 points"
RBIHF_TEAM_PAGE_HTML = '<html><body><a href="/league/u18" title="Under 18">Under 18</a><a href="/league/team/4448">Antwerp</a></body></html>'


def standings_rows(standings, competition=0):
    return [(row["position"], row["team_name"], row["played"], row["points"]) for row in standings["competitions"][competition]["rows"]]


class RBIHFStandingsParsingTests(TestCase):
    PLAYED = [
        ("816", "4448", "Antwerp", "4450", "Sharks", "3-6"),
        ("823", "4445", "Haskey", "4443", "Bulldogs", "9-1"),
        ("900", "4443", "Bulldogs", "4448", "Antwerp", "Unfinished"),
    ]
    UPCOMING = [("821", "4445", "Haskey", "4447", "Leuven")]

    def test_points_rules_are_parsed(self):
        simple = rbihf_standings.parse_points_rule("Won: 2 points | Draw: 1 point | Lost: 0 points More statistics.")
        self.assertEqual((simple.win, simple.draw, simple.loss, simple.has_overtime), (2, 1, 0, False))

        overtime = rbihf_standings.parse_points_rule(RBIHF_OT_RULE)
        self.assertEqual((overtime.win, overtime.ot_win, overtime.ot_loss, overtime.loss, overtime.has_overtime), (3, 2, 1, 0, True))

    def test_only_ranked_tabs_are_parsed_with_every_team_and_finished_games(self):
        [competition] = rbihf_standings.parse_league(rbihf_league_html(self.PLAYED, self.UPCOMING, friendly_played=[("2004", "4450", "Sharks", "4445", "Haskey", "1-5")]))

        self.assertEqual((competition.type_id, competition.name), ("513", "Regular Competition"))
        self.assertEqual(set(competition.teams), {"4448", "4450", "4445", "4443", "4447"})
        self.assertEqual([game.game_id for game in competition.games], ["816", "823"])  # "Unfinished" doesn't count
        self.assertEqual(rbihf_standings.league_name(rbihf_league_html()), "Under 18")

    def test_the_table_is_ordered_by_points_then_goal_difference_then_name(self):
        [competition] = rbihf_standings.parse_league(rbihf_league_html(self.PLAYED, self.UPCOMING))
        rows = rbihf_standings.compute_table(competition)

        self.assertEqual([(row["position"], row["team_name"], row["points"], row["goal_difference"]) for row in rows], [(1, "Haskey", 2, 8), (2, "Sharks", 2, 3), (3, "Leuven", 0, 0), (4, "Antwerp", 0, -3), (5, "Bulldogs", 0, -8)])
        sharks = rows[1]
        self.assertEqual((sharks["played"], sharks["wins"], sharks["losses"], sharks["goals_for"], sharks["goals_against"]), (1, 1, 0, 6, 3))

    def test_a_draw_scores_the_draw_points(self):
        [competition] = rbihf_standings.parse_league(rbihf_league_html([("1", "4448", "Antwerp", "4450", "Sharks", "2-2")]))
        rows = rbihf_standings.compute_table(competition)

        self.assertEqual([(row["draws"], row["points"]) for row in rows], [(1, 1), (1, 1)])

    def test_a_one_goal_game_decided_after_regulation_earns_overtime_points(self):
        played = [("1", "4448", "Antwerp", "4450", "Sharks", "3-2"), ("2", "4445", "Haskey", "4443", "Bulldogs", "4-3"), ("3", "4447", "Leuven", "4442", "Lions", "5-1")]
        [competition] = rbihf_standings.parse_league(rbihf_league_html(played, rule=RBIHF_OT_RULE))
        checked = []

        def overtime_check(game):
            checked.append(game.game_id)
            return game.game_id == "1"

        rows = {row["team_name"]: row for row in rbihf_standings.compute_table(competition, overtime_check=overtime_check)}

        self.assertEqual(checked, ["1", "2"])  # a three-goal margin can't have gone to overtime
        self.assertEqual((rows["Antwerp"]["ot_wins"], rows["Antwerp"]["points"]), (1, 2))
        self.assertEqual((rows["Sharks"]["ot_losses"], rows["Sharks"]["points"]), (1, 1))
        self.assertEqual((rows["Haskey"]["wins"], rows["Haskey"]["points"]), (1, 3))
        self.assertEqual(rows["Leuven"]["points"], 3)

    def test_a_league_without_overtime_points_never_asks_for_the_overview(self):
        [competition] = rbihf_standings.parse_league(rbihf_league_html([("1", "4448", "Antwerp", "4450", "Sharks", "3-2")]))
        overtime_check = mock.Mock()

        rbihf_standings.compute_table(competition, overtime_check=overtime_check)

        overtime_check.assert_not_called()

    def test_the_overview_tells_whether_regulation_ended_level(self):
        def overview(home, away):
            def cells(values):
                return "".join(f"<td>{value}</td>" for value in values)

            home_row = f'<tr><td rowspan="2" class="goals">Goals</td><td class="title">A</td>{cells(home)}<td>{sum(home)}</td></tr>'
            away_row = f'<tr><td class="title">B</td>{cells(away)}<td>{sum(away)}</td></tr>'
            return {"success": True, "data": f'<table><tr><th colspan="2">Per Period</th></tr>{home_row}{away_row}</table>'}

        self.assertFalse(rbihf_standings.parse_overview(overview([0, 3, 2], [0, 2, 0])))
        self.assertTrue(rbihf_standings.parse_overview(overview([1, 1, 0, 1], [0, 2, 0, 0])))
        self.assertIsNone(rbihf_standings.parse_overview({"success": False, "data": ""}))

    def test_season_code_runs_summer_to_summer(self):
        self.assertEqual(rbihf_standings.season_code(date(2026, 9, 27)), "2627")
        self.assertEqual(rbihf_standings.season_code(date(2027, 3, 1)), "2627")


class RBIHFStandingsRefreshTests(ImportSourcesTestBase):
    TEAM_URL = "https://www.rbihf.be/league/team/4448"
    PLAYED = [("816", "4448", "Antwerp", "4450", "Sharks", "3-6")]

    def patch_fetch(self, *pages):
        return mock.patch("events.services.rbihf_standings.fetch_html", side_effect=list(pages))

    def stored(self, team=None):
        (team or self.team).refresh_from_db()
        return (team or self.team).import_sources[0]["standings"]

    def test_refresh_finds_the_league_and_stores_the_table(self):
        entry = self.store(url=self.TEAM_URL)
        with self.patch_fetch(RBIHF_TEAM_PAGE_HTML, rbihf_league_html(self.PLAYED)) as mock_fetch:
            self.assertTrue(rbihf_standings.refresh_entry(self.team, entry))

        self.assertEqual([call.args[0] for call in mock_fetch.call_args_list], [self.TEAM_URL, "https://www.rbihf.be/league/u18"])
        standings = self.stored()
        self.assertEqual((standings["league"], standings["league_slug"], standings["rbihf_team_id"], standings["error"]), ("Under 18", "u18", "4448", ""))
        self.assertEqual(standings_rows(standings), [(1, "Sharks", 1, 2), (2, "Antwerp", 1, 0)])

    def test_the_league_page_is_cached_and_the_league_remembered(self):
        entry = self.store(url=self.TEAM_URL)
        with self.patch_fetch(RBIHF_TEAM_PAGE_HTML, rbihf_league_html(self.PLAYED)):
            rbihf_standings.refresh_entry(self.team, entry)

        self.team.refresh_from_db()
        with self.patch_fetch() as mock_fetch:
            self.assertTrue(rbihf_standings.refresh_entry(self.team, self.team.import_sources[0]))
        mock_fetch.assert_not_called()

        with self.patch_fetch(rbihf_league_html([*self.PLAYED, ("817", "4450", "Sharks", "4448", "Antwerp", "1-4")])) as mock_fetch:
            rbihf_standings.refresh_entry(self.team, self.team.import_sources[0], fresh=True)
        mock_fetch.assert_called_once_with("https://www.rbihf.be/league/u18")
        self.assertEqual(standings_rows(self.stored()), [(1, "Antwerp", 2, 2), (2, "Sharks", 2, 2)])

    def test_a_failed_refresh_keeps_the_last_good_table(self):
        entry = self.store(url=self.TEAM_URL)
        with self.patch_fetch(RBIHF_TEAM_PAGE_HTML, rbihf_league_html(self.PLAYED)):
            rbihf_standings.refresh_entry(self.team, entry)
        cache.clear()

        self.team.refresh_from_db()
        with self.patch_fetch(RBIHFImportError("RBIHF is down")):
            self.assertFalse(rbihf_standings.refresh_entry(self.team, self.team.import_sources[0]))

        standings = self.stored()
        self.assertEqual(standings["error"], "RBIHF is down")
        self.assertEqual(standings_rows(standings), [(1, "Sharks", 1, 2), (2, "Antwerp", 1, 0)])

    def test_a_reimport_keeps_the_standings_only_for_the_same_url(self):
        entry = self.store(url=self.TEAM_URL)
        import_sources.update_entry(self.team, entry, standings={"competitions": []})

        self.store(url=self.TEAM_URL)
        self.assertIn("standings", self.team.import_sources[0])

        self.store(url="https://www.rbihf.be/league/team/9999")
        self.assertNotIn("standings", self.team.import_sources[0])

    def test_a_finished_game_refreshes_its_teams_matching_source_without_the_cache(self):
        self.enable_flag("RBIHF")
        self.store(url=self.TEAM_URL)
        self.store(url="https://www.rbihf.be/league/team/5555", label="Cup")
        game = self.make_event(kind=Event.EventKind.GAME, competition="RBIHF", external_game_id="816", external_source_id="4448")
        game.teams.add(self.team)

        with mock.patch("events.services.rbihf_standings.refresh_entry", return_value=True) as mock_refresh:
            rbihf_standings.refresh_for_finished_game(game)

        [call] = mock_refresh.call_args_list
        self.assertEqual((call.args[1]["url"], call.kwargs["fresh"]), (self.TEAM_URL, True))

    def test_a_finished_game_does_nothing_without_the_rbihf_feature(self):
        self.store(url=self.TEAM_URL)
        game = self.make_event(kind=Event.EventKind.GAME, competition="RBIHF", external_game_id="816", external_source_id="4448")
        game.teams.add(self.team)

        with mock.patch("events.services.rbihf_standings.refresh_entry") as mock_refresh:
            rbihf_standings.refresh_for_finished_game(game)

        mock_refresh.assert_not_called()

    def test_the_poll_refreshes_standings_when_a_game_finishes(self):
        self.enable_flag("RBIHF")
        game = self.make_event(kind=Event.EventKind.GAME, competition="RBIHF", external_game_id="1234", is_live=True, start=timezone.now() - timedelta(hours=2), end=timezone.now() - timedelta(minutes=10))
        response = mock.Mock(status_code=200)
        response.json.return_value = {"live": False, "scoreA": 2, "scoreB": 3}

        with mock.patch("events.competition.hockey.requests.get", return_value=response), mock.patch("events.management.commands.poll_live_game_results.refresh_for_finished_game") as mock_refresh:
            call_command("poll_live_game_results")

        mock_refresh.assert_called_once_with(game)

    def test_the_nightly_job_refreshes_only_clubs_with_the_rbihf_feature(self):
        other_club = Club.objects.create(name="Rival", slug="rival-standings")
        Season.objects.create(club=other_club, start_date=self.season.start_date, end_date=self.season.end_date)
        other_team = Team.objects.create(club=other_club, name="Rival Team", short_name="RT")
        self.enable_flag("RBIHF")
        self.store(url=self.TEAM_URL)
        import_sources.record_source(other_team, source=import_sources.SOURCE_RBIHF, url=self.TEAM_URL, season=Season.objects.get(club=other_club))

        with mock.patch("events.services.rbihf_standings.refresh_entry", return_value=True) as mock_refresh:
            result = call_command("refresh_standings")

        self.assertEqual([call.args[0] for call in mock_refresh.call_args_list], [self.team])
        self.assertIn("Refreshed standings for 1 competition source(s); 0 failed.", result)


class SetGatheringTimesCommandTests(EventsTestBase):
    """events.management.commands.set_gathering_times -- fills in a missing
    gathering time N minutes before the start, never overwriting one."""

    def setUp(self):
        # Maintenance is cached (features.models) -- same leak concern as the job command tests.
        cache.clear()
        self.addCleanup(cache.clear)

    def run_command(self, *args):
        out = StringIO()
        call_command("set_gathering_times", *args, stdout=out)
        return out.getvalue()

    def test_sets_a_missing_gathering_time_and_skips_an_existing_one(self):
        empty = self.make_event(title="No gathering")
        existing_gathering = self.future - timedelta(minutes=15)
        kept = self.make_event(title="Has gathering", gathering=existing_gathering)

        output = self.run_command("45")

        empty.refresh_from_db()
        kept.refresh_from_db()
        self.assertEqual(empty.gathering, empty.start - timedelta(minutes=45))
        self.assertEqual(kept.gathering, existing_gathering)
        self.assertIn("on 1 event(s); skipped 1 that already had one", output)

    def test_past_and_cancelled_events_are_left_alone_by_default(self):
        past = self.make_event(start=timezone.now() - timedelta(days=2))
        cancelled = self.make_event(cancelled=True)

        self.run_command("30")

        past.refresh_from_db()
        cancelled.refresh_from_db()
        self.assertIsNone(past.gathering)
        self.assertIsNone(cancelled.gathering)

        self.run_command("30", "--include-past")

        past.refresh_from_db()
        self.assertEqual(past.gathering, past.start - timedelta(minutes=30))

    def test_club_and_kind_filters(self):
        other_club = Club.objects.create(name="Other", slug="other-gathering")
        other_event = self.make_event(club=other_club, season=None)
        training = self.make_event(kind=Event.EventKind.TRAINING)
        game = self.make_event(kind=Event.EventKind.GAME)

        self.run_command("60", "--club", self.club.slug, "--kind", "game")

        for event in (other_event, training, game):
            event.refresh_from_db()
        self.assertEqual((other_event.gathering, training.gathering), (None, None))
        self.assertEqual(game.gathering, game.start - timedelta(minutes=60))

    def test_dry_run_saves_nothing(self):
        event = self.make_event(title="Cup final")

        output = self.run_command("45", "--dry-run")

        event.refresh_from_db()
        self.assertIsNone(event.gathering)
        self.assertIn("Cup final", output)
        self.assertIn("Would set the gathering time 45 min before the start on 1 event(s)", output)

    def test_rejects_a_non_positive_number_or_unknown_club(self):
        with self.assertRaisesMessage(CommandError, "positive"):
            self.run_command("0")
        with self.assertRaisesMessage(CommandError, "No club"):
            self.run_command("30", "--club", "nope")


TIMELINE_FIXTURES = Path(__file__).resolve().parent / "test_fixtures" / "game_timeline"


def timeline_fixture(name):
    """A real per-game payload (players pseudonymised) -- see events/test_fixtures/game_timeline."""
    return (TIMELINE_FIXTURES / name).read_text()


def rbihf_game_data():
    return timeline_parsers.rbihf_extract_gd(timeline_fixture("rbihf_game_137.html"))


class TimelineParserTests(TestCase):
    """events.services.timeline_parsers against saved real payloads -- the
    expected totals are the real results of those games."""

    def parse_all(self):
        return {
            "rbihf": timeline_parsers.finalize_document(timeline_parsers.parse_rbihf(timeline_fixture("rbihf_timeline_137.json"), rbihf_game_data())),
            "cehl": timeline_parsers.finalize_document(timeline_parsers.parse_cehl(timeline_fixture("cehl_timeline_7101.json"))),
            "dfel": timeline_parsers.finalize_document(timeline_parsers.parse_hockeydata(timeline_fixture("hockeydata_report.json"))),
            "ijnl": timeline_parsers.finalize_document(timeline_parsers.parse_ijnl(timeline_fixture("ijnl_game_920c9dc5.json"))),
        }

    def test_every_source_yields_the_real_score_with_unique_ids_in_order(self):
        expected = {"rbihf": ({"home": 5, "away": 2}, 7), "cehl": ({"home": 1, "away": 4}, 5), "dfel": ({"home": 5, "away": 1}, 6), "ijnl": ({"home": 4, "away": 0}, 4)}
        for source, document in self.parse_all().items():
            with self.subTest(source=source):
                score, goals = expected[source]
                events = document["events"]
                self.assertEqual(document["score"], score)
                self.assertEqual(sum(event["type"] == "goal" for event in events), goals)
                self.assertEqual(len({event["id"] for event in events}), len(events))
                seconds = [event["seconds"] for event in events]
                self.assertEqual(seconds, sorted(seconds))
                self.assertEqual(document["version"], timeline_parsers.VERSION)
                self.assertTrue(all(event["goal"]["scorer"]["name"] for event in events if event["type"] == "goal"))

    def test_period_scores(self):
        documents = self.parse_all()

        self.assertEqual([(p["label"], p["home"], p["away"]) for p in documents["rbihf"]["periods"]], [("1", 0, 0), ("2", 3, 2), ("3", 2, 0)])
        self.assertEqual([(p["label"], p["home"], p["away"]) for p in documents["ijnl"]["periods"]], [("1", 3, 0), ("2", 1, 0), ("3", 0, 0)])

    def test_rbihf_goal_and_penalty_details(self):
        events = self.parse_all()["rbihf"]["events"]
        goal = next(event for event in events if event["type"] == "goal")
        penalty = next(event for event in events if event["type"] == "penalty")

        self.assertEqual((goal["side"], goal["period"], goal["goal"]["strength"], goal["goal"]["score_after"], len(goal["goal"]["assists"])), ("away", "2", "EQ", {"home": 0, "away": 1}, 2))
        self.assertIsInstance(goal["goal"]["scorer"]["number"], int)
        self.assertEqual((penalty["penalty"]["code"], penalty["penalty"]["description"], penalty["penalty"]["minutes"]), ("HOOK", "Hooking", 2))
        self.assertEqual({event["type"] for event in events}, {"goal", "penalty", "goalie_change", "timeout", "period_start", "period_end"})

    def test_rbihf_without_the_roster_still_parses_but_has_no_names(self):
        row = '<tr class="goal"><td class="time">01:00</td><td class="event">Goal<br />EQ</td><td class="team"></td><td class="note"><span> (#)</span> scored the <span class="highlight">1-0</span>, no assists.</td></tr>'
        bare = json.dumps({"success": True, "live": False, "data": f"<table>{row}</table>"})

        [goal] = timeline_parsers.parse_rbihf(bare, {})["events"]

        self.assertEqual((goal["goal"]["scorer"], goal["side"]), ({"name": ""}, None))

    def test_hockeydata_ids_strength_and_hyphenated_codes(self):
        events = self.parse_all()["dfel"]["events"]
        powerplay = next(event for event in events if event["type"] == "goal" and event["goal"]["strength"] == "PP1")
        hyphenated = next(event for event in events if event["type"] == "penalty" and event["penalty"]["code"] == "HI-ST")

        self.assertTrue(powerplay["id"].startswith("hd:"))
        self.assertEqual(hyphenated["penalty"]["description"], "High Sticking")
        self.assertEqual(self.parse_all()["dfel"]["team_stats"]["home"]["pp_goals"], 2)

    def test_ijnl_state_badges_and_keys(self):
        document = self.parse_all()["ijnl"]
        winner = next(event for event in document["events"] if event["type"] == "goal" and event["goal"]["game_winning"])

        self.assertEqual(document["status"], "final")
        self.assertTrue(winner["id"].startswith("ijnl:goal:home:13:39"))
        self.assertEqual(next(event for event in document["events"] if event["type"] == "penalty")["penalty"]["minutes"], 2)

    def test_a_malformed_payload_is_a_parse_error(self):
        for parse, payload in [(timeline_parsers.parse_cehl, "<html>"), (timeline_parsers.parse_hockeydata, json.dumps({"statusId": 0, "statusMsg": "Invalid apiKey"})), (timeline_parsers.parse_ijnl, "nope")]:
            with self.subTest(parse=parse.__name__), self.assertRaises(timeline_parsers.TimelineParseError):
                parse(payload)
        with self.assertRaises(timeline_parsers.TimelineParseError):
            timeline_parsers.rbihf_extract_gd("<html></html>")

    def test_rbihf_form_fields_nest_like_jquery(self):
        fields = dict(timeline_parsers.rbihf_form_fields({"gameNr": "137", "home": {"team": {"18438": {"nr": 35}}}}))

        self.assertEqual(fields, {"gd[gameNr]": "137", "gd[home][team][18438][nr]": "35"})


class GameTimelineTestBase(EventsTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.home_location = Location.objects.create(club=cls.club, name="Home Arena", address="1 St", city="Hasselt", zip_code="3500", country="BE", is_home=True)
        cls.opponent = Opponent.objects.create(club=cls.club, name="Olympia Heist")

    def setUp(self):
        # Flag membership is cached (features.models) -- cleared so one test's flag doesn't leak into the next.
        cache.clear()
        self.addCleanup(cache.clear)

    def enable(self, *flags):
        for name in flags:
            get_waffle_flag_model().objects.get_or_create(name=name)[0].clubs.add(self.club)
        cache.clear()

    def make_game(self, **kwargs):
        kwargs.setdefault("kind", Event.EventKind.GAME)
        kwargs.setdefault("competition", "RBIHF")
        kwargs.setdefault("external_game_id", "137")
        kwargs.setdefault("location", self.home_location)
        kwargs.setdefault("opponent", self.opponent)
        kwargs.setdefault("start", timezone.now() - timedelta(hours=1))
        game = self.make_event(**kwargs)
        game.teams.add(self.team)
        return game

    def rbihf_fetch(self):
        raw = timeline_fixture("rbihf_timeline_137.json")
        return game_timeline.TimelineFetch(document=timeline_parsers.parse_rbihf(raw, rbihf_game_data()), raw=raw)


class GameTimelineStoreTests(GameTimelineTestBase):
    def test_store_saves_the_document_and_skips_an_unchanged_one(self):
        game = self.make_game()

        timeline, changed = game_timeline.store(game, self.rbihf_fetch())
        first_changed_at = timeline.changed_at
        timeline_again, changed_again = game_timeline.store(game, self.rbihf_fetch())

        self.assertTrue(changed)
        self.assertFalse(changed_again)
        self.assertEqual(timeline_again.changed_at, first_changed_at)
        self.assertEqual((timeline.source, timeline.status, timeline.data["score"]), ("RBIHF", GameTimeline.Status.FINAL, {"home": 5, "away": 2}))
        self.assertTrue(timeline.raw)

    def test_a_live_game_is_stored_as_live(self):
        game = self.make_game(is_live=True)

        timeline, _changed = game_timeline.store(game, self.rbihf_fetch())

        self.assertEqual(timeline.status, GameTimeline.Status.LIVE)

    def test_display_periods_is_from_our_side(self):
        home_game = self.make_game()
        away_game = self.make_game(external_game_id="138", location=None)
        for game in (home_game, away_game):
            game_timeline.store(game, self.rbihf_fetch())

        home_rows = [row for period in game_timeline.display_periods(home_game.timeline, home_game) for row in period["rows"] if row["type"] == "goal"]
        away_rows = [row for period in game_timeline.display_periods(away_game.timeline, away_game) for row in period["rows"] if row["type"] == "goal"]

        self.assertEqual((home_rows[0]["ours"], home_rows[0]["team"], home_rows[0]["score"]), (False, "Olympia Heist", "0-1"))
        self.assertEqual((away_rows[0]["ours"], away_rows[0]["team"], away_rows[0]["score"]), (True, "1st", "1-0"))
        self.assertTrue(home_rows[0]["text"].startswith("Goal: "))


class GameTimelineFetchTests(GameTimelineTestBase):
    """fetch_game_info's timeline hook -- gated on the "game_timeline" flag on
    top of the competition flag, and never allowed to break the score update."""

    def time_php(self, live=False, score_a=5, score_b=2):
        response = mock.Mock(status_code=200)
        response.json.return_value = {"live": live, "scoreA": score_a, "scoreB": score_b}
        return response

    def test_no_timeline_without_the_flag(self):
        self.enable("RBIHF")
        game = self.make_game()

        with mock.patch("events.competition.hockey.requests.get", return_value=self.time_php()), mock.patch("events.competition.hockey.RBIHF.fetch_timeline") as mock_timeline:
            self.assertTrue(fetch_game_info(game))

        mock_timeline.assert_not_called()
        self.assertFalse(GameTimeline.objects.filter(event=game).exists())

    def test_the_timeline_is_stored_with_the_score(self):
        self.enable("RBIHF", "game_timeline")
        game = self.make_game()

        with mock.patch("events.competition.hockey.requests.get", return_value=self.time_php()), mock.patch("events.competition.hockey.RBIHF.fetch_timeline", return_value=self.rbihf_fetch()):
            fetch_game_info(game)

        game.refresh_from_db()
        self.assertEqual((game.score_for, game.score_against), (5, 2))
        self.assertEqual(len(game.timeline.data["events"]), 27)

    def test_a_timeline_failure_keeps_the_score_and_the_previous_data(self):
        self.enable("RBIHF", "game_timeline")
        game = self.make_game()
        game_timeline.store(game, self.rbihf_fetch())

        with mock.patch("events.competition.hockey.requests.get", return_value=self.time_php(score_a=6)), mock.patch("events.competition.hockey.RBIHF.fetch_timeline", side_effect=requests.ConnectionError("RBIHF is down")):
            self.assertTrue(fetch_game_info(game))

        game.refresh_from_db()
        game.timeline.refresh_from_db()
        self.assertEqual(game.score_for, 6)
        self.assertEqual(game.timeline.last_error, "RBIHF is down")
        self.assertEqual(len(game.timeline.data["events"]), 27)

    def test_rbihf_posts_the_game_pages_roster_and_caches_it(self):
        game = self.make_game()
        page = mock.Mock(status_code=200, text=timeline_fixture("rbihf_game_137.html"))
        page.raise_for_status = mock.Mock()
        timeline = mock.Mock(status_code=200, text=timeline_fixture("rbihf_timeline_137.json"))
        timeline.raise_for_status = mock.Mock()

        with mock.patch("events.competition.hockey.requests.get", return_value=page) as mock_get, mock.patch("events.competition.hockey.requests.post", return_value=timeline) as mock_post:
            fetched = RBIHF().fetch_timeline(game)
            RBIHF().fetch_timeline(game)

        mock_get.assert_called_once()
        self.assertIn(f"/game/{self.season.start_date:%y}{self.season.end_date:%y}/137", mock_get.call_args.args[0])
        self.assertEqual(mock_post.call_count, 2)
        self.assertIn(("gd[gameNr]", "137"), mock_post.call_args.kwargs["data"])
        self.assertEqual(sum(event["type"] == "goal" for event in fetched.document["events"]), 7)

    def test_cehl_and_ijnl_reuse_what_the_score_fetch_downloaded(self):
        cehl_game = self.make_game(competition="CEHL", external_game_id="7101")
        cehl = CEHL()
        cehl.timeline_payload = (cehl_game.pk, timeline_fixture("cehl_timeline_7101.json"))
        ijnl_game = self.make_game(competition="IJNL", external_game_id="920c9dc5")
        ijnl = IJNL()
        ijnl.game_payload = (ijnl_game.pk, json.loads(timeline_fixture("ijnl_game_920c9dc5.json")))

        with mock.patch("events.competition.hockey.requests.get") as mock_get, mock.patch("events.services.ijnl_import.requests.get") as mock_ijnl_get:
            cehl_events = cehl.fetch_timeline(cehl_game).document["events"]
            ijnl_events = ijnl.fetch_timeline(ijnl_game).document["events"]

        mock_get.assert_not_called()
        mock_ijnl_get.assert_not_called()
        self.assertEqual((len(cehl_events), len(ijnl_events)), (24, 13))

    def test_dfel_reads_the_game_report_with_a_cached_key(self):
        game = self.make_game(competition="DFEL", external_game_id="c441c652", external_source_id="70779/21691")

        with mock.patch("events.services.dfel_import.fetch_api_key", return_value="key") as mock_key, mock.patch("events.services.dfel_import.fetch_game_report", return_value=timeline_fixture("hockeydata_report.json")) as mock_report:
            DFEL().fetch_timeline(game)
            fetched = DFEL().fetch_timeline(game)

        mock_key.assert_called_once_with("70779", "21691")
        mock_report.assert_called_with("key", "c441c652")
        self.assertEqual(fetched.document["source"], "DFEL")


class SettleGameTimelinesTests(GameTimelineTestBase):
    def finished_game(self, **kwargs):
        game = self.make_game(start=timezone.now() - timedelta(hours=5), **kwargs)
        game_timeline.store(game, self.rbihf_fetch())
        return game

    def test_an_unchanged_final_timeline_is_finalized_and_a_changed_one_is_not(self):
        self.enable("RBIHF", "game_timeline")
        stable = self.finished_game()
        corrected = self.finished_game(external_game_id="138")
        changed_document = self.rbihf_fetch()
        changed_document.document["events"] = changed_document.document["events"][:-1]

        def fetch(event):
            return changed_document if event.pk == corrected.pk else self.rbihf_fetch()

        with mock.patch("events.competition.hockey.RBIHF.fetch_timeline", side_effect=fetch):
            result = call_command("settle_game_timelines")

        stable.timeline.refresh_from_db()
        corrected.timeline.refresh_from_db()
        self.assertIsNotNone(stable.timeline.finalized_at)
        self.assertIsNone(corrected.timeline.finalized_at)
        self.assertIn("1 finalized, 1 still changing", result)

    def test_games_of_clubs_without_the_flag_or_too_recent_are_left_alone(self):
        self.enable("RBIHF")
        self.finished_game()
        self.enable("game_timeline")
        recent = self.make_game(external_game_id="139", start=timezone.now() - timedelta(minutes=30))
        game_timeline.store(recent, self.rbihf_fetch())
        get_waffle_flag_model().objects.get(name="game_timeline").clubs.remove(self.club)
        cache.clear()

        with mock.patch("events.competition.hockey.RBIHF.fetch_timeline") as mock_fetch:
            call_command("settle_game_timelines")

        mock_fetch.assert_not_called()


class FetchGameTimelinesCommandTests(GameTimelineTestBase):
    def test_backfills_played_games_and_finalizes_old_ones(self):
        self.enable("RBIHF", "game_timeline")
        old = self.make_game(start=timezone.now() - timedelta(days=20))
        recent = self.make_game(external_game_id="138", start=timezone.now() - timedelta(days=1))
        self.make_game(external_game_id="139", start=timezone.now() + timedelta(days=3))  # not played yet

        with mock.patch("events.competition.hockey.RBIHF.fetch_timeline", side_effect=lambda event: self.rbihf_fetch()):
            call_command("fetch_game_timelines", stdout=StringIO())

        old.timeline.refresh_from_db()
        recent.timeline.refresh_from_db()
        self.assertIsNotNone(old.timeline.finalized_at)
        self.assertIsNone(recent.timeline.finalized_at)
        self.assertEqual(GameTimeline.objects.count(), 2)

    def test_dry_run_fetches_nothing_and_the_flag_is_required(self):
        self.enable("RBIHF", "game_timeline")
        self.make_game()
        out = StringIO()

        with mock.patch("events.competition.hockey.RBIHF.fetch_timeline") as mock_fetch:
            call_command("fetch_game_timelines", "--dry-run", stdout=out)
        mock_fetch.assert_not_called()
        self.assertIn("Would fetch 1 game timeline(s)", out.getvalue())

        get_waffle_flag_model().objects.get(name="game_timeline").clubs.remove(self.club)
        cache.clear()
        with self.assertRaisesMessage(CommandError, "game_timeline flag"):
            call_command("fetch_game_timelines", "--club", self.club.slug, stdout=StringIO())


def moment_doc(status, events, home=0, away=0):
    return {"status": status, "score": {"home": home, "away": away}, "events": events}


def goal_item(event_id, side, seconds, home, away, name="Sam"):
    return {
        "id": event_id,
        "type": "goal",
        "side": side,
        "period": "1",
        "seconds": seconds,
        "time": f"{seconds // 60:02d}:{seconds % 60:02d}",
        "goal": {"scorer": {"name": name, "number": 9}, "assists": [], "score_after": {"home": home, "away": away}},
    }


class DeriveMomentsTests(TestCase):
    """events.services.game_moments.derive_moments -- the pure rules."""

    def kinds(self, previous, current, our_side="home", first_fetch=False):
        return [moment.kind for moment in game_moments.derive_moments(previous, current, our_side=our_side, first_fetch=first_fetch)]

    def test_the_first_fetch_is_silent(self):
        current = moment_doc("live", [goal_item("g1", "home", 60, 1, 0)], home=1)

        self.assertEqual(self.kinds(None, current, first_fetch=True), [])

    def test_kick_off_goals_from_our_side_and_the_result(self):
        scheduled = moment_doc("scheduled", [])
        live = moment_doc("live", [goal_item("g1", "home", 60, 1, 0)], home=1)
        final = moment_doc("final", [goal_item("g1", "home", 60, 1, 0), goal_item("g2", "away", 120, 1, 1)], home=1, away=1)

        self.assertEqual(self.kinds(scheduled, live), ["game_started", "goal_for"])
        self.assertEqual(self.kinds(live, final), ["goal_against", "game_final"])
        self.assertEqual(self.kinds(live, final, our_side="away"), ["goal_for", "game_final"])

    def test_the_score_is_from_our_side(self):
        live = moment_doc("live", [])
        current = moment_doc("live", [goal_item("g1", "home", 60, 1, 0)], home=1)

        [moment] = game_moments.derive_moments(live, current, our_side="away", first_fetch=False)

        self.assertEqual((moment.kind, moment.payload["score"], moment.key), ("goal_against", {"for": 0, "against": 1}, "goal_against:g1"))

    def test_penalties_and_period_ends(self):
        live = moment_doc("live", [])
        current = moment_doc(
            "live",
            [
                {"id": "p1", "type": "penalty", "side": "away", "period": "1", "seconds": 300, "penalty": {"player": {"name": "Rex"}, "description": "Tripping", "minutes": 2}},
                {"id": "pe1", "type": "period_end", "side": None, "period": "1", "seconds": 1200},
            ],
        )

        self.assertEqual(self.kinds(live, current), ["penalty_against", "period_ended"])

    def test_corrections_after_the_final_are_silent(self):
        final = moment_doc("final", [goal_item("g1", "home", 60, 1, 0)], home=1)
        corrected = moment_doc("final", [goal_item("g1", "home", 60, 1, 0), goal_item("g2", "home", 3000, 2, 0)], home=2)

        self.assertEqual(self.kinds(final, corrected), [])

    def test_a_vanished_goal_is_cancelled_only_when_the_score_drops(self):
        before = moment_doc("live", [goal_item("g1", "home", 60, 1, 0, name="Sam")], home=1)
        rekeyed = moment_doc("live", [goal_item("g1b", "home", 60, 1, 0, name="Kim")], home=1)
        taken_back = moment_doc("live", [], home=0)

        self.assertEqual(self.kinds(before, rekeyed), ["goal_for"])  # same goal, corrected scorer -> new id, score unchanged
        self.assertEqual(self.kinds(before, taken_back), ["goal_cancelled"])

    def test_a_late_catch_up_is_recorded_but_not_notifiable(self):
        live = moment_doc("live", [])
        caught_up = moment_doc("live", [goal_item("old", "home", 60, 1, 0), goal_item("new", "home", 1500, 2, 0)], home=2)

        moments = {moment.key: moment for moment in game_moments.derive_moments(live, caught_up, our_side="home", first_fetch=False)}

        self.assertFalse(moments["goal_for:old"].notifiable)
        self.assertTrue(moments["goal_for:new"].notifiable)


class RecordGameMomentsTests(GameTimelineTestBase):
    """game_timeline.store -> game_timeline_changed -> GameMoment rows."""

    def fetch(self, status_events):
        return game_timeline.TimelineFetch(document={"events": status_events})

    def test_a_changed_timeline_records_its_moments_once(self):
        game = self.make_game(is_live=True)
        game_timeline.store(game, self.fetch([]))  # first fetch: silent
        game_timeline.store(game, self.fetch([goal_item("g1", "home", 60, 1, 0)]))

        game_moments.game_timeline_changed.send(sender=Event, event=game, previous={"status": "live", "events": [], "score": {"home": 0, "away": 0}}, current=game.timeline.data, first_fetch=False)

        self.assertEqual(list(GameMoment.objects.filter(event=game).values_list("kind", "key")), [("goal_for", "goal_for:g1")])

    def test_the_first_fetch_records_nothing(self):
        game = self.make_game(is_live=True)

        game_timeline.store(game, self.rbihf_fetch())

        self.assertFalse(GameMoment.objects.filter(event=game).exists())

    def test_describe_moment(self):
        game = self.make_game(is_live=True)
        moment = GameMoment.objects.create(event=game, key="goal_for:g1", kind=GameMoment.Kind.GOAL_FOR, payload={"scorer": {"name": "Sam", "number": 9}, "score": {"for": 2, "against": 1}})

        self.assertEqual(game_moments.describe_moment(moment), "Goal for us by Sam (#9), 2-1.")


class GamePushTests(GameTimelineTestBase):
    """events.services.game_push -- live game pushes to the accounts that
    follow one of the game's teams; one coalesced push per poll, no cap."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.fan = get_user_model().objects.create_user(email="fan@example.com", password="pw-secret-123")
        cls.stranger = get_user_model().objects.create_user(email="stranger@example.com", password="pw-secret-123")

    def moment(self, game, key, kind=GameMoment.Kind.GOAL_FOR, seconds=60, notifiable=True, **payload):
        return GameMoment.objects.create(event=game, key=key, kind=kind, game_seconds=seconds, notifiable=notifiable, payload={"scorer": {"name": "Sam", "number": 9}, "score": {"for": 1, "against": 0}, **payload})

    def deliver(self, game, moments):
        with mock.patch("events.services.game_push._send_native", return_value=1) as native, mock.patch("events.services.game_push._send_web", return_value=0) as web:
            reached = game_push.deliver_moments(game.pk, [moment.pk for moment in moments])
        return reached, native, web

    def test_followers_get_one_coalesced_push_and_others_none(self):
        game = self.make_game(is_live=True)
        game_push.set_followed_teams(self.fan, self.club, [self.team.pk])
        goal = self.moment(game, "goal_for:g1")
        penalty = self.moment(game, "penalty_against:p1", kind=GameMoment.Kind.PENALTY_AGAINST, seconds=90, player={"name": "Rex"}, description="Tripping")

        reached, native, web = self.deliver(game, [penalty, goal])

        self.assertEqual(reached, 1)
        native.assert_called_once()
        self.assertEqual(native.call_args.args[0], self.fan)
        body = native.call_args.kwargs["body"]
        self.assertEqual(body.splitlines(), ["Goal for us by Sam (#9), 1-0.", "Penalty for the opponent: Rex, Tripping."])
        self.assertEqual(native.call_args.kwargs["title"], "1st vs Olympia Heist")
        self.assertEqual(native.call_args.kwargs["data"]["target"], {"type": "event", "id": str(game.pk)})
        web.assert_called_once()
        self.assertEqual(GameMoment.objects.filter(event=game, delivered_at__isnull=False).count(), 2)

    def test_catch_ups_are_never_pushed_and_a_moment_is_pushed_once(self):
        game = self.make_game(is_live=True)
        game_push.set_followed_teams(self.fan, self.club, [self.team.pk])
        late = self.moment(game, "goal_for:old", notifiable=False)
        fresh = self.moment(game, "goal_for:new")

        _reached, native, _web = self.deliver(game, [late, fresh])
        self.assertEqual(native.call_count, 1)
        self.assertNotIn("old", native.call_args.kwargs["body"])

        reached_again, native_again, _web = self.deliver(game, [fresh])
        self.assertEqual(reached_again, 0)
        native_again.assert_not_called()

    def test_there_is_no_cap_per_game(self):
        game = self.make_game(is_live=True)
        game_push.set_followed_teams(self.fan, self.club, [self.team.pk])

        for number in range(15):
            reached, _native, _web = self.deliver(game, [self.moment(game, f"goal_for:g{number}")])
            self.assertEqual(reached, 1)

    def test_recording_moments_schedules_delivery_after_commit(self):
        game = self.make_game(is_live=True)
        game_timeline.store(game, game_timeline.TimelineFetch(document={"events": []}))

        # Patch outermost: captured on-commit callbacks run when the capture block exits.
        with mock.patch("events.services.game_push.run_in_background") as background, self.captureOnCommitCallbacks(execute=True):
            game_timeline.store(game, game_timeline.TimelineFetch(document={"events": [goal_item("g1", "home", 60, 1, 0)]}))

        func, event_id, ids = background.call_args.args
        self.assertEqual((func, event_id, len(ids)), (game_push.deliver_moments, game.pk, 1))

    def test_following_only_keeps_this_clubs_teams_and_replaces_the_set(self):
        other_club = Club.objects.create(name="Rival", slug="rival-push")
        theirs = Team.objects.create(club=other_club, name="Theirs", short_name="TH")
        second = Team.objects.create(club=self.club, name="Second", short_name="2nd")

        self.assertEqual(game_push.set_followed_teams(self.fan, self.club, [self.team.pk, theirs.pk]), {self.team.pk})
        game_push.set_followed_teams(self.fan, self.club, [second.pk])

        self.assertEqual(game_push.followed_team_ids(self.fan, self.club), {second.pk})
        self.assertEqual(game_push.followers(self.make_game()), [])
