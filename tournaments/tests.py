import datetime
import itertools

from django.contrib import admin, messages
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core import mail
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import IntegrityError, models
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from waffle import get_waffle_flag_model

from authentication.models import User
from club.models import Club
from events.models import Event, Location
from members.models import Member
from rosterchief.test_support import make_club, make_season
from teams.models import Position, Team, TeamMembership

from .admin import TournamentAdmin, TournamentStageAdmin
from .models import (
    BreakRule,
    DressingRoom,
    DressingRoomAssignment,
    Match,
    MatchOfficial,
    MatchReferee,
    Tournament,
    TournamentField,
    TournamentGroup,
    TournamentRegistration,
    TournamentRegistrationPlayer,
    TournamentRosterEntry,
    TournamentStage,
    TournamentTask,
    TournamentTaskClaim,
    TournamentTeam,
)
from .services.dressing_rooms import DressingRoomAssignmentError, assign_dressing_room, release_dressing_room
from .services.registration import RegistrationDecisionError, approve_registration, reject_registration
from .services.scheduling import (
    DEFAULT_GAP,
    DEFAULT_MATCH_DURATION,
    SchedulingError,
    allocate_schedule,
    compute_standings,
    gap_for_placement,
    generate_next_swiss_round,
    generate_stage_matches,
    record_result,
    reschedule_match,
    resolve_match_slots,
    round_robin_pairings,
    snake_seed_groups,
    validate_placement,
)
from .services.tasks import TaskClaimError, claim_task, unclaim_task


class TournamentsTestCase(TestCase):
    # Shared read-only scaffolding for every tournaments test -- one club, one
    # season, one real team/member for the "linked entity" side of the various
    # xor pairs, one tournament. Mirrors TeamsTestCase/EventsTestBase's own shape.
    @classmethod
    def setUpTestData(cls):
        cls.club = make_club()
        cls.season = make_season(cls.club)
        cls.team = Team.objects.create(club=cls.club, name="First Team", short_name="1st")
        cls.member = Member.objects.create(first_name="Jane", last_name="Doe")
        cls.tournament = Tournament.objects.create(
            club=cls.club,
            name="Summer Cup",
            season=cls.season,
            start_date=datetime.date(2026, 9, 19),
            end_date=datetime.date(2026, 9, 20),
        )


class TournamentModelTests(TournamentsTestCase):
    def test_str_returns_name(self):
        self.assertEqual(str(self.tournament), "Summer Cup")

    def test_slug_is_generated_from_name_on_save(self):
        self.assertEqual(self.tournament.slug, "summer-cup")

    def test_status_defaults_to_draft(self):
        self.assertEqual(self.tournament.status, Tournament.Status.DRAFT)

    def test_registration_open_defaults_to_true(self):
        self.assertTrue(self.tournament.registration_open)

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

    def test_slug_is_unique_per_club(self):
        with self.assertRaises(IntegrityError):
            Tournament.objects.create(club=self.club, name="Other Cup", slug="summer-cup", season=self.season, start_date=self.tournament.start_date, end_date=self.tournament.end_date)

    def test_same_slug_allowed_in_a_different_club(self):
        other = make_club(name="Rival FC", slug="rival-fc")
        other_season = make_season(other)
        Tournament.objects.create(club=other, name="Other Cup", slug="summer-cup", season=other_season, start_date=self.tournament.start_date, end_date=self.tournament.end_date)

        self.assertEqual(Tournament.objects.filter(slug="summer-cup").count(), 2)

    def test_clean_rejects_an_event_from_another_club(self):
        other = make_club(name="Rival FC", slug="rival-fc")
        event = Event.objects.create(club=other, kind=Event.EventKind.TOURNAMENT, title="Cup", start=timezone.now())
        self.tournament.event = event

        with self.assertRaises(ValidationError) as ctx:
            self.tournament.full_clean()
        self.assertIn("event", ctx.exception.error_dict)

    def test_clean_rejects_an_event_from_another_season(self):
        other_season = make_season(self.club, start_year=2020)
        event = Event.objects.create(club=self.club, season=other_season, kind=Event.EventKind.TOURNAMENT, title="Cup", start=timezone.now())
        self.tournament.event = event

        with self.assertRaises(ValidationError) as ctx:
            self.tournament.full_clean()
        self.assertIn("event", ctx.exception.error_dict)

    def test_clean_accepts_an_event_with_no_season_set(self):
        event = Event.objects.create(club=self.club, season=None, kind=Event.EventKind.TOURNAMENT, title="Cup", start=timezone.now())
        self.tournament.event = event

        self.tournament.full_clean()

    def test_clean_accepts_an_event_in_the_same_club_and_season(self):
        event = Event.objects.create(club=self.club, season=self.season, kind=Event.EventKind.TOURNAMENT, title="Cup", start=timezone.now())
        self.tournament.event = event

        self.tournament.full_clean()


class TournamentFieldModelTests(TournamentsTestCase):
    def test_str(self):
        field = TournamentField.objects.create(tournament=self.tournament, name="Field 1")
        self.assertEqual(str(field), "Summer Cup - Field 1")

    def test_availability_defaults_to_an_empty_list(self):
        field = TournamentField.objects.create(tournament=self.tournament, name="Field 1")
        self.assertEqual(field.availability, [])

    def test_location_is_optional_and_reachable(self):
        location = Location.objects.create(club=self.club, name="Arena", address="1 St", city="Town", zip_code="1000", country="BE")
        field = TournamentField.objects.create(tournament=self.tournament, name="Field 1", location=location)

        self.assertEqual(field.location, location)


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


