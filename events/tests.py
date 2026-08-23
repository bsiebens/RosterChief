from datetime import date, datetime, time, timedelta
from decimal import Decimal
from io import StringIO
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone
from waffle import get_waffle_flag_model

from club.models import Club, ClubMembership, OnboardingRequirement, Season
from club.services.onboarding import mark_bypassed, mark_complete
from features.models import JobToggle, Maintenance
from members.models import Group, GroupMembership, Member
from notifications.models import Notification
from teams.models import Position, RefereeLevel, RefereeProfile, StaffAssignment, Team, TeamMembership

from .admin import EventAdminForm
from .models import Attendance, Competition, Event, EventReferee, EventSeries, Lineup, LineupSelection, Location, Opponent, RefereeSignup
from .services import (
    blocked_upcoming_events_for_member,
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
from .services.attendance import member_attendance_counts
from .services.calendar import add_months, agenda_groups, month_bounds, month_grid, season_grid, week_bounds, week_grid
from .services.lineup import cancel_scheduled_publish, notify_dropout, publish_lineup, schedule_lineup_publish, selected_members_by_position, toggle_selection
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
from .tasks import publish_scheduled_lineups, send_deadline_reminders


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

    def home_fixture_row(self, game_id="5002", date="2026-09-12"):
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
        cls.referee_profile = RefereeProfile.objects.create(member=cls.referee, level=cls.level, valid_until=timezone.localdate() + timedelta(days=30))

    def make_eligible_profile(self, member, level=None):
        return RefereeProfile.objects.create(member=member, level=level or self.level, valid_until=timezone.localdate() + timedelta(days=30))

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
        RefereeProfile.objects.create(member=unrelated_referee)  # no level set

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
        RefereeProfile.objects.create(member=expired_referee, level=self.level, valid_until=timezone.localdate() - timedelta(days=1))

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
        cls.referee_profile = RefereeProfile.objects.create(member=cls.referee, level=cls.level, valid_until=timezone.localdate() + timedelta(days=30))

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

    def test_decline_never_removes_an_admin_made_assignment_for_the_same_member(self):
        game = self.make_home_game()
        signup = RefereeSignup.objects.get(event=game, member=self.referee)
        EventReferee.objects.create(event=game, member=self.referee, assigned_by=self.alice)

        decline_referee_signup(signup)

        self.assertTrue(EventReferee.objects.filter(event=game, member=self.referee, assigned_by=self.alice).exists())


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

        result = send_deadline_reminders()

        self.assertTrue(Notification.objects.filter(member=self.alice, title=event.title).exists())
        self.assertTrue(Notification.objects.filter(member=self.bob, title=event.title).exists())
        self.assertIn("Reminded 2 member(s) across 1 event(s)", result)

    def test_falls_back_to_the_event_start_when_no_deadline_is_set(self):
        event = self.make_event_with_roster(start=timezone.now() + timedelta(days=6), deadline=None)

        send_deadline_reminders()

        self.assertTrue(Notification.objects.filter(member=self.alice, title=event.title).exists())

    def test_does_not_remind_before_the_window_opens(self):
        self.make_event_with_roster(start=timezone.now() + timedelta(days=30), deadline=timezone.now() + timedelta(days=20))

        send_deadline_reminders()

        self.assertFalse(Notification.objects.exists())

    def test_does_not_remind_after_the_deadline_has_passed(self):
        # Deadline already gone, but the event itself hasn't started yet -- the
        # window is closed, not "still open and overdue".
        self.make_event_with_roster(start=timezone.now() + timedelta(days=2), deadline=timezone.now() - timedelta(hours=1))

        send_deadline_reminders()

        self.assertFalse(Notification.objects.exists())

    def test_only_notifies_members_who_have_not_answered(self):
        event = self.make_event_with_roster(start=timezone.now() + timedelta(days=10), deadline=timezone.now() + timedelta(days=6))
        Attendance.objects.filter(event=event, member=self.alice).update(status=Attendance.AttendanceStatus.PRESENT)

        send_deadline_reminders()

        self.assertFalse(Notification.objects.filter(member=self.alice).exists())
        self.assertTrue(Notification.objects.filter(member=self.bob).exists())

    def test_does_not_notify_the_same_event_twice(self):
        self.make_event_with_roster(start=timezone.now() + timedelta(days=10), deadline=timezone.now() + timedelta(days=6))

        send_deadline_reminders()
        Notification.objects.all().delete()
        send_deadline_reminders()

        self.assertFalse(Notification.objects.exists())

    def test_marks_the_event_processed_even_when_nobody_needed_notifying(self):
        event = self.make_event_with_roster(start=timezone.now() + timedelta(days=10), deadline=timezone.now() + timedelta(days=6))
        Attendance.objects.filter(event=event).update(status=Attendance.AttendanceStatus.PRESENT)

        send_deadline_reminders()

        event.refresh_from_db()
        self.assertIsNotNone(event.deadline_reminder_sent_at)

    def test_skips_a_cancelled_event(self):
        self.make_event_with_roster(start=timezone.now() + timedelta(days=10), deadline=timezone.now() + timedelta(days=6), cancelled=True)

        send_deadline_reminders()

        self.assertFalse(Notification.objects.exists())

    def test_raises_during_maintenance_instead_of_silently_skipping(self):
        Maintenance.start(user=None)

        with self.assertRaises(RuntimeError):
            send_deadline_reminders()

    def test_raises_when_paused_from_the_control_panel(self):
        JobToggle.set_enabled("events.tasks.send_deadline_reminders", False)

        with self.assertRaises(RuntimeError):
            send_deadline_reminders()


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

        result = publish_scheduled_lineups()

        lineup.refresh_from_db()
        self.assertIsNotNone(lineup.published_at)
        self.assertIsNone(lineup.scheduled_publish_at)
        self.assertIn("Published 1 scheduled line-up(s)", result)

    def test_leaves_a_not_yet_due_schedule_alone(self):
        _event, lineup = self.make_game_with_lineup()
        lineup.scheduled_publish_at = timezone.now() + timedelta(days=1)
        lineup.save()

        publish_scheduled_lineups()

        lineup.refresh_from_db()
        self.assertIsNone(lineup.published_at)
        self.assertIsNotNone(lineup.scheduled_publish_at)

    def test_ignores_a_lineup_with_no_schedule_at_all(self):
        _event, lineup = self.make_game_with_lineup()

        result = publish_scheduled_lineups()

        lineup.refresh_from_db()
        self.assertIsNone(lineup.published_at)
        self.assertIn("Published 0 scheduled line-up(s)", result)

    def test_already_published_lineup_is_not_touched_again(self):
        _event, lineup = self.make_game_with_lineup()
        published_at = timezone.now() - timedelta(days=1)
        lineup.published_at = published_at
        lineup.scheduled_publish_at = timezone.now() - timedelta(minutes=1)
        lineup.save()

        publish_scheduled_lineups()

        lineup.refresh_from_db()
        self.assertEqual(lineup.published_at, published_at)

    def test_raises_during_maintenance_instead_of_silently_skipping(self):
        Maintenance.start(user=None)

        with self.assertRaises(RuntimeError):
            publish_scheduled_lineups()

    def test_raises_when_paused_from_the_control_panel(self):
        JobToggle.set_enabled("events.tasks.publish_scheduled_lineups", False)

        with self.assertRaises(RuntimeError):
            publish_scheduled_lineups()
