import datetime

from django.utils import timezone

from api.protected.tests import CLUB_HOST, OTHER_HOST, AppApiTestCase
from club.models import ClubMembership, ClubRole, Season
from members.models import Member
from rosterchief.test_support import make_club
from teams.models import NumberPool, Position, StaffAssignment, Team, TeamMembership

TEAMS = "/manage/teams"


class PanelTeamsTests(AppApiTestCase):
    def setUp(self):
        super().setUp()
        self.team = Team.objects.create(club=self.club, name="U12", short_name="U12", ordering=2)
        self.other_team = Team.objects.create(club=self.club, name="U14", short_name="U14", ordering=1)
        self.forward = Position.objects.create(club=self.club, name="Forward", short_name="F")
        self.coach_pos = Position.objects.create(club=self.club, name="Assistant", short_name="AC", staff_position=True)
        self.head_pos = Position.objects.create(club=self.club, name="Head coach", short_name="HC", staff_position=True, management_position=True)
        self.kim = self.child  # active club member, eligible for rosters

    # --- helpers ------------------------------------------------------------

    def as_role(self, role):
        ClubRole.objects.update_or_create(club=self.club, member=self.member, defaults={"role": role})
        return self.login_with_totp()

    def as_admin(self):
        return self.as_role(ClubRole.Roles.ADMIN)

    def as_manager(self, team=None):
        StaffAssignment.objects.create(team=team or self.team, member=self.member, season=self.season, position=self.head_pos)
        return self.login_with_totp()

    def as_coach(self, team=None):
        StaffAssignment.objects.create(team=team or self.team, member=self.member, season=self.season, position=self.coach_pos)
        return self.login()

    def call(self, method, path, token=None, data=None, host=CLUB_HOST, **headers):
        return self.api(method, path, token=token, host=host, data=data, **headers)

    def player(self, first="Sam", last="Smith", jersey=None, team=None):
        member = Member.objects.create(first_name=first, last_name=last)
        ClubMembership.objects.create(club=self.club, member=member, season=self.season, status=ClubMembership.StatusChoices.ACTIVE)
        if jersey is None and team is None:
            return member
        return TeamMembership.objects.create(team=team or self.team, member=member, season=self.season, position=self.forward, jersey_number=jersey)

    # --- auth ---------------------------------------------------------------

    def test_requires_token(self):
        for method, path in [("get", TEAMS), ("get", f"{TEAMS}/{self.team.pk}"), ("post", TEAMS), ("get", "/manage/seasons"), ("get", "/manage/seasons/current"), ("get", f"{TEAMS}/{self.team.pk}/roster")]:
            self.assertError(self.call(method, path), 401, "unauthenticated")

    def test_plain_member_is_forbidden(self):
        token = self.login()
        for path in (TEAMS, "/manage/seasons", f"{TEAMS}/{self.team.pk}/roster"):
            self.assertError(self.call("get", path, token), 403, "forbidden")

    def test_other_club_is_not_found_for_a_foreign_team(self):
        token = self.as_admin()
        other = make_club(name="Rival FC", slug="rival-fc")
        foreign = Team.objects.create(club=other, name="Foreign", short_name="F")
        for method, path in [("get", f"{TEAMS}/{foreign.pk}"), ("patch", f"{TEAMS}/{foreign.pk}"), ("delete", f"{TEAMS}/{foreign.pk}"), ("get", f"{TEAMS}/{foreign.pk}/roster"), ("get", f"{TEAMS}/{foreign.pk}/staff")]:
            self.assertError(self.call(method, path, token, data={} if method == "patch" else None), 404, "not_found")
        self.assertTrue(Team.objects.filter(pk=foreign.pk).exists())

    def test_other_club_host_refuses_a_non_member(self):
        token = self.as_admin()
        make_club(name="Rival FC", slug="rival-fc")
        self.assertError(self.call("get", TEAMS, token, host=OTHER_HOST), 403, "not_a_member")

    # --- seasons ------------------------------------------------------------

    def test_seasons_list_and_current(self):
        today = timezone.localdate()
        old = Season.objects.create(club=self.club, start_date=today - datetime.timedelta(days=800), end_date=today - datetime.timedelta(days=500))
        Season.objects.create(club=make_club(name="Rival FC", slug="rival-fc"), start_date=today, end_date=today + datetime.timedelta(days=5))
        token = self.as_coach()

        body = self.call("get", "/manage/seasons", token).json()
        self.assertEqual([item["id"] for item in body["items"]], [str(self.season.pk), str(old.pk)])
        self.assertEqual([item["is_current"] for item in body["items"]], [True, False])

        current = self.call("get", "/manage/seasons/current", token).json()["season"]
        self.assertEqual(current["id"], str(self.season.pk))

    def test_no_current_season(self):
        token = self.as_admin()
        Season.objects.update(start_date=datetime.date(2000, 1, 1), end_date=datetime.date(2000, 12, 31))
        self.assertIsNone(self.call("get", "/manage/seasons/current", token).json()["season"])
        self.assertEqual(self.call("get", f"{TEAMS}/{self.team.pk}/roster", token).json(), {"season_id": None, "players": [], "staff": []})
        self.assertError(self.call("post", f"{TEAMS}/{self.team.pk}/staff", token, data={"member_id": str(self.kim.pk), "position_id": str(self.coach_pos.pk)}), 409, "no_current_season")

    # --- team list / detail ---------------------------------------------------

    def test_admin_lists_all_teams_in_web_order_with_counts(self):
        unordered = Team.objects.create(club=self.club, name="Adults", short_name="A")
        Team.objects.create(club=make_club(name="Rival FC", slug="rival-fc"), name="Foreign", short_name="F")
        self.player(jersey=7)
        token = self.as_admin()

        body = self.call("get", TEAMS, token).json()
        self.assertEqual([t["name"] for t in body["items"]], ["U14", "U12", "Adults"])
        self.assertIsNone(body["next_cursor"])
        self.assertEqual(body["items"][1]["player_count"], 1)
        self.assertEqual(body["items"][2]["id"], str(unordered.pk))

    def test_list_paginates_and_searches(self):
        token = self.as_admin()
        first = self.call("get", f"{TEAMS}?limit=1", token).json()
        self.assertEqual([t["name"] for t in first["items"]], ["U14"])
        second = self.call("get", f"{TEAMS}?limit=1&cursor={first['next_cursor']}", token).json()
        self.assertEqual([t["name"] for t in second["items"]], ["U12"])
        self.assertEqual([t["name"] for t in self.call("get", f"{TEAMS}?q=u12", token).json()["items"]], ["U12"])

    def test_coach_sees_only_their_teams(self):
        token = self.as_coach(self.team)
        self.assertEqual([t["name"] for t in self.call("get", TEAMS, token).json()["items"]], ["U12"])
        self.assertEqual(self.call("get", f"{TEAMS}/{self.team.pk}", token).status_code, 200)
        self.assertError(self.call("get", f"{TEAMS}/{self.other_team.pk}", token), 404, "not_found")

    def test_detail_flags(self):
        coach = self.as_coach()
        body = self.call("get", f"{TEAMS}/{self.team.pk}", coach).json()
        self.assertEqual((body["can_manage"], body["can_edit"], body["staff_count"]), (False, False, 1))

    def test_manager_detail_can_manage_not_edit(self):
        body = self.call("get", f"{TEAMS}/{self.team.pk}", self.as_manager()).json()
        self.assertEqual((body["can_manage"], body["can_edit"]), (True, False))

    # --- team create / update / delete ------------------------------------------

    def test_create_team(self):
        token = self.as_role(ClubRole.Roles.MEMBER_ADMIN)
        pool = NumberPool.objects.create(club=self.club, name="Pool", min_number=1, max_number=99)
        response = self.call("post", TEAMS, token, data={"name": "U10", "short_name": "U10", "age_min": 8, "age_max": 10, "pool_id": str(pool.pk), "feeds_into_id": str(self.team.pk)})
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        team = Team.objects.get(pk=body["id"])
        self.assertEqual((team.club, team.pool, team.feeds_into, team.age_max), (self.club, pool, self.team, 10))
        self.assertTrue(body["can_edit"])

    def test_create_is_idempotent(self):
        token = self.as_admin()
        data = {"name": "U10", "short_name": "U10"}
        first = self.call("post", TEAMS, token, data=data, HTTP_IDEMPOTENCY_KEY="k1")
        second = self.call("post", TEAMS, token, data=data, HTTP_IDEMPOTENCY_KEY="k1")
        self.assertEqual((first.status_code, second.status_code), (201, 201))
        self.assertEqual(Team.objects.filter(name="U10").count(), 1)

    def test_create_validation(self):
        token = self.as_admin()
        duplicate = self.call("post", TEAMS, token, data={"name": "U12", "short_name": "x"})
        self.assertError(duplicate, 422, "validation_error")
        self.assertIn("name", duplicate.json()["error"]["fields"])
        bad_ages = self.call("post", TEAMS, token, data={"name": "New", "short_name": "N", "age_min": 12, "age_max": 8})
        self.assertError(bad_ages, 422, "validation_error")
        missing = self.call("post", TEAMS, token, data={"short_name": "N"})
        self.assertError(missing, 422, "validation_error")

    def test_create_rejects_foreign_pool(self):
        token = self.as_admin()
        pool = NumberPool.objects.create(club=make_club(name="Rival FC", slug="rival-fc"), name="Pool", min_number=1, max_number=9)
        response = self.call("post", TEAMS, token, data={"name": "U10", "short_name": "U10", "pool_id": str(pool.pk)})
        self.assertError(response, 422, "validation_error")
        self.assertIn("pool", response.json()["error"]["fields"])

    def test_manager_cannot_create_edit_or_delete(self):
        token = self.as_manager()
        self.assertError(self.call("post", TEAMS, token, data={"name": "U10", "short_name": "U10"}), 403, "forbidden")
        self.assertError(self.call("patch", f"{TEAMS}/{self.team.pk}", token, data={"name": "X"}), 403, "forbidden")
        self.assertError(self.call("delete", f"{TEAMS}/{self.team.pk}", token), 403, "forbidden")

    def test_update_team_is_partial(self):
        token = self.as_admin()
        response = self.call("patch", f"{TEAMS}/{self.team.pk}", token, data={"short_name": "Twelve", "age_max": 12})
        self.assertEqual(response.status_code, 200, response.content)
        self.team.refresh_from_db()
        self.assertEqual((self.team.name, self.team.short_name, self.team.age_max, self.team.ordering), ("U12", "Twelve", 12, 2))
        cleared = self.call("patch", f"{TEAMS}/{self.team.pk}", token, data={"ordering": None})
        self.assertEqual(cleared.status_code, 200)
        self.team.refresh_from_db()
        self.assertIsNone(self.team.ordering)

    def test_update_rejects_name_clash_but_allows_own_name(self):
        token = self.as_admin()
        self.assertError(self.call("patch", f"{TEAMS}/{self.team.pk}", token, data={"name": "U14"}), 422, "validation_error")
        self.assertEqual(self.call("patch", f"{TEAMS}/{self.team.pk}", token, data={"name": "U12"}).status_code, 200)
        self.assertError(self.call("patch", f"{TEAMS}/{self.team.pk}", token, data={"feeds_into_id": str(self.team.pk)}), 422, "validation_error")

    def test_delete_team(self):
        token = self.as_admin()
        self.assertEqual(self.call("delete", f"{TEAMS}/{self.other_team.pk}", token).status_code, 204)
        self.assertFalse(Team.objects.filter(pk=self.other_team.pk).exists())

    # --- roster -----------------------------------------------------------------

    def test_roster_lists_players_and_staff(self):
        self.player("Ann", "Zed", jersey=9)
        StaffAssignment.objects.create(team=self.team, member=self.kim, season=self.season, position=self.coach_pos)
        token = self.as_coach()
        body = self.call("get", f"{TEAMS}/{self.team.pk}/roster", token).json()
        self.assertEqual(body["season_id"], str(self.season.pk))
        self.assertEqual([(p["first_name"], p["jersey_number"], p["position"]) for p in body["players"]], [("Ann", 9, "Forward")])
        self.assertEqual({s["position"] for s in body["staff"]}, {"Assistant"})
        past = Season.objects.create(club=self.club, start_date=datetime.date(2000, 1, 1), end_date=datetime.date(2000, 12, 31))
        self.assertEqual(self.call("get", f"{TEAMS}/{self.team.pk}/roster?season_id={past.pk}", token).json()["players"], [])

    def test_roster_unknown_season_is_404(self):
        other_season = Season.objects.create(club=make_club(name="Rival FC", slug="rival-fc"), start_date=datetime.date(2000, 1, 1), end_date=datetime.date(2000, 12, 31))
        token = self.as_admin()
        self.assertError(self.call("get", f"{TEAMS}/{self.team.pk}/roster?season_id={other_season.pk}", token), 404, "not_found")

    def test_add_player(self):
        token = self.as_admin()
        response = self.call("post", f"{TEAMS}/{self.team.pk}/roster", token, data={"member_id": str(self.kim.pk), "position_id": str(self.forward.pk), "jersey_number": 10, "is_captain": True})
        self.assertEqual(response.status_code, 201, response.content)
        membership = TeamMembership.objects.get(team=self.team, member=self.kim)
        self.assertEqual((membership.season, membership.jersey_number, membership.is_captain), (self.season, 10, True))
        self.assertEqual(response.json()["id"], str(membership.pk))

    def test_manager_adds_player_to_own_team_only(self):
        token = self.as_manager(self.team)
        data = {"member_id": str(self.kim.pk), "position_id": str(self.forward.pk)}
        self.assertEqual(self.call("post", f"{TEAMS}/{self.team.pk}/roster", token, data=data).status_code, 201)
        self.assertError(self.call("post", f"{TEAMS}/{self.other_team.pk}/roster", token, data=data), 403, "forbidden")

    def test_plain_coach_cannot_change_roster(self):
        token = self.as_coach()
        data = {"member_id": str(self.kim.pk), "position_id": str(self.forward.pk)}
        self.assertError(self.call("post", f"{TEAMS}/{self.team.pk}/roster", token, data=data), 403, "forbidden")
        membership = self.player("Ann", "Zed", jersey=3)
        self.assertError(self.call("patch", f"{TEAMS}/{self.team.pk}/roster/{membership.pk}", token, data={"jersey_number": 4}), 403, "forbidden")
        self.assertError(self.call("delete", f"{TEAMS}/{self.team.pk}/roster/{membership.pk}", token), 403, "forbidden")
        self.assertError(self.call("post", f"{TEAMS}/{self.team.pk}/staff", token, data={"member_id": str(self.kim.pk), "position_id": str(self.coach_pos.pk)}), 403, "forbidden")

    def test_duplicate_jersey_number_is_a_field_error(self):
        self.player("Ann", "Zed", jersey=9)
        token = self.as_admin()
        response = self.call("post", f"{TEAMS}/{self.team.pk}/roster", token, data={"member_id": str(self.kim.pk), "position_id": str(self.forward.pk), "jersey_number": 9})
        self.assertError(response, 422, "validation_error")
        self.assertIn("jersey_number", response.json()["error"]["fields"])
        self.assertFalse(TeamMembership.objects.filter(member=self.kim).exists())

    def test_same_jersey_number_allowed_on_another_team(self):
        self.player("Ann", "Zed", jersey=9, team=self.other_team)
        token = self.as_admin()
        response = self.call("post", f"{TEAMS}/{self.team.pk}/roster", token, data={"member_id": str(self.kim.pk), "position_id": str(self.forward.pk), "jersey_number": 9})
        self.assertEqual(response.status_code, 201, response.content)

    def test_add_player_validation(self):
        token = self.as_admin()
        url = f"{TEAMS}/{self.team.pk}/roster"
        staff_position = self.call("post", url, token, data={"member_id": str(self.kim.pk), "position_id": str(self.coach_pos.pk)})
        self.assertIn("position", staff_position.json()["error"]["fields"])
        stranger = Member.objects.create(first_name="No", last_name="Club")
        not_eligible = self.call("post", url, token, data={"member_id": str(stranger.pk), "position_id": str(self.forward.pk)})
        self.assertIn("member", not_eligible.json()["error"]["fields"])
        self.assertError(self.call("post", url, token, data={"position_id": str(self.forward.pk)}), 422, "validation_error")

    def test_cannot_add_same_player_twice(self):
        token = self.as_admin()
        data = {"member_id": str(self.kim.pk), "position_id": str(self.forward.pk)}
        self.assertEqual(self.call("post", f"{TEAMS}/{self.team.pk}/roster", token, data=data).status_code, 201)
        self.assertError(self.call("post", f"{TEAMS}/{self.team.pk}/roster", token, data=data), 422, "validation_error")

    def test_update_player(self):
        membership = self.player("Ann", "Zed", jersey=9)
        other = self.player("Bo", "Yu", jersey=10)
        token = self.as_admin()
        url = f"{TEAMS}/{self.team.pk}/roster/{membership.pk}"

        response = self.call("patch", url, token, data={"jersey_number": 11, "is_alternate_captain": True})
        self.assertEqual(response.status_code, 200, response.content)
        membership.refresh_from_db()
        self.assertEqual((membership.jersey_number, membership.is_alternate_captain, membership.position), (11, True, self.forward))

        clash = self.call("patch", url, token, data={"jersey_number": other.jersey_number})
        self.assertError(clash, 422, "validation_error")
        self.assertIn("jersey_number", clash.json()["error"]["fields"])
        self.assertEqual(self.call("patch", url, token, data={"jersey_number": 11}).status_code, 200)

    def test_update_and_remove_scoped_to_team_and_club(self):
        membership = self.player("Ann", "Zed", jersey=9)
        token = self.as_admin()
        wrong_team = f"{TEAMS}/{self.other_team.pk}/roster/{membership.pk}"
        self.assertError(self.call("patch", wrong_team, token, data={"jersey_number": 1}), 404, "not_found")
        self.assertError(self.call("delete", wrong_team, token), 404, "not_found")

    def test_remove_player(self):
        membership = self.player("Ann", "Zed", jersey=9)
        token = self.as_manager()
        self.assertEqual(self.call("delete", f"{TEAMS}/{self.team.pk}/roster/{membership.pk}", token).status_code, 204)
        self.assertFalse(TeamMembership.objects.filter(pk=membership.pk).exists())

    # --- staff --------------------------------------------------------------------

    def test_staff_add_list_remove(self):
        token = self.as_admin()
        response = self.call("post", f"{TEAMS}/{self.team.pk}/staff", token, data={"member_id": str(self.kim.pk), "position_id": str(self.head_pos.pk)})
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertTrue(body["is_manager"])

        listed = self.call("get", f"{TEAMS}/{self.team.pk}/staff", token).json()
        self.assertEqual([item["id"] for item in listed["items"]], [body["id"]])
        self.assertEqual(listed["season_id"], str(self.season.pk))

        self.assertEqual(self.call("delete", f"{TEAMS}/{self.team.pk}/staff/{body['id']}", token).status_code, 204)
        self.assertFalse(StaffAssignment.objects.filter(pk=body["id"]).exists())

    def test_staff_validation(self):
        token = self.as_admin()
        url = f"{TEAMS}/{self.team.pk}/staff"
        player_position = self.call("post", url, token, data={"member_id": str(self.kim.pk), "position_id": str(self.forward.pk)})
        self.assertIn("position", player_position.json()["error"]["fields"])
        data = {"member_id": str(self.kim.pk), "position_id": str(self.coach_pos.pk)}
        self.assertEqual(self.call("post", url, token, data=data).status_code, 201)
        self.assertError(self.call("post", url, token, data=data), 422, "validation_error")

    def test_manager_staff_authority_is_per_team(self):
        token = self.as_manager(self.team)
        data = {"member_id": str(self.kim.pk), "position_id": str(self.coach_pos.pk)}
        self.assertError(self.call("post", f"{TEAMS}/{self.other_team.pk}/staff", token, data=data), 403, "forbidden")
        assignment = StaffAssignment.objects.create(team=self.other_team, member=self.kim, season=self.season, position=self.coach_pos)
        self.assertError(self.call("delete", f"{TEAMS}/{self.other_team.pk}/staff/{assignment.pk}", token), 403, "forbidden")
        self.assertError(self.call("get", f"{TEAMS}/{self.other_team.pk}/staff", token), 404, "not_found")

    def test_remove_staff_from_other_team_is_404(self):
        assignment = StaffAssignment.objects.create(team=self.other_team, member=self.kim, season=self.season, position=self.coach_pos)
        token = self.as_admin()
        self.assertError(self.call("delete", f"{TEAMS}/{self.team.pk}/staff/{assignment.pk}", token), 404, "not_found")