class TournamentTeamModelTests(TournamentsTestCase):
    def test_str_and_display_name_for_a_real_team(self):
        entry = TournamentTeam.objects.create(club=self.club, tournament=self.tournament, team=self.team)

        self.assertEqual(entry.display_name, "First Team")
        self.assertEqual(str(entry), "First Team")

    def test_str_and_display_name_for_a_foreign_team(self):
        entry = TournamentTeam.objects.create(club=self.club, tournament=self.tournament, foreign_name="Guest FC")

        self.assertEqual(entry.display_name, "Guest FC")
        self.assertEqual(str(entry), "Guest FC")

    def test_team_xor_foreign_name_rejects_neither_set(self):
        with self.assertRaises(IntegrityError):
            TournamentTeam.objects.create(club=self.club, tournament=self.tournament)

    def test_team_xor_foreign_name_rejects_both_set(self):
        with self.assertRaises(IntegrityError):
            TournamentTeam.objects.create(club=self.club, tournament=self.tournament, team=self.team, foreign_name="Guest FC")

    def test_team_xor_foreign_name_accepts_team_only(self):
        TournamentTeam.objects.create(club=self.club, tournament=self.tournament, team=self.team)

    def test_team_xor_foreign_name_accepts_foreign_name_only(self):
        TournamentTeam.objects.create(club=self.club, tournament=self.tournament, foreign_name="Guest FC")

    def test_real_team_is_unique_per_tournament(self):
        TournamentTeam.objects.create(club=self.club, tournament=self.tournament, team=self.team)

        with self.assertRaises(IntegrityError):
            TournamentTeam.objects.create(club=self.club, tournament=self.tournament, team=self.team)

    def test_several_foreign_entries_are_allowed_in_the_same_tournament(self):
        TournamentTeam.objects.create(club=self.club, tournament=self.tournament, foreign_name="Guest FC")
        TournamentTeam.objects.create(club=self.club, tournament=self.tournament, foreign_name="Other FC")

        self.assertEqual(self.tournament.teams.count(), 2)

    def test_slug_is_generated_from_display_name(self):
        entry = TournamentTeam.objects.create(club=self.club, tournament=self.tournament, foreign_name="Guest FC")

        self.assertEqual(entry.slug, "guest-fc")

    def test_same_slug_allowed_in_a_different_tournament(self):
        TournamentTeam.objects.create(club=self.club, tournament=self.tournament, foreign_name="Guest FC")
        other_tournament = Tournament.objects.create(club=self.club, name="Winter Cup", season=self.season, start_date=self.tournament.start_date, end_date=self.tournament.end_date)

        other_entry = TournamentTeam.objects.create(club=self.club, tournament=other_tournament, foreign_name="Guest FC")

        self.assertEqual(other_entry.slug, "guest-fc")

    def test_slug_is_unique_within_the_same_tournament(self):
        TournamentTeam.objects.create(club=self.club, tournament=self.tournament, foreign_name="Guest FC")

        second = TournamentTeam.objects.create(club=self.club, tournament=self.tournament, foreign_name="Guest FC")

        self.assertEqual(second.slug, "guest-fc-2")

    def test_clean_rejects_a_team_from_another_club(self):
        other = make_club(name="Rival FC", slug="rival-fc")
        other_team = Team.objects.create(club=other, name="Rival Team", short_name="RT")
        entry = TournamentTeam(club=self.club, tournament=self.tournament, team=other_team)

        with self.assertRaises(ValidationError) as ctx:
            entry.full_clean()
        self.assertIn("team", ctx.exception.error_dict)

    def test_clean_accepts_a_team_from_the_same_club(self):
        entry = TournamentTeam(club=self.club, tournament=self.tournament, team=self.team)

        entry.full_clean()


