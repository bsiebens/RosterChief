"""Fixture imports API (``/teams/{id}/fixture-sources``, ``/teams/{id}/standings``, ``/fixture-imports/json``):
feature gating, permissions, preview shape with mapping problems, applying choices, token expiry, idempotency,
re-checks and forgetting a source. No network: every fetch is mocked as in events/tests_imports."""

import json
from datetime import timedelta
from unittest import mock

import requests
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import Client
from django.utils import timezone
from django_tenants.utils import schema_context

from club.models import Domain
from club.services import grant_login
from controlpanel.tests import login_session
from events.models import Event, Location, Opponent
from events.services import fixture_previews, import_sources
from events.tests_imports import RBIHF_TEAM_ID, RBIHF_TEAM_NAME, ImportSourcesTestBase, http_response, rbihf_row, rbihf_sample_html
from members.models import AuditEntry, Member
from teams.models import Position, StaffAssignment

BASE = "/api/v1"
RBIHF_GET = "events.services.rbihf_import.requests.get"


def away_row(game_id="5010", *, days=10, venue="Antwerp Ice Rink", home_name="Brussels Bears"):
    date = (timezone.now() + timedelta(days=days)).strftime("%Y-%m-%d")
    return {"game_id": game_id, "date": date, "hour": "18:30", "venue": venue, "home_id": "4500", "home_name": home_name, "visit_id": RBIHF_TEAM_ID, "visit_name": RBIHF_TEAM_NAME}


class FixtureApiTestBase(ImportSourcesTestBase):
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
        cls.host = Domain.objects.get(tenant=cls.club, is_primary=True).domain
        with schema_context(cls.club.schema_name):
            cls.home_location = Location.objects.create(name="Home Arena", address="1 St", city="Antwerp", zip_code="2000", country="BE", is_home=True)
            head = Position.objects.create(name="Head coach", short_name="HC", staff_position=True, management_position=True)
            cls.coach = Member.objects.create(first_name="Hank", last_name="Head", email="head@fiapi.test")
            StaffAssignment.objects.create(team=cls.team, member=cls.coach, season=cls.season, position=head)
        cls.coach_user, _ = grant_login(cls.club, cls.coach)
        cls.owner = type(cls.owner).objects.get(pk=cls.owner.pk)

    def setUp(self):
        super().setUp()
        door = mock.patch("members.services.access.can_access_management", return_value=True)
        door.start()
        self.addCleanup(door.stop)

    # -- helpers --

    def call(self, method, path, user=None, data=None):
        headers = {"HTTP_HOST": self.host, "HTTP_X_SESSION_TOKEN": login_session(user or self.owner)}
        body = {"data": json.dumps(data), "content_type": "application/json"} if data is not None else {}
        response = getattr(Client(), method)(BASE + path, **body, **headers)
        connection.set_tenant(self.club)
        return response


