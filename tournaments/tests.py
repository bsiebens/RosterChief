import datetime

from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone

from events.models import Event, Location
from members.models import Member
from rosterchief.test_support import make_club, make_season
from teams.models import Team

from .models import (
    DressingRoom,
    DressingRoomAssignment,
    Match,
    MatchOfficial,
    MatchReferee,
    Tournament,
    TournamentField,
    TournamentGroup,
    TournamentRegistration,
    TournamentRosterEntry,
    TournamentStage,
    TournamentTask,
    TournamentTaskClaim,
    TournamentTeam,
)


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
