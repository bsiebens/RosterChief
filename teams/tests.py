import datetime

from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db.models import ProtectedError
from django.test import RequestFactory, TestCase
from django.utils import timezone

from club.models import Club, Season
from members.models import Member

from .api import build_roster
from .models import Position, RefereeLevel, RefereeProfile, StaffAssignment, Team, TeamMembership, TeamPhoto


class TeamsTestCase(TestCase):
    # Shared read-only scaffolding for every teams test. The few that delete a fixture
    # (member, team, season) get a per-test copy from setUpTestData and the rows come
    # back with the transaction rollback.
    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        cls.season = Season.objects.create(
            club=cls.club,
            start_date=datetime.date(2026, 8, 1),
            end_date=datetime.date(2027, 5, 31),
        )
        cls.team = Team.objects.create(club=cls.club, name="First Team", short_name="1st")
        cls.forward = Position.objects.create(club=cls.club, name="Forward", short_name="FW")
        cls.coach = Position.objects.create(club=cls.club, name="Head Coach", short_name="HC", staff_position=True)
        cls.member = Member.objects.create(first_name="Jane", last_name="Doe")


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

    def test_referee_management_defaults_to_club(self):
        self.assertEqual(self.team.referee_management, Team.RefereeManagement.CLUB)


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
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.other = Club.objects.create(name="Rival FC", slug="rival-fc")
        cls.other_season = Season.objects.create(club=cls.other, start_date=datetime.date(2026, 8, 1), end_date=datetime.date(2027, 5, 31))
        cls.other_position = Position.objects.create(club=cls.other, name="Forward", short_name="FW")
        cls.other_coach = Position.objects.create(club=cls.other, name="Coach", short_name="C", staff_position=True)

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

    def test_staffassignment_rejects_cross_club_position(self):
        # The StaffAssignment half of test_teammembership_rejects_cross_club_position:
        # clean() validates `position` as well as `season` against the team's club, and
        # nothing exercised that branch -- `other_coach` was sitting unused waiting for it.
        assignment = StaffAssignment(team=self.team, member=self.member, season=self.season, position=self.other_coach)
        with self.assertRaises(ValidationError) as ctx:
            assignment.full_clean()
        self.assertIn("position", ctx.exception.error_dict)

    def test_staffassignment_accepts_same_club(self):
        StaffAssignment(team=self.team, member=self.member, season=self.season, position=self.coach).full_clean()


class TeamPhotoModelTests(TeamsTestCase):
    def test_can_set_a_photo(self):
        photo = TeamPhoto.objects.create(team=self.team, season=self.season, image="clubs/ajax-united/teams/x/26-27/pic.jpg")

        self.assertEqual(str(photo), "First Team - 26-27")
        self.assertEqual(list(self.team.photos.all()), [photo])

    def test_only_one_photo_per_team_and_season(self):
        TeamPhoto.objects.create(team=self.team, season=self.season, image="clubs/ajax-united/teams/x/26-27/pic.jpg")

        with self.assertRaises(IntegrityError):
            TeamPhoto.objects.create(team=self.team, season=self.season, image="clubs/ajax-united/teams/x/26-27/pic2.jpg")

    def test_the_same_team_can_have_a_photo_in_a_different_season(self):
        other_season = Season.objects.create(club=self.club, start_date=datetime.date(2000, 1, 1), end_date=datetime.date(2000, 12, 31))
        TeamPhoto.objects.create(team=self.team, season=self.season, image="clubs/ajax-united/teams/x/26-27/pic.jpg")

        TeamPhoto.objects.create(team=self.team, season=other_season, image="clubs/ajax-united/teams/x/00-00/pic.jpg")

        self.assertEqual(self.team.photos.count(), 2)

    def test_season_is_protected_while_a_photo_references_it(self):
        TeamPhoto.objects.create(team=self.team, season=self.season, image="clubs/ajax-united/teams/x/26-27/pic.jpg")

        with self.assertRaises(ProtectedError):
            self.season.delete()

    def test_deleting_the_team_deletes_its_photos(self):
        TeamPhoto.objects.create(team=self.team, season=self.season, image="clubs/ajax-united/teams/x/26-27/pic.jpg")

        self.team.delete()

        self.assertEqual(TeamPhoto.objects.count(), 0)