class TournamentRosterEntryModelTests(TournamentsTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.tournament_team = TournamentTeam.objects.create(club=cls.club, tournament=cls.tournament, team=cls.team)

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


class MatchModelTests(TournamentsTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.stage = TournamentStage.objects.create(tournament=cls.tournament, name="Bracket", format=TournamentStage.Format.SINGLE_ELIM)
        cls.home = TournamentTeam.objects.create(club=cls.club, tournament=cls.tournament, foreign_name="Home Squad")
        cls.away = TournamentTeam.objects.create(club=cls.club, tournament=cls.tournament, foreign_name="Away Squad")

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
        room = DressingRoom.objects.create(club=self.club, name="Room 1")
        self.assertEqual(str(room), "Room 1")

    def test_location_is_optional_and_reachable(self):
        location = Location.objects.create(club=self.club, name="Arena", address="1 St", city="Town", zip_code="1000", country="BE")
        room = DressingRoom.objects.create(club=self.club, name="Room 1", location=location)

        self.assertEqual(room.location, location)


class DressingRoomAssignmentModelTests(TournamentsTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.room = DressingRoom.objects.create(club=cls.club, name="Room 1")
        cls.tournament_team = TournamentTeam.objects.create(club=cls.club, tournament=cls.tournament, team=cls.team)

    def test_str(self):
        assignment = DressingRoomAssignment.objects.create(dressing_room=self.room, tournament_team=self.tournament_team)
        self.assertEqual(str(assignment), "Room 1 - First Team")

    def test_dressing_room_and_tournament_team_is_unique(self):
        DressingRoomAssignment.objects.create(dressing_room=self.room, tournament_team=self.tournament_team)

        with self.assertRaises(IntegrityError):
            DressingRoomAssignment.objects.create(dressing_room=self.room, tournament_team=self.tournament_team)

    def test_the_same_team_can_be_assigned_a_different_room(self):
        other_room = DressingRoom.objects.create(club=self.club, name="Room 2")
        DressingRoomAssignment.objects.create(dressing_room=self.room, tournament_team=self.tournament_team)

        DressingRoomAssignment.objects.create(dressing_room=other_room, tournament_team=self.tournament_team)

        self.assertEqual(self.tournament_team.dressing_room_assignments.count(), 2)

    def test_the_same_room_can_be_assigned_a_different_team(self):
        other_entry = TournamentTeam.objects.create(club=self.club, tournament=self.tournament, foreign_name="Guest FC")
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
        reviewer = User.objects.create_user(email="staff@example.com", password="pw-secret-123")
        registration = TournamentRegistration.objects.create(tournament=self.tournament, foreign_name="Visiting FC", foreign_contact_email="visitors@example.com")
        TournamentRegistrationPlayer.objects.create(registration=registration, first_name="Sam", last_name="Striker", jersey_number=9)
        TournamentRegistrationPlayer.objects.create(registration=registration, first_name="Dee", last_name="Fender")

        team = approve_registration(registration, reviewed_by=reviewer)

        registration.refresh_from_db()
        self.assertEqual(registration.status, TournamentRegistration.Status.APPROVED)
        self.assertEqual(registration.reviewed_by, reviewer)
        self.assertIsNotNone(registration.reviewed_at)
        self.assertEqual(team.tournament, self.tournament)
        self.assertEqual(team.club, self.club)
        self.assertEqual(team.foreign_name, "Visiting FC")
        self.assertEqual(team.registration, registration)
        roster = {entry.display_name: entry for entry in team.roster_entries.all()}
        self.assertEqual(set(roster), {"Sam Striker", "Dee Fender"})
        self.assertEqual(roster["Sam Striker"].jersey_number, 9)
        self.assertIsNone(roster["Dee Fender"].jersey_number)

    def test_approving_a_club_team_registration_snapshots_the_season_roster(self):
        reviewer = User.objects.create_user(email="staff@example.com", password="pw-secret-123")
        position = Position.objects.create(club=self.club, name="Forward", short_name="FW")
        TeamMembership.objects.create(team=self.team, member=self.member, season=self.season, position=position, jersey_number=7)
        registration = TournamentRegistration.objects.create(tournament=self.tournament, team=self.team)

        team = approve_registration(registration, reviewed_by=reviewer)

        roster = list(team.roster_entries.all())
        self.assertEqual(len(roster), 1)
        self.assertEqual(roster[0].member, self.member)
        self.assertEqual(roster[0].jersey_number, 7)
        self.assertEqual(roster[0].position_label, "Forward")

    def test_approving_a_club_team_registration_does_not_use_another_seasons_roster(self):
        reviewer = User.objects.create_user(email="staff@example.com", password="pw-secret-123")
        other_season = make_season(self.club, start_year=2020)
        TeamMembership.objects.create(team=self.team, member=self.member, season=other_season)
        registration = TournamentRegistration.objects.create(tournament=self.tournament, team=self.team)

        team = approve_registration(registration, reviewed_by=reviewer)

        self.assertEqual(team.roster_entries.count(), 0)

    def test_cannot_approve_an_already_decided_registration(self):
        reviewer = User.objects.create_user(email="staff@example.com", password="pw-secret-123")
        registration = TournamentRegistration.objects.create(tournament=self.tournament, foreign_name="Visiting FC")
        approve_registration(registration, reviewed_by=reviewer)

        with self.assertRaises(RegistrationDecisionError):
            approve_registration(registration, reviewed_by=reviewer)


class RejectRegistrationServiceTests(TournamentsTestCase):
    def test_rejecting_sets_status_and_reason(self):
        reviewer = User.objects.create_user(email="staff@example.com", password="pw-secret-123")
        registration = TournamentRegistration.objects.create(tournament=self.tournament, foreign_name="Visiting FC")

        reject_registration(registration, reviewed_by=reviewer, reason="Tournament is full.")

        registration.refresh_from_db()
        self.assertEqual(registration.status, TournamentRegistration.Status.REJECTED)
        self.assertEqual(registration.rejection_reason, "Tournament is full.")
        self.assertEqual(registration.reviewed_by, reviewer)
        self.assertFalse(TournamentTeam.objects.filter(registration=registration).exists())

    def test_cannot_reject_an_already_decided_registration(self):
        reviewer = User.objects.create_user(email="staff@example.com", password="pw-secret-123")
        registration = TournamentRegistration.objects.create(tournament=self.tournament, foreign_name="Visiting FC")
        reject_registration(registration, reviewed_by=reviewer)

        with self.assertRaises(RegistrationDecisionError):
            reject_registration(registration, reviewed_by=reviewer)


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "testserver"])
class TournamentRegistrationViewTests(TournamentsTestCase):
    """tournaments:register -- the public, unauthenticated tournament-entry
    page for a visiting (foreign) team (tournaments.views.TournamentRegistrationView)."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.tournament.status = Tournament.Status.PUBLISHED
        cls.tournament.save()

    def setUp(self):
        # waffle caches Flag lookups outside the DB transaction each test
        # rolls back -- see management/tests.py's own FeatureGatedSectionsTests
        # for the same reasoning. The "tournaments" flag row itself already
        # exists (seeded by 0002_create_tournaments_flag), disabled
        # everywhere -- get_or_create finds it rather than colliding on name.
        cache.clear()
        self.addCleanup(cache.clear)
        get_waffle_flag_model().objects.get_or_create(name="tournaments")[0].clubs.add(self.club)

    def _url(self):
        return reverse("tournaments:register", kwargs={"tournament_slug": self.tournament.slug})

    def formset_management(self, total=1, prefix="players"):
        return {f"{prefix}-TOTAL_FORMS": str(total), f"{prefix}-INITIAL_FORMS": "0", f"{prefix}-MIN_NUM_FORMS": "0", f"{prefix}-MAX_NUM_FORMS": "1000"}

    def player_data(self, index=0, prefix="players", **overrides):
        data = {f"{prefix}-{index}-first_name": "Sam", f"{prefix}-{index}-last_name": "Striker", f"{prefix}-{index}-jersey_number": "9"}
        data.update({f"{prefix}-{index}-{key}": value for key, value in overrides.items()})
        return data

    def contact_data(self, **overrides):
        data = {"foreign_name": "Visiting FC", "foreign_contact_name": "Casey Coach", "foreign_contact_email": "casey@example.com", "foreign_contact_phone": ""}
        data.update(overrides)
        return data

    def test_get_renders_the_form(self):
        response = self.client.get(self._url(), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Roster")

    def test_post_with_valid_data_creates_a_pending_registration_and_players(self):
        data = self.contact_data() | self.formset_management() | self.player_data()

        response = self.client.post(self._url(), data, HTTP_HOST="ajax-united.rosterchief.app")

        registration = TournamentRegistration.objects.get()
        self.assertEqual(registration.tournament, self.tournament)
        self.assertEqual(registration.foreign_name, "Visiting FC")
        self.assertEqual(registration.foreign_contact_email, "casey@example.com")
        self.assertEqual(registration.status, TournamentRegistration.Status.PENDING)
        self.assertRedirects(response, reverse("tournaments:registration_status", kwargs={"tournament_slug": self.tournament.slug, "token": registration.status_token}))

        player = registration.players.get()
        self.assertEqual(player.first_name, "Sam")
        self.assertEqual(player.last_name, "Striker")
        self.assertEqual(player.jersey_number, 9)

        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("casey@example.com", mail.outbox[0].to)

    def test_post_with_invalid_data_rerenders_with_errors_and_creates_nothing(self):
        data = self.contact_data(foreign_name="") | self.formset_management() | self.player_data()

        response = self.client.post(self._url(), data, HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(TournamentRegistration.objects.exists())
        self.assertEqual(len(mail.outbox), 0)

    def test_post_with_no_players_rerenders_with_errors_and_creates_nothing(self):
        data = self.contact_data() | self.formset_management()

        response = self.client.post(self._url(), data, HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(TournamentRegistration.objects.exists())

    def test_a_tournament_in_progress_404s(self):
        self.tournament.status = Tournament.Status.IN_PROGRESS
        self.tournament.save()

        response = self.client.get(self._url(), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.status_code, 404)

    def test_a_cancelled_tournament_404s(self):
        self.tournament.status = Tournament.Status.CANCELLED
        self.tournament.save()

        response = self.client.get(self._url(), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.status_code, 404)

    def test_a_draft_tournament_still_accepts_entries(self):
        self.tournament.status = Tournament.Status.DRAFT
        self.tournament.save()

        response = self.client.get(self._url(), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.status_code, 200)

    def test_an_unknown_tournament_slug_404s(self):
        response = self.client.get(reverse("tournaments:register", kwargs={"tournament_slug": "no-such-tournament"}), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.status_code, 404)

    def test_404s_when_the_feature_flag_is_off(self):
        get_waffle_flag_model().objects.get(name="tournaments").clubs.remove(self.club)
        cache.clear()

        response = self.client.get(self._url(), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.status_code, 404)

    def test_closed_registration_shows_a_note_not_a_404(self):
        self.tournament.registration_open = False
        self.tournament.save()

        response = self.client.get(self._url(), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Registration is closed")
        self.assertNotContains(response, "Roster")

    def test_closed_registration_rejects_a_post_without_creating_anything(self):
        self.tournament.registration_open = False
        self.tournament.save()
        data = self.contact_data() | self.formset_management() | self.player_data()

        response = self.client.post(self._url(), data, HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(TournamentRegistration.objects.exists())


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "testserver"])
class TournamentRegistrationStatusViewTests(TournamentsTestCase):
    """tournaments:registration_status -- the token-gated status page
    (tournaments.views.TournamentRegistrationStatusView), roster edits
    included while the registration is still pending."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.registration = TournamentRegistration.objects.create(tournament=cls.tournament, foreign_name="Visiting FC", foreign_contact_email="casey@example.com")
        cls.player = TournamentRegistrationPlayer.objects.create(registration=cls.registration, first_name="Sam", last_name="Striker", jersey_number=9)

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        get_waffle_flag_model().objects.get_or_create(name="tournaments")[0].clubs.add(self.club)

    def _url(self, token=None):
        return reverse("tournaments:registration_status", kwargs={"tournament_slug": self.tournament.slug, "token": token or self.registration.status_token})

    def formset_management(self, total, prefix="players"):
        return {f"{prefix}-TOTAL_FORMS": str(total), f"{prefix}-INITIAL_FORMS": "0", f"{prefix}-MIN_NUM_FORMS": "0", f"{prefix}-MAX_NUM_FORMS": "1000"}

    def test_shows_pending_status(self):
        response = self.client.get(self._url(), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Pending review")

    def test_shows_approved_status(self):
        self.registration.status = TournamentRegistration.Status.APPROVED
        self.registration.save()

        response = self.client.get(self._url(), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertContains(response, "Approved")

    def test_shows_rejected_status_with_reason(self):
        self.registration.status = TournamentRegistration.Status.REJECTED
        self.registration.rejection_reason = "Tournament is full."
        self.registration.save()

        response = self.client.get(self._url(), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertContains(response, "Not accepted")
        self.assertContains(response, "Tournament is full.")

    def test_an_unknown_token_404s(self):
        response = self.client.get(self._url(token="not-a-real-token"), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.status_code, 404)

    def test_404s_when_the_feature_flag_is_off(self):
        get_waffle_flag_model().objects.get(name="tournaments").clubs.remove(self.club)
        cache.clear()

        response = self.client.get(self._url(), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.status_code, 404)

    def test_pending_registration_shows_an_editable_roster_form(self):
        response = self.client.get(self._url(), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertContains(response, "Save roster")
        self.assertContains(response, "Sam")

    def test_decided_registration_shows_a_read_only_roster(self):
        self.registration.status = TournamentRegistration.Status.APPROVED
        self.registration.save()

        response = self.client.get(self._url(), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertNotContains(response, "Save roster")
        self.assertContains(response, "Sam")

    def test_updating_the_roster_while_pending_replaces_the_players(self):
        data = self.formset_management(total=1) | {"players-0-first_name": "Robin", "players-0-last_name": "Newcomer", "players-0-jersey_number": "11"}

        response = self.client.post(self._url(), data, HTTP_HOST="ajax-united.rosterchief.app")

        self.assertRedirects(response, self._url())
        self.assertEqual(self.registration.players.count(), 1)
        player = self.registration.players.get()
        self.assertEqual(player.first_name, "Robin")
        self.assertEqual(player.jersey_number, 11)

    def test_updating_the_roster_with_no_players_rerenders_with_an_error(self):
        data = self.formset_management(total=0)

        response = self.client.post(self._url(), data, HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.registration.players.count(), 1)  # unchanged

    def test_cannot_update_the_roster_once_approved(self):
        self.registration.status = TournamentRegistration.Status.APPROVED
        self.registration.save()
        data = self.formset_management(total=1) | {"players-0-first_name": "Robin", "players-0-last_name": "Newcomer", "players-0-jersey_number": "11"}

        response = self.client.post(self._url(), data, HTTP_HOST="ajax-united.rosterchief.app")

        self.assertRedirects(response, self._url())
        self.assertEqual(self.registration.players.get(), self.player)  # untouched


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


class GenerateStageMatchesTests(TournamentsTestCase):
    def make_teams(self, count):
        return [TournamentTeam.objects.create(club=self.club, tournament=self.tournament, foreign_name=f"Team {i}", seed=i) for i in range(1, count + 1)]

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


class ResolveMatchSlotsTests(TournamentsTestCase):
    def test_completing_a_match_advances_the_winner(self):
        teams = self.make_teams = [TournamentTeam.objects.create(club=self.club, tournament=self.tournament, foreign_name=f"T{i}", seed=i) for i in range(1, 5)]
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
        teams = [TournamentTeam.objects.create(club=self.club, tournament=self.tournament, foreign_name=f"T{i}", seed=i) for i in range(1, 5)]
        stage = TournamentStage.objects.create(tournament=self.tournament, name="KO", format=TournamentStage.Format.SINGLE_ELIM)
        generate_stage_matches(stage, entries=teams)
        semi = Match.objects.get(stage=stage, round_number=1, sequence=0)

        self.assertEqual(resolve_match_slots(semi), [])

    def test_group_completion_resolves_a_crossover_slot(self):
        teams = [TournamentTeam.objects.create(club=self.club, tournament=self.tournament, foreign_name=f"T{i}", seed=i) for i in range(1, 3)]
        group_stage = TournamentStage.objects.create(tournament=self.tournament, name="Groups", format=TournamentStage.Format.GROUPS, sequence=0, config={"num_groups": 1})
        generate_stage_matches(group_stage, entries=teams)
        group = group_stage.groups.get()
        knockout_stage = TournamentStage.objects.create(tournament=self.tournament, name="Final", format=TournamentStage.Format.CUSTOM, sequence=1)
        crossover = Match.objects.create(stage=knockout_stage, round_number=1, home_source_group=group, home_source_rank=1)

        only_match = group.matches.get()
        only_match.score_home, only_match.score_away, only_match.status = 2, 0, Match.Status.COMPLETE
        only_match.save()
        resolve_match_slots(only_match)

        crossover.refresh_from_db()
        self.assertEqual(crossover.home_team_id, only_match.home_team_id)


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


class AllocateScheduleTests(TournamentsTestCase):
    def test_raises_without_any_fields(self):
        teams = [TournamentTeam.objects.create(club=self.club, tournament=self.tournament, foreign_name=f"T{i}", seed=i) for i in range(1, 5)]
        stage = TournamentStage.objects.create(tournament=self.tournament, name="RR", format=TournamentStage.Format.ROUND_ROBIN)
        generate_stage_matches(stage, entries=teams)

        with self.assertRaises(SchedulingError):
            allocate_schedule(self.tournament)

    def test_no_two_matches_share_a_field_and_overlap(self):
        teams = [TournamentTeam.objects.create(club=self.club, tournament=self.tournament, foreign_name=f"T{i}", seed=i) for i in range(1, 9)]
        TournamentField.objects.create(tournament=self.tournament, name="Field 1", availability=[{"date": "2026-09-19", "start": "09:00", "end": "20:00"}])
        stage = TournamentStage.objects.create(tournament=self.tournament, name="RR", format=TournamentStage.Format.ROUND_ROBIN)
        generate_stage_matches(stage, entries=teams)

        allocate_schedule(self.tournament)

        by_field = list(Match.objects.filter(stage=stage).order_by("scheduled_start"))
        for earlier, later in itertools.pairwise(by_field):
            self.assertLessEqual(earlier.scheduled_start + DEFAULT_MATCH_DURATION, later.scheduled_start)

    def test_a_team_never_double_booked_across_fields(self):
        teams = [TournamentTeam.objects.create(club=self.club, tournament=self.tournament, foreign_name=f"T{i}", seed=i) for i in range(1, 5)]
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
        teams = [TournamentTeam.objects.create(club=self.club, tournament=self.tournament, foreign_name=f"T{i}", seed=i) for i in range(1, 9)]
        field = TournamentField.objects.create(tournament=self.tournament, name="Field 1", availability=[{"date": "2026-09-19", "start": "09:00", "end": "20:00"}])
        BreakRule.objects.create(tournament=self.tournament, field=field, trigger_type=BreakRule.TriggerType.GAME_COUNT, trigger_every_n_games=1, gap_minutes=30, replaces_default_gap=True)
        stage = TournamentStage.objects.create(tournament=self.tournament, name="RR", format=TournamentStage.Format.ROUND_ROBIN)
        generate_stage_matches(stage, entries=teams)

        allocate_schedule(self.tournament)

        ordered = list(Match.objects.filter(stage=stage).order_by("scheduled_start"))
        gap = ordered[1].scheduled_start - (ordered[0].scheduled_start + DEFAULT_MATCH_DURATION)
        self.assertEqual(gap, datetime.timedelta(minutes=30))

    def test_tbd_matches_are_left_unscheduled(self):
        teams = [TournamentTeam.objects.create(club=self.club, tournament=self.tournament, foreign_name=f"T{i}", seed=i) for i in range(1, 5)]
        TournamentField.objects.create(tournament=self.tournament, name="Field 1", availability=[{"date": "2026-09-19", "start": "09:00", "end": "20:00"}])
        stage = TournamentStage.objects.create(tournament=self.tournament, name="KO", format=TournamentStage.Format.SINGLE_ELIM)
        generate_stage_matches(stage, entries=teams)

        allocate_schedule(self.tournament)

        final = Match.objects.get(stage=stage, round_number=2)
        self.assertIsNone(final.scheduled_start)


class ValidatePlacementTests(TournamentsTestCase):
    def test_detects_field_double_booking(self):
        field = TournamentField.objects.create(tournament=self.tournament, name="Field 1")
        teams = [TournamentTeam.objects.create(club=self.club, tournament=self.tournament, foreign_name=f"T{i}") for i in range(1, 5)]
        stage = TournamentStage.objects.create(tournament=self.tournament, name="RR", format=TournamentStage.Format.CUSTOM)
        start = timezone.now() + datetime.timedelta(days=1)
        existing = Match.objects.create(stage=stage, home_team=teams[0], away_team=teams[1], field=field, scheduled_start=start)
        candidate = Match.objects.create(stage=stage, home_team=teams[2], away_team=teams[3])

        violations = validate_placement(candidate, field, start, start + DEFAULT_MATCH_DURATION)

        self.assertTrue(violations)
        existing.delete()

    def test_no_violations_for_a_clean_placement(self):
        field = TournamentField.objects.create(tournament=self.tournament, name="Field 1")
        teams = [TournamentTeam.objects.create(club=self.club, tournament=self.tournament, foreign_name=f"T{i}") for i in range(1, 3)]
        stage = TournamentStage.objects.create(tournament=self.tournament, name="RR", format=TournamentStage.Format.CUSTOM)
        candidate = Match.objects.create(stage=stage, home_team=teams[0], away_team=teams[1])
        start = timezone.now() + datetime.timedelta(days=1)

        self.assertEqual(validate_placement(candidate, field, start, start + DEFAULT_MATCH_DURATION), [])

    def test_detects_a_team_already_playing_elsewhere(self):
        field_a = TournamentField.objects.create(tournament=self.tournament, name="Field 1")
        field_b = TournamentField.objects.create(tournament=self.tournament, name="Field 2")
        teams = [TournamentTeam.objects.create(club=self.club, tournament=self.tournament, foreign_name=f"T{i}") for i in range(1, 5)]
        stage = TournamentStage.objects.create(tournament=self.tournament, name="RR", format=TournamentStage.Format.CUSTOM)
        start = timezone.now() + datetime.timedelta(days=1)
        Match.objects.create(stage=stage, home_team=teams[0], away_team=teams[1], field=field_a, scheduled_start=start)
        candidate = Match.objects.create(stage=stage, home_team=teams[0], away_team=teams[2])

        violations = validate_placement(candidate, field_b, start, start + DEFAULT_MATCH_DURATION)

        self.assertTrue(violations)

    def test_detects_a_blackout_window_overlap(self):
        field = TournamentField.objects.create(tournament=self.tournament, name="Field 1")
        BreakRule.objects.create(tournament=self.tournament, trigger_type=BreakRule.TriggerType.CLOCK_WINDOW, trigger_window_start=datetime.time(12, 0), trigger_window_end=datetime.time(13, 0))
        teams = [TournamentTeam.objects.create(club=self.club, tournament=self.tournament, foreign_name=f"T{i}") for i in range(1, 3)]
        stage = TournamentStage.objects.create(tournament=self.tournament, name="RR", format=TournamentStage.Format.CUSTOM)
        candidate = Match.objects.create(stage=stage, home_team=teams[0], away_team=teams[1])
        start = timezone.make_aware(datetime.datetime(2026, 9, 19, 12, 15))

        violations = validate_placement(candidate, field, start, start + DEFAULT_MATCH_DURATION)

        self.assertTrue(violations)


class ComputeStandingsTests(TournamentsTestCase):
    def make_group_stage(self, count):
        teams = [TournamentTeam.objects.create(club=self.club, tournament=self.tournament, foreign_name=f"T{i}", seed=i) for i in range(1, count + 1)]
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
        team_a, team_b, team_c, team_d, team_e = [TournamentTeam.objects.create(club=self.club, tournament=self.tournament, foreign_name=name, seed=seed) for seed, name in enumerate(["A", "B", "C", "D", "E"], start=1)]
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
        return [TournamentTeam.objects.create(club=self.club, tournament=self.tournament, foreign_name=f"Team {i}", seed=i) for i in range(1, count + 1)]

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
        return [TournamentTeam.objects.create(club=self.club, tournament=self.tournament, foreign_name=f"Team {i}", seed=i) for i in range(1, count + 1)]

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
        return [TournamentTeam.objects.create(club=self.club, tournament=self.tournament, foreign_name=f"Team {i}", seed=i) for i in range(1, count + 1)]

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
        teams = [TournamentTeam.objects.create(club=self.club, tournament=self.tournament, foreign_name=f"T{i}", seed=i) for i in range(1, 17)]
        TournamentField.objects.create(tournament=self.tournament, name="Field 1", availability=[{"date": "2026-09-19", "start": "09:00", "end": "20:00"}])
        stage = TournamentStage.objects.create(tournament=self.tournament, name="RR", format=TournamentStage.Format.ROUND_ROBIN)
        generate_stage_matches(stage, entries=teams)

        allocate_schedule(self.tournament)

        ordered = list(Match.objects.filter(stage=stage).order_by("scheduled_start"))
        self.assertEqual(len(ordered), 120)  # 16 choose 2
        for earlier, later in itertools.pairwise(ordered):
            self.assertLessEqual(earlier.scheduled_start + DEFAULT_MATCH_DURATION, later.scheduled_start)

    def test_blackout_spanning_the_whole_working_window_pushes_every_match_past_it(self):
        teams = [TournamentTeam.objects.create(club=self.club, tournament=self.tournament, foreign_name=f"T{i}", seed=i) for i in range(1, 5)]
        field = TournamentField.objects.create(tournament=self.tournament, name="Field 1", availability=[{"date": "2026-09-19", "start": "09:00", "end": "20:00"}])
        BreakRule.objects.create(tournament=self.tournament, field=field, trigger_type=BreakRule.TriggerType.CLOCK_WINDOW, trigger_window_start=datetime.time(9, 0), trigger_window_end=datetime.time(20, 0))
        stage = TournamentStage.objects.create(tournament=self.tournament, name="RR", format=TournamentStage.Format.ROUND_ROBIN)
        generate_stage_matches(stage, entries=teams)

        allocate_schedule(self.tournament)

        for match in Match.objects.filter(stage=stage):
            local_time = timezone.localtime(match.scheduled_start).time()
            self.assertFalse(datetime.time(9, 0) <= local_time < datetime.time(20, 0))


class TournamentAdminActionsTests(TournamentsTestCase):
    """The Django admin actions that make the scheduling engine clickable --
    called directly on the ModelAdmin instance (RequestFactory + a fallback
    messages storage), the same message-capturing setup as
    controlpanel.tests.NotifyTests' own request()."""

    def make_teams(self, count):
        return [TournamentTeam.objects.create(club=self.club, tournament=self.tournament, foreign_name=f"T{i}", seed=i) for i in range(1, count + 1)]

    def make_request(self):
        request = RequestFactory().get("/")
        request.session = {}
        storage = FallbackStorage(request)
        request._messages = storage
        return request, storage

    def test_generate_matches_action_creates_matches_and_shows_success(self):
        self.make_teams(4)
        stage = TournamentStage.objects.create(tournament=self.tournament, name="RR", format=TournamentStage.Format.ROUND_ROBIN)
        model_admin = TournamentStageAdmin(TournamentStage, admin.site)
        request, storage = self.make_request()

        model_admin.generate_matches(request, TournamentStage.objects.filter(pk=stage.pk))

        self.assertEqual(stage.matches.count(), 6)  # 4 choose 2
        [message] = list(storage)
        self.assertEqual(message.level, messages.SUCCESS)

    def test_generate_matches_action_shows_error_and_creates_nothing_when_already_generated(self):
        teams = self.make_teams(4)
        stage = TournamentStage.objects.create(tournament=self.tournament, name="RR", format=TournamentStage.Format.ROUND_ROBIN)
        generate_stage_matches(stage, entries=teams)
        existing_count = stage.matches.count()
        model_admin = TournamentStageAdmin(TournamentStage, admin.site)
        request, storage = self.make_request()

        model_admin.generate_matches(request, TournamentStage.objects.filter(pk=stage.pk))

        self.assertEqual(stage.matches.count(), existing_count)
        [message] = list(storage)
        self.assertEqual(message.level, messages.ERROR)

    def test_allocate_schedule_action_schedules_matches(self):
        teams = self.make_teams(4)
        TournamentField.objects.create(tournament=self.tournament, name="Field 1", availability=[{"date": "2026-09-19", "start": "09:00", "end": "20:00"}])
        stage = TournamentStage.objects.create(tournament=self.tournament, name="RR", format=TournamentStage.Format.ROUND_ROBIN)
        generate_stage_matches(stage, entries=teams)
        model_admin = TournamentAdmin(Tournament, admin.site)
        request, storage = self.make_request()

        model_admin.allocate_schedule_action(request, Tournament.objects.filter(pk=self.tournament.pk))

        self.assertTrue(Match.objects.filter(stage=stage, scheduled_start__isnull=False).exists())
        [message] = list(storage)
        self.assertEqual(message.level, messages.SUCCESS)

    def test_allocate_schedule_action_shows_error_when_no_fields_yet(self):
        teams = self.make_teams(4)
        stage = TournamentStage.objects.create(tournament=self.tournament, name="RR", format=TournamentStage.Format.ROUND_ROBIN)
        generate_stage_matches(stage, entries=teams)
        model_admin = TournamentAdmin(Tournament, admin.site)
        request, storage = self.make_request()

        model_admin.allocate_schedule_action(request, Tournament.objects.filter(pk=self.tournament.pk))

        self.assertFalse(Match.objects.filter(stage=stage, scheduled_start__isnull=False).exists())
        [message] = list(storage)
        self.assertEqual(message.level, messages.ERROR)


class RescheduleMatchTests(TournamentsTestCase):
    def test_valid_reschedule_saves_field_and_time(self):
        teams = [TournamentTeam.objects.create(club=self.club, tournament=self.tournament, foreign_name=f"T{i}") for i in range(1, 3)]
        field = TournamentField.objects.create(tournament=self.tournament, name="Field 1")
        stage = TournamentStage.objects.create(tournament=self.tournament, name="RR", format=TournamentStage.Format.CUSTOM)
        match = Match.objects.create(stage=stage, home_team=teams[0], away_team=teams[1])
        start = timezone.now() + datetime.timedelta(days=1)

        reschedule_match(match, field=field, scheduled_start=start)

        match.refresh_from_db()
        self.assertEqual(match.field, field)
        self.assertEqual(match.scheduled_start, start)

    def test_conflicting_reschedule_raises_and_does_not_save(self):
        teams = [TournamentTeam.objects.create(club=self.club, tournament=self.tournament, foreign_name=f"T{i}") for i in range(1, 5)]
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
        teams = [TournamentTeam.objects.create(club=self.club, tournament=self.tournament, foreign_name=f"T{i}") for i in range(1, 3)]
        stage = TournamentStage.objects.create(tournament=self.tournament, name="RR", format=TournamentStage.Format.CUSTOM)
        match = Match.objects.create(stage=stage, home_team=teams[0], away_team=teams[1])

        record_result(match, score_home=3, score_away=1)

        match.refresh_from_db()
        self.assertEqual(match.status, Match.Status.COMPLETE)
        self.assertEqual(match.score_home, 3)
        self.assertEqual(match.score_away, 1)

    def test_recording_a_result_cascades_to_a_dependent_match(self):
        teams = [TournamentTeam.objects.create(club=self.club, tournament=self.tournament, foreign_name=f"T{i}", seed=i) for i in range(1, 5)]
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


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "testserver"])
class TournamentListViewTests(TournamentsTestCase):
    """tournaments:list -- the public tournament list (tournaments.views.TournamentListView)."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        get_waffle_flag_model().objects.get_or_create(name="tournaments")[0].clubs.add(self.club)

    def _url(self):
        return reverse("tournaments:list")

    def test_only_publicly_listable_statuses_show(self):
        self.tournament.status = Tournament.Status.DRAFT
        self.tournament.save()
        cancelled = Tournament.objects.create(club=self.club, name="Cancelled Cup", season=self.season, start_date=datetime.date(2026, 9, 1), end_date=datetime.date(2026, 9, 2), status=Tournament.Status.CANCELLED)
        published = Tournament.objects.create(club=self.club, name="Autumn Cup", season=self.season, start_date=datetime.date(2026, 10, 1), end_date=datetime.date(2026, 10, 2), status=Tournament.Status.PUBLISHED)

        response = self.client.get(self._url(), HTTP_HOST="ajax-united.rosterchief.app")

        tournaments = list(response.context["tournaments"])
        self.assertIn(published, tournaments)
        self.assertNotIn(self.tournament, tournaments)
        self.assertNotIn(cancelled, tournaments)

    def test_splits_upcoming_and_past(self):
        self.tournament.status = Tournament.Status.PUBLISHED
        self.tournament.save()
        completed = Tournament.objects.create(club=self.club, name="Spring Cup", season=self.season, start_date=datetime.date(2026, 4, 1), end_date=datetime.date(2026, 4, 2), status=Tournament.Status.COMPLETED)

        response = self.client.get(self._url(), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertIn(self.tournament, response.context["upcoming_tournaments"])
        self.assertIn(completed, response.context["past_tournaments"])
        self.assertNotIn(completed, response.context["upcoming_tournaments"])

    def test_scoped_to_the_current_club(self):
        self.tournament.status = Tournament.Status.PUBLISHED
        self.tournament.save()
        other_club = Club.objects.create(name="Rival FC", slug="rival-fc")
        Tournament.objects.create(club=other_club, name="Rival Cup", season=make_season(other_club), start_date=datetime.date(2026, 9, 1), end_date=datetime.date(2026, 9, 2), status=Tournament.Status.PUBLISHED)

        response = self.client.get(self._url(), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(list(response.context["tournaments"]), [self.tournament])

    def test_404s_when_the_feature_flag_is_off(self):
        get_waffle_flag_model().objects.get(name="tournaments").clubs.remove(self.club)
        cache.clear()

        response = self.client.get(self._url(), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.status_code, 404)


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "testserver"])
class TournamentDetailViewTests(TournamentsTestCase):
    """tournaments:detail -- the tournament "home" page (tournaments.views.TournamentDetailView)."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        get_waffle_flag_model().objects.get_or_create(name="tournaments")[0].clubs.add(self.club)

    def _url(self, tournament=None):
        return reverse("tournaments:detail", kwargs={"slug": (tournament or self.tournament).slug})

    def test_renders_for_a_published_tournament(self):
        self.tournament.status = Tournament.Status.PUBLISHED
        self.tournament.save()

        response = self.client.get(self._url(), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.tournament.name)

    def test_a_draft_tournament_still_resolves_by_direct_link(self):
        # DRAFT isn't on the public list, but (like registration itself) a
        # direct link to it isn't a secret -- see TournamentDetailView's own
        # docstring for the reasoning.
        response = self.client.get(self._url(), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.status_code, 200)

    def test_404s_for_a_tournament_in_another_club(self):
        other_club = Club.objects.create(name="Rival FC", slug="rival-fc")
        other_tournament = Tournament.objects.create(club=other_club, name="Rival Cup", season=make_season(other_club), start_date=datetime.date(2026, 9, 1), end_date=datetime.date(2026, 9, 2), status=Tournament.Status.PUBLISHED)

        response = self.client.get(self._url(other_tournament), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.status_code, 404)

    def test_404s_when_the_feature_flag_is_off(self):
        get_waffle_flag_model().objects.get(name="tournaments").clubs.remove(self.club)
        cache.clear()

        response = self.client.get(self._url(), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.status_code, 404)

    def test_shows_a_register_link_when_open(self):
        response = self.client.get(self._url(), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertContains(response, reverse("tournaments:register", kwargs={"tournament_slug": self.tournament.slug}))

    def test_hides_the_register_link_and_shows_a_note_when_closed(self):
        self.tournament.registration_open = False
        self.tournament.save()

        response = self.client.get(self._url(), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertNotContains(response, reverse("tournaments:register", kwargs={"tournament_slug": self.tournament.slug}))
        self.assertContains(response, "Registration is closed")

    def test_hides_the_register_link_once_in_progress(self):
        self.tournament.status = Tournament.Status.IN_PROGRESS
        self.tournament.save()

        response = self.client.get(self._url(), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertNotContains(response, reverse("tournaments:register", kwargs={"tournament_slug": self.tournament.slug}))


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "testserver"])
class TournamentScheduleViewTests(TournamentsTestCase):
    """tournaments:schedule -- the full read-only schedule (tournaments.views.TournamentScheduleView)."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        get_waffle_flag_model().objects.get_or_create(name="tournaments")[0].clubs.add(self.club)

    def _url(self, tournament=None):
        return reverse("tournaments:schedule", kwargs={"slug": (tournament or self.tournament).slug})

    def test_groups_matches_by_stage_then_round(self):
        teams = [TournamentTeam.objects.create(club=self.club, tournament=self.tournament, foreign_name=f"T{i}", seed=i) for i in range(1, 5)]
        stage = TournamentStage.objects.create(tournament=self.tournament, name="Round Robin", format=TournamentStage.Format.ROUND_ROBIN)
        generate_stage_matches(stage, entries=teams)

        response = self.client.get(self._url(), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.status_code, 200)
        stages = response.context["stages"]
        self.assertEqual(len(stages), 1)
        self.assertEqual(stages[0]["stage"], stage)
        rounds = stages[0]["rounds"]
        self.assertEqual(len(rounds), 3)  # 4 teams round robin -> 3 rounds
        for round_row in rounds:
            self.assertEqual(len(round_row["matches"]), 2)  # 2 matches per round
        self.assertEqual(sum(len(round_row["matches"]) for round_row in rounds), 6)  # 4 choose 2

    def test_404s_for_a_tournament_in_another_club(self):
        other_club = Club.objects.create(name="Rival FC", slug="rival-fc")
        other_tournament = Tournament.objects.create(club=other_club, name="Rival Cup", season=make_season(other_club), start_date=datetime.date(2026, 9, 1), end_date=datetime.date(2026, 9, 2))

        response = self.client.get(self._url(other_tournament), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.status_code, 404)

    def test_404s_when_the_feature_flag_is_off(self):
        get_waffle_flag_model().objects.get(name="tournaments").clubs.remove(self.club)
        cache.clear()

        response = self.client.get(self._url(), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.status_code, 404)


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "testserver"])
class TournamentScheduleRowsViewTests(TournamentsTestCase):
    """tournaments:schedule_rows -- the htmx-polled partial (tournaments.views.TournamentScheduleRowsView)."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        get_waffle_flag_model().objects.get_or_create(name="tournaments")[0].clubs.add(self.club)

    def test_returns_only_the_rows_partial(self):
        teams = [TournamentTeam.objects.create(club=self.club, tournament=self.tournament, foreign_name=f"T{i}", seed=i) for i in range(1, 3)]
        stage = TournamentStage.objects.create(tournament=self.tournament, name="RR", format=TournamentStage.Format.ROUND_ROBIN)
        generate_stage_matches(stage, entries=teams)

        response = self.client.get(reverse("tournaments:schedule_rows", kwargs={"slug": self.tournament.slug}), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "tournaments/_schedule_rows.html")
        self.assertTemplateNotUsed(response, "registration/_base.html")
        self.assertNotContains(response, "<html")


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "testserver"])
class TournamentStandingsViewTests(TournamentsTestCase):
    """tournaments:standings -- stage/group standings plus bracket progression
    (tournaments.views.TournamentStandingsView)."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        get_waffle_flag_model().objects.get_or_create(name="tournaments")[0].clubs.add(self.club)

    def _url(self, tournament=None):
        return reverse("tournaments:standings", kwargs={"slug": (tournament or self.tournament).slug})

    def test_shows_the_right_ranked_order(self):
        teams = [TournamentTeam.objects.create(club=self.club, tournament=self.tournament, foreign_name=f"T{i}", seed=i) for i in range(1, 4)]
        stage = TournamentStage.objects.create(tournament=self.tournament, name="RR", format=TournamentStage.Format.ROUND_ROBIN)
        generate_stage_matches(stage, entries=teams)
        for match in stage.matches.all():
            winner_is_home = match.home_team.seed < match.away_team.seed
            match.score_home, match.score_away = (2, 0) if winner_is_home else (0, 2)
            match.status = Match.Status.COMPLETE
            match.save()

        response = self.client.get(self._url(), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.status_code, 200)
        rows = response.context["standings_stages"][0]["tables"][0]["rows"]
        self.assertEqual([row["team"] for row in rows], [row["team"] for row in compute_standings(stage)])  # sanity: same order as the raw call
        self.assertEqual(rows[0]["team"], teams[0])
        self.assertEqual(rows[0]["rank"], 1)
        self.assertEqual(rows[-1]["team"], teams[-1])

    def test_groups_stage_gets_one_table_per_group(self):
        teams = [TournamentTeam.objects.create(club=self.club, tournament=self.tournament, foreign_name=f"T{i}", seed=i) for i in range(1, 7)]
        stage = TournamentStage.objects.create(tournament=self.tournament, name="Groups", format=TournamentStage.Format.GROUPS, config={"num_groups": 2})
        generate_stage_matches(stage, entries=teams)

        response = self.client.get(self._url(), HTTP_HOST="ajax-united.rosterchief.app")

        tables = response.context["standings_stages"][0]["tables"]
        self.assertEqual(len(tables), 2)
        self.assertTrue(all(table["group"] is not None for table in tables))

    def test_single_elim_stage_is_a_bracket_not_a_table(self):
        teams = [TournamentTeam.objects.create(club=self.club, tournament=self.tournament, foreign_name=f"T{i}", seed=i) for i in range(1, 5)]
        stage = TournamentStage.objects.create(tournament=self.tournament, name="KO", format=TournamentStage.Format.SINGLE_ELIM)
        generate_stage_matches(stage, entries=teams)

        response = self.client.get(self._url(), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.context["standings_stages"], [])
        bracket_stages = response.context["bracket_stages"]
        self.assertEqual(len(bracket_stages), 1)
        self.assertEqual(len(bracket_stages[0]["rounds"]), 2)  # semis, final

    def test_404s_for_a_tournament_in_another_club(self):
        other_club = Club.objects.create(name="Rival FC", slug="rival-fc")
        other_tournament = Tournament.objects.create(club=other_club, name="Rival Cup", season=make_season(other_club), start_date=datetime.date(2026, 9, 1), end_date=datetime.date(2026, 9, 2))

        response = self.client.get(self._url(other_tournament), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.status_code, 404)

    def test_404s_when_the_feature_flag_is_off(self):
        get_waffle_flag_model().objects.get(name="tournaments").clubs.remove(self.club)
        cache.clear()

        response = self.client.get(self._url(), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.status_code, 404)


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "testserver"])
class TournamentStandingsRowsViewTests(TournamentsTestCase):
    """tournaments:standings_rows -- the htmx-polled partial (tournaments.views.TournamentStandingsRowsView)."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        get_waffle_flag_model().objects.get_or_create(name="tournaments")[0].clubs.add(self.club)

    def test_returns_only_the_rows_partial(self):
        teams = [TournamentTeam.objects.create(club=self.club, tournament=self.tournament, foreign_name=f"T{i}", seed=i) for i in range(1, 3)]
        stage = TournamentStage.objects.create(tournament=self.tournament, name="RR", format=TournamentStage.Format.ROUND_ROBIN)
        generate_stage_matches(stage, entries=teams)

        response = self.client.get(reverse("tournaments:standings_rows", kwargs={"slug": self.tournament.slug}), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "tournaments/_standings_rows.html")
        self.assertTemplateNotUsed(response, "registration/_base.html")
        self.assertNotContains(response, "<html")


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "testserver"])
class TournamentTeamScheduleViewTests(TournamentsTestCase):
    """tournaments:team_schedule -- one TournamentTeam's own matches only
    (tournaments.views.TournamentTeamScheduleView)."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        get_waffle_flag_model().objects.get_or_create(name="tournaments")[0].clubs.add(self.club)

    def _url(self, team, tournament=None):
        return reverse("tournaments:team_schedule", kwargs={"slug": (tournament or self.tournament).slug, "team_slug": team.slug})

    def test_only_shows_this_teams_own_matches(self):
        team_a, team_b, team_c = [TournamentTeam.objects.create(club=self.club, tournament=self.tournament, foreign_name=name, seed=seed) for seed, name in enumerate(["A", "B", "C"], start=1)]
        stage = TournamentStage.objects.create(tournament=self.tournament, name="RR", format=TournamentStage.Format.ROUND_ROBIN)
        generate_stage_matches(stage, entries=[team_a, team_b, team_c])

        response = self.client.get(self._url(team_a), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.status_code, 200)
        matches = response.context["matches"]
        self.assertEqual(len(matches), 2)  # A plays B and C, not B-vs-C
        for match in matches:
            self.assertIn(team_a.id, (match.home_team_id, match.away_team_id))

    def test_404_for_an_unknown_team_slug(self):
        response = self.client.get(reverse("tournaments:team_schedule", kwargs={"slug": self.tournament.slug, "team_slug": "no-such-team"}), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.status_code, 404)

    def test_404s_for_a_team_in_another_club(self):
        other_club = Club.objects.create(name="Rival FC", slug="rival-fc")
        other_tournament = Tournament.objects.create(club=other_club, name="Rival Cup", season=make_season(other_club), start_date=datetime.date(2026, 9, 1), end_date=datetime.date(2026, 9, 2))
        other_team = TournamentTeam.objects.create(club=other_club, tournament=other_tournament, foreign_name="Rival Team")

        response = self.client.get(self._url(other_team, tournament=other_tournament), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.status_code, 404)

    def test_404s_when_the_feature_flag_is_off(self):
        team = TournamentTeam.objects.create(club=self.club, tournament=self.tournament, foreign_name="Solo FC")
        get_waffle_flag_model().objects.get(name="tournaments").clubs.remove(self.club)
        cache.clear()

        response = self.client.get(self._url(team), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.status_code, 404)


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


class AssignDressingRoomServiceTests(TournamentsTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.room = DressingRoom.objects.create(club=cls.club, name="Room A")
        cls.team_a = TournamentTeam.objects.create(club=cls.club, tournament=cls.tournament, foreign_name="Team A")
        cls.team_b = TournamentTeam.objects.create(club=cls.club, tournament=cls.tournament, foreign_name="Team B")

    def test_assigns_a_free_room(self):
        assignment = assign_dressing_room(self.room, self.team_a)

        self.assertEqual(assignment.dressing_room, self.room)
        self.assertEqual(assignment.tournament_team, self.team_a)

    def test_cannot_assign_the_same_team_to_the_same_room_twice(self):
        assign_dressing_room(self.room, self.team_a)

        with self.assertRaises(DressingRoomAssignmentError):
            assign_dressing_room(self.room, self.team_a)

    def test_whole_tournament_booking_blocks_any_other_team(self):
        assign_dressing_room(self.room, self.team_a)  # no window -- whole tournament

        with self.assertRaises(DressingRoomAssignmentError):
            assign_dressing_room(self.room, self.team_b)

    def test_non_overlapping_time_windows_are_both_allowed(self):
        start = timezone.now() + datetime.timedelta(days=1)
        assign_dressing_room(self.room, self.team_a, assigned_from=start, assigned_until=start + datetime.timedelta(hours=1))

        assign_dressing_room(self.room, self.team_b, assigned_from=start + datetime.timedelta(hours=1), assigned_until=start + datetime.timedelta(hours=2))  # should not raise

        self.assertEqual(self.room.assignments.count(), 2)

    def test_overlapping_time_windows_conflict(self):
        start = timezone.now() + datetime.timedelta(days=1)
        assign_dressing_room(self.room, self.team_a, assigned_from=start, assigned_until=start + datetime.timedelta(hours=2))

        with self.assertRaises(DressingRoomAssignmentError):
            assign_dressing_room(self.room, self.team_b, assigned_from=start + datetime.timedelta(hours=1), assigned_until=start + datetime.timedelta(hours=3))

    def test_release_removes_the_assignment(self):
        assignment = assign_dressing_room(self.room, self.team_a)

        release_dressing_room(assignment)

        self.assertEqual(self.room.assignments.count(), 0)
