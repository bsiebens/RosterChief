import datetime

from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db.models import ProtectedError
from django.test import TestCase

from club.models import Club, Season
from members.models import Member

from .models import Position, StaffAssignment, Team, TeamMembership


class TeamsTestCase(TestCase):
    def setUp(self):
        self.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        self.season = Season.objects.create(
            club=self.club,
            start_date=datetime.date(2026, 8, 1),
            end_date=datetime.date(2027, 5, 31),
        )
        self.team = Team.objects.create(club=self.club, name="First Team", short_name="1st")
        self.forward = Position.objects.create(club=self.club, name="Forward", short_name="FW")
        self.coach = Position.objects.create(club=self.club, name="Head Coach", short_name="HC", staff_position=True)
        self.member = Member.objects.create(first_name="Jane", last_name="Doe")


class TeamModelTests(TeamsTestCase):
    def test_str_returns_name(self):
        self.assertEqual(str(self.team), "First Team")

    def test_team_name_is_unique_per_club(self):
        with self.assertRaises(IntegrityError):
            Team.objects.create(club=self.club, name="First Team", short_name="dup")

    def test_same_team_name_allowed_in_other_club(self):
        other = Club.objects.create(name="Rival FC", slug="rival-fc")
        Team.objects.create(club=other, name="First Team", short_name="1st")

        self.assertEqual(Team.objects.filter(name="First Team").count(), 2)


class PositionModelTests(TeamsTestCase):
    def test_str_returns_name(self):
        self.assertEqual(str(self.forward), "Forward")

    def test_defaults(self):
        self.assertFalse(self.forward.staff_position)
        self.assertEqual(self.forward.ordering, 0)

    def test_positions_ordered_by_ordering_then_name(self):
        keeper = Position.objects.create(club=self.club, name="Keeper", short_name="GK", ordering=1)

        ordered = list(Position.objects.filter(club=self.club).values_list("name", flat=True))
        # ordering=0 entries first (alphabetical), then ordering=1.
        self.assertEqual(ordered, ["Forward", "Head Coach", "Keeper"])
        self.assertEqual(keeper.ordering, 1)

    def test_position_name_is_unique_per_club(self):
        with self.assertRaises(IntegrityError):
            Position.objects.create(club=self.club, name="Forward", short_name="dup")

    def test_management_position_must_also_be_a_staff_position(self):
        with self.assertRaises(IntegrityError):
            Position.objects.create(club=self.club, name="Bogus", short_name="BG", staff_position=False, management_position=True)

    def test_management_staff_position_is_allowed(self):
        position = Position.objects.create(club=self.club, name="Manager", short_name="MG", staff_position=True, management_position=True)

        self.assertTrue(position.management_position)


class TeamMembershipModelTests(TeamsTestCase):
    def test_can_create_roster_entry(self):
        entry = TeamMembership.objects.create(team=self.team, member=self.member, season=self.season, position=self.forward, jersey_number=9)

        self.assertEqual(entry.position, self.forward)
        self.assertFalse(entry.is_captain)
        self.assertEqual(str(entry), "First Team - Jane Doe")
        self.assertEqual(list(self.team.roster.all()), [entry])

    def test_member_is_unique_per_team_and_season(self):
        TeamMembership.objects.create(team=self.team, member=self.member, season=self.season, position=self.forward, jersey_number=9)

        with self.assertRaises(IntegrityError):
            TeamMembership.objects.create(team=self.team, member=self.member, season=self.season, position=self.forward, jersey_number=10)

    def test_jersey_number_is_unique_per_team_and_season(self):
        TeamMembership.objects.create(team=self.team, member=self.member, season=self.season, position=self.forward, jersey_number=9)
        other = Member.objects.create(first_name="John", last_name="Smith")

        with self.assertRaises(IntegrityError):
            TeamMembership.objects.create(team=self.team, member=other, season=self.season, position=self.forward, jersey_number=9)

    def test_season_is_protected_while_referenced(self):
        TeamMembership.objects.create(team=self.team, member=self.member, season=self.season, position=self.forward)

        with self.assertRaises(ProtectedError):
            self.season.delete()


class StaffAssignmentModelTests(TeamsTestCase):
    def test_can_assign_staff(self):
        assignment = StaffAssignment.objects.create(team=self.team, member=self.member, season=self.season, position=self.coach)

        self.assertEqual(assignment.position, self.coach)
        self.assertEqual(str(assignment), "First Team - Jane Doe")
        self.assertEqual(list(self.team.staff_assignments.all()), [assignment])

    def test_member_is_unique_per_team_and_season(self):
        StaffAssignment.objects.create(team=self.team, member=self.member, season=self.season, position=self.coach)

        with self.assertRaises(IntegrityError):
            StaffAssignment.objects.create(team=self.team, member=self.member, season=self.season, position=self.coach)

    def test_roster_and_staff_use_separate_reverse_accessors(self):
        TeamMembership.objects.create(team=self.team, member=self.member, season=self.season, position=self.forward)
        coach_member = Member.objects.create(first_name="Coach", last_name="Carter")
        StaffAssignment.objects.create(team=self.team, member=coach_member, season=self.season, position=self.coach)

        self.assertEqual(self.forward.team_memberships.count(), 1)
        self.assertEqual(self.coach.staff_assignments.count(), 1)


class RosterCleanTests(TeamsTestCase):
    def setUp(self):
        super().setUp()
        self.other = Club.objects.create(name="Rival FC", slug="rival-fc")
        self.other_season = Season.objects.create(club=self.other, start_date=datetime.date(2026, 8, 1), end_date=datetime.date(2027, 5, 31))
        self.other_position = Position.objects.create(club=self.other, name="Forward", short_name="FW")
        self.other_coach = Position.objects.create(club=self.other, name="Coach", short_name="C", staff_position=True)

    def test_teammembership_rejects_cross_club_season(self):
        entry = TeamMembership(team=self.team, member=self.member, season=self.other_season, position=self.forward)
        with self.assertRaises(ValidationError) as ctx:
            entry.full_clean()
        self.assertIn("season", ctx.exception.error_dict)

    def test_teammembership_rejects_cross_club_position(self):
        entry = TeamMembership(team=self.team, member=self.member, season=self.season, position=self.other_position)
        with self.assertRaises(ValidationError) as ctx:
            entry.full_clean()
        self.assertIn("position", ctx.exception.error_dict)

    def test_teammembership_accepts_same_club(self):
        TeamMembership(team=self.team, member=self.member, season=self.season, position=self.forward).full_clean()

    def test_staffassignment_rejects_cross_club_season(self):
        assignment = StaffAssignment(team=self.team, member=self.member, season=self.other_season, position=self.coach)
        with self.assertRaises(ValidationError) as ctx:
            assignment.full_clean()
        self.assertIn("season", ctx.exception.error_dict)

    def test_staffassignment_accepts_same_club(self):
        StaffAssignment(team=self.team, member=self.member, season=self.season, position=self.coach).full_clean()