class RefereeLevelModelTests(TeamsTestCase):
    def test_str(self):
        level = RefereeLevel.objects.create(club=self.club, name="Regional")
        self.assertEqual(str(level), "Regional")

    def test_name_is_unique_per_club(self):
        RefereeLevel.objects.create(club=self.club, name="Regional")

        with self.assertRaises(IntegrityError):
            RefereeLevel.objects.create(club=self.club, name="Regional")

    def test_same_name_allowed_in_another_club(self):
        other = Club.objects.create(name="Rival FC", slug="rival-fc")
        RefereeLevel.objects.create(club=self.club, name="Regional")

        RefereeLevel.objects.create(club=other, name="Regional")

        self.assertEqual(RefereeLevel.objects.filter(name="Regional").count(), 2)

    def test_teams_starts_empty(self):
        level = RefereeLevel.objects.create(club=self.club, name="Regional")
        self.assertEqual(list(level.teams.all()), [])

    def test_can_qualify_for_multiple_teams(self):
        other_team = Team.objects.create(club=self.club, name="Second Team", short_name="2nd")
        level = RefereeLevel.objects.create(club=self.club, name="Regional")

        level.teams.set([self.team, other_team])

        self.assertEqual(set(level.teams.all()), {self.team, other_team})

    def test_team_reverse_accessor(self):
        level = RefereeLevel.objects.create(club=self.club, name="Regional")
        level.teams.add(self.team)

        self.assertEqual(list(self.team.referee_levels.all()), [level])

    def test_eligible_team_ids_with_no_inheritance_is_just_its_own_teams(self):
        level = RefereeLevel.objects.create(club=self.club, name="Regional")
        level.teams.add(self.team)

        self.assertEqual(level.eligible_team_ids(), {self.team.pk})

    def test_a_higher_level_inherits_its_lower_levels_teams(self):
        other_team = Team.objects.create(club=self.club, name="Second Team", short_name="2nd")
        regional = RefereeLevel.objects.create(club=self.club, name="Regional")
        regional.teams.add(self.team)
        national = RefereeLevel.objects.create(club=self.club, name="National", inherits_from=regional)
        national.teams.add(other_team)

        self.assertEqual(national.eligible_team_ids(), {self.team.pk, other_team.pk})
        # Inheritance is one-directional -- Regional doesn't gain National's teams.
        self.assertEqual(regional.eligible_team_ids(), {self.team.pk})

    def test_inheritance_is_transitive_through_a_chain(self):
        other_team = Team.objects.create(club=self.club, name="Second Team", short_name="2nd")
        third_team = Team.objects.create(club=self.club, name="Third Team", short_name="3rd")
        local = RefereeLevel.objects.create(club=self.club, name="Local")
        local.teams.add(self.team)
        regional = RefereeLevel.objects.create(club=self.club, name="Regional", inherits_from=local)
        regional.teams.add(other_team)
        national = RefereeLevel.objects.create(club=self.club, name="National", inherits_from=regional)
        national.teams.add(third_team)

        self.assertEqual(national.eligible_team_ids(), {self.team.pk, other_team.pk, third_team.pk})

    def test_a_level_cannot_inherit_from_itself(self):
        level = RefereeLevel.objects.create(club=self.club, name="Regional")
        level.inherits_from = level

        with self.assertRaises(ValidationError):
            level.clean()

    def test_a_level_cannot_indirectly_inherit_from_itself(self):
        regional = RefereeLevel.objects.create(club=self.club, name="Regional")
        national = RefereeLevel.objects.create(club=self.club, name="National", inherits_from=regional)
        regional.inherits_from = national

        with self.assertRaises(ValidationError):
            regional.clean()

    def test_inherits_from_must_be_the_same_club(self):
        other_club = Club.objects.create(name="Rival FC", slug="rival-fc")
        other_level = RefereeLevel.objects.create(club=other_club, name="Regional")
        level = RefereeLevel.objects.create(club=self.club, name="National", inherits_from=other_level)

        with self.assertRaises(ValidationError):
            level.clean()

    def test_deleting_an_inherited_from_level_is_protected(self):
        regional = RefereeLevel.objects.create(club=self.club, name="Regional")
        RefereeLevel.objects.create(club=self.club, name="National", inherits_from=regional)

        with self.assertRaises(ProtectedError):
            regional.delete()