class FixtureImportsApiTests(FixtureApiTestBase):
    @property
    def path(self):
        return f"/teams/{self.team.pk}/fixture-sources"

    def preview(self, rows, user=None, **extra):
        with mock.patch(RBIHF_GET, return_value=http_response(rbihf_sample_html(rows))):
            return self.call("post", f"{self.path}/preview", user, {"source": "RBIHF", "url": self.RBIHF_URL, **extra})

    # -- reading --

    def test_sources_are_readable_by_staff_but_only_settings_holders_can_import(self):
        self.enable("competition_rbihf")
        self.store(label="Division 1")

        owner = self.call("get", self.path).json()
        coach = self.call("get", self.path, self.coach_user)

        self.assertEqual(coach.status_code, 200)
        self.assertEqual((owner["can_import"], coach.json()["can_import"]), (True, False))
        [source] = owner["sources"]
        self.assertEqual((source["source"], source["competition_label"], source["season_id"], source["enabled"]), ("RBIHF", "Division 1", str(self.season.pk), True))
        self.assertEqual(source["id"], fixture_previews.entry_id(source))
        available = {row["source"]: row for row in owner["available"]}
        self.assertEqual({code: row["enabled"] for code, row in available.items()}, {"RBIHF": True, "DFEL": False, "IJNL": False})
        self.assertIn("rbihf.be/league/team/", available["RBIHF"]["example"])
        self.assertEqual(self.call("get", "/teams/00000000-0000-0000-0000-000000000000/fixture-sources").status_code, 404)

    # -- gating --

    def test_preview_needs_the_feature_and_the_settings_permission(self):
        response = self.preview([rbihf_row()])
        self.assertEqual(response.status_code, 403)
        self.assertIn("RBIHF fixtures", response.json()["detail"])

        self.enable("competition_rbihf")
        self.assertEqual(self.preview([rbihf_row()], self.coach_user).status_code, 403)
        self.assertEqual(self.preview([rbihf_row()]).status_code, 200)
        self.assertFalse(Event.objects.exists())

    def test_a_fetch_problem_is_a_422_on_the_url(self):
        self.enable("competition_rbihf", "competition_dfel")
        with mock.patch(RBIHF_GET, side_effect=requests.ConnectionError("boom")):
            response = self.call("post", f"{self.path}/preview", data={"source": "RBIHF", "url": self.RBIHF_URL})
        self.assertEqual(response.status_code, 422)
        self.assertIn("url", response.json()["errors"])
        bad = self.call("post", f"{self.path}/preview", data={"source": "DFEL", "url": "https://example.com"})
        self.assertEqual((bad.status_code, list(bad.json()["errors"])), (422, ["url"]))

    # -- preview and import --

    def test_preview_lists_games_and_the_mapping_problems(self):
        self.enable("competition_rbihf")
        tigers = Opponent.objects.create(name="Amsterdam Tigers HC")

        body = self.preview([rbihf_row(), away_row()], competition_label="Division 1").json()

        self.assertTrue(body["token"])
        self.assertEqual((body["source"], body["competition_label"], body["scraped_team_name"], body["season_id"]), ("RBIHF", "Division 1", RBIHF_TEAM_NAME, str(self.season.pk)))
        self.assertEqual(body["counts"], {"create": 2, "update": 0, "remove": 0, "unchanged": 0})
        home, away = body["games"]
        self.assertEqual((home["action"], home["game_id"], home["our_side"], home["home"], home["away"]), ("create", "5002", "home", "1st", "Amsterdam Tigers"))
        self.assertEqual(home["location"]["id"], str(self.home_location.pk))
        self.assertEqual((away["our_side"], away["home"], away["venue"], away["location"]), ("away", "Brussels Bears", "Antwerp Ice Rink", None))
        opponents = {row["source_name"]: row for row in body["unknown_opponents"]}
        self.assertEqual(set(opponents), {"Amsterdam Tigers", "Brussels Bears"})
        self.assertEqual([s["id"] for s in opponents["Amsterdam Tigers"]["suggestions"]], [str(tigers.pk)])
        self.assertEqual(opponents["Brussels Bears"]["default"], "create")
        [location] = body["unknown_locations"]
        self.assertEqual((location["source_text"], location["game_ids"]), ("Antwerp Ice Rink", ["5010"]))

    def test_import_applies_the_choices_and_a_second_run_changes_nothing(self):
        self.enable("competition_rbihf")
        tigers = Opponent.objects.create(name="Amsterdam Tigers HC")
        token = self.preview([rbihf_row(), away_row()], competition_label="Division 1").json()["token"]
        choices = {
            "token": token,
            "opponents": {"Amsterdam Tigers": {"opponent_id": str(tigers.pk)}, "Brussels Bears": {"create": True, "name": "Bears Brussels"}},
            "locations": {"Antwerp Ice Rink": {"create": {"name": "Antwerp Ice Rink", "address": "Ring 1", "city": "Antwerp", "zip_code": "2000", "country": "BE"}}},
        }

        self.assertEqual(self.call("post", f"{self.path}/import", self.coach_user, choices).status_code, 403)
        response = self.call("post", f"{self.path}/import", data=choices)

        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual({k: body[k] for k in ("created", "updated", "deleted", "opponents_created", "locations_created")}, {"created": 2, "updated": 0, "deleted": 0, "opponents_created": 1, "locations_created": 1})
        self.assertEqual([(s["source"], s["competition_label"]) for s in body["sources"]], [("RBIHF", "Division 1")])
        self.assertIsNotNone(body["sources"][0]["last_imported"])
        home, away = Event.objects.get(external_game_id="5002"), Event.objects.get(external_game_id="5010")
        self.assertEqual((home.opponent, home.location, home.competition_label), (tigers, self.home_location, "Division 1"))
        tigers.refresh_from_db()
        self.assertEqual(tigers.alternative_names, ["Amsterdam Tigers"])
        self.assertEqual((away.opponent.name, away.opponent.alternative_names, away.location.name), ("Bears Brussels", ["Brussels Bears"], "Antwerp Ice Rink"))
        self.assertTrue(AuditEntry.objects.filter(action="events.fixtures_imported", target_id=str(self.team.pk)).exists())

        again = self.call("post", f"{self.path}/import", data=choices).json()
        self.assertEqual({k: again[k] for k in ("created", "updated", "deleted", "opponents_created", "locations_created")}, {"created": 0, "updated": 0, "deleted": 0, "opponents_created": 0, "locations_created": 0})
        self.assertEqual((Event.objects.count(), Location.objects.count(), Opponent.objects.count()), (2, 2, 2))

        rerun = self.preview([rbihf_row(), away_row()], competition_label="Division 1").json()
        self.assertEqual(rerun["counts"], {"create": 0, "update": 0, "remove": 0, "unchanged": 2})
        self.assertEqual([game["action"] for game in rerun["games"]], ["unchanged", "unchanged"])
        self.assertEqual((rerun["unknown_opponents"], rerun["unknown_locations"], rerun["has_changes"]), ([], [], False))

    def test_unmapped_games_fall_back_to_the_source_spelling_and_per_game_overrides_win(self):
        self.enable("competition_rbihf")
        token = self.preview([rbihf_row(), away_row()]).json()["token"]

        self.call("post", f"{self.path}/import", data={"token": token, "game_locations": {"5002": None, "5010": str(self.home_location.pk)}})

        home, away = Event.objects.get(external_game_id="5002"), Event.objects.get(external_game_id="5010")
        self.assertEqual((home.opponent.name, home.location, away.location), ("Amsterdam Tigers", None, self.home_location))

    def test_a_bad_choice_is_a_422_and_nothing_is_saved(self):
        self.enable("competition_rbihf")
        token = self.preview([rbihf_row()]).json()["token"]

        response = self.call("post", f"{self.path}/import", data={"token": token, "opponents": {"Amsterdam Tigers": {"opponent_id": "00000000-0000-0000-0000-000000000000"}}})

        self.assertEqual(response.status_code, 422)
        self.assertIn("opponents.Amsterdam Tigers", response.json()["errors"])
        self.assertFalse(Event.objects.exists())

    def test_an_expired_or_foreign_token_asks_to_run_the_preview_again(self):
        self.enable("competition_rbihf")
        token = self.preview([rbihf_row()]).json()["token"]
        other_team = type(self.team).objects.create(name="Second Team", short_name="2nd")

        foreign = self.call("post", f"/teams/{other_team.pk}/fixture-sources/import", data={"token": token})
        self.assertEqual((foreign.status_code, list(foreign.json()["errors"])), (422, ["token"]))
        cache.clear()
        expired = self.call("post", f"{self.path}/import", data={"token": token})
        self.assertEqual(expired.status_code, 422)
        self.assertIn("run it again", expired.json()["detail"])
        self.assertFalse(Event.objects.exists())

    # -- re-check and remove --

    def test_recheck_reports_the_diff_records_last_check_and_can_be_applied(self):
        self.enable("competition_rbihf")
        self.import_rows([rbihf_row()])
        entry = self.store()
        source_id = fixture_previews.entry_id(entry)

        with self.patch_fetch(rbihf_sample_html([rbihf_row(hour="20:00")])):
            response = self.call("post", f"{self.path}/{source_id}/recheck")

        body = response.json()
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual([row["game_id"] for row in body["check"]["rescheduled"]], ["5002"])
        self.assertEqual(body["source"]["last_check"], {"rescheduled": 1, "new_games": 0, "removed_games": 0, "error": ""})
        self.assertIsNotNone(body["source"]["last_checked"])
        [game] = body["preview"]["games"]
        self.assertEqual((game["action"], list(game["changes"])), ("update", ["start"]))
        self.assertEqual(self.stored()[0]["last_check"]["rescheduled"], 1)

        applied = self.call("post", f"{self.path}/import", data={"token": body["preview"]["token"]}).json()
        self.assertEqual(applied["updated"], 1)
        self.assertEqual(timezone.localtime(Event.objects.get().start).strftime("%H:%M"), "20:00")

    def test_recheck_of_a_failing_source_is_reported_not_raised(self):
        self.enable("competition_rbihf")
        source_id = fixture_previews.entry_id(self.store())

        with self.patch_fetch(import_sources.ImportSourceError("RBIHF is down")):
            body = self.call("post", f"{self.path}/{source_id}/recheck").json()

        self.assertEqual((body["check"]["error"], body["preview"]), ("RBIHF is down", None))
        self.assertEqual(self.stored()[0]["last_check"]["error"], "RBIHF is down")

    def test_recheck_needs_settings_and_the_feature_and_a_known_source(self):
        source_id = fixture_previews.entry_id(self.store())
        self.assertEqual(self.call("post", f"{self.path}/{source_id}/recheck").status_code, 403)
        self.enable("competition_rbihf")
        self.assertEqual(self.call("post", f"{self.path}/{source_id}/recheck", self.coach_user).status_code, 403)
        self.assertEqual(self.call("post", f"{self.path}/nope/recheck").status_code, 404)

    def test_recheck_all_skips_sources_without_the_feature(self):
        self.enable("competition_rbihf")
        self.store(label="Division 1")
        self.store(source=import_sources.SOURCE_DFEL)

        with self.patch_fetch(rbihf_sample_html([rbihf_row()])):
            body = self.call("post", f"{self.path}/recheck-all").json()

        self.assertEqual(body["season_id"], str(self.season.pk))
        self.assertEqual([r["source"]["source"] for r in body["results"]], ["RBIHF"])
        self.assertEqual(body["results"][0]["check"]["new_games"][0]["game_id"], "5002")
        [skipped] = body["skipped"]
        self.assertEqual(skipped["source"], "DFEL")
        self.assertIn("DFEL fixtures", skipped["reason"])
        self.assertEqual(self.call("post", f"{self.path}/recheck-all", self.coach_user).status_code, 403)

    def test_remove_forgets_the_source_but_keeps_its_games(self):
        self.import_rows([rbihf_row()])
        source_id = fixture_previews.entry_id(self.store())

        self.assertEqual(self.call("delete", f"{self.path}/{source_id}", self.coach_user).status_code, 403)
        self.assertEqual(self.call("delete", f"{self.path}/{source_id}").status_code, 204)

        self.assertEqual(self.stored(), [])
        self.assertEqual(Event.objects.count(), 1)
        self.assertTrue(AuditEntry.objects.filter(action="events.fixture_source_removed").exists())
        self.assertEqual(self.call("delete", f"{self.path}/{source_id}").status_code, 404)

    # -- standings --

    def test_standings_need_the_rbihf_feature(self):
        entry = self.store()
        rows = [{"team_id": RBIHF_TEAM_ID, "team": RBIHF_TEAM_NAME, "points": 6}, {"team_id": "4464", "team": "Tigers", "points": 3}]
        competition = {"type_id": "1", "name": "Regular Competition", "points_rule": "Won: 3 points", "has_overtime": False, "rows": rows}
        standings = {"league": "Division 1", "league_url": "https://www.rbihf.be/league/d1", "rbihf_team_id": RBIHF_TEAM_ID, "competitions": [competition]}
        import_sources.update_entry(self.team, entry, standings={**standings, "updated_at": timezone.now().isoformat(), "error": ""})

        self.assertEqual(self.call("get", f"/teams/{self.team.pk}/standings").status_code, 404)
        self.enable("competition_rbihf")
        [table] = self.call("get", f"/teams/{self.team.pk}/standings", self.coach_user).json()

        self.assertEqual((table["league"], table["competition"]), ("Division 1", "Regular Competition"))
        self.assertEqual([row["is_own_team"] for row in table["rows"]], [True, False])


