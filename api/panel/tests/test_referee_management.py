import datetime

from django.utils import timezone
from waffle import get_waffle_flag_model

from api.protected.tests import CLUB_HOST, OTHER_HOST, AppApiTestCase
from club.models import ClubRole, RefereeManager
from events.models import Event, EventReferee, Location, RefereeSignup
from members.models import Member
from rosterchief.test_support import make_club
from teams.models import OfficialLevel, OfficialProfile, Position, RefereeLevel, RefereeProfile, StaffAssignment, Team

PATH = "/manage/referees/management"


class RefereeManagementTests(AppApiTestCase):
    def setUp(self):
        super().setUp()
        self.team = Team.objects.create(club=self.club, name="U12", short_name="U12")
        self.ground = Location.objects.create(club=self.club, name="Home Ground", address="1 St", city="Town", zip_code="1000", country="BE", is_home=True)
        self.game = Event.objects.create(club=self.club, kind="game", title="Derby", start=timezone.now() + datetime.timedelta(days=5), location=self.ground)
        self.game.teams.add(self.team)
        valid = timezone.localdate() + datetime.timedelta(days=60)
        ref_level = RefereeLevel.objects.create(club=self.club, name="Regional")
        ref_level.teams.add(self.team)
        self.referee = Member.objects.create(first_name="Ref", last_name="Eree")
        RefereeProfile.objects.create(club=self.club, member=self.referee, level=ref_level, valid_until=valid)
        off_level = OfficialLevel.objects.create(club=self.club, name="Table")
        off_level.teams.add(self.team)
        self.official = Member.objects.create(first_name="Olly", last_name="Official")
        OfficialProfile.objects.create(club=self.club, member=self.official, level=off_level, valid_until=valid)

    def as_role(self, role):
        ClubRole.objects.update_or_create(club=self.club, member=self.member, defaults={"role": role})
        return self.login_with_totp()

    def as_admin(self):
        return self.as_role(ClubRole.Roles.ADMIN)

    def call(self, method, path, token=None, host=CLUB_HOST):
        return self.api(method, path, token=token, host=host)

    def officials_on(self):
        get_waffle_flag_model().objects.get_or_create(name="officials")[0].clubs.add(self.club)

    def as_team_manager(self):
        head = Position.objects.create(club=self.club, name="Head coach", short_name="HC", staff_position=True, management_position=True)
        StaffAssignment.objects.create(team=self.team, member=self.member, season=self.season, position=head)
        return self.login_with_totp()

    def get(self, token, query="", **kwargs):
        return self.call("get", PATH + query, token, **kwargs)

    def test_requires_token(self):
        self.assertError(self.get(None), 401, "unauthenticated")

    def test_plain_member_and_team_manager_are_403(self):
        self.assertError(self.get(self.login()), 403, "forbidden")

    def test_team_manager_is_403(self):
        self.assertError(self.get(self.as_team_manager()), 403, "forbidden")

    def test_other_club_host_is_403(self):
        make_club(name="Rival FC", slug="rival-fc")
        token = self.as_admin()
        self.assertError(self.get(token, host=OTHER_HOST), 403, "not_a_member")

    def test_admin_sees_the_game_with_candidates(self):
        token = self.as_admin()
        body = self.get(token).json()
        self.assertEqual((body["range"], body["need"], body["officials_enabled"]), ("10", "all", False))
        self.assertEqual([g["id"] for g in body["games"]], [str(self.game.pk)])
        game = body["games"][0]
        self.assertTrue(game["referees"]["needed"])
        self.assertEqual([c["member_id"] for c in game["referees"]["candidates"]], [str(self.referee.pk)])
        self.assertIsNone(game["officials"])
        self.assertIsNone(body["official_status"])
        self.assertEqual(body["referee_status"]["without_anyone"], 1)
        self.assertEqual(body["need_counts"]["referees"], 1)
        self.assertEqual(game["teams"][0]["name"], "U12")

    def test_referee_admin_grant_and_member_admin_may_read(self):
        RefereeManager.objects.create(club=self.club, member=self.member)
        self.assertEqual(self.get(self.login_with_totp()).status_code, 200)

    def test_member_admin_may_read(self):
        self.assertEqual(self.get(self.as_role(ClubRole.Roles.MEMBER_ADMIN)).status_code, 200)

    def test_officials_block_when_enabled(self):
        self.officials_on()
        body = self.get(self.as_admin()).json()
        self.assertTrue(body["officials_enabled"])
        game = body["games"][0]
        self.assertEqual([c["member_id"] for c in game["officials"]["candidates"]], [str(self.official.pk)])
        self.assertEqual(body["official_status"]["without_anyone"], 1)

    def test_need_filter_hides_fully_staffed_games(self):
        token = self.as_admin()
        EventReferee.objects.create(event=self.game, member=self.referee)
        EventReferee.objects.create(event=self.game, external_name="External")
        self.assertEqual(len(self.get(token, "?need=all").json()["games"]), 1)
        body = self.get(token, "?need=referees").json()
        self.assertEqual(body["games"], [])
        self.assertEqual(body["need_counts"]["all"], 1)
        self.assertEqual(body["referee_status"]["fully_staffed"], 1)
        game = self.get(token).json()["games"][0]
        self.assertTrue(game["referees"]["full"])
        self.assertEqual(len(game["referees"]["assigned"]), 2)
        self.assertEqual(self.get(token).json()["workload"][0]["games"], 1)

    def test_need_officials_falls_back_without_the_module(self):
        self.assertEqual(self.get(self.as_admin(), "?need=officials").json()["need"], "all")

    def test_unknown_values_fall_back(self):
        body = self.get(self.as_admin(), "?range=999&need=bogus").json()
        self.assertEqual((body["range"], body["need"]), ("10", "all"))

    def test_week_range_excludes_a_far_game(self):
        far = Event.objects.create(club=self.club, kind="game", title="Far", start=timezone.now() + datetime.timedelta(days=40), location=self.ground)
        far.teams.add(self.team)
        token = self.as_admin()
        self.assertIn(str(far.pk), [g["id"] for g in self.get(token, "?range=25").json()["games"]])
        self.assertNotIn(str(far.pk), [g["id"] for g in self.get(token, "?range=two_weeks").json()["games"]])

    def test_signups_are_listed(self):
        RefereeSignup.objects.create(event=self.game, member=self.referee, status=RefereeSignup.Status.ACCEPTED)
        game = self.get(self.as_admin()).json()["games"][0]
        self.assertEqual([s["member_id"] for s in game["referees"]["signups"]["interested"]], [str(self.referee.pk)])

    def test_other_clubs_games_are_not_listed(self):
        other = make_club(name="Rival FC", slug="rival-fc")
        Event.objects.create(club=other, kind="game", title="Theirs", start=timezone.now() + datetime.timedelta(days=2))
        self.assertEqual([g["title"] for g in self.get(self.as_admin()).json()["games"]], ["Derby"])