class RefereeProfileModelTests(TeamsTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.level = RefereeLevel.objects.create(club=cls.club, name="Regional")
        cls.level.teams.add(cls.team)

    def test_str(self):
        profile = RefereeProfile.objects.create(member=self.member)
        self.assertEqual(str(profile), "Jane Doe (referee)")

    def test_member_is_one_to_one(self):
        RefereeProfile.objects.create(member=self.member)

        with self.assertRaises(IntegrityError):
            RefereeProfile.objects.create(member=self.member)

    def test_no_level_is_never_eligible_even_with_a_future_validity(self):
        profile = RefereeProfile.objects.create(member=self.member, valid_until=datetime.date(2099, 1, 1))
        self.assertTrue(profile.is_currently_valid)  # the date itself is fine...
        self.assertFalse(profile.is_eligible)  # ...but there's no level, so not eligible
        self.assertEqual(list(profile.eligible_teams), [])

    def test_no_validity_set_is_not_currently_valid_or_eligible(self):
        profile = RefereeProfile.objects.create(member=self.member, level=self.level)
        self.assertFalse(profile.is_currently_valid)
        self.assertFalse(profile.is_eligible)
        self.assertEqual(list(profile.eligible_teams), [])

    def test_valid_until_today_is_currently_valid_and_eligible(self):
        profile = RefereeProfile.objects.create(member=self.member, level=self.level, valid_until=timezone.localdate())
        self.assertTrue(profile.is_currently_valid)
        self.assertTrue(profile.is_eligible)

    def test_valid_until_in_the_past_is_not_currently_valid_or_eligible(self):
        profile = RefereeProfile.objects.create(member=self.member, level=self.level, valid_until=timezone.localdate() - datetime.timedelta(days=1))
        self.assertFalse(profile.is_currently_valid)
        self.assertFalse(profile.is_eligible)
        self.assertEqual(list(profile.eligible_teams), [])

    def test_eligible_teams_come_from_the_level_when_eligible(self):
        profile = RefereeProfile.objects.create(member=self.member, level=self.level, valid_until=timezone.localdate() + datetime.timedelta(days=1))
        self.assertEqual(list(profile.eligible_teams), [self.team])

    def test_eligible_teams_include_what_the_level_inherits(self):
        other_team = Team.objects.create(club=self.club, name="Second Team", short_name="2nd")
        national = RefereeLevel.objects.create(club=self.club, name="National", inherits_from=self.level)
        national.teams.add(other_team)
        profile = RefereeProfile.objects.create(member=self.member, level=national, valid_until=timezone.localdate() + datetime.timedelta(days=1))

        self.assertEqual(set(profile.eligible_teams), {self.team, other_team})

    def test_deleting_a_referenced_level_is_protected(self):
        RefereeProfile.objects.create(member=self.member, level=self.level, valid_until=timezone.localdate())

        with self.assertRaises(ProtectedError):
            self.level.delete()

    def test_deleting_the_member_deletes_the_profile(self):
        profile = RefereeProfile.objects.create(member=self.member)

        self.member.delete()

        self.assertFalse(RefereeProfile.objects.filter(pk=profile.pk).exists())


class RosterApiTests(TeamsTestCase):
    """build_roster (teams/api.py) -- called directly, not through Ninja's routing,
    same reasoning the module's own docstring gives for splitting it out. A roster
    spot placed from the Sign-up page (management.forms.SignupTeamPlacementForm)
    has no position yet -- this must group and sort those without crashing on
    m.position.name."""

    def make_request(self):
        return RequestFactory().get("/")

    def test_groups_players_by_position(self):
        # self.forward (TeamsTestCase's own fixture) sorts second here on purpose --
        # confirms ordering follows Position.ordering, not creation/name order.
        self.forward.ordering = 2
        self.forward.save()
        defence = Position.objects.create(club=self.club, name="Defence", ordering=1)
        striker = Member.objects.create(first_name="Sam", last_name="Striker")
        defender = Member.objects.create(first_name="Dee", last_name="Fender")
        TeamMembership.objects.create(team=self.team, member=striker, season=self.season, position=self.forward, jersey_number=9)
        TeamMembership.objects.create(team=self.team, member=defender, season=self.season, position=defence, jersey_number=4)

        roster = build_roster(self.team, self.make_request())

        self.assertEqual([group.position for group in roster.players], ["Defence", "Forward"])
        self.assertEqual(roster.players[1].players[0].last_name, "Striker")

    def test_a_positionless_member_groups_under_an_empty_label_without_crashing(self):
        placed = Member.objects.create(first_name="Noor", last_name="Placed")
        TeamMembership.objects.create(team=self.team, member=placed, season=self.season, position=None, jersey_number=None)

        roster = build_roster(self.team, self.make_request())

        self.assertEqual(len(roster.players), 1)
        self.assertEqual(roster.players[0].position, "")
        self.assertEqual(roster.players[0].players[0].last_name, "Placed")

    def test_positionless_members_sort_after_every_real_position_group(self):
        placed_member = Member.objects.create(first_name="Noor", last_name="Placed")
        assigned_member = Member.objects.create(first_name="Sam", last_name="Striker")
        TeamMembership.objects.create(team=self.team, member=placed_member, season=self.season, position=None)
        TeamMembership.objects.create(team=self.team, member=assigned_member, season=self.season, position=self.forward, jersey_number=9)

        roster = build_roster(self.team, self.make_request())

        self.assertEqual([group.position for group in roster.players], ["Forward", ""])