class PlayDayImportApiTests(FixtureApiTestBase):
    def upload(self, payload, user=None):
        file = SimpleUploadedFile("days.json", json.dumps(payload).encode())
        response = Client().post(BASE + "/fixture-imports/json/preview", {"file": file}, HTTP_HOST=self.host, HTTP_X_SESSION_TOKEN=login_session(user or self.owner))
        connection.set_tenant(self.club)
        return response

    def payload(self, hosted=True):
        game = {"id": "g1", "date": "2026-12-18", "start": "11:45", "end": "13:45", "city": "Mechelen", "teams": ["1st"], "hosted_by_us": hosted, "participants": ["COP", "IHC"]}
        return {"source": "Kalender Promo", "our_code": "COP", "games": [game]}

    def test_preview_and_import_with_the_home_warning(self):
        mechelen = Location.objects.create(name="Ice Mechelen", address="2 St", city="Mechelen", zip_code="2800", country="BE")
        self.assertEqual(self.upload(self.payload(), self.coach_user).status_code, 403)
        self.assertEqual(self.upload({"games": []}).status_code, 422)

        body = self.upload(self.payload()).json()
        [game] = body["games"]
        self.assertEqual((game["action"], game["location"]["id"], game["warning"], game["teams"][0]["name"]), ("create", str(self.home_location.pk), None, "First Team"))

        refused = self.call("post", "/fixture-imports/json/import", data={"token": body["token"], "locations": {"g1": str(mechelen.pk)}})
        self.assertEqual(refused.status_code, 422)
        self.assertIn("locations.g1", refused.json()["errors"])
        self.assertFalse(Event.objects.exists())
        done = self.call("post", "/fixture-imports/json/import", data={"token": body["token"], "locations": {"g1": str(mechelen.pk)}, "acknowledge_warnings": True})
        self.assertEqual(done.json(), {"created": 1, "updated": 0})
        self.assertEqual(Event.objects.get(external_game_id="g1").location, mechelen)
        again = self.call("post", "/fixture-imports/json/import", data={"token": body["token"], "locations": {"g1": str(mechelen.pk)}, "acknowledge_warnings": True})
        self.assertEqual(again.json(), {"created": 0, "updated": 0})
        self.assertTrue(AuditEntry.objects.filter(action="events.play_days_imported").exists())
        cache.clear()
        self.assertEqual(self.call("post", "/fixture-imports/json/import", data={"token": body["token"]}).status_code, 422)
