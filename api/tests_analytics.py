"""Game analytics API (``/teams/{id}/analytics``, ``/members/{id}/game-stats``, ``/events/{id}/strength-overrides/{goal}``
and the ``analytics_available`` flag on ``GET /teams/{id}``): gating on the sport and the ``analytics`` feature, the
numbers from a seeded game timeline (the documents of events/tests_game_stats), filters, member stats and who may
correct a goal's strength."""

import json
from datetime import timedelta
from unittest import mock

from django.db import connection
from django.test import Client
from django.utils import timezone
from django_tenants.utils import schema_context

from club.models import Domain, Sport
from club.services import grant_login
from controlpanel.tests import login_session
from events.models import Event, GameTimeline
from events.services.game_stats import sync_game_stats
from events.tests_game_stats import DOCUMENT, FLAGGED
from events.tests_timelines import GameTestBase
from features.services import set_override
from members.models import Member
from teams.models import Position, StaffAssignment, Team, TeamMembership

BASE = "/api/v1"


class AnalyticsApiTests(GameTestBase):
    @classmethod
    def setUpClass(cls):
        cls._mfa = mock.patch("authorization.middleware.is_local_development", return_value=True)
        cls._mfa.start()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls._mfa.stop()

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.hockey = Sport.objects.create(name="Ice Hockey")
        cls.football = Sport.objects.create(name="Football")
        cls.club.sport = cls.hockey
        cls.club.save(update_fields=["sport"])
        cls.host = Domain.objects.get(tenant=cls.club, is_primary=True).domain
        with schema_context(cls.club.schema_name):
            cls.other_team = Team.objects.create(name="Second", short_name="2nd")
            cls.scorer = Member.objects.create(first_name="Sam", last_name="Scorer")
            TeamMembership.objects.create(team=cls.team, member=cls.scorer, season=cls.season, jersey_number=78)
            coach_position = Position.objects.create(name="Coach", short_name="C", staff_position=True, management_position=True)
            cls.coach = Member.objects.create(first_name="Cy", last_name="Coach", email="cy@analytics.test")
            cls.other_coach = Member.objects.create(first_name="Oz", last_name="Other", email="oz@analytics.test")
            StaffAssignment.objects.create(team=cls.team, member=cls.coach, season=cls.season, position=coach_position)
            StaffAssignment.objects.create(team=cls.other_team, member=cls.other_coach, season=cls.season, position=coach_position)
        cls.coach_user, _ = grant_login(cls.club, cls.coach)
        cls.other_user, _ = grant_login(cls.club, cls.other_coach)
        cls.owner = type(cls.owner).objects.get(pk=cls.owner.pk)

    def setUp(self):
        super().setUp()
        door = mock.patch("members.services.access.can_access_management", return_value=True)
        door.start()
        self.addCleanup(door.stop)
        # Our side is away (no location): 3-1 win, Sam Scorer scores a game-winning PP goal.
        self.game = self.make_game(location=None, start=timezone.now() - timedelta(days=2), score_for=3, score_against=1)
        sync_game_stats(self.game, DOCUMENT)

    def enable(self, on=True):
        set_override(self.club, "analytics", enabled=on)

    def call(self, method, path, user=None, data=None):
        headers = {"HTTP_HOST": self.host, "HTTP_X_SESSION_TOKEN": login_session(user or self.owner)}
        body = {"data": json.dumps(data), "content_type": "application/json"} if data is not None else {}
        response = getattr(Client(), method)(BASE + path, **body, **headers)
        connection.set_tenant(self.club)
        return response

    @property
    def path(self):
        return f"/teams/{self.team.pk}/analytics"

    # -- gating --

    def test_feature_off_is_a_404_and_the_team_says_analytics_are_unavailable(self):
        self.assertEqual(self.call("get", self.path).status_code, 404)
        self.assertEqual(self.call("get", f"/members/{self.scorer.pk}/game-stats").status_code, 404)
        self.assertFalse(self.call("get", f"/teams/{self.team.pk}").json()["analytics_available"])

    def test_the_wrong_sport_is_a_404_even_with_the_feature(self):
        self.enable()
        self.club.sport = self.football
        self.club.save(update_fields=["sport"])
        self.addCleanup(lambda: (setattr(self.club, "sport", self.hockey), self.club.save(update_fields=["sport"])))
        self.assertEqual(self.call("get", self.path).status_code, 404)
        self.assertFalse(self.call("get", f"/teams/{self.team.pk}").json()["analytics_available"])

    def test_on_the_document_carries_the_numbers_of_the_game_sheet(self):
        self.enable()
        self.assertTrue(self.call("get", f"/teams/{self.team.pk}").json()["analytics_available"])

        response = self.call("get", self.path, self.coach_user)

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["capabilities"], {"analytics": True})
        self.assertEqual((data["games"], data["timeline_games"], data["record"], data["points_table"]), (1, 1, {"won": 1, "drawn": 0, "lost": 0}, 3))
        self.assertEqual((data["goals_for"], data["goals_against"], data["goal_difference"], data["avg_for"]), (3, 1, 2, 3.0))
        self.assertEqual((data["pim_for"], data["pim_against"], data["pp_goals_for"], data["game_winning_goals"]), (4, 2, 1, 1))
        [scorer] = data["top_scorers"]
        self.assertEqual((scorer["name"], scorer["member_id"], scorer["total"], scorer["number"]), ("Sam Scorer", str(self.scorer.pk), 1, 78))
        self.assertIsNone(data["top_assists"][0]["member_id"])  # Al Assist is not on the roster
        self.assertEqual(data["top_offenders"][0]["name"], "Pat Penalty")
        self.assertEqual([row["result"] for row in data["form"]], ["W"])
        self.assertEqual(data["form"][0]["event"]["id"], str(self.game.pk))
        self.assertEqual([(row["label"], row["goals_for"], row["goals_against"]) for row in data["periods"]], [("1", 1, 0), ("2", 0, 1)])
        self.assertEqual((data["goal_minutes"][0]["goals_for"], data["goal_minutes"][0]["goals_against"]), (1, 1))
        self.assertEqual(data["busiest_minutes"]["label"], "0-5")
        self.assertEqual(data["split"]["away"]["games"], 1)
        [row] = data["game_rows"]
        self.assertEqual((row["event"]["opponent"], row["result"], row["goals_for"], row["goals_against"], row["pim_for"]), ("Olympia Heist", "W", 3, 1, 4))
        self.assertEqual(data["opponents"], [{"name": "Olympia Heist", "games": 1, "won": 1, "drawn": 0, "lost": 0, "gf": 3, "ga": 1}])
        self.assertEqual(data["trend"]["last"], 2)
        self.assertIsNone(data["trend"]["points"][0]["game"])
        # The sheet calls g1 a power-play goal, but nobody of theirs was in the box: flagged.
        self.assertEqual([(row["goal"], row["recorded"], row["expected"]) for row in data["strength_warnings"]], [("g1", "PP1", "EQ")])
        self.assertEqual(data["season"]["id"], str(self.season.pk))

    def test_unknown_team_is_a_404_and_unknown_season_a_422(self):
        self.enable()
        self.assertEqual(self.call("get", "/teams/00000000-0000-0000-0000-000000000000/analytics").status_code, 404)
        self.assertEqual(self.call("get", f"{self.path}?season=00000000-0000-0000-0000-000000000000").status_code, 422)
        self.assertEqual(self.call("get", f"{self.path}?scope=nope").status_code, 422)

    # -- filters --

    def test_scope_friendlies_and_competition_filters(self):
        self.enable()
        Event.objects.filter(pk=self.game.pk).update(is_friendly=True, competition_label="Cup")
        home = self.make_game(external_game_id="h", start=timezone.now() - timedelta(days=1), score_for=0, score_against=2)

        def games(query):
            return self.call("get", f"{self.path}?{query}").json()

        everything = games("")
        self.assertEqual((everything["games"], everything["competitions"]), (2, ["Cup", "RBIHF"]))
        self.assertEqual(games("scope=home")["games"], 1)
        self.assertEqual(games("scope=away")["record"]["won"], 1)
        self.assertEqual(games("scope=last5")["games"], 2)
        no_friendlies = games("friendlies=false")
        self.assertEqual((no_friendlies["games"], no_friendlies["record"]["lost"], no_friendlies["friendlies"]), (1, 1, False))
        cup = games("competition=Cup")
        self.assertEqual((cup["games"], cup["competition"], cup["game_rows"][0]["event"]["competition"]), (1, "Cup", "Cup"))
        self.assertEqual(games("competition=Cup")["game_rows"][0]["event"]["is_friendly"], True)
        self.assertEqual(games("scope=home")["game_rows"][0]["event"]["id"], str(home.pk))

    def test_a_season_without_games_is_an_empty_document(self):
        self.enable()
        Event.objects.all().delete()
        data = self.call("get", self.path).json()
        self.assertEqual((data["games"], data["form"], data["trend"], data["top_scorers"], data["busiest_minutes"]), (0, [], None, [], None))

    # -- member stats --

    def test_member_game_stats(self):
        self.enable()
        second = self.make_game(external_game_id="2", location=None, start=timezone.now() - timedelta(days=1), score_for=1, score_against=0)
        sync_game_stats(second, {"events": [{"id": "x", "type": "goal", "period": "1", "seconds": 10, "side": "away", "goal": {"scorer": {"name": "Sam Scorer", "number": 78}, "assists": []}}]})

        data = self.call("get", f"/members/{self.scorer.pk}/game-stats").json()

        self.assertEqual((data["games"], data["goals"], data["points"], data["pp_goals"], data["game_winning_goals"], data["goalie"], data["season"]), (2, 2, 2, 1, 1, None, None))
        [row] = data["teams"]
        self.assertEqual((row["team"]["name"], row["season"]["id"], row["goals"]), ("First Team", str(self.season.pk), 2))
        in_season = self.call("get", f"/members/{self.scorer.pk}/game-stats?season={self.season.pk}").json()
        self.assertEqual((in_season["goals"], in_season["season"]["id"]), (2, str(self.season.pk)))
        nobody = self.call("get", f"/members/{self.coach.pk}/game-stats").json()
        self.assertEqual((nobody["games"], nobody["teams"]), (0, []))
        self.assertEqual(self.call("get", "/members/00000000-0000-0000-0000-000000000000/game-stats").status_code, 404)

    def test_member_game_stats_follow_member_visibility(self):
        self.enable()
        with mock.patch("members.services.access.can_view_member", return_value=False):
            self.assertEqual(self.call("get", f"/members/{self.scorer.pk}/game-stats", self.other_user).status_code, 404)

    # -- strength corrections --

    def warnings(self, event, user=None):
        return [row for row in self.call("get", self.path, user).json()["strength_warnings"] if row["event"]["id"] == str(event.pk)]

    def flag(self):
        flagged = self.make_game(external_game_id="f", location=None, start=timezone.now() - timedelta(days=3), score_for=1, score_against=0)
        GameTimeline.objects.create(event=flagged, source="test", data=FLAGGED, status=GameTimeline.Status.FINAL)
        sync_game_stats(flagged, FLAGGED)
        return flagged

    def test_a_flagged_goal_is_listed_with_who_may_fix_it(self):
        self.enable()
        flagged = self.flag()

        [warning] = self.warnings(flagged, self.coach_user)
        self.assertEqual((warning["event"]["id"], warning["goal"], warning["recorded"], warning["expected"], warning["keep"], warning["can_fix"]), (str(flagged.pk), "g1", "EQ", "PP1", "EQ", True))
        self.assertEqual(warning["expected_label"], "power play")
        self.assertEqual(warning["penalties"][0]["ours"], False)
        self.assertFalse(self.warnings(flagged, self.other_user)[0]["can_fix"])

    def test_override_permissions_validation_and_undo(self):
        self.enable()
        flagged = self.flag()
        url = f"/events/{flagged.pk}/strength-overrides/g1"

        self.assertEqual(self.call("put", url, self.other_user, {"strength": "PP1"}).status_code, 403)
        self.assertEqual(self.call("put", url, self.coach_user, {"strength": "XX"}).status_code, 422)
        self.assertEqual(self.call("put", f"/events/{flagged.pk}/strength-overrides/nope", self.coach_user, {"strength": "PP1"}).status_code, 422)
        self.assertEqual(self.call("put", "/events/00000000-0000-0000-0000-000000000000/strength-overrides/g1", self.coach_user, {"strength": "PP1"}).status_code, 404)

        response = self.call("put", url, self.coach_user, {"strength": "PP1"})
        self.assertEqual((response.status_code, response.json()["overrides"]), (200, {"g1": "PP1"}))
        self.assertEqual((self.call("get", self.path).json()["pp_goals_for"], self.warnings(flagged)), (2, []))

        self.assertEqual(self.call("delete", url, self.other_user).status_code, 403)
        self.assertEqual(self.call("delete", url, self.coach_user).json()["overrides"], {})
        self.assertEqual(self.call("delete", url, self.coach_user).status_code, 422)  # nothing left to undo
        self.assertEqual(len(self.warnings(flagged)), 1)

    def test_overrides_are_gated_too(self):
        flagged = self.flag()
        self.assertEqual(self.call("put", f"/events/{flagged.pk}/strength-overrides/g1", data={"strength": "PP1"}).status_code, 404)
