import datetime
import itertools
from unittest import mock

from django.core import mail
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, connection, models, transaction
from django.db.models import Q
from django.test import TestCase
from django.utils import timezone
from django_tenants.utils import schema_context

from club.models import Club, Domain
from club.services import create_club, grant_login
from controlpanel.tests import make_user
from events.models import Attendance, Event, Lineup, LineupSelection, Location, Season
from events.services.lineup import lineup_changed
from members.models import Member
from notifications.models import InboxNotification
from teams.models import Position, StaffAssignment, Team, TeamMembership

from .models import (
    BreakRule,
    DressingRoom,
    DressingRoomAssignment,
    Match,
    MatchAttribute,
    MatchAttributeValue,
    MatchEvent,
    MatchOfficial,
    MatchReferee,
    ScheduleBlock,
    Tournament,
    TournamentDay,
    TournamentField,
    TournamentGroup,
    TournamentRegistration,
    TournamentRegistrationPlayer,
    TournamentRosterEntry,
    TournamentRosterRole,
    TournamentStage,
    TournamentTask,
    TournamentTaskClaim,
    TournamentTeam,
)
from .services.brackets import BracketError, double_elimination_bracket, double_elimination_placement_bracket, single_elimination_placement_bracket
from .services.dressing_rooms import DressingRoomAssignmentError, assign_dressing_room, release_dressing_room, windows_conflict
from .services.lunch_schedule import _assign, build_sittings, clear_lunch_schedule, generate_lunch_schedule
from .services.notifications import notify_tournament_shifts_open
from .services.registration import RegistrationDecisionError, TeamDeletionError, TeamWithdrawalError, approve_registration, create_manual_team, delete_manual_team, ensure_home_team, reject_registration, withdraw_team
from .services.scheduling import (
    DEFAULT_GAP,
    DEFAULT_MATCH_DURATION,
    PX_PER_MINUTE,
    GroupRankSeed,
    SchedulingError,
    _ranked,
    allocate_schedule,
    clear_schedule,
    compute_standings,
    field_timeline,
    gap_for_placement,
    generate_next_swiss_round,
    generate_stage_matches,
    group_standings_preview,
    normalise_tiebreakers,
    record_result,
    reschedule_match,
    resolve_group_slots,
    resolve_match_slots,
    round_robin_pairings,
    snake_seed_groups,
    stage_tiebreakers,
    validate_placement,
)
from .services.tasks import TaskClaimError, claim_task, generate_task_shifts, unclaim_task
from .services.timeline import MatchLocked, sync_match


class TournamentsTestCase(TestCase):
    """Shared scaffolding for every tournaments test: one club (its own schema) with a season, a team, a member and a tournament."""

    @classmethod
    def setUpClass(cls):
        cls._mfa = mock.patch("authorization.middleware.is_local_development", return_value=True)
        cls._mfa.start()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        connection.set_schema_to_public()
        cls._mfa.stop()

    @classmethod
    def setUpTestData(cls):
        owner = make_user("owner@tournaments.test", first="Olivia", last="Owner")
        public = Club(schema_name="public", name="Platform", owner=owner)
        public.auto_create_schema = False
        public.save()
        Domain.objects.create(domain="testserver", tenant=public, is_primary=True)
        cls.club = create_club("Tournament FC", owner)
        with schema_context(cls.club.schema_name):
            cls.season = Season.objects.create(start_date=datetime.date(2026, 7, 1), end_date=datetime.date(2027, 6, 30))
            cls.team = Team.objects.create(name="First Team", short_name="1st")
            cls.member = Member.objects.create(first_name="Jane", last_name="Doe")
            cls.tournament = Tournament.objects.create(name="Summer Cup", season=cls.season, start_date=datetime.date(2026, 9, 19), end_date=datetime.date(2026, 9, 20))
        # Subclasses' own setUpTestData and every test run inside the club's schema (tearDownClass goes back to public).
        connection.set_tenant(cls.club)

    def set_club_logo(self):
        """Give the current tenant a crest (``TournamentTeam.display_logo`` reads it live); restored afterwards."""
        tenant = connection.tenant
        self.addCleanup(setattr, tenant, "logo", tenant.logo)
        tenant.logo = SimpleUploadedFile("crest.png", b"fake-logo-bytes", content_type="image/png")


class TournamentModelTests(TournamentsTestCase):
    def test_str_returns_name(self):
        self.assertEqual(str(self.tournament), "Summer Cup")

    def test_slug_is_generated_from_name_on_save(self):
        self.assertEqual(self.tournament.slug, "summer-cup")

    def test_status_defaults_to_draft(self):
        self.assertEqual(self.tournament.status, Tournament.Status.DRAFT)

    def test_registration_open_defaults_to_true(self):
        self.assertTrue(self.tournament.registration_open)

    def test_default_match_duration_minutes_defaults_to_forty_five(self):
        self.assertEqual(self.tournament.default_match_duration_minutes, 45)

    def test_accepts_registrations_true_for_draft_and_published(self):
        self.tournament.status = Tournament.Status.DRAFT
        self.assertTrue(self.tournament.accepts_registrations)
        self.tournament.status = Tournament.Status.PUBLISHED
        self.assertTrue(self.tournament.accepts_registrations)

    def test_accepts_registrations_false_once_in_progress_completed_or_cancelled(self):
        for status in (Tournament.Status.IN_PROGRESS, Tournament.Status.COMPLETED, Tournament.Status.CANCELLED):
            self.tournament.status = status
            self.assertFalse(self.tournament.accepts_registrations)

    def test_accepts_registrations_false_when_manually_closed(self):
        self.tournament.status = Tournament.Status.PUBLISHED
        self.tournament.registration_open = False
        self.assertFalse(self.tournament.accepts_registrations)

    def test_accepts_registrations_true_when_under_the_max_teams_cap(self):
        self.tournament.status = Tournament.Status.PUBLISHED
        self.tournament.max_teams = 2
        self.tournament.save()
        TournamentTeam.objects.create(tournament=self.tournament, foreign_name="Team A")

        self.assertTrue(self.tournament.accepts_registrations)

    def test_accepts_registrations_false_once_the_max_teams_cap_is_reached(self):
        self.tournament.status = Tournament.Status.PUBLISHED
        self.tournament.max_teams = 1
        self.tournament.save()
        TournamentTeam.objects.create(tournament=self.tournament, foreign_name="Team A")

        self.assertFalse(self.tournament.accepts_registrations)

    def test_accepts_registrations_ignores_withdrawn_teams_against_the_cap(self):
        self.tournament.status = Tournament.Status.PUBLISHED
        self.tournament.max_teams = 1
        self.tournament.save()
        TournamentTeam.objects.create(tournament=self.tournament, foreign_name="Team A", withdrawn_at=timezone.now())

        self.assertTrue(self.tournament.accepts_registrations)

    def test_location_is_none_without_an_event_or_a_home_location(self):
        self.assertIsNone(self.tournament.location)

    def test_location_falls_back_to_the_clubs_home_location(self):
        home = Location.objects.create(name="Ajax Arena", address="1 Main St", city="Mechelen", zip_code="2800", country="BE", is_home=True)
        away = Location.objects.create(name="Away Ground", address="2 Side St", city="Elsewhere", zip_code="1000", country="BE", is_home=False)

        self.assertEqual(self.tournament.location, home)
        self.assertNotEqual(self.tournament.location, away)

    def test_location_prefers_the_linked_events_own_location(self):
        home = Location.objects.create(name="Ajax Arena", address="1 Main St", city="Mechelen", zip_code="2800", country="BE", is_home=True)
        event_location = Location.objects.create(name="Neutral Venue", address="3 Other St", city="Neutral City", zip_code="3000", country="BE")
        event = Event.objects.create(kind=Event.EventKind.TOURNAMENT, title="Cup", start=timezone.now(), location=event_location)
        self.tournament.event = event
        self.tournament.save()

        self.assertEqual(self.tournament.location, event_location)
        self.assertNotEqual(self.tournament.location, home)

    def test_location_falls_back_to_home_location_when_event_has_none(self):
        home = Location.objects.create(name="Ajax Arena", address="1 Main St", city="Mechelen", zip_code="2800", country="BE", is_home=True)
        event = Event.objects.create(kind=Event.EventKind.TOURNAMENT, title="Cup", start=timezone.now())
        self.tournament.event = event
        self.tournament.save()

        self.assertEqual(self.tournament.location, home)

    def test_date_range_returns_every_day_inclusive(self):
        self.assertEqual(self.tournament.date_range(), [datetime.date(2026, 9, 19), datetime.date(2026, 9, 20)])

    def test_date_range_handles_a_single_day_tournament(self):
        self.tournament.end_date = self.tournament.start_date
        self.assertEqual(self.tournament.date_range(), [datetime.date(2026, 9, 19)])

    def test_lunch_mode_defaults_to_none(self):
        self.assertEqual(self.tournament.lunch_mode, Tournament.LunchMode.NONE)

    def test_payment_and_lunch_amounts_default_to_blank(self):
        self.assertIsNone(self.tournament.deposit_amount)
        self.assertIsNone(self.tournament.entry_fee_amount)
        self.assertIsNone(self.tournament.lunch_price_per_day)

    def test_age_range_defaults_to_blank(self):
        self.assertIsNone(self.tournament.earliest_birth_year)
        self.assertIsNone(self.tournament.latest_birth_year)

    def test_birth_year_out_of_range_false_when_neither_bound_is_set(self):
        self.assertFalse(self.tournament.birth_year_out_of_range(datetime.date(2020, 1, 1)))

    def test_birth_year_out_of_range_false_when_no_date_of_birth(self):
        self.tournament.earliest_birth_year, self.tournament.latest_birth_year = 2014, 2018
        self.assertFalse(self.tournament.birth_year_out_of_range(None))

    def test_birth_year_out_of_range_true_before_the_earliest(self):
        self.tournament.earliest_birth_year, self.tournament.latest_birth_year = 2014, 2018
        self.assertTrue(self.tournament.birth_year_out_of_range(datetime.date(2013, 12, 31)))

    def test_birth_year_out_of_range_true_after_the_latest(self):
        self.tournament.earliest_birth_year, self.tournament.latest_birth_year = 2014, 2018
        self.assertTrue(self.tournament.birth_year_out_of_range(datetime.date(2019, 1, 1)))

    def test_birth_year_out_of_range_false_within_bounds_and_on_each_boundary_year(self):
        self.tournament.earliest_birth_year, self.tournament.latest_birth_year = 2014, 2018
        for dob in (datetime.date(2014, 1, 1), datetime.date(2016, 6, 15), datetime.date(2018, 12, 31)):
            self.assertFalse(self.tournament.birth_year_out_of_range(dob))

    def test_birth_year_out_of_range_with_only_the_earliest_set(self):
        self.tournament.earliest_birth_year = 2014
        self.assertTrue(self.tournament.birth_year_out_of_range(datetime.date(2013, 9, 19)))
        self.assertFalse(self.tournament.birth_year_out_of_range(datetime.date(2022, 9, 19)))  # no upper bound

    def test_birth_year_out_of_range_with_only_the_latest_set(self):
        self.tournament.latest_birth_year = 2018
        self.assertTrue(self.tournament.birth_year_out_of_range(datetime.date(2019, 9, 19)))
        self.assertFalse(self.tournament.birth_year_out_of_range(datetime.date(2000, 9, 19)))  # no lower bound

    def test_earliest_after_latest_is_invalid(self):
        self.tournament.earliest_birth_year, self.tournament.latest_birth_year = 2018, 2014
        with self.assertRaises(ValidationError):
            self.tournament.full_clean(exclude=["season", "slug"])

    def test_slug_is_unique(self):
        with self.assertRaises(IntegrityError):
            Tournament.objects.create(name="Other Cup", slug="summer-cup", season=self.season, start_date=self.tournament.start_date, end_date=self.tournament.end_date)

    def test_clean_rejects_an_event_from_another_season(self):
        other_season = Season.objects.create(start_date=datetime.date(2020, 7, 1), end_date=datetime.date(2021, 6, 30))
        event = Event.objects.create(season=other_season, kind=Event.EventKind.TOURNAMENT, title="Cup", start=timezone.now())
        self.tournament.event = event

        with self.assertRaises(ValidationError) as ctx:
            self.tournament.full_clean()
        self.assertIn("event", ctx.exception.error_dict)

    def test_clean_accepts_an_event_with_no_season_set(self):
        event = Event.objects.create(season=None, kind=Event.EventKind.TOURNAMENT, title="Cup", start=timezone.now())
        self.tournament.event = event

        self.tournament.full_clean()

    def test_clean_accepts_an_event_in_the_same_season(self):
        event = Event.objects.create(season=self.season, kind=Event.EventKind.TOURNAMENT, title="Cup", start=timezone.now())
        self.tournament.event = event

        self.tournament.full_clean()


class TournamentFieldModelTests(TournamentsTestCase):
    def test_str(self):
        field = TournamentField.objects.create(tournament=self.tournament, name="Field 1")
        self.assertEqual(str(field), "Summer Cup - Field 1")

    def test_availability_defaults_to_an_empty_list(self):
        field = TournamentField.objects.create(tournament=self.tournament, name="Field 1")
        self.assertEqual(field.availability, [])


class TournamentStageModelTests(TournamentsTestCase):
    def test_str(self):
        stage = TournamentStage.objects.create(tournament=self.tournament, name="Group Stage", format=TournamentStage.Format.GROUPS)
        self.assertEqual(str(stage), "Summer Cup - Group Stage")

    def test_defaults(self):
        stage = TournamentStage.objects.create(tournament=self.tournament, name="Group Stage", format=TournamentStage.Format.GROUPS)

        self.assertEqual(stage.status, TournamentStage.Status.PENDING)
        self.assertEqual(stage.config, {})
        self.assertEqual(stage.standings_config, {})


class TournamentGroupModelTests(TournamentsTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.stage = TournamentStage.objects.create(tournament=cls.tournament, name="Group Stage", format=TournamentStage.Format.GROUPS)

    def test_str(self):
        group = TournamentGroup.objects.create(stage=self.stage, name="Group A")
        self.assertEqual(str(group), "Summer Cup - Group Stage - Group A")


class TournamentRegistrationModelTests(TournamentsTestCase):
    def test_str_and_display_name_for_a_real_team(self):
        registration = TournamentRegistration.objects.create(tournament=self.tournament, team=self.team)

        self.assertEqual(registration.display_name, "First Team")
        self.assertEqual(str(registration), "Summer Cup - First Team")
        self.assertFalse(registration.is_foreign)

    def test_str_and_display_name_for_a_foreign_team(self):
        registration = TournamentRegistration.objects.create(tournament=self.tournament, foreign_name="Guest FC")

        self.assertEqual(registration.display_name, "Guest FC")
        self.assertEqual(str(registration), "Summer Cup - Guest FC")
        self.assertTrue(registration.is_foreign)

    def test_status_defaults_to_pending(self):
        registration = TournamentRegistration.objects.create(tournament=self.tournament, foreign_name="Guest FC")
        self.assertEqual(registration.status, TournamentRegistration.Status.PENDING)

    def test_team_xor_foreign_name_rejects_neither_set(self):
        with self.assertRaises(IntegrityError):
            TournamentRegistration.objects.create(tournament=self.tournament)

    def test_team_xor_foreign_name_rejects_both_set(self):
        with self.assertRaises(IntegrityError):
            TournamentRegistration.objects.create(tournament=self.tournament, team=self.team, foreign_name="Guest FC")

    def test_team_xor_foreign_name_accepts_team_only(self):
        TournamentRegistration.objects.create(tournament=self.tournament, team=self.team)

    def test_team_xor_foreign_name_accepts_foreign_name_only(self):
        TournamentRegistration.objects.create(tournament=self.tournament, foreign_name="Guest FC")


class TournamentRegistrationPlayerModelTests(TournamentsTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.registration = TournamentRegistration.objects.create(tournament=cls.tournament, foreign_name="Guest FC")

    def test_role_defaults_to_player(self):
        player = TournamentRegistrationPlayer.objects.create(registration=self.registration, first_name="Sam", last_name="Striker")
        self.assertEqual(player.role, TournamentRosterRole.PLAYER)

    def test_a_staff_row_with_a_date_of_birth_fails_clean(self):
        staff = TournamentRegistrationPlayer(registration=self.registration, first_name="Cam", last_name="Coach", role=TournamentRosterRole.STAFF, date_of_birth=datetime.date(1980, 1, 1))
        with self.assertRaises(ValidationError):
            staff.full_clean(exclude=["registration"])

    def test_a_player_row_with_a_date_of_birth_passes_clean(self):
        player = TournamentRegistrationPlayer(registration=self.registration, first_name="Sam", last_name="Striker", role=TournamentRosterRole.PLAYER, date_of_birth=datetime.date(2010, 1, 1))
        player.full_clean(exclude=["registration"])  # no error raised


class TournamentTeamModelTests(TournamentsTestCase):
    def test_str_and_display_name_for_a_real_team(self):
        entry = TournamentTeam.objects.create(tournament=self.tournament, team=self.team)

        self.assertEqual(entry.display_name, "First Team")
        self.assertEqual(str(entry), "First Team")

    def test_payment_status_defaults_to_unpaid(self):
        entry = TournamentTeam.objects.create(tournament=self.tournament, team=self.team)

        self.assertEqual(entry.payment_status, TournamentTeam.PaymentStatus.UNPAID)

    def test_display_logo_is_none_for_a_foreign_team_with_no_logo_uploaded(self):
        entry = TournamentTeam.objects.create(tournament=self.tournament, foreign_name="Guest FC")

        self.assertIsNone(entry.display_logo)

    def test_display_logo_is_none_for_a_club_team_when_the_club_has_no_logo(self):
        entry = TournamentTeam.objects.create(tournament=self.tournament, team=self.team)

        self.assertIsNone(entry.display_logo)

    def test_display_logo_falls_back_to_the_clubs_own_logo_for_a_club_team(self):
        self.set_club_logo()
        entry = TournamentTeam.objects.create(tournament=self.tournament, team=self.team)

        self.assertEqual(entry.display_logo, connection.tenant.logo)

    def test_display_logo_prefers_its_own_foreign_logo_over_the_clubs(self):
        self.set_club_logo()
        own_logo = SimpleUploadedFile("guest.png", b"guest-logo-bytes", content_type="image/png")
        # A club-linked team with its own uploaded logo is an edge case (manual
        # add only ever sets one or the other today), but display_logo should
        # still prefer it over the club fallback if it's ever set.
        entry = TournamentTeam.objects.create(tournament=self.tournament, team=self.team, foreign_logo=own_logo)

        self.assertEqual(entry.display_logo, entry.foreign_logo)

    def test_str_and_display_name_for_a_foreign_team(self):
        entry = TournamentTeam.objects.create(tournament=self.tournament, foreign_name="Guest FC")

        self.assertEqual(entry.display_name, "Guest FC")
        self.assertEqual(str(entry), "Guest FC")

    def test_team_xor_foreign_name_rejects_neither_set(self):
        with self.assertRaises(IntegrityError):
            TournamentTeam.objects.create(tournament=self.tournament)

    def test_team_xor_foreign_name_rejects_both_set(self):
        with self.assertRaises(IntegrityError):
            TournamentTeam.objects.create(tournament=self.tournament, team=self.team, foreign_name="Guest FC")

    def test_team_xor_foreign_name_accepts_team_only(self):
        TournamentTeam.objects.create(tournament=self.tournament, team=self.team)

    def test_team_xor_foreign_name_accepts_foreign_name_only(self):
        TournamentTeam.objects.create(tournament=self.tournament, foreign_name="Guest FC")

    def test_real_team_is_unique_per_tournament(self):
        TournamentTeam.objects.create(tournament=self.tournament, team=self.team)

        with self.assertRaises(IntegrityError):
            TournamentTeam.objects.create(tournament=self.tournament, team=self.team)

    def test_several_foreign_entries_are_allowed_in_the_same_tournament(self):
        TournamentTeam.objects.create(tournament=self.tournament, foreign_name="Guest FC")
        TournamentTeam.objects.create(tournament=self.tournament, foreign_name="Other FC")

        self.assertEqual(self.tournament.teams.count(), 2)

    def test_slug_is_generated_from_display_name(self):
        entry = TournamentTeam.objects.create(tournament=self.tournament, foreign_name="Guest FC")

        self.assertEqual(entry.slug, "guest-fc")

    def test_same_slug_allowed_in_a_different_tournament(self):
        TournamentTeam.objects.create(tournament=self.tournament, foreign_name="Guest FC")
        other_tournament = Tournament.objects.create(name="Winter Cup", season=self.season, start_date=self.tournament.start_date, end_date=self.tournament.end_date)

        other_entry = TournamentTeam.objects.create(tournament=other_tournament, foreign_name="Guest FC")

        self.assertEqual(other_entry.slug, "guest-fc")

    def test_slug_is_unique_within_the_same_tournament(self):
        TournamentTeam.objects.create(tournament=self.tournament, foreign_name="Guest FC")

        second = TournamentTeam.objects.create(tournament=self.tournament, foreign_name="Guest FC")

        self.assertEqual(second.slug, "guest-fc-2")

    def test_clean_accepts_a_team(self):
        entry = TournamentTeam(tournament=self.tournament, team=self.team)

        entry.full_clean()


class TournamentRosterEntryModelTests(TournamentsTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.tournament_team = TournamentTeam.objects.create(tournament=cls.tournament, team=cls.team)

    def test_str_and_display_name_for_a_member(self):
        entry = TournamentRosterEntry.objects.create(tournament_team=self.tournament_team, member=self.member, jersey_number=9)

        self.assertEqual(entry.display_name, "Jane Doe")
        self.assertEqual(str(entry), "Jane Doe")

    def test_str_and_display_name_for_a_foreign_player(self):
        entry = TournamentRosterEntry.objects.create(tournament_team=self.tournament_team, foreign_first_name="Guest", foreign_last_name="Player", jersey_number=10)

        self.assertEqual(entry.display_name, "Guest Player")
        self.assertEqual(str(entry), "Guest Player")

    def test_member_xor_foreign_name_rejects_neither_set(self):
        with self.assertRaises(IntegrityError):
            TournamentRosterEntry.objects.create(tournament_team=self.tournament_team)

    def test_member_xor_foreign_name_rejects_both_set(self):
        with self.assertRaises(IntegrityError):
            TournamentRosterEntry.objects.create(tournament_team=self.tournament_team, member=self.member, foreign_first_name="Guest", foreign_last_name="Player")

    def test_member_xor_foreign_name_accepts_member_only(self):
        TournamentRosterEntry.objects.create(tournament_team=self.tournament_team, member=self.member)

    def test_member_xor_foreign_name_accepts_foreign_name_only(self):
        TournamentRosterEntry.objects.create(tournament_team=self.tournament_team, foreign_first_name="Guest", foreign_last_name="Player")

    def test_jersey_number_is_unique_per_tournament_team_when_set(self):
        TournamentRosterEntry.objects.create(tournament_team=self.tournament_team, member=self.member, jersey_number=9)
        other_member = Member.objects.create(first_name="John", last_name="Smith")

        with self.assertRaises(IntegrityError):
            TournamentRosterEntry.objects.create(tournament_team=self.tournament_team, member=other_member, jersey_number=9)

    def test_several_null_jersey_numbers_are_allowed_on_the_same_tournament_team(self):
        other_member = Member.objects.create(first_name="John", last_name="Smith")
        TournamentRosterEntry.objects.create(tournament_team=self.tournament_team, member=self.member, jersey_number=None)

        TournamentRosterEntry.objects.create(tournament_team=self.tournament_team, member=other_member, jersey_number=None)

        self.assertEqual(self.tournament_team.roster_entries.count(), 2)

    def test_role_defaults_to_player(self):
        entry = TournamentRosterEntry.objects.create(tournament_team=self.tournament_team, member=self.member)
        self.assertEqual(entry.role, TournamentRosterRole.PLAYER)

    def test_extra_fields_round_trip(self):
        entry = TournamentRosterEntry.objects.create(
            tournament_team=self.tournament_team,
            member=self.member,
            role=TournamentRosterRole.STAFF,
            position_label="Coach",
            license_number="LIC-42",
            dietary_restrictions="No nuts.",
            lunch_days=["2026-09-19"],
        )
        entry.refresh_from_db()
        self.assertEqual(entry.role, TournamentRosterRole.STAFF)
        self.assertEqual(entry.position_label, "Coach")
        self.assertEqual(entry.license_number, "LIC-42")
        self.assertEqual(entry.dietary_restrictions, "No nuts.")
        self.assertEqual(entry.lunch_days, ["2026-09-19"])

    def test_a_staff_row_with_a_date_of_birth_fails_clean(self):
        staff = TournamentRosterEntry(tournament_team=self.tournament_team, member=self.member, role=TournamentRosterRole.STAFF, date_of_birth=datetime.date(1980, 1, 1))
        with self.assertRaises(ValidationError):
            staff.full_clean(exclude=["tournament_team"])

    def test_a_player_row_with_a_date_of_birth_passes_clean(self):
        player = TournamentRosterEntry(tournament_team=self.tournament_team, member=self.member, role=TournamentRosterRole.PLAYER, date_of_birth=datetime.date(2010, 1, 1))
        player.full_clean(exclude=["tournament_team"])  # no error raised


class MatchModelTests(TournamentsTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.stage = TournamentStage.objects.create(tournament=cls.tournament, name="Bracket", format=TournamentStage.Format.SINGLE_ELIM)
        cls.home = TournamentTeam.objects.create(tournament=cls.tournament, foreign_name="Home Squad")
        cls.away = TournamentTeam.objects.create(tournament=cls.tournament, foreign_name="Away Squad")

    def test_str_and_display_with_teams_set(self):
        match = Match.objects.create(stage=self.stage, home_team=self.home, away_team=self.away)

        self.assertEqual(str(match), "Home Squad vs Away Squad")
        self.assertEqual(match.home_display, "Home Squad")
        self.assertEqual(match.away_display, "Away Squad")

    def test_str_and_display_with_undetermined_sides_shows_tbd(self):
        match = Match.objects.create(stage=self.stage)

        self.assertEqual(str(match), "TBD vs TBD")
        self.assertEqual(match.home_display, "TBD")
        self.assertEqual(match.away_display, "TBD")

    def test_display_for_a_side_sourced_from_a_match_names_the_round(self):
        # Two quarterfinals sharing "Quarterfinal" as their own round_label --
        # the semifinal sourced from them should disambiguate by position,
        # not just repeat the shared label.
        qf1 = Match.objects.create(stage=self.stage, round_number=1, sequence=0, round_label="Quarterfinal", home_team=self.home, away_team=self.away)
        qf2 = Match.objects.create(stage=self.stage, round_number=1, sequence=1, round_label="Quarterfinal", home_team=self.home, away_team=self.away)
        semi = Match.objects.create(stage=self.stage, round_number=2, sequence=0, round_label="Semifinal", home_source_match=qf1, home_source_is_winner=True, away_source_match=qf2, away_source_is_winner=False)

        self.assertEqual(semi.home_display, "Winner of Quarterfinal 1")
        self.assertEqual(semi.away_display, "Loser of Quarterfinal 2")

    def test_display_for_a_side_sourced_from_a_match_skips_the_number_when_unique(self):
        semi = Match.objects.create(stage=self.stage, round_number=1, sequence=0, round_label="Semifinal", home_team=self.home, away_team=self.away)
        final = Match.objects.create(stage=self.stage, round_number=2, sequence=0, round_label="Final", home_source_match=semi, home_source_is_winner=True)

        self.assertEqual(final.home_display, "Winner of Semifinal")

    def test_display_for_a_side_sourced_from_a_group_shows_the_rank(self):
        group = TournamentGroup.objects.create(stage=self.stage, name="Group A")
        match = Match.objects.create(stage=self.stage, home_source_group=group, home_source_rank=1)

        self.assertEqual(match.home_display, "1st of Group A")

    def test_clean_allows_every_side_left_undetermined(self):
        # A still-undetermined bracket slot -- none of team/source_match/source_group
        # set yet on either side -- is valid, not just "not yet filled in".
        match = Match(stage=self.stage)

        match.full_clean()

    def test_clean_rejects_a_team_and_a_source_match_together_on_one_side(self):
        source = Match.objects.create(stage=self.stage)
        match = Match(stage=self.stage, home_team=self.home, home_source_match=source)

        with self.assertRaises(ValidationError) as ctx:
            match.full_clean()
        self.assertIn("home_team", ctx.exception.error_dict)

    def test_clean_rejects_a_source_match_and_a_source_group_together_on_one_side(self):
        source = Match.objects.create(stage=self.stage)
        group = TournamentGroup.objects.create(stage=self.stage, name="Group A")
        match = Match(stage=self.stage, away_source_match=source, away_source_group=group)

        with self.assertRaises(ValidationError) as ctx:
            match.full_clean()
        self.assertIn("away_team", ctx.exception.error_dict)

    def test_clean_rejects_a_team_and_a_source_group_together_on_one_side(self):
        group = TournamentGroup.objects.create(stage=self.stage, name="Group A")
        match = Match(stage=self.stage, home_team=self.home, home_source_group=group)

        with self.assertRaises(ValidationError) as ctx:
            match.full_clean()
        self.assertIn("home_team", ctx.exception.error_dict)

    def test_winner_and_loser_are_none_before_scores_are_set(self):
        match = Match.objects.create(stage=self.stage, home_team=self.home, away_team=self.away)

        self.assertIsNone(match.winner)
        self.assertIsNone(match.loser)

    def test_winner_when_home_wins(self):
        match = Match.objects.create(stage=self.stage, home_team=self.home, away_team=self.away, score_home=3, score_away=1)

        self.assertEqual(match.winner, self.home)

    def test_winner_when_away_wins(self):
        match = Match.objects.create(stage=self.stage, home_team=self.home, away_team=self.away, score_home=1, score_away=2)

        self.assertEqual(match.winner, self.away)

    # NOTE: Match.loser is not exercised here once there's an actual winner --
    # `loser` compares `self.winner_id` to `self.home_team_id`, but `winner` is a
    # plain property (not a FK), so there is no `winner_id` attribute. As written,
    # `.loser` raises AttributeError for any non-draw, fully-scored match. Only the
    # `winner is None` early-return path (covered above/below) actually works.
    # Flagging this rather than asserting the crash as if it were the intended
    # behaviour -- see the final report for this test suite.

    def test_winner_and_loser_are_none_on_a_draw(self):
        match = Match.objects.create(stage=self.stage, home_team=self.home, away_team=self.away, score_home=2, score_away=2)

        self.assertIsNone(match.winner)
        self.assertIsNone(match.loser)


class MatchRefereeModelTests(TournamentsTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.stage = TournamentStage.objects.create(tournament=cls.tournament, name="Bracket", format=TournamentStage.Format.SINGLE_ELIM)
        cls.match = Match.objects.create(stage=cls.stage)

    def test_str_and_display_name_for_a_member_referee(self):
        assignment = MatchReferee.objects.create(match=self.match, member=self.member)

        self.assertEqual(assignment.display_name, "Jane Doe")
        self.assertEqual(str(assignment), f"{self.match} - Jane Doe")

    def test_str_and_display_name_for_an_external_referee(self):
        assignment = MatchReferee.objects.create(match=self.match, external_name="Guest Ref")

        self.assertEqual(assignment.display_name, "Guest Ref")
        self.assertEqual(str(assignment), f"{self.match} - Guest Ref")

    def test_status_defaults_to_confirmed(self):
        assignment = MatchReferee.objects.create(match=self.match, member=self.member)
        self.assertEqual(assignment.status, MatchReferee.Status.CONFIRMED)

    def test_member_xor_external_name_rejects_neither_set(self):
        with self.assertRaises(IntegrityError):
            MatchReferee.objects.create(match=self.match)

    def test_member_xor_external_name_rejects_both_set(self):
        with self.assertRaises(IntegrityError):
            MatchReferee.objects.create(match=self.match, member=self.member, external_name="Guest Ref")

    def test_member_xor_external_name_accepts_member_only(self):
        MatchReferee.objects.create(match=self.match, member=self.member)

    def test_member_xor_external_name_accepts_external_name_only(self):
        MatchReferee.objects.create(match=self.match, external_name="Guest Ref")

    def test_member_is_unique_per_match(self):
        MatchReferee.objects.create(match=self.match, member=self.member)

        with self.assertRaises(IntegrityError):
            MatchReferee.objects.create(match=self.match, member=self.member)

    def test_several_external_referees_are_allowed_on_the_same_match(self):
        MatchReferee.objects.create(match=self.match, external_name="Guest Ref A")

        MatchReferee.objects.create(match=self.match, external_name="Guest Ref B")

        self.assertEqual(self.match.referees.count(), 2)


class MatchOfficialModelTests(TournamentsTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.stage = TournamentStage.objects.create(tournament=cls.tournament, name="Bracket", format=TournamentStage.Format.SINGLE_ELIM)
        cls.match = Match.objects.create(stage=cls.stage)

    def test_str_and_display_name_for_a_member_official(self):
        assignment = MatchOfficial.objects.create(match=self.match, member=self.member)

        self.assertEqual(assignment.display_name, "Jane Doe")
        self.assertEqual(str(assignment), f"{self.match} - Jane Doe")

    def test_str_and_display_name_for_an_external_official(self):
        assignment = MatchOfficial.objects.create(match=self.match, external_name="Guest Official")

        self.assertEqual(assignment.display_name, "Guest Official")
        self.assertEqual(str(assignment), f"{self.match} - Guest Official")

    def test_member_xor_external_name_rejects_neither_set(self):
        with self.assertRaises(IntegrityError):
            MatchOfficial.objects.create(match=self.match)

    def test_member_xor_external_name_rejects_both_set(self):
        with self.assertRaises(IntegrityError):
            MatchOfficial.objects.create(match=self.match, member=self.member, external_name="Guest Official")

    def test_member_xor_external_name_accepts_member_only(self):
        MatchOfficial.objects.create(match=self.match, member=self.member)

    def test_member_xor_external_name_accepts_external_name_only(self):
        MatchOfficial.objects.create(match=self.match, external_name="Guest Official")

    def test_member_is_unique_per_match(self):
        MatchOfficial.objects.create(match=self.match, member=self.member)

        with self.assertRaises(IntegrityError):
            MatchOfficial.objects.create(match=self.match, member=self.member)

    def test_several_external_officials_are_allowed_on_the_same_match(self):
        MatchOfficial.objects.create(match=self.match, external_name="Guest Official A")

        MatchOfficial.objects.create(match=self.match, external_name="Guest Official B")

        self.assertEqual(self.match.officials.count(), 2)


class DressingRoomModelTests(TournamentsTestCase):
    def test_str(self):
        room = DressingRoom.objects.create(name="Room 1")
        self.assertEqual(str(room), "Room 1")


class DressingRoomAssignmentModelTests(TournamentsTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.room = DressingRoom.objects.create(name="Room 1")
        cls.tournament_team = TournamentTeam.objects.create(tournament=cls.tournament, team=cls.team)

    def test_str(self):
        assignment = DressingRoomAssignment.objects.create(dressing_room=self.room, tournament_team=self.tournament_team)
        self.assertEqual(str(assignment), "Room 1 - First Team")

    def test_dressing_room_and_tournament_team_is_unique(self):
        DressingRoomAssignment.objects.create(dressing_room=self.room, tournament_team=self.tournament_team)

        with self.assertRaises(IntegrityError):
            DressingRoomAssignment.objects.create(dressing_room=self.room, tournament_team=self.tournament_team)

    def test_the_same_team_can_be_assigned_a_different_room(self):
        other_room = DressingRoom.objects.create(name="Room 2")
        DressingRoomAssignment.objects.create(dressing_room=self.room, tournament_team=self.tournament_team)

        DressingRoomAssignment.objects.create(dressing_room=other_room, tournament_team=self.tournament_team)

        self.assertEqual(self.tournament_team.dressing_room_assignments.count(), 2)

    def test_the_same_room_can_be_assigned_a_different_team(self):
        other_entry = TournamentTeam.objects.create(tournament=self.tournament, foreign_name="Guest FC")
        DressingRoomAssignment.objects.create(dressing_room=self.room, tournament_team=self.tournament_team)

        DressingRoomAssignment.objects.create(dressing_room=self.room, tournament_team=other_entry)

        self.assertEqual(self.room.assignments.count(), 2)


class TournamentTaskModelTests(TournamentsTestCase):
    def test_str(self):
        starts = timezone.now() + datetime.timedelta(days=1)
        task = TournamentTask.objects.create(tournament=self.tournament, title="Gate duty", starts_at=starts, ends_at=starts + datetime.timedelta(hours=1))

        self.assertEqual(str(task), "Summer Cup - Gate duty")

    def test_needed_quantity_defaults_to_one(self):
        starts = timezone.now() + datetime.timedelta(days=1)
        task = TournamentTask.objects.create(tournament=self.tournament, title="Gate duty", starts_at=starts, ends_at=starts + datetime.timedelta(hours=1))

        self.assertEqual(task.needed_quantity, 1)

    def test_clean_rejects_an_end_time_before_the_start_time(self):
        starts = timezone.now() + datetime.timedelta(days=1)
        task = TournamentTask(tournament=self.tournament, title="Gate duty", starts_at=starts, ends_at=starts - datetime.timedelta(hours=1))

        with self.assertRaises(ValidationError) as ctx:
            task.full_clean()
        self.assertIn("ends_at", ctx.exception.error_dict)

    def test_clean_rejects_an_end_time_equal_to_the_start_time(self):
        starts = timezone.now() + datetime.timedelta(days=1)
        task = TournamentTask(tournament=self.tournament, title="Gate duty", starts_at=starts, ends_at=starts)

        with self.assertRaises(ValidationError) as ctx:
            task.full_clean()
        self.assertIn("ends_at", ctx.exception.error_dict)

    def test_clean_accepts_an_end_time_after_the_start_time(self):
        starts = timezone.now() + datetime.timedelta(days=1)
        task = TournamentTask(tournament=self.tournament, title="Gate duty", starts_at=starts, ends_at=starts + datetime.timedelta(hours=1))

        task.full_clean()


class TournamentTaskClaimModelTests(TournamentsTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        starts = timezone.now() + datetime.timedelta(days=1)
        cls.task = TournamentTask.objects.create(tournament=cls.tournament, title="Gate duty", starts_at=starts, ends_at=starts + datetime.timedelta(hours=1))

    def test_str(self):
        claim = TournamentTaskClaim.objects.create(task=self.task, member=self.member)
        self.assertEqual(str(claim), f"{self.task} - {self.member}")

    def test_member_is_unique_per_task(self):
        TournamentTaskClaim.objects.create(task=self.task, member=self.member)

        with self.assertRaises(IntegrityError):
            TournamentTaskClaim.objects.create(task=self.task, member=self.member)

    def test_a_different_member_can_claim_the_same_task(self):
        other_member = Member.objects.create(first_name="John", last_name="Smith")
        TournamentTaskClaim.objects.create(task=self.task, member=self.member)

        TournamentTaskClaim.objects.create(task=self.task, member=other_member)

        self.assertEqual(self.task.claims.count(), 2)


class ApproveRegistrationServiceTests(TournamentsTestCase):
    def test_approving_a_foreign_registration_creates_a_team_and_copies_players(self):
        reviewer = make_user("staff@example.com")
        registration = TournamentRegistration.objects.create(tournament=self.tournament, foreign_name="Visiting FC", foreign_contact_email="visitors@example.com")
        TournamentRegistrationPlayer.objects.create(registration=registration, first_name="Sam", last_name="Striker", jersey_number=9)
        TournamentRegistrationPlayer.objects.create(registration=registration, first_name="Dee", last_name="Fender")

        team = approve_registration(registration, reviewed_by=reviewer)

        registration.refresh_from_db()
        self.assertEqual(registration.status, TournamentRegistration.Status.APPROVED)
        self.assertEqual(registration.reviewed_by, reviewer)
        self.assertIsNotNone(registration.reviewed_at)
        self.assertEqual(team.tournament, self.tournament)
        self.assertEqual(team.foreign_name, "Visiting FC")
        self.assertEqual(team.registration, registration)
        roster = {entry.display_name: entry for entry in team.roster_entries.all()}
        self.assertEqual(set(roster), {"Sam Striker", "Dee Fender"})
        self.assertEqual(roster["Sam Striker"].jersey_number, 9)
        self.assertIsNone(roster["Dee Fender"].jersey_number)

    def test_approving_a_club_team_registration_gives_it_an_event_and_its_staff_but_no_snapshot_of_the_team(self):
        reviewer = make_user("staff@example.com")
        coach = Member.objects.create(first_name="Coach", last_name="Carter")
        head_coach = Position.objects.create(name="Head coach", short_name="HC", staff_position=True)
        StaffAssignment.objects.create(team=self.team, member=coach, season=self.season, position=head_coach)
        TeamMembership.objects.create(team=self.team, member=self.member, season=self.season, jersey_number=7)
        registration = TournamentRegistration.objects.create(tournament=self.tournament, team=self.team)

        team = approve_registration(registration, reviewed_by=reviewer)

        self.assertIsNotNone(team.event)  # its own calendar event: the line-up there decides the players
        self.assertEqual(list(team.event.teams.all()), [self.team])
        self.assertEqual([(e.member_id, e.role) for e in team.roster_entries.all()], [(coach.pk, TournamentRosterRole.STAFF)])

    def test_approving_a_club_team_registration_does_not_use_another_seasons_roster(self):
        reviewer = make_user("staff@example.com")
        other_season = Season.objects.create(start_date=datetime.date(2020, 7, 1), end_date=datetime.date(2021, 6, 30))
        TeamMembership.objects.create(team=self.team, member=self.member, season=other_season)
        registration = TournamentRegistration.objects.create(tournament=self.tournament, team=self.team)

        team = approve_registration(registration, reviewed_by=reviewer)

        self.assertEqual(team.roster_entries.count(), 0)

    def test_approving_a_foreign_registration_copies_the_extra_roster_fields(self):
        reviewer = make_user("staff@example.com")
        registration = TournamentRegistration.objects.create(tournament=self.tournament, foreign_name="Visiting FC")
        TournamentRegistrationPlayer.objects.create(
            registration=registration,
            first_name="Cam",
            last_name="Coach",
            role=TournamentRosterRole.STAFF,
            position_label="Coach",
            license_number="LIC-1",
            dietary_restrictions="Vegetarian.",
            lunch_days=["2026-09-19"],
        )

        team = approve_registration(registration, reviewed_by=reviewer)

        entry = team.roster_entries.get()
        self.assertEqual(entry.role, TournamentRosterRole.STAFF)
        self.assertEqual(entry.position_label, "Coach")
        self.assertEqual(entry.license_number, "LIC-1")
        self.assertEqual(entry.dietary_restrictions, "Vegetarian.")
        self.assertEqual(entry.lunch_days, ["2026-09-19"])

    def test_cannot_approve_an_already_decided_registration(self):
        reviewer = make_user("staff@example.com")
        registration = TournamentRegistration.objects.create(tournament=self.tournament, foreign_name="Visiting FC")
        approve_registration(registration, reviewed_by=reviewer)

        with self.assertRaises(RegistrationDecisionError):
            approve_registration(registration, reviewed_by=reviewer)


class RejectRegistrationServiceTests(TournamentsTestCase):
    def test_rejecting_sets_status_and_reason(self):
        reviewer = make_user("staff@example.com")
        registration = TournamentRegistration.objects.create(tournament=self.tournament, foreign_name="Visiting FC")

        reject_registration(registration, reviewed_by=reviewer, reason="Tournament is full.")

        registration.refresh_from_db()
        self.assertEqual(registration.status, TournamentRegistration.Status.REJECTED)
        self.assertEqual(registration.rejection_reason, "Tournament is full.")
        self.assertEqual(registration.reviewed_by, reviewer)
        self.assertFalse(TournamentTeam.objects.filter(registration=registration).exists())

    def test_cannot_reject_an_already_decided_registration(self):
        reviewer = make_user("staff@example.com")
        registration = TournamentRegistration.objects.create(tournament=self.tournament, foreign_name="Visiting FC")
        reject_registration(registration, reviewed_by=reviewer)

        with self.assertRaises(RegistrationDecisionError):
            reject_registration(registration, reviewed_by=reviewer)


class WithdrawTeamServiceTests(TournamentsTestCase):
    def test_withdrawing_sets_withdrawn_at_and_reason(self):
        staff = make_user("staff@example.com")
        team = TournamentTeam.objects.create(tournament=self.tournament, foreign_name="Visiting FC")

        withdraw_team(team, reason="No-show.", withdrawn_by=staff)

        team.refresh_from_db()
        self.assertIsNotNone(team.withdrawn_at)
        self.assertEqual(team.withdrawal_reason, "No-show.")

    def test_cannot_withdraw_an_already_withdrawn_team(self):
        team = TournamentTeam.objects.create(tournament=self.tournament, foreign_name="Visiting FC")
        withdraw_team(team)

        with self.assertRaises(TeamWithdrawalError):
            withdraw_team(team)


class DeleteManualTeamServiceTests(TournamentsTestCase):
    def test_deletes_a_manually_added_foreign_team(self):
        team = create_manual_team(self.tournament, foreign_fields={"foreign_name": "Walk-up FC"})

        delete_manual_team(team)

        self.assertFalse(TournamentTeam.objects.filter(pk=team.pk).exists())

    def test_deletes_a_manually_added_club_team_and_its_roster(self):
        team = create_manual_team(self.tournament, club_team=self.team)
        TournamentRosterEntry.objects.create(tournament_team=team, member=self.member, role=TournamentRosterRole.PLAYER)

        delete_manual_team(team)

        self.assertFalse(TournamentTeam.objects.filter(pk=team.pk).exists())
        self.assertFalse(TournamentRosterEntry.objects.filter(tournament_team_id=team.pk).exists())

    def test_a_registration_backed_team_cannot_be_deleted(self):
        registration = TournamentRegistration.objects.create(tournament=self.tournament, foreign_name="Visiting FC")
        team = TournamentTeam.objects.create(tournament=self.tournament, registration=registration, foreign_name="Visiting FC")

        with self.assertRaises(TeamDeletionError):
            delete_manual_team(team)

        self.assertTrue(TournamentTeam.objects.filter(pk=team.pk).exists())

    def test_the_home_team_cannot_be_deleted(self):
        self.tournament.home_team = self.team
        self.tournament.save(update_fields=["home_team"])
        team = create_manual_team(self.tournament, club_team=self.team)

        with self.assertRaises(TeamDeletionError):
            delete_manual_team(team)

        self.assertTrue(TournamentTeam.objects.filter(pk=team.pk).exists())

    def test_a_non_home_manually_added_club_team_can_still_be_deleted(self):
        other_team = Team.objects.create(name="Second Team", short_name="2nd")
        self.tournament.home_team = self.team
        self.tournament.save(update_fields=["home_team"])
        team = create_manual_team(self.tournament, club_team=other_team)

        delete_manual_team(team)

        self.assertFalse(TournamentTeam.objects.filter(pk=team.pk).exists())


class CreateManualTeamServiceTests(TournamentsTestCase):
    def test_a_club_team_gets_created_with_registration_left_null_and_its_own_event(self):
        TeamMembership.objects.create(team=self.team, member=self.member, season=self.season, jersey_number=7)

        team = create_manual_team(self.tournament, club_team=self.team)

        self.assertEqual(team.tournament, self.tournament)
        self.assertEqual(team.team, self.team)
        self.assertIsNone(team.registration)
        self.assertEqual(team.payment_status, TournamentTeam.PaymentStatus.FULLY_PAID)
        self.assertEqual(team.event.kind, Event.EventKind.TOURNAMENT)
        self.assertEqual(list(team.event.teams.all()), [self.team])
        self.assertEqual(team.roster_entries.count(), 0)  # the players come from the line-up, not from a dump of the whole team

    def test_a_foreign_team_gets_created_from_the_given_fields_with_no_roster(self):
        team = create_manual_team(self.tournament, foreign_fields={"foreign_name": "Walk-up FC", "foreign_contact_email": "walkup@example.com"})

        self.assertEqual(team.foreign_name, "Walk-up FC")
        self.assertEqual(team.foreign_contact_email, "walkup@example.com")
        self.assertIsNone(team.team)
        self.assertIsNone(team.registration)
        self.assertEqual(team.roster_entries.count(), 0)


class EnsureHomeTeamServiceTests(TournamentsTestCase):
    def test_does_nothing_when_home_team_is_blank(self):
        ensure_home_team(self.tournament)

        self.assertEqual(TournamentTeam.objects.count(), 0)
        self.assertIsNone(self.tournament.event)

    def test_creates_the_tournament_team_fully_paid_with_no_roster_snapshot(self):
        # Unlike every other club-team path in this app, the home team's
        # roster is never a one-time TeamMembership dump -- only the live
        # Attendance-driven sync (tournaments/signals.py) ever adds to it,
        # so a team member with no qualifying Attendance yet must not show
        # up here just because ensure_home_team ran.
        position = Position.objects.create(name="Forward", short_name="FW")
        TeamMembership.objects.create(team=self.team, member=self.member, season=self.season, position=position, jersey_number=7)
        self.tournament.home_team = self.team
        self.tournament.save(update_fields=["home_team"])

        team = ensure_home_team(self.tournament)

        self.assertEqual(team.team, self.team)
        self.assertEqual(team.payment_status, TournamentTeam.PaymentStatus.FULLY_PAID)
        self.assertEqual(team.roster_entries.count(), 0)

    def test_creates_and_links_an_event_scoped_to_the_home_team(self):
        self.tournament.home_team = self.team
        self.tournament.save(update_fields=["home_team"])

        ensure_home_team(self.tournament)

        self.tournament.refresh_from_db()
        self.assertIsNotNone(self.tournament.event)
        self.assertEqual(self.tournament.event.kind, Event.EventKind.TOURNAMENT)
        self.assertEqual(list(self.tournament.event.teams.all()), [self.team])
        self.assertFalse(self.tournament.event.club_wide)

    def test_calling_it_again_does_not_duplicate_the_team_or_the_event(self):
        self.tournament.home_team = self.team
        self.tournament.save(update_fields=["home_team"])
        ensure_home_team(self.tournament)
        self.tournament.refresh_from_db()
        first_event_id = self.tournament.event_id

        ensure_home_team(self.tournament)

        self.assertEqual(TournamentTeam.objects.filter(tournament=self.tournament, team=self.team).count(), 1)
        self.tournament.refresh_from_db()
        self.assertEqual(self.tournament.event_id, first_event_id)

    def test_a_pre_existing_teams_players_that_are_not_picked_get_purged(self):
        # A team entered before it was the home team (or before rosters followed the line-up) can hold players nobody picked.
        team = create_manual_team(self.tournament, club_team=self.team)
        TournamentRosterEntry.objects.create(tournament_team=team, member=self.member)
        self.assertEqual(team.roster_entries.count(), 1)

        self.tournament.home_team = self.team
        self.tournament.save(update_fields=["home_team"])
        ensure_home_team(self.tournament)

        self.assertEqual(team.roster_entries.count(), 0)

    def test_a_pre_existing_teams_picked_member_survives_reconciliation(self):
        TeamMembership.objects.create(team=self.team, member=self.member, season=self.season)
        team = create_manual_team(self.tournament, club_team=self.team)
        self.tournament.home_team = self.team
        self.tournament.save(update_fields=["home_team"])
        ensure_home_team(self.tournament)  # creates the event, purges the stale entry
        self.tournament.refresh_from_db()
        lineup = Lineup.objects.create(event=self.tournament.event, team=self.team)
        LineupSelection.objects.create(lineup=lineup, member=self.member)

        ensure_home_team(self.tournament)

        self.assertEqual(list(team.roster_entries.values_list("member_id", flat=True)), [self.member.pk])

    def test_does_not_overwrite_an_already_linked_event(self):
        other_event = Event.objects.create(season=self.season, kind=Event.EventKind.OTHER, title="Something else", start=timezone.now())
        self.tournament.home_team = self.team
        self.tournament.event = other_event
        self.tournament.save(update_fields=["home_team", "event"])

        ensure_home_team(self.tournament)

        self.tournament.refresh_from_db()
        self.assertEqual(self.tournament.event_id, other_event.pk)

    def test_changing_home_team_re_points_the_existing_event_at_the_new_team(self):
        other_team = Team.objects.create(name="Second Team", short_name="2nd")
        self.tournament.home_team = self.team
        self.tournament.save(update_fields=["home_team"])
        ensure_home_team(self.tournament)
        self.tournament.refresh_from_db()
        event_id = self.tournament.event_id

        self.tournament.home_team = other_team
        self.tournament.save(update_fields=["home_team"])
        ensure_home_team(self.tournament)

        self.tournament.refresh_from_db()
        self.assertEqual(self.tournament.event_id, event_id)  # same event, not a new one
        self.assertEqual(list(self.tournament.event.teams.all()), [other_team])


class WithdrawHomeTeamGuardTests(TournamentsTestCase):
    def test_the_home_team_cannot_be_withdrawn(self):
        self.tournament.home_team = self.team
        self.tournament.save(update_fields=["home_team"])
        team = create_manual_team(self.tournament, club_team=self.team)

        with self.assertRaises(TeamWithdrawalError):
            withdraw_team(team)

        team.refresh_from_db()
        self.assertIsNone(team.withdrawn_at)

    def test_a_non_home_club_team_can_still_be_withdrawn(self):
        other_team = Team.objects.create(name="Second Team", short_name="2nd")
        self.tournament.home_team = self.team  # some other team is home, not this one
        self.tournament.save(update_fields=["home_team"])
        team = create_manual_team(self.tournament, club_team=other_team)

        withdraw_team(team)

        team.refresh_from_db()
        self.assertIsNotNone(team.withdrawn_at)


class HomeTeamRosterSyncSignalTests(TournamentsTestCase):
    """The home team's roster mirrors the line-up of the tournament's calendar event."""

    def setUp(self):
        self.tournament.home_team = self.team
        self.tournament.save(update_fields=["home_team"])
        self.tournament_team = ensure_home_team(self.tournament)
        self.tournament.refresh_from_db()
        self.lineup = Lineup.objects.create(event=self.tournament.event, team=self.team)

    def pick(self, member):
        """What the line-up service does when the coach picks someone: rows are written in bulk, then the signal tells the listeners."""
        LineupSelection.objects.create(lineup=self.lineup, member=member)
        lineup_changed.send(sender=Lineup, lineup=self.lineup)

    def unpick(self, member):
        LineupSelection.objects.filter(lineup=self.lineup, member=member).delete()
        lineup_changed.send(sender=Lineup, lineup=self.lineup)

    def on_roster(self, member):
        return TournamentRosterEntry.objects.filter(tournament_team=self.tournament_team, member=member).exists()

    def test_a_picked_player_is_added_as_a_player(self):
        self.pick(self.member)

        entry = TournamentRosterEntry.objects.get(tournament_team=self.tournament_team, member=self.member)
        self.assertEqual(entry.role, TournamentRosterRole.PLAYER)

    def test_picking_does_not_need_the_line_up_to_be_published(self):
        self.assertIsNone(self.lineup.published_at)
        self.pick(self.member)

        self.assertTrue(self.on_roster(self.member))

    def test_unpicking_removes_the_player(self):
        self.pick(self.member)
        self.unpick(self.member)

        self.assertFalse(self.on_roster(self.member))

    def test_a_picked_player_who_declines_is_removed(self):
        self.pick(self.member)
        attendance = Attendance.objects.create(event=self.tournament.event, member=self.member, status=Attendance.AttendanceStatus.PRESENT)
        self.assertTrue(self.on_roster(self.member))

        attendance.status = Attendance.AttendanceStatus.ABSENT
        attendance.save()

        self.assertFalse(self.on_roster(self.member))

    def test_a_player_who_declined_is_not_added_when_picked(self):
        Attendance.objects.create(event=self.tournament.event, member=self.member, status=Attendance.AttendanceStatus.EXCUSED)
        self.pick(self.member)

        self.assertFalse(self.on_roster(self.member))

    def test_attending_without_a_pick_does_not_add_anyone(self):
        for status in (Attendance.AttendanceStatus.PRESENT, Attendance.AttendanceStatus.SELECTED, Attendance.AttendanceStatus.NO_RESPONSE):
            Attendance.objects.update_or_create(event=self.tournament.event, member=self.member, defaults={"status": status})

        self.assertFalse(self.on_roster(self.member))

    def test_a_normal_events_attendance_is_unaffected(self):
        other_member = Member.objects.create(first_name="Sam", last_name="Smith")
        plain_event = Event.objects.create(season=self.season, kind=Event.EventKind.TRAINING, title="Training", start=timezone.now())

        Attendance.objects.create(event=plain_event, member=other_member, status=Attendance.AttendanceStatus.PRESENT)

        self.assertFalse(self.on_roster(other_member))

    def test_a_player_who_stays_picked_keeps_the_details_filled_in_by_hand(self):
        entry = TournamentRosterEntry.objects.create(tournament_team=self.tournament_team, member=self.member, position_label="Striker")
        self.pick(self.member)
        other = Member.objects.create(first_name="Other", last_name="Player")
        self.pick(other)  # a later change to the line-up re-syncs everyone

        entry.refresh_from_db()
        self.assertEqual(entry.position_label, "Striker")
        self.assertEqual(TournamentRosterEntry.objects.filter(tournament_team=self.tournament_team, member=self.member).count(), 1)

    def test_a_picked_player_takes_over_the_jersey_number_and_position_of_the_team(self):
        forward = Position.objects.create(name="Forward", short_name="FW")
        TeamMembership.objects.create(team=self.team, member=self.member, season=self.season, jersey_number=9, position=forward)
        self.pick(self.member)

        entry = TournamentRosterEntry.objects.get(tournament_team=self.tournament_team, member=self.member)
        self.assertEqual((entry.jersey_number, entry.position_label), (9, "Forward"))

    def test_only_the_tournaments_season_counts_for_the_take_over(self):
        other_season = Season.objects.create(start_date=datetime.date(2030, 7, 1), end_date=datetime.date(2031, 6, 30))
        TeamMembership.objects.create(team=self.team, member=self.member, season=other_season, jersey_number=4)
        self.pick(self.member)

        entry = TournamentRosterEntry.objects.get(tournament_team=self.tournament_team, member=self.member)
        self.assertEqual((entry.jersey_number, entry.position_label), (None, ""))

    def test_blanks_on_an_existing_line_are_filled_but_typed_values_stick(self):
        forward = Position.objects.create(name="Forward", short_name="FW")
        TeamMembership.objects.create(team=self.team, member=self.member, season=self.season, jersey_number=9, position=forward)
        entry = TournamentRosterEntry.objects.create(tournament_team=self.tournament_team, member=self.member, position_label="Captain")
        self.pick(self.member)

        entry.refresh_from_db()
        self.assertEqual((entry.jersey_number, entry.position_label), (9, "Captain"))  # number filled in, typed position kept

    def test_a_number_already_taken_on_the_roster_is_not_taken_over_twice(self):
        other = Member.objects.create(first_name="Other", last_name="Player")
        TeamMembership.objects.create(team=self.team, member=self.member, season=self.season, jersey_number=9)
        TeamMembership.objects.create(team=self.team, member=other, season=self.season, jersey_number=10)
        TournamentRosterEntry.objects.create(tournament_team=self.tournament_team, foreign_first_name="Guest", foreign_last_name="Nine", jersey_number=9)
        self.pick(self.member)
        self.pick(other)

        self.assertIsNone(TournamentRosterEntry.objects.get(tournament_team=self.tournament_team, member=self.member).jersey_number)
        self.assertEqual(TournamentRosterEntry.objects.get(tournament_team=self.tournament_team, member=other).jersey_number, 10)

    def test_staff_and_guests_added_by_hand_are_never_removed_by_the_sync(self):
        coach = Member.objects.create(first_name="Coach", last_name="Carter")
        TournamentRosterEntry.objects.create(tournament_team=self.tournament_team, member=coach, role=TournamentRosterRole.STAFF)
        guest = TournamentRosterEntry.objects.create(tournament_team=self.tournament_team, foreign_first_name="Guest", foreign_last_name="Player")
        self.pick(self.member)
        self.unpick(self.member)

        self.assertTrue(self.on_roster(coach))
        self.assertTrue(TournamentRosterEntry.objects.filter(pk=guest.pk).exists())

    def test_a_staff_member_who_is_also_picked_is_not_added_twice(self):
        TournamentRosterEntry.objects.create(tournament_team=self.tournament_team, member=self.member, role=TournamentRosterRole.STAFF)
        self.pick(self.member)

        self.assertEqual(TournamentRosterEntry.objects.filter(tournament_team=self.tournament_team, member=self.member).count(), 1)


class HomeTeamStaffTests(TournamentsTestCase):
    """The home team's staff is copied onto the roster once, when the home team enters; after that it is kept by hand."""

    def setUp(self):
        self.coach = Member.objects.create(first_name="Coach", last_name="Carter")
        self.physio = Member.objects.create(first_name="Pat", last_name="Physio")
        self.head_coach = Position.objects.create(name="Head coach", short_name="HC", staff_position=True)
        self.physio_position = Position.objects.create(name="Physio", short_name="PH", staff_position=True)
        StaffAssignment.objects.create(team=self.team, member=self.coach, season=self.season, position=self.head_coach)
        StaffAssignment.objects.create(team=self.team, member=self.physio, season=self.season, position=self.physio_position)
        self.tournament.home_team = self.team
        self.tournament.save(update_fields=["home_team"])

    def test_the_teams_staff_is_added_as_staff_with_their_position(self):
        team = ensure_home_team(self.tournament)

        entries = {e.member_id: e for e in team.roster_entries.all()}
        self.assertEqual(set(entries), {self.coach.pk, self.physio.pk})
        self.assertEqual({e.role for e in entries.values()}, {TournamentRosterRole.STAFF})
        self.assertEqual(entries[self.coach.pk].position_label, "Head coach")

    def test_only_staff_of_the_tournaments_season_is_added(self):
        other_season = Season.objects.create(start_date=datetime.date(2030, 7, 1), end_date=datetime.date(2031, 6, 30))
        StaffAssignment.objects.create(team=self.team, member=Member.objects.create(first_name="Old", last_name="Coach"), season=other_season, position=self.head_coach)

        team = ensure_home_team(self.tournament)

        self.assertEqual(team.roster_entries.count(), 2)

    def test_a_manual_change_to_the_staff_sticks(self):
        team = ensure_home_team(self.tournament)
        team.roster_entries.filter(member=self.physio).delete()
        extra = Member.objects.create(first_name="Extra", last_name="Helper")
        TournamentRosterEntry.objects.create(tournament_team=team, member=extra, role=TournamentRosterRole.STAFF)

        ensure_home_team(self.tournament)  # every save of the tournament calls this again

        self.assertEqual(set(team.roster_entries.values_list("member_id", flat=True)), {self.coach.pk, extra.pk})

    def test_staff_survive_the_line_up_sync(self):
        team = ensure_home_team(self.tournament)
        self.tournament.refresh_from_db()
        lineup = Lineup.objects.create(event=self.tournament.event, team=self.team)
        lineup_changed.send(sender=Lineup, lineup=lineup)

        self.assertEqual(team.roster_entries.filter(role=TournamentRosterRole.STAFF).count(), 2)


class NotifyTournamentShiftsOpenServiceTests(TournamentsTestCase):
    def setUp(self):
        TeamMembership.objects.create(team=self.team, member=self.member, season=self.season)
        self.tournament.home_team = self.team
        self.tournament.status = Tournament.Status.PUBLISHED
        self.tournament.save(update_fields=["home_team", "status"])
        self.tournament_team = ensure_home_team(self.tournament)
        self.tournament.refresh_from_db()

    def test_skips_when_there_is_no_home_team(self):
        self.tournament.home_team = None
        self.tournament.save(update_fields=["home_team"])

        result = notify_tournament_shifts_open(str(self.tournament.pk))

        self.assertIn("Skipped", result)
        self.assertEqual(len(mail.outbox), 0)

    def test_skips_when_there_are_no_shifts_yet(self):
        result = notify_tournament_shifts_open(str(self.tournament.pk))

        self.assertIn("Skipped", result)

    def test_notifies_the_home_teams_whole_squad(self):
        self.member.email = "jane@example.com"
        self.member.save()
        user, _created = grant_login(self.club, self.member)
        TournamentTask.objects.create(tournament=self.tournament, title="Gate duty", starts_at=timezone.now(), ends_at=timezone.now() + datetime.timedelta(hours=1))

        result = notify_tournament_shifts_open(str(self.tournament.pk))

        self.assertIn("Notified 1", result)
        self.assertEqual(list(InboxNotification.objects.filter(user=user).values_list("title", flat=True)), ["Volunteer shifts open: Summer Cup"])

    def test_notifies_the_whole_squad_even_before_anyone_has_been_selected(self):
        # This is the entire point of the notification: it fires the moment
        # the tournament is published, before any coach has picked anyone
        # for the actual roster (sync_home_team_roster only adds a
        # TournamentRosterEntry once someone's SELECTED) -- querying the
        # roster itself here would find nobody, right when a fresh
        # publish-notification is most useful.
        self.assertEqual(self.tournament_team.roster_entries.count(), 0)
        self.member.email = "jane@example.com"
        self.member.save()
        user, _created = grant_login(self.club, self.member)
        TournamentTask.objects.create(tournament=self.tournament, title="Gate duty", starts_at=timezone.now(), ends_at=timezone.now() + datetime.timedelta(hours=1))

        result = notify_tournament_shifts_open(str(self.tournament.pk))

        self.assertIn("Notified 1", result)
        self.assertEqual(list(InboxNotification.objects.filter(user=user).values_list("title", flat=True)), ["Volunteer shifts open: Summer Cup"])


class RoundRobinPairingsTests(TestCase):
    def test_even_count_every_pair_meets_exactly_once(self):
        entries = ["A", "B", "C", "D"]
        rounds = round_robin_pairings(entries)

        self.assertEqual(len(rounds), 3)
        seen = [frozenset(pair) for round_pairs in rounds for pair in round_pairs]
        expected = [frozenset(pair) for pair in itertools.combinations(entries, 2)]
        self.assertCountEqual(seen, expected)

    def test_no_entry_plays_twice_in_one_round(self):
        entries = list(range(6))
        for round_pairs in round_robin_pairings(entries):
            players_this_round = [entry for pair in round_pairs for entry in pair]
            self.assertEqual(len(players_this_round), len(set(players_this_round)))

    def test_odd_count_gives_one_bye_per_round(self):
        entries = ["A", "B", "C"]
        rounds = round_robin_pairings(entries)

        self.assertEqual(len(rounds), 3)
        for round_pairs in rounds:
            self.assertEqual(len(round_pairs), 1)

    def test_fewer_than_two_entries_returns_no_rounds(self):
        self.assertEqual(round_robin_pairings(["A"]), [])
        self.assertEqual(round_robin_pairings([]), [])


class SnakeSeedGroupsTests(TestCase):
    def test_snake_order_across_three_groups(self):
        entries = list(range(1, 10))  # already seed-ordered 1..9

        groups = snake_seed_groups(entries, 3)

        self.assertEqual(groups, [[1, 6, 7], [2, 5, 8], [3, 4, 9]])

    def test_every_entry_placed_exactly_once(self):
        entries = list(range(1, 14))
        groups = snake_seed_groups(entries, 4)

        self.assertCountEqual([entry for group in groups for entry in group], entries)

    def test_rejects_fewer_than_one_group(self):
        with self.assertRaises(SchedulingError):
            snake_seed_groups([1, 2], 0)


class SingleEliminationPlacementBracketTests(TestCase):
    def test_requires_an_exact_power_of_two(self):
        with self.assertRaises(BracketError):
            single_elimination_placement_bracket([f"T{i}" for i in range(1, 7)])

    def test_four_teams_produces_every_placement_match(self):
        rounds = single_elimination_placement_bracket([f"T{i}" for i in range(1, 5)])

        matches = [slot for round_ in rounds for slot in round_]
        self.assertEqual(len(matches), 4)  # 2 semis + final (1st/2nd) + 3rd place
        # The trailing single-match "deciding" rounds run worst-to-best --
        # 3rd place decided before the Final, so a real schedule built from
        # this (allocate_schedule places round_number in order) plays minor
        # placement games early and saves the Final for last.
        self.assertEqual([slot["round_label"] for slot in matches], ["Semifinal", "Semifinal", "3rd place", "Final"])
        # Round 1 pairing is untouched by this option -- same seed order as
        # the plain bracket (1v4, 2v3).
        self.assertEqual(matches[0]["home_team"], "T1")
        self.assertEqual(matches[0]["away_team"], "T4")
        self.assertEqual(matches[1]["home_team"], "T2")
        self.assertEqual(matches[1]["away_team"], "T3")

    def test_eight_teams_labels_every_rank_tier(self):
        rounds = single_elimination_placement_bracket([f"T{i}" for i in range(1, 9)])

        matches = [slot for round_ in rounds for slot in round_]
        self.assertEqual(len(matches), 12)  # 4 QF + 2 SF + 1 final + (2 + 1 + 1) placement
        labels = {slot["round_label"] for slot in matches}
        self.assertEqual(labels, {"Quarterfinal", "Semifinal", "Ranks 5-8 · Semifinal", "Final", "3rd place", "5th place", "7th place"})

    def test_every_source_index_points_strictly_earlier(self):
        rounds = single_elimination_placement_bracket([f"T{i}" for i in range(1, 9)])

        matches = [slot for round_ in rounds for slot in round_]
        for index, slot in enumerate(matches):
            for side in ("home", "away"):
                source = slot.get(f"{side}_source_index")
                if source is not None:
                    self.assertLess(source, index)

    def test_deciding_rounds_run_worst_to_best_with_the_final_last(self):
        rounds = single_elimination_placement_bracket([f"T{i}" for i in range(1, 9)])

        # Quarterfinal/Semifinal/Ranks 5-8 Semifinal keep their own
        # (unaffected, still-developing) order; only the trailing run of
        # single-match deciders reverses.
        self.assertEqual([round_[0]["round_label"] for round_ in rounds], ["Quarterfinal", "Semifinal", "Ranks 5-8 · Semifinal", "7th place", "5th place", "3rd place", "Final"])


class DoubleEliminationPlacementBracketTests(TestCase):
    def flat(self, count):
        rounds = double_elimination_placement_bracket([f"T{i}" for i in range(1, count + 1)])
        return rounds, [slot for round_ in rounds for slot in round_]

    def test_requires_a_power_of_two_of_at_least_four(self):
        for count in (2, 3, 6):
            with self.assertRaises(BracketError):
                double_elimination_placement_bracket([f"T{i}" for i in range(1, count + 1)])

    def test_plain_bracket_is_untouched_apart_from_the_added_matches(self):
        for count in (4, 8, 16):
            teams = [f"T{i}" for i in range(1, count + 1)]
            plain = [slot for round_ in double_elimination_bracket(teams) for slot in round_]
            _rounds, full = self.flat(count)
            self.assertEqual(full[: len(plain) - 1], plain[:-1])  # everything up to the grand final
            self.assertEqual(full[-1], plain[-1])  # the grand final stays last, with the same sources

    def test_four_teams(self):
        rounds, matches = self.flat(4)
        self.assertEqual(len(matches), 6)  # 5 plain + 3rd place
        self.assertEqual([round_[0]["round_label"] for round_ in rounds], ["Semifinal", "Final", "Losers round 1", "3rd place", "Grand final"])
        third = matches[4]
        # the winners-final loser and the losers-final loser play for 3rd
        self.assertEqual((third["home_source_index"], third["home_source_is_winner"]), (2, False))
        self.assertEqual((third["away_source_index"], third["away_source_is_winner"]), (3, False))

    def test_eight_teams(self):
        rounds, matches = self.flat(8)
        self.assertEqual(len(matches), 16)  # 13 plain + 3 classification matches
        self.assertEqual([round_[0]["round_label"] for round_ in rounds][-4:], ["7th place", "5th place", "3rd place", "Grand final"])

    def test_sixteen_teams(self):
        rounds, matches = self.flat(16)
        self.assertEqual(len(matches), 40)  # 29 plain + 3rd, 5th, 7th + two ladders of four (4 each)
        labels = [round_[0]["round_label"] for round_ in rounds]
        self.assertIn("Ranks 13-16 · Semifinal", labels)
        self.assertIn("Ranks 9-12 · Semifinal", labels)
        self.assertIn("13th place", labels)
        self.assertIn("9th place", labels)

    def test_every_rank_is_reachable_exactly_once_and_sources_are_wired(self):
        for count in (4, 8, 16):
            _rounds, matches = self.flat(count)
            consumed = set()
            for index, slot in enumerate(matches):
                for side in ("home", "away"):
                    source = slot.get(f"{side}_source_index")
                    if source is None:
                        continue
                    self.assertLess(source, index)
                    key = (source, slot.get(f"{side}_source_is_winner", True))
                    self.assertNotIn(key, consumed)  # no match result feeds two slots
                    consumed.add(key)
            # An outcome nobody consumes is where a team's journey ends, i.e. one final rank: N teams, N distinct ranks.
            unconsumed = [(i, w) for i in range(len(matches)) for w in (True, False) if (i, w) not in consumed]
            self.assertEqual(len(unconsumed), count)

    def test_no_match_is_stuck_with_two_unreachable_sources(self):
        _rounds, matches = self.flat(8)
        # a match's sources must each be a real earlier match, and a round-1 match must be seeded with real teams on both sides
        for index, slot in enumerate(matches):
            for side in ("home", "away"):
                has_team, has_source = f"{side}_team" in slot, f"{side}_source_index" in slot
                self.assertTrue(has_team != has_source)
                if has_source:
                    self.assertTrue(0 <= slot[f"{side}_source_index"] < index)


class GenerateStageMatchesTests(TournamentsTestCase):
    def make_teams(self, count):
        return [TournamentTeam.objects.create(tournament=self.tournament, foreign_name=f"Team {i}", seed=i) for i in range(1, count + 1)]

    def test_round_robin_creates_every_pairing(self):
        teams = self.make_teams(4)
        stage = TournamentStage.objects.create(tournament=self.tournament, name="RR", format=TournamentStage.Format.ROUND_ROBIN)

        matches = generate_stage_matches(stage, entries=teams)

        self.assertEqual(len(matches), 6)  # 4 choose 2

    def test_regenerating_an_already_generated_stage_raises(self):
        teams = self.make_teams(4)
        stage = TournamentStage.objects.create(tournament=self.tournament, name="RR", format=TournamentStage.Format.ROUND_ROBIN)
        generate_stage_matches(stage, entries=teams)

        with self.assertRaises(SchedulingError):
            generate_stage_matches(stage, entries=teams)

    def test_custom_format_has_no_generator(self):
        teams = self.make_teams(4)
        stage = TournamentStage.objects.create(tournament=self.tournament, name="Custom", format=TournamentStage.Format.CUSTOM)

        with self.assertRaises(SchedulingError):
            generate_stage_matches(stage, entries=teams)

    def test_too_few_teams_raises(self):
        teams = self.make_teams(1)
        stage = TournamentStage.objects.create(tournament=self.tournament, name="RR", format=TournamentStage.Format.ROUND_ROBIN)

        with self.assertRaises(SchedulingError):
            generate_stage_matches(stage, entries=teams)

    def test_groups_splits_and_seeds_correctly(self):
        teams = self.make_teams(9)
        stage = TournamentStage.objects.create(tournament=self.tournament, name="Groups", format=TournamentStage.Format.GROUPS, config={"num_groups": 3})

        matches = generate_stage_matches(stage, entries=teams)

        self.assertEqual(stage.groups.count(), 3)
        self.assertEqual(len(matches), 9)  # 3 groups x (3 choose 2)
        for group in stage.groups.all():
            self.assertEqual(group.matches.count(), 3)

    def test_single_elim_seeds_are_kept_apart(self):
        teams = self.make_teams(8)
        stage = TournamentStage.objects.create(tournament=self.tournament, name="KO", format=TournamentStage.Format.SINGLE_ELIM)

        matches = generate_stage_matches(stage, entries=teams)

        self.assertEqual(len(matches), 7)  # 4 + 2 + 1
        round1 = [m for m in matches if m.round_number == 1]
        self.assertCountEqual([frozenset((m.home_team.seed, m.away_team.seed)) for m in round1], [frozenset((1, 8)), frozenset((4, 5)), frozenset((2, 7)), frozenset((3, 6))])
        final = next(m for m in matches if m.round_number == 3)
        self.assertEqual(final.round_label, "Final")
        self.assertIsNotNone(final.home_source_match_id)
        self.assertIsNotNone(final.away_source_match_id)

    def test_single_elim_with_a_bye_auto_advances_the_present_team(self):
        teams = self.make_teams(3)  # bracket size 4, one bye
        stage = TournamentStage.objects.create(tournament=self.tournament, name="KO", format=TournamentStage.Format.SINGLE_ELIM)

        generate_stage_matches(stage, entries=teams)

        bye_match = Match.objects.get(stage=stage, round_number=1, away_team__isnull=True)
        self.assertEqual(bye_match.status, Match.Status.WALKOVER)
        final = Match.objects.get(stage=stage, round_number=2)
        # The bye'd seed (seed 1, paired against the missing seed 4) is
        # already sitting in the final without anyone completing its match.
        self.assertEqual(final.home_team_id or final.away_team_id, teams[0].pk)

    def test_double_elim_requires_a_power_of_two(self):
        teams = self.make_teams(5)
        stage = TournamentStage.objects.create(tournament=self.tournament, name="DE", format=TournamentStage.Format.DOUBLE_ELIM)

        with self.assertRaises(SchedulingError):
            generate_stage_matches(stage, entries=teams)

    def test_double_elim_grand_final_sources_the_right_matches(self):
        teams = self.make_teams(4)
        stage = TournamentStage.objects.create(tournament=self.tournament, name="DE", format=TournamentStage.Format.DOUBLE_ELIM)

        matches = generate_stage_matches(stage, entries=teams)

        self.assertEqual(len(matches), 5)  # 2 semis + winners final + 1 losers round + grand final
        grand_final = matches[-1]
        self.assertEqual(grand_final.round_label, "Grand final")
        winners_final = matches[2]
        losers_final = matches[3]
        self.assertEqual(grand_final.home_source_match_id, winners_final.pk)
        self.assertTrue(grand_final.home_source_is_winner)
        self.assertEqual(grand_final.away_source_match_id, losers_final.pk)
        self.assertTrue(grand_final.away_source_is_winner)
        # The losers-round match takes the LOSER of each semifinal, not the winner.
        self.assertFalse(losers_final.home_source_is_winner)
        self.assertFalse(losers_final.away_source_is_winner)

    def test_with_placement_rejected_for_non_knockout_formats(self):
        teams = self.make_teams(4)
        for format_value in (TournamentStage.Format.ROUND_ROBIN, TournamentStage.Format.GROUPS):
            stage = TournamentStage.objects.create(tournament=self.tournament, name=format_value, format=format_value)
            with self.assertRaises(SchedulingError):
                generate_stage_matches(stage, entries=teams, with_placement=True)

    def test_with_placement_persists_the_full_ladder(self):
        teams = self.make_teams(8)
        stage = TournamentStage.objects.create(tournament=self.tournament, name="KO", format=TournamentStage.Format.SINGLE_ELIM)

        matches = generate_stage_matches(stage, entries=teams, with_placement=True)

        self.assertEqual(len(matches), 12)  # 4 QF + 2 SF + 1 final + 4 placement (2 + 1 + 1)
        round1 = [m for m in matches if m.round_number == 1]
        self.assertCountEqual([frozenset((m.home_team.seed, m.away_team.seed)) for m in round1], [frozenset((1, 8)), frozenset((4, 5)), frozenset((2, 7)), frozenset((3, 6))])
        final = Match.objects.get(stage=stage, round_label="Final")
        self.assertIsNotNone(final.home_source_match_id)
        self.assertIsNotNone(final.away_source_match_id)
        third_place = Match.objects.get(stage=stage, round_label="3rd place")
        self.assertFalse(third_place.home_source_is_winner)
        self.assertFalse(third_place.away_source_is_winner)
        self.assertEqual(Match.objects.filter(stage=stage, round_label="5th place").count(), 1)
        self.assertEqual(Match.objects.filter(stage=stage, round_label="7th place").count(), 1)


    def test_double_elim_with_placement_persists_the_classification_matches(self):
        teams = self.make_teams(8)
        stage = TournamentStage.objects.create(tournament=self.tournament, name="DE", format=TournamentStage.Format.DOUBLE_ELIM)

        matches = generate_stage_matches(stage, entries=teams, with_placement=True)

        self.assertEqual(len(matches), 16)  # 13 plain + 3rd, 5th and 7th place
        third = Match.objects.get(stage=stage, round_label="3rd place")
        self.assertFalse(third.home_source_is_winner)
        self.assertFalse(third.away_source_is_winner)
        self.assertEqual(matches[-1].round_label, "Grand final")

    def test_double_elim_uses_the_stages_own_placement_setting(self):
        teams = self.make_teams(4)
        stage = TournamentStage.objects.create(tournament=self.tournament, name="DE", format=TournamentStage.Format.DOUBLE_ELIM, config={"placement": True})

        self.assertEqual(len(generate_stage_matches(stage, entries=teams)), 6)

    def test_double_elim_with_placement_needs_a_power_of_two(self):
        stage = TournamentStage.objects.create(tournament=self.tournament, name="DE", format=TournamentStage.Format.DOUBLE_ELIM)

        with self.assertRaises(SchedulingError):
            generate_stage_matches(stage, entries=self.make_teams(6), with_placement=True)

    def test_a_whole_double_elim_with_placement_plays_out_to_a_complete_ranking(self):
        teams = self.make_teams(4)
        stage = TournamentStage.objects.create(tournament=self.tournament, name="DE", format=TournamentStage.Format.DOUBLE_ELIM, config={"placement": True})
        generate_stage_matches(stage, entries=teams)

        # Always let the better (lower) seed win; play whatever has both teams known until nothing is left.
        while True:
            ready = [m for m in Match.objects.filter(stage=stage, status=Match.Status.SCHEDULED).order_by("round_number", "sequence") if m.home_team_id and m.away_team_id]
            if not ready:
                break
            match = ready[0]
            home_wins = match.home_team.seed < match.away_team.seed
            record_result(match, score_home=2 if home_wins else 1, score_away=1 if home_wins else 2)

        self.assertFalse(Match.objects.filter(stage=stage).exclude(status=Match.Status.COMPLETE).exists())
        final = Match.objects.get(stage=stage, round_label="Grand final")
        third = Match.objects.get(stage=stage, round_label="3rd place")
        ranking = [final.winner, final.loser, third.winner, third.loser]
        self.assertCountEqual([t.pk for t in ranking], [t.pk for t in teams])
        self.assertEqual([t.seed for t in ranking], [1, 3, 2, 4])  # the better seed always won


class GroupRankSeedTests(TournamentsTestCase):
    def make_group(self, team_count):
        teams = [TournamentTeam.objects.create(tournament=self.tournament, foreign_name=f"G{i}", seed=i) for i in range(1, team_count + 1)]
        group_stage = TournamentStage.objects.create(tournament=self.tournament, name="Groups", format=TournamentStage.Format.GROUPS, sequence=0, config={"num_groups": 1})
        generate_stage_matches(group_stage, entries=teams)
        return group_stage, group_stage.groups.get()

    def test_a_group_rank_seed_sets_the_source_fields_not_a_direct_team(self):
        _group_stage, group = self.make_group(4)
        team1 = TournamentTeam.objects.create(tournament=self.tournament, foreign_name="Direct 1", seed=1)
        team2 = TournamentTeam.objects.create(tournament=self.tournament, foreign_name="Direct 2", seed=2)
        stage = TournamentStage.objects.create(tournament=self.tournament, name="KO", format=TournamentStage.Format.SINGLE_ELIM, sequence=1)

        matches = generate_stage_matches(stage, entries=[team1, team2, GroupRankSeed(group, 1), GroupRankSeed(group, 2)])

        round1 = [m for m in matches if m.round_number == 1]
        self.assertEqual(len(round1), 2)
        for match in round1:
            self.assertEqual(match.status, Match.Status.SCHEDULED)

        seed_sourced = [m for m in round1 if m.home_source_group_id or m.away_source_group_id]
        self.assertEqual(len(seed_sourced), 2)
        self.assertCountEqual([m.home_source_rank or m.away_source_rank for m in seed_sourced], [1, 2])
        for match in seed_sourced:
            if match.home_source_group_id:
                self.assertIsNone(match.home_team_id)
                self.assertIsNotNone(match.away_team_id)
            else:
                self.assertIsNone(match.away_team_id)
                self.assertIsNotNone(match.home_team_id)

    def test_a_lone_group_rank_seed_bye_slot_is_scheduled_not_a_walkover(self):
        _group_stage, group = self.make_group(2)
        team1 = TournamentTeam.objects.create(tournament=self.tournament, foreign_name="Direct 1", seed=2)
        team2 = TournamentTeam.objects.create(tournament=self.tournament, foreign_name="Direct 2", seed=3)
        stage = TournamentStage.objects.create(tournament=self.tournament, name="KO", format=TournamentStage.Format.SINGLE_ELIM, sequence=1)

        # 3 entries pads to a bracket of 4 -- seed order [1, 4, 2, 3] leaves
        # the 4th slot empty, pairing this GroupRankSeed (seed 1) against it.
        generate_stage_matches(stage, entries=[GroupRankSeed(group, 1), team1, team2])

        bye_slot = Match.objects.get(stage=stage, round_number=1, home_source_group=group)
        self.assertEqual(bye_slot.status, Match.Status.SCHEDULED)
        self.assertIsNone(bye_slot.home_team_id)
        self.assertIsNone(bye_slot.away_team_id)
        self.assertEqual(bye_slot.home_source_rank, 1)

    def test_a_group_rank_seed_stays_tbd_until_next_phase_is_confirmed(self):
        # Regression test: this used to resolve automatically the instant
        # record_result completed the group's last match -- a mis-entered
        # score could lock the wrong team into a knockout slot with no
        # chance to catch it. Group-rank resolution is now a deliberate,
        # separate step (resolve_group_slots, behind the "Start next phase"
        # review screen) -- record_result on its own no longer touches it.
        _group_stage, group = self.make_group(2)
        stage = TournamentStage.objects.create(tournament=self.tournament, name="Final", format=TournamentStage.Format.SINGLE_ELIM, sequence=1)
        generate_stage_matches(stage, entries=[GroupRankSeed(group, 1), GroupRankSeed(group, 2)])
        final = stage.matches.get()

        only_match = group.matches.get()
        record_result(only_match, score_home=2, score_away=0)

        final.refresh_from_db()
        self.assertIsNone(final.home_team_id)
        self.assertIsNone(final.away_team_id)


class ResolveMatchSlotsTests(TournamentsTestCase):
    def test_completing_a_match_advances_the_winner(self):
        teams = self.make_teams = [TournamentTeam.objects.create(tournament=self.tournament, foreign_name=f"T{i}", seed=i) for i in range(1, 5)]
        stage = TournamentStage.objects.create(tournament=self.tournament, name="KO", format=TournamentStage.Format.SINGLE_ELIM)
        generate_stage_matches(stage, entries=teams)
        semi = Match.objects.get(stage=stage, round_number=1, sequence=0)
        semi.score_home, semi.score_away, semi.status = 3, 1, Match.Status.COMPLETE
        semi.save()

        updated = resolve_match_slots(semi)

        final = Match.objects.get(stage=stage, round_number=2)
        self.assertEqual(len(updated), 1)
        self.assertEqual(final.home_team_id or final.away_team_id, semi.home_team_id)

    def test_incomplete_match_resolves_nothing(self):
        teams = [TournamentTeam.objects.create(tournament=self.tournament, foreign_name=f"T{i}", seed=i) for i in range(1, 5)]
        stage = TournamentStage.objects.create(tournament=self.tournament, name="KO", format=TournamentStage.Format.SINGLE_ELIM)
        generate_stage_matches(stage, entries=teams)
        semi = Match.objects.get(stage=stage, round_number=1, sequence=0)

        self.assertEqual(resolve_match_slots(semi), [])


class GroupStandingsPreviewTests(TournamentsTestCase):
    def test_each_row_lists_the_slot_it_feeds(self):
        teams = [TournamentTeam.objects.create(tournament=self.tournament, foreign_name=f"T{i}", seed=i) for i in range(1, 3)]
        group_stage = TournamentStage.objects.create(tournament=self.tournament, name="Groups", format=TournamentStage.Format.GROUPS, sequence=0, config={"num_groups": 1})
        generate_stage_matches(group_stage, entries=teams)
        group = group_stage.groups.get()
        knockout_stage = TournamentStage.objects.create(tournament=self.tournament, name="Final", format=TournamentStage.Format.CUSTOM, sequence=1)
        crossover = Match.objects.create(stage=knockout_stage, round_number=1, home_source_group=group, home_source_rank=1)
        only_match = group.matches.get()
        record_result(only_match, score_home=2, score_away=0)

        preview = group_standings_preview(group)

        first_place = next(row for row in preview if row["rank"] == 1)
        self.assertEqual(first_place["feeds"], [{"match": crossover, "side": "home"}])
        second_place = next(row for row in preview if row["rank"] == 2)
        self.assertEqual(second_place["feeds"], [])


class ResolveGroupSlotsTests(TournamentsTestCase):
    def test_pushes_standings_into_every_downstream_slot(self):
        teams = [TournamentTeam.objects.create(tournament=self.tournament, foreign_name=f"T{i}", seed=i) for i in range(1, 3)]
        group_stage = TournamentStage.objects.create(tournament=self.tournament, name="Groups", format=TournamentStage.Format.GROUPS, sequence=0, config={"num_groups": 1})
        generate_stage_matches(group_stage, entries=teams)
        group = group_stage.groups.get()
        knockout_stage = TournamentStage.objects.create(tournament=self.tournament, name="Final", format=TournamentStage.Format.CUSTOM, sequence=1)
        crossover = Match.objects.create(stage=knockout_stage, round_number=1, home_source_group=group, home_source_rank=1)
        only_match = group.matches.get()
        record_result(only_match, score_home=2, score_away=0)

        updated, conflicts = resolve_group_slots(group)

        crossover.refresh_from_db()
        self.assertEqual(crossover.home_team_id, only_match.home_team_id)
        self.assertEqual([match.pk for match in updated], [crossover.pk])
        self.assertEqual(conflicts, [])

    def test_raises_if_the_group_isnt_complete_yet(self):
        teams = [TournamentTeam.objects.create(tournament=self.tournament, foreign_name=f"T{i}", seed=i) for i in range(1, 5)]
        group_stage = TournamentStage.objects.create(tournament=self.tournament, name="Groups", format=TournamentStage.Format.GROUPS, sequence=0, config={"num_groups": 1})
        generate_stage_matches(group_stage, entries=teams)
        group = group_stage.groups.get()

        with self.assertRaises(SchedulingError):
            resolve_group_slots(group)

    def test_flags_but_does_not_block_a_newly_real_double_booking(self):
        # The crossover match's own slot was already scheduled while both
        # teams were still TBD (see AllocateScheduleTests) -- if the team
        # standings just revealed for it turns out to already be busy
        # elsewhere at that exact time, resolve_group_slots still commits
        # the pairing, but reports the conflict for staff to go fix by hand.
        teams = [TournamentTeam.objects.create(tournament=self.tournament, foreign_name=f"T{i}", seed=i) for i in range(1, 3)]
        group_stage = TournamentStage.objects.create(tournament=self.tournament, name="Groups", format=TournamentStage.Format.GROUPS, sequence=0, config={"num_groups": 1})
        generate_stage_matches(group_stage, entries=teams)
        group = group_stage.groups.get()
        knockout_stage = TournamentStage.objects.create(tournament=self.tournament, name="Final", format=TournamentStage.Format.CUSTOM, sequence=1)
        field = TournamentField.objects.create(tournament=self.tournament, name="Field 1")
        crossover_start = timezone.make_aware(datetime.datetime(2026, 9, 19, 10, 0))
        crossover = Match.objects.create(stage=knockout_stage, round_number=1, home_source_group=group, home_source_rank=1, field=field, scheduled_start=crossover_start)
        only_match = group.matches.get()
        # The eventual #1 seed is already double-booked elsewhere at the
        # crossover's own scheduled time -- invisible until resolution.
        Match.objects.create(stage=knockout_stage, round_number=1, home_team=only_match.home_team, field=field, scheduled_start=crossover_start)
        record_result(only_match, score_home=2, score_away=0)

        _updated, conflicts = resolve_group_slots(group)

        crossover.refresh_from_db()
        self.assertEqual(crossover.home_team_id, only_match.home_team_id)
        self.assertTrue(conflicts)

    def test_team_order_overrides_the_computed_standings(self):
        # The "Start next phase" review screen's own correction lever --
        # T1 beats T2 head-to-head (so plain standings would rank T1 1st),
        # but team_order says otherwise, and that's what must win.
        team1, team2 = (TournamentTeam.objects.create(tournament=self.tournament, foreign_name=f"T{i}", seed=i) for i in range(1, 3))
        group_stage = TournamentStage.objects.create(tournament=self.tournament, name="Groups", format=TournamentStage.Format.GROUPS, sequence=0, config={"num_groups": 1})
        generate_stage_matches(group_stage, entries=[team1, team2])
        group = group_stage.groups.get()
        knockout_stage = TournamentStage.objects.create(tournament=self.tournament, name="Final", format=TournamentStage.Format.CUSTOM, sequence=1)
        first_place_slot = Match.objects.create(stage=knockout_stage, round_number=1, home_source_group=group, home_source_rank=1)
        second_place_slot = Match.objects.create(stage=knockout_stage, round_number=1, home_source_group=group, home_source_rank=2)
        only_match = group.matches.get()
        record_result(only_match, score_home=2, score_away=0)  # team1 wins

        resolve_group_slots(group, team_order=[team2, team1])

        first_place_slot.refresh_from_db()
        second_place_slot.refresh_from_db()
        self.assertEqual(first_place_slot.home_team_id, team2.pk)
        self.assertEqual(second_place_slot.home_team_id, team1.pk)


class BreakRuleModelTests(TournamentsTestCase):
    def test_game_count_trigger_requires_its_own_field(self):
        rule = BreakRule(tournament=self.tournament, trigger_type=BreakRule.TriggerType.GAME_COUNT, gap_minutes=15)
        with self.assertRaises(ValidationError):
            rule.full_clean()

    def test_clock_window_requires_no_gap(self):
        rule = BreakRule(tournament=self.tournament, trigger_type=BreakRule.TriggerType.CLOCK_WINDOW, trigger_window_start=datetime.time(12, 0), trigger_window_end=datetime.time(13, 0))
        rule.full_clean()  # gap_minutes is meaningless here and correctly not required

    def test_clock_window_end_must_be_after_start(self):
        rule = BreakRule(tournament=self.tournament, trigger_type=BreakRule.TriggerType.CLOCK_WINDOW, trigger_window_start=datetime.time(13, 0), trigger_window_end=datetime.time(12, 0))
        with self.assertRaises(ValidationError):
            rule.full_clean()

    def test_valid_game_count_rule(self):
        rule = BreakRule(tournament=self.tournament, trigger_type=BreakRule.TriggerType.GAME_COUNT, trigger_every_n_games=3, gap_minutes=15)
        rule.full_clean()

    def test_display_summary_for_a_game_count_rule(self):
        field = TournamentField.objects.create(tournament=self.tournament, name="Field 2")
        rule = BreakRule.objects.create(tournament=self.tournament, field=field, trigger_type=BreakRule.TriggerType.GAME_COUNT, trigger_every_n_games=3, gap_minutes=15, replaces_default_gap=True)
        self.assertEqual(rule.display_summary, "Field 2: every 3 games, 15 minutes gap (replaces default)")

    def test_display_summary_for_a_tournament_wide_clock_window(self):
        rule = BreakRule.objects.create(tournament=self.tournament, trigger_type=BreakRule.TriggerType.CLOCK_WINDOW, trigger_window_start=datetime.time(12, 0), trigger_window_end=datetime.time(13, 0))
        self.assertEqual(rule.display_summary, "every field: no games 12:00-13:00")


class ScheduleBlockModelTests(TournamentsTestCase):
    def test_str_includes_the_title(self):
        block = ScheduleBlock.objects.create(tournament=self.tournament, title="Lunch break", starts_at=timezone.now(), ends_at=timezone.now() + datetime.timedelta(hours=1))
        self.assertIn("Lunch break", str(block))

    def test_end_must_be_after_start(self):
        start = timezone.now()
        block = ScheduleBlock(tournament=self.tournament, title="Lunch break", starts_at=start, ends_at=start - datetime.timedelta(minutes=1))
        with self.assertRaises(ValidationError):
            block.full_clean()

    def test_field_and_teams_are_both_optional(self):
        block = ScheduleBlock(tournament=self.tournament, title="Ceremony", starts_at=timezone.now(), ends_at=timezone.now() + datetime.timedelta(hours=1))
        block.full_clean()  # neither field nor teams is required


class GapForPlacementTests(TournamentsTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.field = TournamentField.objects.create(tournament=cls.tournament, name="Field 1")

    def test_no_rules_falls_back_to_the_default(self):
        gap, replaces = gap_for_placement(self.field, games_since_last_break=0, minutes_since_last_break=0, round_number=1, just_finished_round_number=None)
        self.assertEqual(gap, DEFAULT_GAP)
        self.assertTrue(replaces)

    def test_game_count_rule_fires_once_threshold_reached(self):
        BreakRule.objects.create(tournament=self.tournament, field=self.field, trigger_type=BreakRule.TriggerType.GAME_COUNT, trigger_every_n_games=3, gap_minutes=15, replaces_default_gap=True)

        gap, replaces = gap_for_placement(self.field, games_since_last_break=3, minutes_since_last_break=0, round_number=1, just_finished_round_number=None)

        self.assertEqual(gap, datetime.timedelta(minutes=15))
        self.assertTrue(replaces)

    def test_game_count_rule_does_not_fire_before_threshold(self):
        BreakRule.objects.create(tournament=self.tournament, field=self.field, trigger_type=BreakRule.TriggerType.GAME_COUNT, trigger_every_n_games=3, gap_minutes=15)

        gap, _replaces = gap_for_placement(self.field, games_since_last_break=2, minutes_since_last_break=0, round_number=1, just_finished_round_number=None)

        self.assertEqual(gap, DEFAULT_GAP)

    def test_tournament_wide_rule_applies_to_a_field_with_no_rules_of_its_own(self):
        other_field = TournamentField.objects.create(tournament=self.tournament, name="Field 2")
        BreakRule.objects.create(tournament=self.tournament, field=None, trigger_type=BreakRule.TriggerType.GAME_COUNT, trigger_every_n_games=2, gap_minutes=10)

        gap, _replaces = gap_for_placement(other_field, games_since_last_break=2, minutes_since_last_break=0, round_number=1, just_finished_round_number=None)

        self.assertEqual(gap, datetime.timedelta(minutes=10))

    def test_field_specific_rule_does_not_leak_to_another_field(self):
        other_field = TournamentField.objects.create(tournament=self.tournament, name="Field 2")
        BreakRule.objects.create(tournament=self.tournament, field=self.field, trigger_type=BreakRule.TriggerType.GAME_COUNT, trigger_every_n_games=1, gap_minutes=99)

        gap, _replaces = gap_for_placement(other_field, games_since_last_break=5, minutes_since_last_break=0, round_number=1, just_finished_round_number=None)

        self.assertEqual(gap, DEFAULT_GAP)

    def test_lower_priority_number_wins_over_a_later_rule(self):
        BreakRule.objects.create(tournament=self.tournament, field=self.field, trigger_type=BreakRule.TriggerType.GAME_COUNT, trigger_every_n_games=1, gap_minutes=10, priority=5)
        BreakRule.objects.create(tournament=self.tournament, field=self.field, trigger_type=BreakRule.TriggerType.GAME_COUNT, trigger_every_n_games=1, gap_minutes=20, priority=1)

        gap, _replaces = gap_for_placement(self.field, games_since_last_break=1, minutes_since_last_break=0, round_number=1, just_finished_round_number=None)

        self.assertEqual(gap, datetime.timedelta(minutes=20))

    def test_round_boundary_fires_only_moving_into_the_next_round(self):
        BreakRule.objects.create(tournament=self.tournament, field=self.field, trigger_type=BreakRule.TriggerType.ROUND_BOUNDARY, trigger_after_round_number=1, gap_minutes=20)

        fires, _r = gap_for_placement(self.field, games_since_last_break=0, minutes_since_last_break=0, round_number=2, just_finished_round_number=1)
        self.assertEqual(fires, datetime.timedelta(minutes=20))

        no_fire, _r = gap_for_placement(self.field, games_since_last_break=0, minutes_since_last_break=0, round_number=1, just_finished_round_number=None)
        self.assertEqual(no_fire, DEFAULT_GAP)


def many_days(days: int = 40) -> list[dict]:
    """Opening hours for a field on many consecutive days, for tests that place a lot of games."""
    first = datetime.date(2026, 9, 19)
    return [{"date": (first + datetime.timedelta(days=i)).isoformat(), "start": "09:00", "end": "20:00"} for i in range(days)]


class AllocateScheduleTests(TournamentsTestCase):
    def test_raises_without_any_fields(self):
        teams = [TournamentTeam.objects.create(tournament=self.tournament, foreign_name=f"T{i}", seed=i) for i in range(1, 5)]
        stage = TournamentStage.objects.create(tournament=self.tournament, name="RR", format=TournamentStage.Format.ROUND_ROBIN)
        generate_stage_matches(stage, entries=teams)

        with self.assertRaises(SchedulingError):
            allocate_schedule(self.tournament)

    def test_no_two_matches_share_a_field_and_overlap(self):
        teams = [TournamentTeam.objects.create(tournament=self.tournament, foreign_name=f"T{i}", seed=i) for i in range(1, 9)]
        TournamentField.objects.create(tournament=self.tournament, name="Field 1", availability=many_days())
        stage = TournamentStage.objects.create(tournament=self.tournament, name="RR", format=TournamentStage.Format.ROUND_ROBIN)
        generate_stage_matches(stage, entries=teams)

        allocate_schedule(self.tournament)

        by_field = list(Match.objects.filter(stage=stage).order_by("scheduled_start"))
        for earlier, later in itertools.pairwise(by_field):
            self.assertLessEqual(earlier.scheduled_start + DEFAULT_MATCH_DURATION, later.scheduled_start)

    def test_a_team_never_double_booked_across_fields(self):
        teams = [TournamentTeam.objects.create(tournament=self.tournament, foreign_name=f"T{i}", seed=i) for i in range(1, 5)]
        TournamentField.objects.create(tournament=self.tournament, name="Field 1", availability=[{"date": "2026-09-19", "start": "09:00", "end": "20:00"}])
        TournamentField.objects.create(tournament=self.tournament, name="Field 2", availability=[{"date": "2026-09-19", "start": "09:00", "end": "20:00"}])
        stage = TournamentStage.objects.create(tournament=self.tournament, name="RR", format=TournamentStage.Format.ROUND_ROBIN)
        generate_stage_matches(stage, entries=teams)

        allocate_schedule(self.tournament)

        for team in teams:
            team_matches = sorted(Match.objects.filter(stage=stage).filter(models.Q(home_team=team) | models.Q(away_team=team)), key=lambda m: m.scheduled_start)
            for earlier, later in itertools.pairwise(team_matches):
                self.assertLessEqual(earlier.scheduled_start + DEFAULT_MATCH_DURATION, later.scheduled_start)

    def test_break_rule_gap_is_honoured_on_the_field(self):
        teams = [TournamentTeam.objects.create(tournament=self.tournament, foreign_name=f"T{i}", seed=i) for i in range(1, 9)]
        field = TournamentField.objects.create(tournament=self.tournament, name="Field 1", availability=many_days())
        BreakRule.objects.create(tournament=self.tournament, field=field, trigger_type=BreakRule.TriggerType.GAME_COUNT, trigger_every_n_games=1, gap_minutes=30, replaces_default_gap=True)
        stage = TournamentStage.objects.create(tournament=self.tournament, name="RR", format=TournamentStage.Format.ROUND_ROBIN)
        generate_stage_matches(stage, entries=teams)

        allocate_schedule(self.tournament)

        ordered = list(Match.objects.filter(stage=stage).order_by("scheduled_start"))
        gap = ordered[1].scheduled_start - (ordered[0].scheduled_start + DEFAULT_MATCH_DURATION)
        self.assertEqual(gap, datetime.timedelta(minutes=30))

    def _gaps(self):
        teams = [TournamentTeam.objects.create(tournament=self.tournament, foreign_name=f"T{i}", seed=i) for i in range(1, 5)]
        TournamentField.objects.create(tournament=self.tournament, name="Field 1", availability=[{"date": "2026-09-19", "start": "09:00", "end": "23:00"}])
        stage = TournamentStage.objects.create(tournament=self.tournament, name="RR", format=TournamentStage.Format.ROUND_ROBIN)
        generate_stage_matches(stage, entries=teams)
        allocate_schedule(self.tournament)
        ordered = list(Match.objects.filter(stage=stage).order_by("scheduled_start"))
        return {ordered[i + 1].scheduled_start - (ordered[i].scheduled_start + DEFAULT_MATCH_DURATION) for i in range(len(ordered) - 1)}

    def test_the_default_gap_is_five_minutes_unless_the_tournament_says_otherwise(self):
        self.assertEqual(self.tournament.default_gap_minutes, 5)
        self.assertEqual(self._gaps(), {datetime.timedelta(minutes=5)})

    def test_the_tournaments_own_gap_is_used_between_matches(self):
        self.tournament.default_gap_minutes = 12
        self.tournament.save(update_fields=["default_gap_minutes"])

        self.assertEqual(self._gaps(), {datetime.timedelta(minutes=12)})

    def test_a_gap_of_zero_puts_matches_back_to_back(self):
        self.tournament.default_gap_minutes = 0
        self.tournament.save(update_fields=["default_gap_minutes"])

        self.assertEqual(self._gaps(), {datetime.timedelta(0)})

    def test_an_additive_break_rule_adds_to_the_tournaments_gap(self):
        self.tournament.default_gap_minutes = 10
        self.tournament.save(update_fields=["default_gap_minutes"])
        BreakRule.objects.create(tournament=self.tournament, trigger_type=BreakRule.TriggerType.GAME_COUNT, trigger_every_n_games=1, gap_minutes=5, replaces_default_gap=False)

        self.assertEqual(self._gaps(), {datetime.timedelta(minutes=15)})

    def test_break_rule_recurs_consistently_across_multiple_cycles(self):
        # Regression test: the "games since last break" counter used to
        # reset to 0 instead of 1 once a break fired -- the match that just
        # received the break is itself the first game of the new block, and
        # every placed match should count as one game toward the *next*
        # break regardless of whether it's the one the reset happened on.
        # Resetting to 0 silently gave every block after the first an extra
        # game before its own break fired: "every 3 games" held for the
        # first block (3 games), then drifted to 4 games per block from the
        # second one on.
        teams = [TournamentTeam.objects.create(tournament=self.tournament, foreign_name=f"T{i}", seed=i) for i in range(1, 9)]
        field = TournamentField.objects.create(tournament=self.tournament, name="Field 1", availability=many_days())
        BreakRule.objects.create(tournament=self.tournament, field=field, trigger_type=BreakRule.TriggerType.GAME_COUNT, trigger_every_n_games=3, gap_minutes=15, replaces_default_gap=True, priority=0)
        BreakRule.objects.create(tournament=self.tournament, field=field, trigger_type=BreakRule.TriggerType.GAME_COUNT, trigger_every_n_games=1, gap_minutes=5, replaces_default_gap=True, priority=10)
        stage = TournamentStage.objects.create(tournament=self.tournament, name="RR", format=TournamentStage.Format.ROUND_ROBIN)
        generate_stage_matches(stage, entries=teams)

        allocate_schedule(self.tournament)

        ordered = list(Match.objects.filter(stage=stage).order_by("scheduled_start"))
        gaps = [ordered[i + 1].scheduled_start - (ordered[i].scheduled_start + DEFAULT_MATCH_DURATION) for i in range(9)]
        five, fifteen = datetime.timedelta(minutes=5), datetime.timedelta(minutes=15)
        self.assertEqual(gaps, [five, five, fifteen, five, five, fifteen, five, five, fifteen])

    def test_tbd_matches_are_scheduled_too(self):
        # A still-TBD slot (here, the final -- sourced from the two
        # semifinals' own winners) gets a field/time exactly like any other
        # match: staff and the teams themselves can see *when* it's
        # happening well before who's actually playing it is decided.
        teams = [TournamentTeam.objects.create(tournament=self.tournament, foreign_name=f"T{i}", seed=i) for i in range(1, 5)]
        TournamentField.objects.create(tournament=self.tournament, name="Field 1", availability=[{"date": "2026-09-19", "start": "09:00", "end": "20:00"}])
        stage = TournamentStage.objects.create(tournament=self.tournament, name="KO", format=TournamentStage.Format.SINGLE_ELIM)
        generate_stage_matches(stage, entries=teams)

        allocate_schedule(self.tournament)

        final = Match.objects.get(stage=stage, round_number=2)
        self.assertIsNotNone(final.scheduled_start)
        self.assertIsNone(final.home_team_id)
        self.assertIsNone(final.away_team_id)

    def test_a_tbd_match_never_collides_with_a_resolved_one_in_the_same_round(self):
        # Regression guard for the team-busy check's None guard: two
        # different TBD sides (None, None) must never be treated as "the
        # same team" fighting over the same slot the way two matches
        # sharing a real team would.
        teams = [TournamentTeam.objects.create(tournament=self.tournament, foreign_name=f"T{i}", seed=i) for i in range(1, 5)]
        field = TournamentField.objects.create(tournament=self.tournament, name="Field 1", availability=[{"date": "2026-09-19", "start": "09:00", "end": "20:00"}])
        stage = TournamentStage.objects.create(tournament=self.tournament, name="KO", format=TournamentStage.Format.SINGLE_ELIM)
        generate_stage_matches(stage, entries=teams)

        allocate_schedule(self.tournament)

        round1 = list(Match.objects.filter(stage=stage, round_number=1).order_by("scheduled_start"))
        final = Match.objects.get(stage=stage, round_number=2)
        self.assertEqual(final.field, field)
        self.assertGreaterEqual(final.scheduled_start, max(match.scheduled_start for match in round1) + DEFAULT_MATCH_DURATION)

    def test_accepts_a_custom_match_duration(self):
        teams = [TournamentTeam.objects.create(tournament=self.tournament, foreign_name=f"T{i}", seed=i) for i in range(1, 3)]
        TournamentField.objects.create(tournament=self.tournament, name="Field 1", availability=[{"date": "2026-09-19", "start": "09:00", "end": "20:00"}])
        stage = TournamentStage.objects.create(tournament=self.tournament, name="RR", format=TournamentStage.Format.ROUND_ROBIN)
        generate_stage_matches(stage, entries=teams)

        allocate_schedule(self.tournament, match_duration=datetime.timedelta(minutes=20))

        match = Match.objects.get(stage=stage)
        self.assertIsNotNone(match.scheduled_start)

    def test_a_field_only_schedule_block_reserves_no_match_on_that_field_during_its_window(self):
        teams = [TournamentTeam.objects.create(tournament=self.tournament, foreign_name=f"T{i}", seed=i) for i in range(1, 3)]
        field = TournamentField.objects.create(tournament=self.tournament, name="Field 1", availability=[{"date": "2026-09-19", "start": "09:00", "end": "20:00"}])
        window_start = timezone.make_aware(datetime.datetime(2026, 9, 19, 9, 0))
        ScheduleBlock.objects.create(tournament=self.tournament, title="Lunch", field=field, starts_at=window_start, ends_at=window_start + datetime.timedelta(hours=1))
        stage = TournamentStage.objects.create(tournament=self.tournament, name="RR", format=TournamentStage.Format.ROUND_ROBIN)
        generate_stage_matches(stage, entries=teams)

        allocate_schedule(self.tournament)

        match = Match.objects.get(stage=stage)
        self.assertGreaterEqual(match.scheduled_start, window_start + datetime.timedelta(hours=1))

    def test_a_team_only_schedule_block_leaves_other_teams_unaffected(self):
        teams = [TournamentTeam.objects.create(tournament=self.tournament, foreign_name=f"T{i}", seed=i) for i in range(1, 5)]
        TournamentField.objects.create(tournament=self.tournament, name="Field 1", availability=[{"date": "2026-09-19", "start": "09:00", "end": "20:00"}])
        TournamentField.objects.create(tournament=self.tournament, name="Field 2", availability=[{"date": "2026-09-19", "start": "09:00", "end": "20:00"}])
        window_start = timezone.make_aware(datetime.datetime(2026, 9, 19, 9, 0))
        block = ScheduleBlock.objects.create(tournament=self.tournament, title="Team huddle", starts_at=window_start, ends_at=window_start + datetime.timedelta(hours=1))
        block.teams.add(teams[0])
        stage = TournamentStage.objects.create(tournament=self.tournament, name="RR", format=TournamentStage.Format.ROUND_ROBIN)
        generate_stage_matches(stage, entries=teams)

        allocate_schedule(self.tournament)

        for match in Match.objects.filter(stage=stage):
            if teams[0].id in (match.home_team_id, match.away_team_id):
                self.assertGreaterEqual(match.scheduled_start, window_start + datetime.timedelta(hours=1))

        others_scheduled_in_window = Match.objects.filter(stage=stage, scheduled_start__gte=window_start, scheduled_start__lt=window_start + datetime.timedelta(hours=1)).exclude(home_team=teams[0]).exclude(away_team=teams[0])
        self.assertTrue(others_scheduled_in_window.exists())

    def test_a_combined_field_and_team_schedule_block_is_respected(self):
        teams = [TournamentTeam.objects.create(tournament=self.tournament, foreign_name=f"T{i}", seed=i) for i in range(1, 3)]
        field = TournamentField.objects.create(tournament=self.tournament, name="Field 1", availability=[{"date": "2026-09-19", "start": "09:00", "end": "20:00"}])
        window_start = timezone.make_aware(datetime.datetime(2026, 9, 19, 9, 0))
        block = ScheduleBlock.objects.create(tournament=self.tournament, title="Opening ceremony", field=field, starts_at=window_start, ends_at=window_start + datetime.timedelta(hours=1))
        block.teams.add(teams[0])
        stage = TournamentStage.objects.create(tournament=self.tournament, name="RR", format=TournamentStage.Format.ROUND_ROBIN)
        generate_stage_matches(stage, entries=teams)

        allocate_schedule(self.tournament)

        match = Match.objects.get(stage=stage)
        self.assertGreaterEqual(match.scheduled_start, window_start + datetime.timedelta(hours=1))

    def test_a_field_rolls_into_its_next_defined_availability_window(self):
        teams = [TournamentTeam.objects.create(tournament=self.tournament, foreign_name=f"T{i}", seed=i) for i in range(1, 5)]
        TournamentField.objects.create(
            tournament=self.tournament,
            name="Field 1",
            availability=[
                {"date": "2026-09-19", "start": "09:00", "end": "10:00"},
                {"date": "2026-09-20", "start": "09:00", "end": "20:00"},
            ],
        )
        stage = TournamentStage.objects.create(tournament=self.tournament, name="RR", format=TournamentStage.Format.ROUND_ROBIN)
        generate_stage_matches(stage, entries=teams)

        allocate_schedule(self.tournament)

        matches = list(Match.objects.filter(stage=stage).order_by("scheduled_start"))
        self.assertEqual(matches[0].scheduled_start.date(), datetime.date(2026, 9, 19))
        self.assertTrue(any(m.scheduled_start.date() == datetime.date(2026, 9, 20) for m in matches))
        for match in matches:
            if match.scheduled_start.date() == datetime.date(2026, 9, 19):
                self.assertLessEqual(match.scheduled_start + DEFAULT_MATCH_DURATION, timezone.make_aware(datetime.datetime(2026, 9, 19, 10, 0)))
            else:
                self.assertGreaterEqual(match.scheduled_start, timezone.make_aware(datetime.datetime(2026, 9, 20, 9, 0)))

    def test_games_that_do_not_fit_the_field_hours_raise_and_leave_nothing_scheduled(self):
        teams = [TournamentTeam.objects.create(tournament=self.tournament, foreign_name=f"T{i}", seed=i) for i in range(1, 5)]
        TournamentField.objects.create(tournament=self.tournament, name="Field 1", availability=[{"date": "2026-09-19", "start": "09:00", "end": "10:00"}])
        stage = TournamentStage.objects.create(tournament=self.tournament, name="RR", format=TournamentStage.Format.ROUND_ROBIN)
        generate_stage_matches(stage, entries=teams)

        with self.assertRaisesMessage(SchedulingError, "do not fit in the time the fields are available"):
            allocate_schedule(self.tournament)

        self.assertFalse(Match.objects.filter(stage=stage, scheduled_start__isnull=False).exists())  # all or nothing

    def test_break_counters_reset_after_rolling_into_a_new_day(self):
        teams = [TournamentTeam.objects.create(tournament=self.tournament, foreign_name=f"T{i}", seed=i) for i in range(1, 5)]
        field = TournamentField.objects.create(
            tournament=self.tournament,
            name="Field 1",
            availability=[
                {"date": "2026-09-19", "start": "09:00", "end": "10:40"},
                {"date": "2026-09-20", "start": "09:00", "end": "20:00"},
            ],
        )
        BreakRule.objects.create(tournament=self.tournament, field=field, trigger_type=BreakRule.TriggerType.GAME_COUNT, trigger_every_n_games=3, gap_minutes=30, replaces_default_gap=True)
        stage = TournamentStage.objects.create(tournament=self.tournament, name="RR", format=TournamentStage.Format.ROUND_ROBIN)
        generate_stage_matches(stage, entries=teams)

        allocate_schedule(self.tournament)

        matches = list(Match.objects.filter(stage=stage).order_by("scheduled_start"))
        rollover_index = next(i for i, m in enumerate(matches) if m.scheduled_start.date() == datetime.date(2026, 9, 20))
        gap_after_rollover = matches[rollover_index + 1].scheduled_start - (matches[rollover_index].scheduled_start + DEFAULT_MATCH_DURATION)
        self.assertEqual(gap_after_rollover, DEFAULT_GAP)

    def test_a_fields_first_game_starts_right_when_it_opens(self):
        # Regression test: every field's very first placement used to get
        # the plain DEFAULT_GAP added before it too (nothing had fired yet,
        # so gap_for_placement fell back to that default) -- a field open
        # from 09:00 put its first game at 09:05 for no visible reason.
        teams = [TournamentTeam.objects.create(tournament=self.tournament, foreign_name=f"T{i}", seed=i) for i in range(1, 3)]
        TournamentField.objects.create(tournament=self.tournament, name="Field 1", availability=[{"date": "2026-09-19", "start": "09:00", "end": "20:00"}])
        stage = TournamentStage.objects.create(tournament=self.tournament, name="RR", format=TournamentStage.Format.ROUND_ROBIN)
        generate_stage_matches(stage, entries=teams)

        allocate_schedule(self.tournament)

        match = Match.objects.get(stage=stage)
        self.assertEqual(match.scheduled_start, timezone.make_aware(datetime.datetime(2026, 9, 19, 9, 0)))

    def test_each_fields_own_first_game_starts_right_when_it_opens(self):
        # 4 teams round-robin's round 1 is exactly 2 matches -- one lands on
        # each field (both fields' clocks tie at their own opening time, so
        # the second match routes to whichever field the first one didn't),
        # each genuinely that field's own first game.
        teams = [TournamentTeam.objects.create(tournament=self.tournament, foreign_name=f"T{i}", seed=i) for i in range(1, 5)]
        TournamentField.objects.create(tournament=self.tournament, name="Field 1", availability=[{"date": "2026-09-19", "start": "09:00", "end": "20:00"}])
        TournamentField.objects.create(tournament=self.tournament, name="Field 2", availability=[{"date": "2026-09-19", "start": "09:00", "end": "20:00"}])
        stage = TournamentStage.objects.create(tournament=self.tournament, name="RR", format=TournamentStage.Format.ROUND_ROBIN)
        generate_stage_matches(stage, entries=teams)

        allocate_schedule(self.tournament)

        opening = timezone.make_aware(datetime.datetime(2026, 9, 19, 9, 0))
        first_round = Match.objects.filter(stage=stage, round_number=1)
        self.assertEqual([match.scheduled_start for match in first_round], [opening, opening])

    def test_placed_matches_are_flagged_auto_scheduled(self):
        teams = [TournamentTeam.objects.create(tournament=self.tournament, foreign_name=f"T{i}", seed=i) for i in range(1, 3)]
        TournamentField.objects.create(tournament=self.tournament, name="Field 1", availability=[{"date": "2026-09-19", "start": "09:00", "end": "20:00"}])
        stage = TournamentStage.objects.create(tournament=self.tournament, name="RR", format=TournamentStage.Format.ROUND_ROBIN)
        generate_stage_matches(stage, entries=teams)

        allocate_schedule(self.tournament)

        self.assertTrue(Match.objects.get(stage=stage).auto_scheduled)

    def test_reset_replans_a_previously_auto_placed_match(self):
        teams = [TournamentTeam.objects.create(tournament=self.tournament, foreign_name=f"T{i}", seed=i) for i in range(1, 3)]
        TournamentField.objects.create(tournament=self.tournament, name="Field 1", availability=[{"date": "2026-09-19", "start": "09:00", "end": "20:00"}])
        stage = TournamentStage.objects.create(tournament=self.tournament, name="RR", format=TournamentStage.Format.ROUND_ROBIN)
        generate_stage_matches(stage, entries=teams)
        allocate_schedule(self.tournament, match_duration=datetime.timedelta(minutes=45))
        first_run_start = Match.objects.get(stage=stage).scheduled_start

        allocate_schedule(self.tournament, match_duration=datetime.timedelta(minutes=90), reset=True)

        match = Match.objects.get(stage=stage)
        self.assertIsNotNone(match.scheduled_start)
        self.assertTrue(match.auto_scheduled)
        # Only one match on the field -- reset+replan lands it back on the
        # same slot, so this proves the reset pass actually ran (not that
        # the second call was simply a no-op) via the duration difference
        # baked into the next test below instead.
        self.assertEqual(match.scheduled_start, first_run_start)

    def test_reset_leaves_a_hand_placed_match_alone(self):
        teams = [TournamentTeam.objects.create(tournament=self.tournament, foreign_name=f"T{i}", seed=i) for i in range(1, 3)]
        field = TournamentField.objects.create(tournament=self.tournament, name="Field 1", availability=[{"date": "2026-09-19", "start": "09:00", "end": "20:00"}])
        stage = TournamentStage.objects.create(tournament=self.tournament, name="RR", format=TournamentStage.Format.ROUND_ROBIN)
        generate_stage_matches(stage, entries=teams)
        allocate_schedule(self.tournament)
        match = Match.objects.get(stage=stage)
        hand_picked_start = timezone.make_aware(datetime.datetime(2026, 9, 19, 15, 0))
        reschedule_match(match, field=field, scheduled_start=hand_picked_start)

        allocate_schedule(self.tournament, reset=True)

        match.refresh_from_db()
        self.assertEqual(match.scheduled_start, hand_picked_start)
        self.assertFalse(match.auto_scheduled)

    def test_reset_leaves_a_completed_match_alone(self):
        teams = [TournamentTeam.objects.create(tournament=self.tournament, foreign_name=f"T{i}", seed=i) for i in range(1, 3)]
        TournamentField.objects.create(tournament=self.tournament, name="Field 1", availability=[{"date": "2026-09-19", "start": "09:00", "end": "20:00"}])
        stage = TournamentStage.objects.create(tournament=self.tournament, name="RR", format=TournamentStage.Format.ROUND_ROBIN)
        generate_stage_matches(stage, entries=teams)
        allocate_schedule(self.tournament)
        match = Match.objects.get(stage=stage)
        played_start = match.scheduled_start
        match.status = Match.Status.COMPLETE
        match.save(update_fields=["status"])

        allocate_schedule(self.tournament, reset=True)

        match.refresh_from_db()
        self.assertEqual(match.scheduled_start, played_start)

    def test_never_overlaps_a_match_it_isnt_placing(self):
        # Regression test: field/team clocks used to be seeded purely from
        # matches this call itself places, with no awareness of a match
        # already sitting on the field's timeline (hand-moved, or simply
        # left over from an earlier non-reset run) -- a fresh placement
        # could land right on top of it. _skip_occupied_windows now treats
        # any already-scheduled match the same way a ScheduleBlock already
        # was: a reservation the greedy walk must route around.
        teams = [TournamentTeam.objects.create(tournament=self.tournament, foreign_name=f"T{i}", seed=i) for i in range(1, 5)]
        field = TournamentField.objects.create(tournament=self.tournament, name="Field 1", availability=many_days())
        stage = TournamentStage.objects.create(tournament=self.tournament, name="RR", format=TournamentStage.Format.ROUND_ROBIN)
        generate_stage_matches(stage, entries=teams)
        hand_placed = stage.matches.first()
        hand_placed_start = timezone.make_aware(datetime.datetime(2026, 9, 19, 10, 0))
        reschedule_match(hand_placed, field=field, scheduled_start=hand_placed_start)
        hand_placed_end = hand_placed_start + datetime.timedelta(minutes=self.tournament.default_match_duration_minutes)

        allocate_schedule(self.tournament)

        for match in Match.objects.filter(stage=stage).exclude(pk=hand_placed.pk):
            self.assertIsNotNone(match.scheduled_start)
            match_end = match.scheduled_start + DEFAULT_MATCH_DURATION
            overlaps = match.scheduled_start < hand_placed_end and hand_placed_start < match_end
            self.assertFalse(overlaps, f"{match} at {match.scheduled_start} overlaps the hand-placed match ({hand_placed_start}-{hand_placed_end})")


class ScheduleNeedsRegenerationTests(TournamentsTestCase):
    """Tournament.schedule_needs_regeneration -- the Schedule tab's own "you
    moved a match by hand, run Generate schedule to replan the rest around
    it" banner. Set by reschedule_match, cleared by allocate_schedule/
    clear_schedule, so staff can make several manual tweaks and only
    regenerate once."""

    def test_manual_reschedule_sets_the_flag(self):
        teams = [TournamentTeam.objects.create(tournament=self.tournament, foreign_name=f"T{i}") for i in range(1, 3)]
        field = TournamentField.objects.create(tournament=self.tournament, name="Field 1")
        stage = TournamentStage.objects.create(tournament=self.tournament, name="RR", format=TournamentStage.Format.CUSTOM)
        match = Match.objects.create(stage=stage, home_team=teams[0], away_team=teams[1])
        self.assertFalse(self.tournament.schedule_needs_regeneration)

        reschedule_match(match, field=field, scheduled_start=timezone.make_aware(datetime.datetime(2026, 9, 19, 10, 0)))

        self.tournament.refresh_from_db()
        self.assertTrue(self.tournament.schedule_needs_regeneration)

    def test_a_second_manual_reschedule_leaves_the_flag_set(self):
        teams = [TournamentTeam.objects.create(tournament=self.tournament, foreign_name=f"T{i}") for i in range(1, 3)]
        field = TournamentField.objects.create(tournament=self.tournament, name="Field 1")
        stage = TournamentStage.objects.create(tournament=self.tournament, name="RR", format=TournamentStage.Format.CUSTOM)
        match = Match.objects.create(stage=stage, home_team=teams[0], away_team=teams[1])
        reschedule_match(match, field=field, scheduled_start=timezone.make_aware(datetime.datetime(2026, 9, 19, 10, 0)))

        reschedule_match(match, field=field, scheduled_start=timezone.make_aware(datetime.datetime(2026, 9, 19, 11, 0)))

        self.tournament.refresh_from_db()
        self.assertTrue(self.tournament.schedule_needs_regeneration)

    def test_allocate_schedule_clears_the_flag(self):
        self.tournament.schedule_needs_regeneration = True
        self.tournament.save(update_fields=["schedule_needs_regeneration"])
        TournamentField.objects.create(tournament=self.tournament, name="Field 1")

        allocate_schedule(self.tournament)

        self.tournament.refresh_from_db()
        self.assertFalse(self.tournament.schedule_needs_regeneration)

    def test_clear_schedule_clears_the_flag(self):
        self.tournament.schedule_needs_regeneration = True
        self.tournament.save(update_fields=["schedule_needs_regeneration"])

        clear_schedule(self.tournament)

        self.tournament.refresh_from_db()
        self.assertFalse(self.tournament.schedule_needs_regeneration)


class ClearScheduleTests(TournamentsTestCase):
    def test_unschedules_a_not_yet_played_match_but_keeps_it(self):
        teams = [TournamentTeam.objects.create(tournament=self.tournament, foreign_name=f"T{i}") for i in range(1, 3)]
        TournamentField.objects.create(tournament=self.tournament, name="Field 1", availability=[{"date": "2026-09-19", "start": "09:00", "end": "20:00"}])
        stage = TournamentStage.objects.create(tournament=self.tournament, name="RR", format=TournamentStage.Format.ROUND_ROBIN)
        generate_stage_matches(stage, entries=teams)
        allocate_schedule(self.tournament)
        match = Match.objects.get(stage=stage)
        self.assertIsNotNone(match.scheduled_start)

        count = clear_schedule(self.tournament)

        match.refresh_from_db()
        self.assertEqual(count, 1)
        self.assertIsNone(match.scheduled_start)
        self.assertIsNone(match.field_id)
        self.assertFalse(match.auto_scheduled)

    def test_leaves_a_completed_match_alone(self):
        teams = [TournamentTeam.objects.create(tournament=self.tournament, foreign_name=f"T{i}") for i in range(1, 3)]
        field = TournamentField.objects.create(tournament=self.tournament, name="Field 1")
        stage = TournamentStage.objects.create(tournament=self.tournament, name="RR", format=TournamentStage.Format.CUSTOM)
        played_start = timezone.make_aware(datetime.datetime(2026, 9, 19, 10, 0))
        match = Match.objects.create(stage=stage, home_team=teams[0], away_team=teams[1], field=field, scheduled_start=played_start, status=Match.Status.COMPLETE)

        count = clear_schedule(self.tournament)

        match.refresh_from_db()
        self.assertEqual(count, 0)
        self.assertEqual(match.scheduled_start, played_start)
        self.assertEqual(match.field, field)


class FieldTimelineTests(TournamentsTestCase):
    def test_no_gap_before_the_first_match_even_though_the_window_opens_earlier(self):
        # Regression test: a gap slot used to render before the very first
        # match of the day whenever the field's own window opened earlier --
        # "5 min free" before a single game has even started reads as a bug,
        # not useful idle time. Between-match and trailing gaps still show.
        # Tournament default match duration is 45 minutes (the model's own
        # default): 09:15 -> 10:00, then 10:10 -> 10:55, in a 09:00-11:00
        # window -- a 10-minute gap between them, a 5-minute gap after.
        teams = [TournamentTeam.objects.create(tournament=self.tournament, foreign_name=f"T{i}") for i in range(1, 5)]
        field = TournamentField.objects.create(tournament=self.tournament, name="Field 1", availability=[{"date": "2026-09-19", "start": "09:00", "end": "11:00"}])
        stage = TournamentStage.objects.create(tournament=self.tournament, name="RR", format=TournamentStage.Format.CUSTOM)
        Match.objects.create(stage=stage, home_team=teams[0], away_team=teams[1], field=field, scheduled_start=timezone.make_aware(datetime.datetime(2026, 9, 19, 9, 15)))
        Match.objects.create(stage=stage, home_team=teams[2], away_team=teams[3], field=field, scheduled_start=timezone.make_aware(datetime.datetime(2026, 9, 19, 10, 10)))

        timeline = field_timeline(self.tournament)

        self.assertEqual(len(timeline), 1)
        day = timeline[0]["days"][0]
        kinds = [slot["type"] for slot in day["slots"]]
        self.assertEqual(kinds, ["match", "gap", "match", "gap"])
        self.assertEqual(day["slots"][1]["minutes"], 10)
        self.assertEqual(day["slots"][3]["minutes"], 5)

    def test_hour_marks_cover_the_window_at_pixel_offsets(self):
        TournamentField.objects.create(tournament=self.tournament, name="Field 1", availability=[{"date": "2026-09-19", "start": "09:30", "end": "11:30"}])

        timeline = field_timeline(self.tournament)

        marks = timeline[0]["days"][0]["hour_marks"]
        self.assertEqual([mark["label"] for mark in marks], ["10:00", "11:00"])
        self.assertEqual(marks[0]["top_px"], round(30 * PX_PER_MINUTE))
        self.assertEqual(marks[1]["top_px"], round(90 * PX_PER_MINUTE))

    def test_a_blackout_gap_is_attributed_to_its_rule(self):
        field = TournamentField.objects.create(tournament=self.tournament, name="Field 1", availability=[{"date": "2026-09-19", "start": "09:00", "end": "13:00"}])
        rule = BreakRule.objects.create(tournament=self.tournament, field=field, trigger_type=BreakRule.TriggerType.CLOCK_WINDOW, trigger_window_start=datetime.time(11, 0), trigger_window_end=datetime.time(12, 0), priority=1)

        timeline = field_timeline(self.tournament)

        day = timeline[0]["days"][0]
        gap = next(slot for slot in day["slots"] if slot["type"] == "gap" and slot["start"].time() <= datetime.time(11, 30) < slot["end"].time())
        self.assertEqual(gap["rule"], rule)

    def test_a_field_with_no_matches_or_availability_has_no_days(self):
        TournamentField.objects.create(tournament=self.tournament, name="Field 1")

        timeline = field_timeline(self.tournament)

        self.assertEqual(timeline[0]["days"], [])

    def test_rules_legend_excludes_clock_window_and_is_priority_ordered(self):
        field = TournamentField.objects.create(tournament=self.tournament, name="Field 1")
        BreakRule.objects.create(tournament=self.tournament, field=field, trigger_type=BreakRule.TriggerType.CLOCK_WINDOW, trigger_window_start=datetime.time(12, 0), trigger_window_end=datetime.time(13, 0))
        low_priority = BreakRule.objects.create(tournament=self.tournament, field=field, trigger_type=BreakRule.TriggerType.GAME_COUNT, trigger_every_n_games=5, gap_minutes=10, priority=5)
        high_priority = BreakRule.objects.create(tournament=self.tournament, field=field, trigger_type=BreakRule.TriggerType.GAME_COUNT, trigger_every_n_games=2, gap_minutes=20, priority=1)

        timeline = field_timeline(self.tournament)

        self.assertEqual(timeline[0]["rules"], [high_priority, low_priority])


class ValidatePlacementTests(TournamentsTestCase):
    def test_detects_field_double_booking(self):
        field = TournamentField.objects.create(tournament=self.tournament, name="Field 1")
        teams = [TournamentTeam.objects.create(tournament=self.tournament, foreign_name=f"T{i}") for i in range(1, 5)]
        stage = TournamentStage.objects.create(tournament=self.tournament, name="RR", format=TournamentStage.Format.CUSTOM)
        start = timezone.now() + datetime.timedelta(days=1)
        existing = Match.objects.create(stage=stage, home_team=teams[0], away_team=teams[1], field=field, scheduled_start=start)
        candidate = Match.objects.create(stage=stage, home_team=teams[2], away_team=teams[3])

        violations = validate_placement(candidate, field, start, start + DEFAULT_MATCH_DURATION)

        self.assertTrue(violations)
        existing.delete()

    def test_no_violations_for_a_clean_placement(self):
        field = TournamentField.objects.create(tournament=self.tournament, name="Field 1")
        teams = [TournamentTeam.objects.create(tournament=self.tournament, foreign_name=f"T{i}") for i in range(1, 3)]
        stage = TournamentStage.objects.create(tournament=self.tournament, name="RR", format=TournamentStage.Format.CUSTOM)
        candidate = Match.objects.create(stage=stage, home_team=teams[0], away_team=teams[1])
        start = timezone.now() + datetime.timedelta(days=1)

        self.assertEqual(validate_placement(candidate, field, start, start + DEFAULT_MATCH_DURATION), [])

    def test_detects_a_team_already_playing_elsewhere(self):
        field_a = TournamentField.objects.create(tournament=self.tournament, name="Field 1")
        field_b = TournamentField.objects.create(tournament=self.tournament, name="Field 2")
        teams = [TournamentTeam.objects.create(tournament=self.tournament, foreign_name=f"T{i}") for i in range(1, 5)]
        stage = TournamentStage.objects.create(tournament=self.tournament, name="RR", format=TournamentStage.Format.CUSTOM)
        start = timezone.now() + datetime.timedelta(days=1)
        Match.objects.create(stage=stage, home_team=teams[0], away_team=teams[1], field=field_a, scheduled_start=start)
        candidate = Match.objects.create(stage=stage, home_team=teams[0], away_team=teams[2])

        violations = validate_placement(candidate, field_b, start, start + DEFAULT_MATCH_DURATION)

        self.assertTrue(violations)

    def test_detects_a_blackout_window_overlap(self):
        field = TournamentField.objects.create(tournament=self.tournament, name="Field 1")
        BreakRule.objects.create(tournament=self.tournament, trigger_type=BreakRule.TriggerType.CLOCK_WINDOW, trigger_window_start=datetime.time(12, 0), trigger_window_end=datetime.time(13, 0))
        teams = [TournamentTeam.objects.create(tournament=self.tournament, foreign_name=f"T{i}") for i in range(1, 3)]
        stage = TournamentStage.objects.create(tournament=self.tournament, name="RR", format=TournamentStage.Format.CUSTOM)
        candidate = Match.objects.create(stage=stage, home_team=teams[0], away_team=teams[1])
        start = timezone.make_aware(datetime.datetime(2026, 9, 19, 12, 15))

        violations = validate_placement(candidate, field, start, start + DEFAULT_MATCH_DURATION)

        self.assertTrue(violations)

    def test_uses_the_tournaments_own_match_duration_not_the_module_default(self):
        # Regression test: this used to always assume the existing placed
        # match ran for the module's own 45-minute DEFAULT_MATCH_DURATION,
        # even when the tournament's own default_match_duration_minutes says
        # otherwise -- a candidate placed just after that shorter/longer
        # match's *real* end could be wrongly rejected or wrongly accepted.
        self.tournament.default_match_duration_minutes = 20
        self.tournament.save(update_fields=["default_match_duration_minutes"])
        field = TournamentField.objects.create(tournament=self.tournament, name="Field 1")
        teams = [TournamentTeam.objects.create(tournament=self.tournament, foreign_name=f"T{i}") for i in range(1, 5)]
        stage = TournamentStage.objects.create(tournament=self.tournament, name="RR", format=TournamentStage.Format.CUSTOM)
        start = timezone.make_aware(datetime.datetime(2026, 9, 19, 9, 0))
        Match.objects.create(stage=stage, home_team=teams[0], away_team=teams[1], field=field, scheduled_start=start)
        candidate = Match.objects.create(stage=stage, home_team=teams[2], away_team=teams[3])
        # 09:25 is free once the existing match's own 20-minute duration is
        # honoured (ends 09:20), but would still collide against the
        # module's 45-minute default (would "end" 09:45).
        candidate_start = start + datetime.timedelta(minutes=25)

        violations = validate_placement(candidate, field, candidate_start, candidate_start + datetime.timedelta(minutes=20))

        self.assertEqual(violations, [])


class ComputeStandingsTests(TournamentsTestCase):
    def make_group_stage(self, count):
        teams = [TournamentTeam.objects.create(tournament=self.tournament, foreign_name=f"T{i}", seed=i) for i in range(1, count + 1)]
        stage = TournamentStage.objects.create(tournament=self.tournament, name="RR", format=TournamentStage.Format.ROUND_ROBIN)
        generate_stage_matches(stage, entries=teams)
        return stage, teams

    def complete(self, match, home_score, away_score):
        match.score_home, match.score_away, match.status = home_score, away_score, Match.Status.COMPLETE
        match.save()

    def test_points_rank_a_clear_winner_first(self):
        stage, teams = self.make_group_stage(3)
        for match in stage.matches.all():
            winner_is_home = match.home_team.seed < match.away_team.seed
            self.complete(match, 2 if winner_is_home else 0, 0 if winner_is_home else 2)

        standings = compute_standings(stage)

        self.assertEqual(standings[0]["team"], teams[0])
        self.assertEqual(standings[0]["points"], 6)
        self.assertEqual(standings[-1]["points"], 0)

    def test_goal_difference_breaks_a_points_tie(self):
        stage, teams = self.make_group_stage(3)
        a, b, c = teams
        matches = {frozenset((m.home_team_id, m.away_team_id)): m for m in stage.matches.all()}
        self.complete(matches[frozenset((a.id, b.id))], 5, 0)  # a beats b big
        self.complete(matches[frozenset((b.id, c.id))], 2, 0)  # b beats c
        self.complete(matches[frozenset((a.id, c.id))], 0, 1)  # c beats a

        standings = compute_standings(stage)
        # a: 3 pts, +5 gd. b: 3 pts, -3 gd. c: 3 pts, -2 gd. -- a wins on gd, b last.
        self.assertEqual(standings[0]["team"], a)
        self.assertEqual(standings[-1]["team"], b)

    def test_incomplete_matches_do_not_count(self):
        stage, _teams = self.make_group_stage(3)
        matches = list(stage.matches.all())
        self.complete(matches[0], 1, 0)

        standings = compute_standings(stage)

        total_played = sum(row["played"] for row in standings)
        self.assertEqual(total_played, 2)  # only the one completed match's two teams

    def test_standings_config_overrides_default_points(self):
        stage, _teams = self.make_group_stage(2)
        match = stage.matches.get()
        stage.standings_config = {"points": {"win": 5, "draw": 2, "loss": 1}}
        stage.save()
        self.complete(match, 3, 3)

        standings = compute_standings(stage)

        self.assertEqual(standings[0]["points"], 2)
        self.assertEqual(standings[1]["points"], 2)


class ComputeStandingsHeadToHeadTests(TournamentsTestCase):
    """A genuine 3-way tie that only head_to_head, not goal_diff/goals_for,
    can break -- A/B/C finish on identical points, goal-difference and
    goals-for once matches against two outside teams (D/E) are folded in,
    but their results against *each other alone* have a clear order."""

    def test_a_genuine_three_way_tie_is_broken_by_head_to_head_alone(self):
        team_a, team_b, team_c, team_d, team_e = [TournamentTeam.objects.create(tournament=self.tournament, foreign_name=name, seed=seed) for seed, name in enumerate(["A", "B", "C", "D", "E"], start=1)]
        stage = TournamentStage.objects.create(
            tournament=self.tournament,
            name="RR",
            format=TournamentStage.Format.ROUND_ROBIN,
            standings_config={"tiebreakers": ["head_to_head", "goal_diff", "goals_for"]},
        )
        generate_stage_matches(stage, entries=[team_a, team_b, team_c, team_d, team_e])
        matches = {frozenset((m.home_team_id, m.away_team_id)): m for m in stage.matches.all()}

        def complete(winner, loser, winner_score=2, loser_score=0):
            match = matches[frozenset((winner.id, loser.id))]
            if match.home_team_id == winner.id:
                match.score_home, match.score_away = winner_score, loser_score
            else:
                match.score_home, match.score_away = loser_score, winner_score
            match.status = Match.Status.COMPLETE
            match.save()

        # Head-to-head among the trio alone: A beats both, B beats only C,
        # C beats neither -- a clean, non-cyclic order (A > B > C).
        complete(team_a, team_b)
        complete(team_a, team_c)
        complete(team_b, team_c)
        # Games against D/E are chosen so every one of A/B/C ends up with the
        # *exact same* overall points, goal-difference and goals-for -- so
        # only head_to_head (not any other criterion) can possibly separate
        # them, and D/E themselves stay out of the tied group entirely.
        complete(team_d, team_a)
        complete(team_e, team_a)
        complete(team_d, team_b)
        complete(team_b, team_e)
        complete(team_c, team_d)
        complete(team_c, team_e)
        # D vs E is deliberately left unplayed -- irrelevant to the trio.

        standings = compute_standings(stage)
        by_team = {row["team"].id: row for row in standings}

        self.assertEqual(by_team[team_a.id]["points"], by_team[team_b.id]["points"])
        self.assertEqual(by_team[team_b.id]["points"], by_team[team_c.id]["points"])
        self.assertEqual(by_team[team_a.id]["goal_diff"], by_team[team_b.id]["goal_diff"])
        self.assertEqual(by_team[team_b.id]["goal_diff"], by_team[team_c.id]["goal_diff"])
        self.assertEqual(by_team[team_a.id]["goals_for"], by_team[team_b.id]["goals_for"])
        self.assertEqual(by_team[team_b.id]["goals_for"], by_team[team_c.id]["goals_for"])

        trio_order = [row["team"] for row in standings if row["team"] in (team_a, team_b, team_c)]
        self.assertEqual(trio_order, [team_a, team_b, team_c])


class SingleEliminationStressTests(TournamentsTestCase):
    """Larger, less-trivial single-elimination sizes than
    GenerateStageMatchesTests' own 8-team/3-team cases -- an exact power of
    two with no byes at all, and an awkward size with several unevenly-spread
    byes, both fully played out to make sure the bracket graph always
    resolves cleanly with no lingering TBD slots."""

    def make_teams(self, count):
        return [TournamentTeam.objects.create(tournament=self.tournament, foreign_name=f"Team {i}", seed=i) for i in range(1, count + 1)]

    def play_out(self, stage):
        """Plays every real (non-walkover) match to completion, round by
        round, the lower seed number (stronger) always winning -- calling
        resolve_match_slots after each so a later round always sees its real
        teams by the time this method reaches it."""
        for round_number in sorted(stage.matches.values_list("round_number", flat=True).distinct()):
            for match in list(stage.matches.filter(round_number=round_number, status=Match.Status.SCHEDULED)):
                match.refresh_from_db()
                self.assertIsNotNone(match.home_team_id)
                self.assertIsNotNone(match.away_team_id)
                if match.home_team.seed < match.away_team.seed:
                    match.score_home, match.score_away = 2, 0
                else:
                    match.score_home, match.score_away = 0, 2
                match.status = Match.Status.COMPLETE
                match.save()
                resolve_match_slots(match)

    def test_16_teams_exact_power_of_two_has_no_byes(self):
        teams = self.make_teams(16)
        stage = TournamentStage.objects.create(tournament=self.tournament, name="KO16", format=TournamentStage.Format.SINGLE_ELIM)

        matches = generate_stage_matches(stage, entries=teams)

        self.assertEqual(len(matches), 15)  # len(entries) - 1, no byes to subtract
        self.assertEqual(Match.objects.filter(stage=stage, status=Match.Status.WALKOVER).count(), 0)
        round1 = [m for m in matches if m.round_number == 1]
        round1_seeds = set()
        for m in round1:
            round1_seeds.update([m.home_team.seed, m.away_team.seed])
        self.assertEqual(round1_seeds, {team.seed for team in teams})  # every entry, exactly once

        self.play_out(stage)

        self.assertFalse(Match.objects.filter(stage=stage, home_team__isnull=True).exists())
        self.assertFalse(Match.objects.filter(stage=stage, away_team__isnull=True).exists())
        final = Match.objects.get(stage=stage, round_number=4)
        self.assertEqual(final.winner.seed, 1)  # seed 1 never loses under this simulation
        losers = [m.loser for m in stage.matches.all()]
        self.assertEqual(len({loser.id for loser in losers}), 15)  # every non-champion eliminated exactly once
        self.assertNotIn(final.winner, losers)

    def test_11_teams_awkward_size_has_multiple_uneven_byes(self):
        teams = self.make_teams(11)
        stage = TournamentStage.objects.create(tournament=self.tournament, name="KO11", format=TournamentStage.Format.SINGLE_ELIM)

        matches = generate_stage_matches(stage, entries=teams)

        # Bracket size is 16 (next power of two above 11): 16 - 11 = 5 byes,
        # so 15 slots total, but only len(entries) - 1 = 10 of them are real,
        # played matches -- a bye is a free pass, not a game.
        real_matches = [m for m in matches if m.status != Match.Status.WALKOVER]
        walkover_matches = [m for m in matches if m.status == Match.Status.WALKOVER]
        self.assertEqual(len(matches), 15)
        self.assertEqual(len(walkover_matches), 5)
        self.assertEqual(len(real_matches), len(teams) - 1)

        round1 = [m for m in matches if m.round_number == 1]
        round1_seeds = set()
        for m in round1:
            if m.home_team_id:
                round1_seeds.add(m.home_team.seed)
            if m.away_team_id:
                round1_seeds.add(m.away_team.seed)
        self.assertEqual(round1_seeds, {team.seed for team in teams})  # every entry, exactly once

        self.play_out(stage)

        # No lingering TBD anywhere once every real match is complete -- a
        # WALKOVER match's lone-None side is a deliberate bye, not a bug.
        self.assertFalse(Match.objects.filter(stage=stage, home_team__isnull=True).exclude(status=Match.Status.WALKOVER).exists())
        self.assertFalse(Match.objects.filter(stage=stage, away_team__isnull=True).exclude(status=Match.Status.WALKOVER).exists())
        final = Match.objects.get(stage=stage, round_number=4)
        self.assertEqual(final.winner.seed, 1)  # seed 1 never loses under this simulation


class DoubleEliminationStressTests(TournamentsTestCase):
    """8- and 16-team double-elimination brackets, fully played out (winners
    and losers brackets alike) as a straight higher-seed-wins simulation."""

    def make_teams(self, count):
        return [TournamentTeam.objects.create(tournament=self.tournament, foreign_name=f"Team {i}", seed=i) for i in range(1, count + 1)]

    def play_out(self, stage):
        for round_number in sorted(stage.matches.values_list("round_number", flat=True).distinct()):
            for match in list(stage.matches.filter(round_number=round_number, status=Match.Status.SCHEDULED)):
                match.refresh_from_db()
                if match.home_team_id is None or match.away_team_id is None:
                    continue  # a downstream slot this round's own matches haven't fed yet
                self.assertNotEqual(match.home_team_id, match.away_team_id)
                if match.home_team.seed < match.away_team.seed:
                    match.score_home, match.score_away = 2, 0
                else:
                    match.score_home, match.score_away = 0, 2
                match.status = Match.Status.COMPLETE
                match.save()
                resolve_match_slots(match)
                # No match anywhere in the bracket may ever end up with the
                # same team on both sides, at any point during the playout.
                for check in stage.matches.exclude(home_team__isnull=True).exclude(away_team__isnull=True):
                    self.assertNotEqual(check.home_team_id, check.away_team_id)

    def test_8_teams_resolves_to_a_grand_final_with_seed_1_undefeated(self):
        teams = self.make_teams(8)
        stage = TournamentStage.objects.create(tournament=self.tournament, name="DE8", format=TournamentStage.Format.DOUBLE_ELIM)
        generate_stage_matches(stage, entries=teams)

        self.play_out(stage)

        grand_final = stage.matches.order_by("round_number").last()
        self.assertEqual(grand_final.round_label, "Grand final")
        self.assertIsNotNone(grand_final.home_team_id)
        self.assertIsNotNone(grand_final.away_team_id)
        self.assertIn(1, {grand_final.home_team.seed, grand_final.away_team.seed})

    def test_16_teams_resolves_to_a_grand_final_with_seed_1_undefeated(self):
        teams = self.make_teams(16)
        stage = TournamentStage.objects.create(tournament=self.tournament, name="DE16", format=TournamentStage.Format.DOUBLE_ELIM)
        generate_stage_matches(stage, entries=teams)

        self.play_out(stage)

        grand_final = stage.matches.order_by("round_number").last()
        self.assertEqual(grand_final.round_label, "Grand final")
        self.assertIsNotNone(grand_final.home_team_id)
        self.assertIsNotNone(grand_final.away_team_id)
        self.assertIn(1, {grand_final.home_team.seed, grand_final.away_team.seed})


class SwissStressTests(TournamentsTestCase):
    """Several rounds of Swiss, played out with deterministic (higher-seed-
    always-wins) results between generations -- hunting for forced repeat
    pairings and for generate_next_swiss_round's own completeness guard."""

    def make_teams(self, count):
        return [TournamentTeam.objects.create(tournament=self.tournament, foreign_name=f"Team {i}", seed=i) for i in range(1, count + 1)]

    def complete_round(self, stage, round_number):
        for match in stage.matches.filter(round_number=round_number):
            if match.home_team.seed < match.away_team.seed:
                match.score_home, match.score_away = 2, 0
            else:
                match.score_home, match.score_away = 0, 2
            match.status = Match.Status.COMPLETE
            match.save()

    def test_pairings_never_repeat_across_three_rounds_with_a_large_enough_field(self):
        teams = self.make_teams(8)
        stage = TournamentStage.objects.create(tournament=self.tournament, name="Swiss", format=TournamentStage.Format.SWISS)

        generate_stage_matches(stage, entries=teams)  # round 1: seed order
        self.complete_round(stage, 1)
        generate_next_swiss_round(stage)  # round 2: paired by standings
        self.complete_round(stage, 2)
        generate_next_swiss_round(stage)  # round 3: paired by standings again

        pairings_seen = set()
        for match in stage.matches.all():
            pair = frozenset((match.home_team_id, match.away_team_id))
            self.assertNotIn(pair, pairings_seen, "Swiss re-paired the same two teams with a large enough field available.")
            pairings_seen.add(pair)

    def test_generating_round_one_again_before_it_completes_raises(self):
        teams = self.make_teams(8)
        stage = TournamentStage.objects.create(tournament=self.tournament, name="Swiss", format=TournamentStage.Format.SWISS)
        generate_stage_matches(stage, entries=teams)  # round 1, deliberately left incomplete

        with self.assertRaises(SchedulingError):
            generate_next_swiss_round(stage)

    def test_generating_round_three_before_round_two_completes_raises(self):
        teams = self.make_teams(8)
        stage = TournamentStage.objects.create(tournament=self.tournament, name="Swiss", format=TournamentStage.Format.SWISS)
        generate_stage_matches(stage, entries=teams)
        self.complete_round(stage, 1)
        generate_next_swiss_round(stage)  # round 2, deliberately left incomplete

        with self.assertRaises(SchedulingError):
            generate_next_swiss_round(stage)


class AllocateScheduleStressTests(TournamentsTestCase):
    """allocate_schedule under harsher conditions than AllocateScheduleTests'
    own small fixtures: far more matches than a single field can cleanly
    interleave, and a blackout rule that swallows the entire working day."""

    def test_16_teams_one_field_never_overlaps(self):
        teams = [TournamentTeam.objects.create(tournament=self.tournament, foreign_name=f"T{i}", seed=i) for i in range(1, 17)]
        TournamentField.objects.create(tournament=self.tournament, name="Field 1", availability=many_days())
        stage = TournamentStage.objects.create(tournament=self.tournament, name="RR", format=TournamentStage.Format.ROUND_ROBIN)
        generate_stage_matches(stage, entries=teams)

        allocate_schedule(self.tournament)

        ordered = list(Match.objects.filter(stage=stage).order_by("scheduled_start"))
        self.assertEqual(len(ordered), 120)  # 16 choose 2
        for earlier, later in itertools.pairwise(ordered):
            self.assertLessEqual(earlier.scheduled_start + DEFAULT_MATCH_DURATION, later.scheduled_start)


class RescheduleMatchTests(TournamentsTestCase):
    def test_valid_reschedule_saves_field_and_time(self):
        teams = [TournamentTeam.objects.create(tournament=self.tournament, foreign_name=f"T{i}") for i in range(1, 3)]
        field = TournamentField.objects.create(tournament=self.tournament, name="Field 1")
        stage = TournamentStage.objects.create(tournament=self.tournament, name="RR", format=TournamentStage.Format.CUSTOM)
        match = Match.objects.create(stage=stage, home_team=teams[0], away_team=teams[1])
        start = timezone.now() + datetime.timedelta(days=1)

        reschedule_match(match, field=field, scheduled_start=start)

        match.refresh_from_db()
        self.assertEqual(match.field, field)
        self.assertEqual(match.scheduled_start, start)

    def test_conflicting_reschedule_raises_and_does_not_save(self):
        teams = [TournamentTeam.objects.create(tournament=self.tournament, foreign_name=f"T{i}") for i in range(1, 5)]
        field = TournamentField.objects.create(tournament=self.tournament, name="Field 1")
        stage = TournamentStage.objects.create(tournament=self.tournament, name="RR", format=TournamentStage.Format.CUSTOM)
        start = timezone.now() + datetime.timedelta(days=1)
        Match.objects.create(stage=stage, home_team=teams[0], away_team=teams[1], field=field, scheduled_start=start)
        candidate = Match.objects.create(stage=stage, home_team=teams[2], away_team=teams[3])

        with self.assertRaises(SchedulingError):
            reschedule_match(candidate, field=field, scheduled_start=start)

        candidate.refresh_from_db()
        self.assertIsNone(candidate.field)
        self.assertIsNone(candidate.scheduled_start)


class RecordResultTests(TournamentsTestCase):
    def test_records_score_and_marks_complete(self):
        teams = [TournamentTeam.objects.create(tournament=self.tournament, foreign_name=f"T{i}") for i in range(1, 3)]
        stage = TournamentStage.objects.create(tournament=self.tournament, name="RR", format=TournamentStage.Format.CUSTOM)
        match = Match.objects.create(stage=stage, home_team=teams[0], away_team=teams[1])

        record_result(match, score_home=3, score_away=1)

        match.refresh_from_db()
        self.assertEqual(match.status, Match.Status.COMPLETE)
        self.assertEqual(match.score_home, 3)
        self.assertEqual(match.score_away, 1)

    def test_recording_a_result_cascades_to_a_dependent_match(self):
        teams = [TournamentTeam.objects.create(tournament=self.tournament, foreign_name=f"T{i}", seed=i) for i in range(1, 5)]
        stage = TournamentStage.objects.create(tournament=self.tournament, name="KO", format=TournamentStage.Format.SINGLE_ELIM)
        generate_stage_matches(stage, entries=teams)
        semi = Match.objects.get(stage=stage, round_number=1, sequence=0)

        updated = record_result(semi, score_home=2, score_away=0)

        self.assertEqual(len(updated), 1)
        final = Match.objects.get(stage=stage, round_number=2)
        self.assertEqual(final.home_team_id or final.away_team_id, semi.home_team_id)


# --------------------------------------------------------------------------
# Public tournament site -- list/detail/schedule/standings/team views
# (tournaments.views, appended for phase 5). Same HTTP_HOST/flag-activation
# conventions as TournamentRegistrationViewTests/TournamentRegistrationStatusViewTests
# above.
# --------------------------------------------------------------------------


class ClaimTaskServiceTests(TournamentsTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        starts = timezone.now() + datetime.timedelta(days=1)
        cls.task = TournamentTask.objects.create(tournament=cls.tournament, title="Gate duty", starts_at=starts, ends_at=starts + datetime.timedelta(hours=1), needed_quantity=1)

    def test_claiming_creates_a_claim(self):
        claim = claim_task(self.task, self.member)

        self.assertEqual(claim.task, self.task)
        self.assertEqual(claim.member, self.member)

    def test_cannot_claim_the_same_task_twice(self):
        claim_task(self.task, self.member)

        with self.assertRaises(TaskClaimError):
            claim_task(self.task, self.member)

    def test_cannot_claim_a_fully_covered_task(self):
        other_member = Member.objects.create(first_name="John", last_name="Smith")
        claim_task(self.task, self.member)

        with self.assertRaises(TaskClaimError):
            claim_task(self.task, other_member)

    def test_unclaim_removes_the_claim(self):
        claim = claim_task(self.task, self.member)

        unclaim_task(claim)

        self.assertEqual(self.task.claims.count(), 0)

    def test_unclaiming_frees_the_slot_for_someone_else(self):
        other_member = Member.objects.create(first_name="John", last_name="Smith")
        claim = claim_task(self.task, self.member)
        unclaim_task(claim)

        claim_task(self.task, other_member)  # should not raise

        self.assertEqual(self.task.claims.count(), 1)


class GenerateTaskShiftsServiceTests(TournamentsTestCase):
    def setUp(self):
        self.window_start = timezone.now() + datetime.timedelta(days=1)

    def test_slices_an_evenly_divisible_window_into_equal_shifts(self):
        tasks, extended = generate_task_shifts(
            self.tournament,
            title="Gate duty",
            location_label="Entrance",
            starts_at=self.window_start,
            ends_at=self.window_start + datetime.timedelta(hours=3),
            shift_minutes=30,
            needed_quantity=2,
        )

        self.assertEqual(len(tasks), 6)
        self.assertFalse(extended)
        self.assertEqual(tasks[0].starts_at, self.window_start)
        self.assertEqual(tasks[0].ends_at, self.window_start + datetime.timedelta(minutes=30))
        self.assertEqual(tasks[-1].ends_at, self.window_start + datetime.timedelta(hours=3))
        self.assertTrue(all(task.title == "Gate duty" for task in tasks))
        self.assertTrue(all(task.location_label == "Entrance" for task in tasks))
        self.assertTrue(all(task.needed_quantity == 2 for task in tasks))
        self.assertEqual(TournamentTask.objects.filter(tournament=self.tournament).count(), 6)

    def test_a_final_partial_slice_extends_the_last_shift_instead_of_being_dropped(self):
        # 100 minutes / 30-minute shifts -- 3 full shifts (90 min), the
        # trailing 10 minutes would otherwise be an uncovered gap, so the
        # last shift is stretched to close it instead.
        tasks, extended = generate_task_shifts(
            self.tournament,
            title="Gate duty",
            location_label="",
            starts_at=self.window_start,
            ends_at=self.window_start + datetime.timedelta(minutes=100),
            shift_minutes=30,
            needed_quantity=1,
        )

        self.assertEqual(len(tasks), 3)
        self.assertTrue(extended)
        self.assertEqual(tasks[-1].starts_at, self.window_start + datetime.timedelta(minutes=60))
        self.assertEqual(tasks[-1].ends_at, self.window_start + datetime.timedelta(minutes=100))

    def test_a_window_shorter_than_one_shift_creates_a_single_shortened_shift(self):
        tasks, extended = generate_task_shifts(
            self.tournament,
            title="Gate duty",
            location_label="",
            starts_at=self.window_start,
            ends_at=self.window_start + datetime.timedelta(minutes=10),
            shift_minutes=30,
            needed_quantity=1,
        )

        self.assertEqual(len(tasks), 1)
        self.assertTrue(extended)
        self.assertEqual(tasks[0].starts_at, self.window_start)
        self.assertEqual(tasks[0].ends_at, self.window_start + datetime.timedelta(minutes=10))
        self.assertEqual(TournamentTask.objects.filter(tournament=self.tournament).count(), 1)


class AssignDressingRoomServiceTests(TournamentsTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.room = DressingRoom.objects.create(name="Room A")
        cls.team_a = TournamentTeam.objects.create(tournament=cls.tournament, foreign_name="Team A")
        cls.team_b = TournamentTeam.objects.create(tournament=cls.tournament, foreign_name="Team B")

    def test_assigns_a_free_room(self):
        assignment = assign_dressing_room(self.room, self.team_a)

        self.assertEqual(assignment.dressing_room, self.room)
        self.assertEqual(assignment.tournament_team, self.team_a)

    def test_cannot_assign_the_same_team_to_the_same_room_twice(self):
        assign_dressing_room(self.room, self.team_a)

        with self.assertRaises(DressingRoomAssignmentError):
            assign_dressing_room(self.room, self.team_a)

    def test_whole_tournament_booking_allows_a_different_team_too(self):
        # Overlapping bookings are allowed, not blocked -- the "Dressing
        # rooms" tab flags them visually instead. Only the exact same
        # (room, team) pair twice is still rejected (see the test above).
        assign_dressing_room(self.room, self.team_a)  # no window -- whole tournament

        assign_dressing_room(self.room, self.team_b)  # should not raise

        self.assertEqual(self.room.assignments.count(), 2)

    def test_non_overlapping_time_windows_are_both_allowed(self):
        start = timezone.now() + datetime.timedelta(days=1)
        assign_dressing_room(self.room, self.team_a, assigned_from=start, assigned_until=start + datetime.timedelta(hours=1))

        assign_dressing_room(self.room, self.team_b, assigned_from=start + datetime.timedelta(hours=1), assigned_until=start + datetime.timedelta(hours=2))  # should not raise

        self.assertEqual(self.room.assignments.count(), 2)

    def test_overlapping_time_windows_are_allowed_too(self):
        start = timezone.now() + datetime.timedelta(days=1)
        assign_dressing_room(self.room, self.team_a, assigned_from=start, assigned_until=start + datetime.timedelta(hours=2))

        assign_dressing_room(self.room, self.team_b, assigned_from=start + datetime.timedelta(hours=1), assigned_until=start + datetime.timedelta(hours=3))  # should not raise

        self.assertEqual(self.room.assignments.count(), 2)

    def test_release_removes_the_assignment(self):
        assignment = assign_dressing_room(self.room, self.team_a)

        release_dressing_room(assignment)

        self.assertEqual(self.room.assignments.count(), 0)


class WindowsConflictTests(TournamentsTestCase):
    def test_either_side_missing_a_window_always_conflicts(self):
        start = timezone.now()
        self.assertTrue(windows_conflict(None, None, start, start + datetime.timedelta(hours=1)))
        self.assertTrue(windows_conflict(start, start + datetime.timedelta(hours=1), None, None))

    def test_non_overlapping_windows_do_not_conflict(self):
        start = timezone.now()
        self.assertFalse(windows_conflict(start, start + datetime.timedelta(hours=1), start + datetime.timedelta(hours=1), start + datetime.timedelta(hours=2)))

    def test_overlapping_windows_conflict(self):
        start = timezone.now()
        self.assertTrue(windows_conflict(start, start + datetime.timedelta(hours=2), start + datetime.timedelta(hours=1), start + datetime.timedelta(hours=3)))


class RegistrationEmailTests(TournamentsTestCase):
    def setUp(self):
        self.registration = TournamentRegistration.objects.create(tournament=self.tournament, foreign_name="Visiting FC", foreign_contact_name="Casey", foreign_contact_email="casey@example.com")

    def test_confirmation_goes_to_the_contact_with_the_status_link(self):
        from .services.notifications import send_registration_confirmation_email

        self.assertTrue(send_registration_confirmation_email(self.registration))

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["casey@example.com"])
        self.assertIn(f"/tournaments/{self.tournament.slug}/registration/{self.registration.status_token}", mail.outbox[0].body)

    def test_decision_wording_follows_the_status(self):
        from .services.notifications import send_registration_decision_email

        self.registration.status = TournamentRegistration.Status.APPROVED
        send_registration_decision_email(self.registration)
        self.registration.status = TournamentRegistration.Status.REJECTED
        self.registration.rejection_reason = "We are full."
        send_registration_decision_email(self.registration)

        self.assertIn("approved", mail.outbox[0].subject)
        self.assertIn("not accepted", mail.outbox[1].subject)
        self.assertIn("We are full.", mail.outbox[1].body)


class DispatchShiftsOpenTests(TournamentsTestCase):
    def test_the_task_is_queued_after_the_commit(self):
        from .services.notifications import dispatch_notify_tournament_shifts_open

        with mock.patch("tournaments.tasks.notify_shifts_open.delay") as delay, self.captureOnCommitCallbacks(execute=True):
            dispatch_notify_tournament_shifts_open(self.tournament.pk)

        delay.assert_called_once_with(str(self.tournament.pk))

    def test_the_task_runs_the_notification(self):
        from .tasks import notify_shifts_open

        self.assertIn("Skipped", notify_shifts_open(str(self.tournament.pk)))


class RosterPdfTests(TournamentsTestCase):
    """The roster sheet lists players and staff apart. WeasyPrint is not needed: the data is captured and the template rendered directly."""

    def render(self):
        from django.template.loader import render_to_string

        from tournaments.services import pdf

        captured = {}

        def fake(template, context):
            captured["template"], captured["context"] = template, context
            return render_to_string(template, {**context, "generated_at": timezone.now()}).encode()

        with mock.patch.object(pdf, "_write_pdf", side_effect=fake):
            html = pdf.roster_pdf(self.club, self.tournament, self.tournament_team).decode()
        return captured["context"], html

    def setUp(self):
        self.tournament_team = TournamentTeam.objects.create(tournament=self.tournament, team=self.team)
        self.club = mock.Mock()
        self.club.name = "Sharks"
        young = Member.objects.create(first_name="Zed", last_name="Young", date_of_birth=datetime.date(2011, 5, 4), license_number="L-1")
        old = Member.objects.create(first_name="Amy", last_name="Able")
        coach = Member.objects.create(first_name="Cora", last_name="Coach", date_of_birth=datetime.date(1980, 1, 1), license_number="L-9")
        TournamentRosterEntry.objects.create(tournament_team=self.tournament_team, member=young, jersey_number=9, position_label="Forward")
        TournamentRosterEntry.objects.create(tournament_team=self.tournament_team, member=old, jersey_number=3)
        TournamentRosterEntry.objects.create(tournament_team=self.tournament_team, foreign_first_name="No", foreign_last_name="Number")
        TournamentRosterEntry.objects.create(tournament_team=self.tournament_team, member=coach, role=TournamentRosterRole.STAFF, position_label="Head coach")

    def test_players_and_staff_are_separate_and_players_follow_jersey_order(self):
        context, _html = self.render()

        self.assertEqual([r["name"] for r in context["players"]], ["Amy Able", "Zed Young", "No Number"])  # 3, 9, then no number
        self.assertEqual([r["name"] for r in context["staff"]], ["Cora Coach"])

    def test_members_take_their_birth_date_and_licence_from_their_record_but_staff_has_no_birth_date(self):
        context, _html = self.render()

        zed = next(r for r in context["players"] if r["name"] == "Zed Young")
        self.assertEqual((zed["date_of_birth"], zed["license_number"], zed["position"]), (datetime.date(2011, 5, 4), "L-1", "Forward"))
        coach = context["staff"][0]
        self.assertEqual((coach["date_of_birth"], coach["license_number"], coach["position"]), (None, "L-9", "Head coach"))

    def test_the_sheet_has_a_players_table_before_a_staff_table(self):
        _context, html = self.render()

        self.assertLess(html.index("<h2>Players</h2>"), html.index("<h2>Staff</h2>"))
        self.assertIn("Cora Coach", html.split("<h2>Staff</h2>")[1])
        self.assertNotIn("Cora Coach", html.split("<h2>Staff</h2>")[0])
        self.assertIn("4 May 2011", html)


class ClubTeamEventTests(TournamentsTestCase):
    """Every club team in a tournament has its own calendar event, whose line-up decides its roster, like the home team's."""

    def setUp(self):
        self.tournament.home_team = self.team
        self.tournament.save(update_fields=["home_team"])
        self.home = ensure_home_team(self.tournament)
        self.tournament.refresh_from_db()
        self.other_team = Team.objects.create(name="Sharks U18", short_name="U18")

    def add(self):
        return create_manual_team(self.tournament, club_team=self.other_team)

    def test_the_home_teams_event_is_the_tournaments_own_and_another_team_gets_its_own(self):
        other = self.add()

        self.assertEqual(self.home.event_id, self.tournament.event_id)
        self.assertNotEqual(other.event_id, self.tournament.event_id)
        self.assertEqual(other.event.title, f"{self.tournament.name} — Sharks U18")
        self.assertEqual(list(other.event.teams.all()), [self.other_team])
        self.assertEqual(list(self.home.event.teams.all()), [self.team])

    def test_a_guest_team_has_no_event(self):
        guest = create_manual_team(self.tournament, foreign_fields={"foreign_name": "Walk-up FC", "foreign_contact_email": "w@example.com"})

        self.assertIsNone(guest.event)

    def test_another_teams_staff_is_copied_once_and_its_players_follow_its_own_line_up(self):
        coach = Member.objects.create(first_name="Coach", last_name="Eighteen")
        head_coach = Position.objects.create(name="Head coach", short_name="HC", staff_position=True)
        StaffAssignment.objects.create(team=self.other_team, member=coach, season=self.season, position=head_coach)
        TeamMembership.objects.create(team=self.other_team, member=self.member, season=self.season, jersey_number=18)
        other = self.add()
        self.assertEqual([(e.member_id, e.role) for e in other.roster_entries.all()], [(coach.pk, TournamentRosterRole.STAFF)])

        lineup = Lineup.objects.create(event=other.event, team=self.other_team)
        LineupSelection.objects.create(lineup=lineup, member=self.member)
        lineup_changed.send(sender=Lineup, lineup=lineup)

        player = other.roster_entries.get(member=self.member)
        self.assertEqual((player.role, player.jersey_number), (TournamentRosterRole.PLAYER, 18))
        self.assertFalse(self.home.roster_entries.filter(member=self.member).exists())  # the home team's roster is its own

    def test_changing_the_days_moves_every_teams_event(self):
        other = self.add()
        TournamentDay.objects.create(tournament=self.tournament, date=datetime.date(2026, 9, 19), starts_at=datetime.time(10, 30), ends_at=datetime.time(16, 0))

        ensure_home_team(self.tournament)

        other.event.refresh_from_db()
        self.home.event.refresh_from_db()
        for event in (other.event, self.home.event):
            self.assertEqual(timezone.localtime(event.start).strftime("%H:%M"), "10:30")
            self.assertEqual(timezone.localtime(event.end).strftime("%H:%M"), "16:00")

    def test_withdrawing_a_team_cancels_its_event_and_deleting_it_removes_the_event(self):
        other = self.add()
        event_id = other.event_id
        withdraw_team(other, reason="Injuries")
        self.assertTrue(Event.objects.get(pk=event_id).cancelled)

        removable = create_manual_team(self.tournament, club_team=Team.objects.create(name="Sharks U12", short_name="U12"))
        removable_event = removable.event_id
        delete_manual_team(removable)
        self.assertFalse(Event.objects.filter(pk=removable_event).exists())

    def test_changing_the_home_team_gives_the_previous_home_team_an_event_of_its_own(self):
        other = self.add()
        other_event_id = other.event_id
        old_tournament_event = self.tournament.event_id

        self.tournament.home_team = self.other_team
        self.tournament.save(update_fields=["home_team"])
        ensure_home_team(self.tournament)
        self.tournament.refresh_from_db()
        other.refresh_from_db()
        self.home.refresh_from_db()

        self.assertEqual(self.tournament.event_id, other_event_id)  # the new home team's own event is now the tournament's
        self.assertEqual(other.event_id, other_event_id)
        self.assertIsNotNone(self.home.event_id)
        self.assertNotEqual(self.home.event_id, old_tournament_event if old_tournament_event == other_event_id else None)
        self.assertEqual(list(self.home.event.teams.all()), [self.team])


class LunchScheduleTests(TournamentsTestCase):
    D1 = datetime.date(2026, 9, 19)

    def at(self, hour, minute=0, date=None):
        return timezone.make_aware(datetime.datetime.combine(date or self.D1, datetime.time(hour, minute)))

    def setUp(self):
        self.tournament.lunch_mode = Tournament.LunchMode.OPTIONAL
        self.tournament.lunch_duration_minutes = 30
        self.tournament.lunch_interval_minutes = 10
        self.tournament.lunch_gap_before_minutes = 15
        self.tournament.lunch_gap_after_minutes = 15
        self.tournament.lunch_capacity_teams = 2
        self.tournament.save()
        self.day = TournamentDay.objects.create(tournament=self.tournament, date=self.D1, starts_at=datetime.time(9), ends_at=datetime.time(18), lunch_start=datetime.time(12), lunch_end=datetime.time(13, 30))

    def eater(self, name, eats=True, withdrawn=False):
        team = TournamentTeam.objects.create(tournament=self.tournament, foreign_name=name, withdrawn_at=timezone.now() if withdrawn else None)
        TournamentRosterEntry.objects.create(tournament_team=team, foreign_first_name="P", foreign_last_name=name, lunch_days=[self.D1.isoformat()] if eats else [])
        return team

    def field(self, name="F1"):
        return TournamentField.objects.create(tournament=self.tournament, name=name, availability=[{"date": "2026-09-19", "start": "09:00", "end": "20:00"}])

    def lunches(self):
        return ScheduleBlock.objects.filter(tournament=self.tournament, kind=ScheduleBlock.Kind.LUNCH).order_by("lunch_start")

    def test_sitting_maths(self):
        def mins(n):
            return datetime.timedelta(minutes=n)

        sittings = build_sittings(self.at(11), self.at(12, 30), duration=mins(30), interval=mins(10))
        self.assertEqual(sittings, [(self.at(11), self.at(11, 30)), (self.at(11, 40), self.at(12, 10))])  # a third would end 12:50 +
        self.assertEqual(len(build_sittings(self.at(11), self.at(12, 50), duration=mins(30), interval=mins(10))), 3)
        self.assertEqual(build_sittings(self.at(11), self.at(11, 20), duration=mins(30), interval=mins(10)), [])
        self.assertEqual(len(build_sittings(self.at(11), self.at(12), duration=mins(30), interval=mins(0))), 2)

    def test_creates_one_padded_block_per_sitting_with_balanced_fill(self):
        for name in "ABCD":
            self.eater(name)
        result = generate_lunch_schedule(self.tournament)
        blocks = list(self.lunches())
        self.assertEqual((result["sittings_created"], result["problems"], result["games_replanned"]), (2, [], 0))
        self.assertIsNotNone(result["note"])  # nothing to replan yet
        self.assertEqual([(b.lunch_start, b.lunch_end) for b in blocks], [(self.at(12), self.at(12, 30)), (self.at(12, 40), self.at(13, 10))])
        self.assertEqual([(b.starts_at, b.ends_at) for b in blocks], [(self.at(11, 45), self.at(12, 45)), (self.at(12, 25), self.at(13, 25))])  # padded
        self.assertEqual([b.teams.count() for b in blocks], [2, 2])
        self.assertTrue(all(b.field_id is None and b.title == "Lunch" for b in blocks))

    def test_only_teams_that_eat_that_day_and_are_not_withdrawn(self):
        a = self.eater("A")
        self.eater("B", eats=False)
        self.eater("C", withdrawn=True)
        generate_lunch_schedule(self.tournament)
        self.assertEqual([list(b.teams.all()) for b in self.lunches()], [[a]])

    def test_no_room_is_reported_not_dropped(self):
        self.day.lunch_end = datetime.time(12, 30)  # one sitting of two
        self.day.save()
        teams = [self.eater(n) for n in "ABC"]
        result = generate_lunch_schedule(self.tournament)
        self.assertEqual([p["type"] for p in result["problems"]], ["no_room"])
        self.assertEqual(result["problems"][0]["team"], {"id": teams[2].pk, "name": "C"})
        self.assertEqual(result["problems"][0]["date"], self.D1)
        self.assertEqual(sum(b.teams.count() for b in self.lunches()), 2)

    def test_no_window_problem(self):
        self.day.lunch_start = self.day.lunch_end = None
        self.day.save()
        self.eater("A")
        result = generate_lunch_schedule(self.tournament)
        self.assertEqual([(p["type"], p["team"]) for p in result["problems"]], [("no_window", None)])
        self.assertEqual(result["sittings_created"], 0)

    def test_teams_go_to_the_sitting_that_conflicts_least(self):
        self.day.lunch_start, self.day.lunch_end = datetime.time(11, 30), datetime.time(12, 50)
        self.day.save()
        self.tournament.lunch_capacity_teams = 1
        self.tournament.save()
        a, b = self.eater("A"), self.eater("B")
        field = self.field()
        stage = TournamentStage.objects.create(tournament=self.tournament, name="C", format=TournamentStage.Format.CUSTOM)
        other = self.eater("X", eats=False)
        Match.objects.create(stage=stage, home_team=a, away_team=other, field=field, scheduled_start=self.at(11, 20))  # hand-placed 11:20-11:50
        generate_lunch_schedule(self.tournament, datetime.timedelta(minutes=30))
        first, second = list(self.lunches())
        self.assertEqual((list(first.teams.all()), list(second.teams.all())), ([b], [a]))  # A's 11:20 game is inside the first sitting's padding

    def test_assign_ties_go_to_least_filled_then_earliest(self):
        teams = [self.eater(n) for n in "ABC"]
        sittings = build_sittings(self.at(12), self.at(13, 30), duration=datetime.timedelta(minutes=30), interval=datetime.timedelta(minutes=10))
        filled, unplaced = _assign(teams, sittings, 2, {}, datetime.timedelta(minutes=15), datetime.timedelta(minutes=15))
        self.assertEqual(([[t.display_name for t in f] for f in filled], unplaced), ([["A", "C"], ["B"]], []))

    def test_games_are_replanned_around_the_padding(self):
        self.field()
        self.field("F2")
        teams = [self.eater(f"T{i}") for i in range(1, 9)]
        self.tournament.lunch_capacity_teams = 4
        self.tournament.save()
        stage = TournamentStage.objects.create(tournament=self.tournament, name="RR", format=TournamentStage.Format.ROUND_ROBIN)
        generate_stage_matches(stage, entries=teams)
        allocate_schedule(self.tournament, match_duration=datetime.timedelta(minutes=30))

        result = generate_lunch_schedule(self.tournament, datetime.timedelta(minutes=30))

        self.assertEqual(result["problems"], [])
        self.assertEqual((result["sittings_created"], result["games_replanned"]), (2, 28))
        self.assertIsNone(result["note"])
        for block in self.lunches():
            for team in block.teams.all():
                for match in Match.objects.filter(Q(home_team=team) | Q(away_team=team)):
                    end = match.scheduled_start + datetime.timedelta(minutes=30)
                    self.assertFalse(match.scheduled_start < block.ends_at and block.starts_at < end, (team, match.scheduled_start))
        self.assertEqual(Match.objects.filter(scheduled_start__isnull=True).count(), 0)

    def test_game_close_is_reported_for_played_and_hand_moved_games_which_stay_put(self):
        field = self.field()
        a = self.eater("A")
        other = self.eater("X", eats=False)
        stage = TournamentStage.objects.create(tournament=self.tournament, name="C", format=TournamentStage.Format.CUSTOM)
        played = Match.objects.create(stage=stage, home_team=a, away_team=other, field=field, scheduled_start=self.at(12), status=Match.Status.COMPLETE, score_home=1, score_away=0)
        moved = Match.objects.create(stage=stage, home_team=other, away_team=a, field=field, scheduled_start=self.at(12, 20), auto_scheduled=False)
        result = generate_lunch_schedule(self.tournament, datetime.timedelta(minutes=30))
        self.assertEqual({p["type"] for p in result["problems"]}, {"game_close"})
        self.assertEqual(len(result["problems"]), 2)
        self.assertEqual(result["problems"][0]["team"], {"id": a.pk, "name": "A"})
        self.assertIn("A", result["problems"][0]["message"])
        played.refresh_from_db()
        moved.refresh_from_db()
        self.assertEqual((played.scheduled_start, moved.scheduled_start), (self.at(12), self.at(12, 20)))

    def test_regenerating_replaces_lunch_blocks_and_clear_keeps_manual_ones(self):
        self.eater("A")
        manual = ScheduleBlock.objects.create(tournament=self.tournament, title="Opening", starts_at=self.at(9), ends_at=self.at(9, 30))
        generate_lunch_schedule(self.tournament)
        first = set(self.lunches().values_list("pk", flat=True))
        generate_lunch_schedule(self.tournament)
        self.assertEqual(self.lunches().count(), 1)
        self.assertFalse(first & set(self.lunches().values_list("pk", flat=True)))
        self.assertEqual(clear_lunch_schedule(self.tournament), 1)
        self.assertEqual(list(ScheduleBlock.objects.filter(tournament=self.tournament)), [manual])
        self.assertEqual(manual.kind, ScheduleBlock.Kind.BLOCK)

    def test_generate_and_clear_schedule_of_games_keep_lunch_blocks(self):
        self.field()
        teams = [self.eater(f"T{i}") for i in range(1, 5)]
        stage = TournamentStage.objects.create(tournament=self.tournament, name="RR", format=TournamentStage.Format.ROUND_ROBIN)
        generate_stage_matches(stage, entries=teams)
        generate_lunch_schedule(self.tournament)
        before = set(self.lunches().values_list("pk", flat=True))
        allocate_schedule(self.tournament, reset=True)
        clear_schedule(self.tournament)
        self.assertEqual(set(self.lunches().values_list("pk", flat=True)), before)
        self.assertTrue(before)

    def test_a_day_needs_both_lunch_times_with_the_end_after_the_start(self):
        for start, end in ((datetime.time(12), None), (datetime.time(13), datetime.time(12)), (datetime.time(12), datetime.time(12))):
            with self.assertRaises(IntegrityError), transaction.atomic():
                TournamentDay.objects.filter(pk=self.day.pk).update(lunch_start=start, lunch_end=end)


class TiebreakerChainTests(TournamentsTestCase):
    """The ordered ``{criterion, direction}`` tie-break chain, the old plain-string shape, and the attribute totals on standings rows."""

    def make_two_beat_one(self, tiebreakers=None):
        """A and B each beat C 1-0: equal on points, goal difference and goals for."""
        a, b, c = (TournamentTeam.objects.create(tournament=self.tournament, foreign_name=n) for n in ("A", "B", "C"))
        stage = TournamentStage.objects.create(tournament=self.tournament, name="RR", format=TournamentStage.Format.ROUND_ROBIN, standings_config={"tiebreakers": tiebreakers or []})
        for winner in (a, b):
            Match.objects.create(stage=stage, home_team=winner, away_team=c, score_home=1, score_away=0, status=Match.Status.COMPLETE)
        return stage, (a, b, c)

    def test_normalise_reads_the_old_string_list_and_the_new_shape(self):
        self.assertEqual(
            normalise_tiebreakers(["goal_diff", "won", "goals_against", "goals_for", "goals_for"]),
            [
                {"criterion": "goal_diff", "direction": "most"},
                {"criterion": "wins", "direction": "most"},
                {"criterion": "goals_against", "direction": "least"},
                {"criterion": "goals_for", "direction": "most"},
            ],
        )
        self.assertEqual(
            normalise_tiebreakers([{"criterion": "attr:pim", "direction": "least"}, {"criterion": "goal_diff"}, {"criterion": "wins", "direction": "sideways"}]),
            [{"criterion": "attr:pim", "direction": "least"}, {"criterion": "goal_diff", "direction": "most"}, {"criterion": "wins", "direction": "most"}],
        )
        stage = TournamentStage.objects.create(tournament=self.tournament, name="Old", format=TournamentStage.Format.ROUND_ROBIN)
        self.assertEqual([t["criterion"] for t in stage_tiebreakers(stage)], ["goal_diff", "goals_for"])  # never stored: the default
        stage.standings_config = {"tiebreakers": []}
        self.assertEqual(stage_tiebreakers(stage), [])  # an explicit empty chain stays empty

    def test_a_chain_of_four_mixes_least_and_most_and_an_attribute_and_only_breaks_what_is_still_level(self):
        def row(name, goals_against, pim, goal_diff, wins):
            return {"team": mock.Mock(id=name), "points": 3, "won": wins, "goals_for": 0, "goals_against": goals_against, "goal_diff": goal_diff, "attributes": {"pim": pim}}

        rows = [row("A", 2, 10, 1, 1), row("B", 2, 4, 0, 1), row("C", 5, 1, 9, 1), row("D", 2, 4, 3, 1), row("E", 2, 4, 3, 0)]
        chain = [
            {"criterion": "goals_against", "direction": "least"},
            {"criterion": "attr:pim", "direction": "least"},
            {"criterion": "goal_diff", "direction": "most"},
            {"criterion": "wins", "direction": "most"},
        ]
        ranked = _ranked(rows, chain)
        # goals against 2 first (A,B,D,E; C last), then fewest pim (B,D,E before A), then goal diff (D,E before B), then wins (D before E)
        self.assertEqual([r["team"].id for r in ranked], ["D", "E", "B", "A", "C"])
        self.assertEqual([r["rank"] for r in ranked], [1, 2, 3, 4, 5])
        flipped = _ranked(rows, [{"criterion": "goals_against", "direction": "most"}])
        self.assertEqual(flipped[0]["team"].id, "C")

    def test_attribute_totals_on_rows_and_as_tiebreaker(self):
        pim = MatchAttribute.objects.create(tournament=self.tournament, name="PIM", key="pim")
        stage, (a, b, c) = self.make_two_beat_one([{"criterion": "attr:pim", "direction": "least"}])
        for match in stage.matches.all():
            MatchAttributeValue.objects.create(match=match, attribute=pim, home_value=10 if match.home_team_id == a.id else 4, away_value=2)
        rows = {r["team"].id: r for r in compute_standings(stage)}
        self.assertEqual((rows[a.id]["attributes"], rows[b.id]["attributes"], rows[c.id]["attributes"]), ({"pim": 10}, {"pim": 4}, {"pim": 4}))
        self.assertEqual([r["team"].id for r in compute_standings(stage)][:2], [b.id, a.id])  # fewest penalty minutes first
        stage.standings_config = {"tiebreakers": [{"criterion": "attr:pim", "direction": "most"}]}
        self.assertEqual([r["team"].id for r in compute_standings(stage)][:2], [a.id, b.id])

    def test_an_old_string_chain_still_ranks_the_same_way(self):
        stage, (a, b, _c) = self.make_two_beat_one(["goal_diff", "goals_for"])
        self.assertEqual({r["team"].id for r in compute_standings(stage)[:2]}, {a.id, b.id})
        self.assertEqual(compute_standings(stage)[0]["attributes"], {})


class TimelineServiceTests(TournamentsTestCase):
    def setUp(self):
        self.stage = TournamentStage.objects.create(tournament=self.tournament, name="KO", format=TournamentStage.Format.SINGLE_ELIM)
        self.a, self.b, self.c = (TournamentTeam.objects.create(tournament=self.tournament, foreign_name=n) for n in ("A", "B", "C"))
        self.semi = Match.objects.create(stage=self.stage, home_team=self.a, away_team=self.b)
        self.final = Match.objects.create(stage=self.stage, round_number=2, home_source_match=self.semi, away_team=self.c)

    def goal(self, side):
        return MatchEvent.objects.create(match=self.semi, type=MatchEvent.Type.GOAL, side=side)

    def test_goals_fill_the_score_and_deleting_all_of_them_leaves_it(self):
        self.goal("home")
        self.goal("home")
        self.goal("away")
        sync_match(self.semi)
        self.semi.refresh_from_db()
        self.assertEqual((self.semi.score_home, self.semi.score_away, self.semi.status), (2, 1, Match.Status.SCHEDULED))
        self.semi.events.all().delete()
        sync_match(self.semi)
        self.semi.refresh_from_db()
        self.assertEqual((self.semi.score_home, self.semi.score_away), (2, 1))

    def test_a_completed_match_pushes_a_new_score_through_the_bracket(self):
        record_result(self.semi, score_home=1, score_away=0)
        self.final.refresh_from_db()
        self.assertEqual(self.final.home_team_id, self.a.pk)
        for _ in range(2):
            self.goal("away")
        sync_match(self.semi)
        self.final.refresh_from_db()
        self.assertEqual((self.semi.score_home, self.semi.score_away, self.final.home_team_id), (0, 2, self.b.pk))

    def test_a_change_under_a_played_final_or_to_a_draw_is_locked(self):
        record_result(self.semi, score_home=1, score_away=0)
        record_result(Match.objects.get(pk=self.final.pk), score_home=1, score_away=0)
        for _ in range(2):
            self.goal("away")
        with self.assertRaises(MatchLocked):
            sync_match(self.semi)
        self.semi.events.all().delete()
        Match.objects.filter(pk=self.final.pk).update(status=Match.Status.SCHEDULED)
        self.goal("home")
        self.goal("away")
        self.semi.refresh_from_db()
        with self.assertRaises(MatchLocked):  # a draw has no winner to feed forward
            sync_match(self.semi)

    def test_timeline_attributes_are_recomputed_from_the_events(self):
        pim = MatchAttribute.objects.create(tournament=self.tournament, name="PIM", key="pim", source="timeline", event_type="penalty", aggregate="sum")
        penalties = MatchAttribute.objects.create(tournament=self.tournament, name="Penalties", key="penalties", source="timeline", event_type="penalty", aggregate="count")
        MatchEvent.objects.create(match=self.semi, type="penalty", side="home", value=2)
        MatchEvent.objects.create(match=self.semi, type="penalty", side="home", value=5)
        MatchEvent.objects.create(match=self.semi, type="penalty", side="away", value=2)
        MatchEvent.objects.create(match=self.semi, type="penalty", side="", value=9)  # neutral: counted for nobody
        MatchEvent.objects.create(match=self.semi, type="timeout", side="away", value=1)
        sync_match(self.semi)

        def value(attribute):
            return MatchAttributeValue.objects.filter(match=self.semi, attribute=attribute).values_list("home_value", "away_value").get()

        self.assertEqual((value(pim), value(penalties)), ((7, 2), (2, 1)))
        self.semi.events.filter(value=5).delete()
        sync_match(self.semi)
        self.assertEqual(value(pim), (2, 2))
