"""Fixture imports API (``/teams/{id}/fixture-sources``, ``/teams/{id}/standings``, ``/fixture-imports/json``):
feature gating, permissions, preview shape with mapping problems, applying choices, token expiry, idempotency,
re-checks and forgetting a source. No network: every fetch is mocked as in events/tests_imports."""

import datetime
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

    def test_run_checks_or_imports_the_stored_sources(self):
        self.enable("competition_rbihf")
        self.store()
        with self.patch_fetch(rbihf_sample_html([rbihf_row()])):
            checked = import_sources.run_team(self.club, self.team, apply=False)
            self.assertEqual((checked["sources"], checked["new"], checked["created"]), (1, 1, 0))
            imported = import_sources.run_team(self.club, self.team, apply=True)
        self.assertEqual((imported["mode"], imported["created"], imported["errors"]), ("import", 1, 0))
        self.assertEqual(Event.objects.filter(teams=self.team, external_game_id="5002").count(), 1)

    def test_run_endpoint_queues_the_task_for_settings_holders_only(self):
        self.enable("competition_rbihf")
        self.store()
        fake = mock.Mock(id="abc", state="PENDING", result=None)
        with mock.patch("events.tasks.run_team_fixtures.delay", return_value=fake) as delay:
            denied = self.call("post", f"{self.path}/run", self.coach_user, {"apply": True})
            ok = self.call("post", f"{self.path}/run", None, {"apply": True})
        self.assertEqual(denied.status_code, 403)
        self.assertEqual((ok.status_code, ok.json()["id"], ok.json()["done"]), (202, "abc", False))
        delay.assert_called_once_with(str(self.team.pk), True)

    def played_game(self, game_id="7001"):
        past = timezone.now() - datetime.timedelta(days=3)
        event = Event.objects.create(title="Played", kind=Event.EventKind.GAME, start=past, competition="RBIHF", external_game_id=game_id)
        event.teams.add(self.team)
        return event

    def fake_source(self):
        class Source:
            def update_game_information(self, event):
                event.score_for, event.score_against = 5, 2
                event.save(update_fields=["score_for", "score_against"])

            def fetch_timeline(self, event):
                return None

        return Source()

    def test_update_past_games_fetches_the_scores(self):
        from events.services import game_timeline

        event = self.played_game()
        with mock.patch.object(game_timeline, "competition_for", return_value=self.fake_source()):
            counts = game_timeline.update_past_games(self.team)
        event.refresh_from_db()
        self.assertEqual((counts["games"], counts["updated"], event.score_for, event.score_against), (1, 1, 5, 2))

    def test_update_game_endpoint_and_update_past_queue(self):
        from events.services import game_timeline

        event = self.played_game()
        with mock.patch.object(game_timeline, "competition_for", return_value=self.fake_source()):
            ok = self.call("post", f"/events/{event.pk}/update-game")
        self.assertEqual((ok.status_code, ok.json()["score_for"], ok.json()["has_game_source"]), (200, 5, True))
        fake = mock.Mock(id="xyz", state="PENDING", result=None)
        with mock.patch("events.tasks.update_past_games.delay", return_value=fake) as delay:
            denied = self.call("post", f"{self.path}/update-past", self.coach_user)
            queued = self.call("post", f"{self.path}/update-past")
        self.assertEqual((denied.status_code, queued.status_code), (403, 202))
        delay.assert_called_once_with(str(self.team.pk))

    def test_score_and_live_flag_can_be_set_by_hand(self):
        event = self.played_game()
        path = f"/events/{event.pk}/score"
        ok = self.call("post", path, data={"score_for": 3, "score_against": 1, "is_live": True})
        self.assertEqual((ok.status_code, ok.json()["score_for"], ok.json()["score_against"], ok.json()["is_live"]), (200, 3, 1, True))
        event.refresh_from_db()
        self.assertEqual((event.score_for, event.score_against, event.is_live), (3, 1, True))
        half = self.call("post", path, data={"score_for": 2, "score_against": None})
        self.assertEqual(half.status_code, 422)
        negative = self.call("post", path, data={"score_for": -1, "score_against": 0})
        self.assertEqual(negative.status_code, 422)
        coach = self.call("post", path, self.coach_user, {"score_for": 4, "score_against": 4})
        self.assertEqual(coach.status_code, 200)  # the head coach of the team may edit its games
        with schema_context(self.club.schema_name):
            fan = Member.objects.create(first_name="Fay", last_name="Fan", email="fan@fiapi.test")
        fan_user, _ = grant_login(self.club, fan)
        self.assertEqual(self.call("post", path, fan_user, {"score_for": 9, "score_against": 9}).status_code, 403)
        cleared = self.call("post", path, data={"score_for": None, "score_against": None, "is_live": False})
        self.assertEqual((cleared.status_code, cleared.json()["score_for"], cleared.json()["is_live"]), (200, None, False))
        training = Event.objects.create(title="Practice", kind=Event.EventKind.TRAINING, start=timezone.now())
        self.assertEqual(self.call("post", f"/events/{training.pk}/score", data={"score_for": 1, "score_against": 0}).status_code, 422)

    def test_game_sheet_endpoint(self):
        from events.models import GameTimeline
        from events.tests_game_stats import DOCUMENT

        event = self.played_game()
        self.assertEqual(self.call("get", f"/events/{event.pk}/game-sheet").status_code, 404)
        self.assertFalse(self.call("get", f"/events/{event.pk}").json()["has_game_sheet"])
        GameTimeline.objects.create(event=event, source="RBIHF", data=DOCUMENT, status="final")
        body = self.call("get", f"/events/{event.pk}/game-sheet").json()
        self.assertTrue(body["periods"])
        self.assertTrue(self.call("get", f"/events/{event.pk}").json()["has_game_sheet"])

    def test_game_sheet_includes_the_lineup_when_stored(self):
        from events.models import GameTimeline
        from events.services.game_stats import sync_game_stats
        from events.tests_game_stats import DOCUMENT

        event = self.played_game()
        side = "home" if event.is_home_game else "away"
        other = "away" if side == "home" else "home"
        document = {**DOCUMENT, "lineups": {side: [{"pos": "DF", "number": 5, "license": "1", "name": "Some Body"}], other: [{"pos": "GO", "number": 1, "license": "2", "name": "Their Keeper"}]}}
        GameTimeline.objects.create(event=event, source="RBIHF", data=document, status="final")
        sync_game_stats(event, document)
        body = self.call("get", f"/events/{event.pk}/game-sheet").json()
        self.assertEqual([p["name"] for p in body["lineup"]["ours"]], ["Some Body"])
        self.assertEqual(body["lineup"]["theirs"][0]["pos"], "GO")

    def test_game_moments_are_for_settings_holders(self):
        from events.models import GameMoment

        event = self.played_game()
        GameMoment.objects.create(event=event, key="g1", kind=GameMoment.Kind.GAME_STARTED, notifiable=False)
        GameMoment.objects.create(event=event, key="g2", kind=GameMoment.Kind.GAME_FINAL, delivered_at=timezone.now())
        self.assertEqual(self.call("get", f"/events/{event.pk}/game-moments", self.coach_user).status_code, 403)
        body = self.call("get", f"/events/{event.pk}/game-moments").json()
        self.assertEqual(sorted(m["state"] for m in body), ["catch_up", "pushed"])

    def test_following_a_game_and_a_team(self):
        event = self.played_game()
        self.assertEqual(self.call("put", f"/events/{event.pk}/follow").json(), {"directly": True, "via_team": False})
        self.assertEqual(self.call("delete", f"/events/{event.pk}/follow").json()["directly"], False)
        body = self.call("put", f"/events/follow-teams/{self.team.pk}").json()
        self.assertEqual(body["team_ids"], [str(self.team.pk)])
        self.assertTrue(self.call("get", f"/events/{event.pk}/follow").json()["via_team"])
        self.assertEqual(self.call("delete", f"/events/follow-teams/{self.team.pk}").json()["team_ids"], [])

    def test_remove_forgets_the_source_but_keeps_its_games(self):
        self.import_rows([rbihf_row()])
        source_id = fixture_previews.entry_id(self.store())

        self.assertEqual(self.call("delete", f"{self.path}/{source_id}", self.coach_user).status_code, 403)
        self.assertEqual(self.call("delete", f"{self.path}/{source_id}").status_code, 204)

        self.assertEqual(self.stored(), [])
        self.assertEqual(Event.objects.count(), 1)
        self.assertTrue(AuditEntry.objects.filter(action="events.fixture_source_removed").exists())
        self.assertEqual(self.call("delete", f"{self.path}/{source_id}").status_code, 404)

    # -- remove together with the unplayed games --

    def _delete_setup(self):
        """Five imported games (upcoming, past, scored, with an answer, upcoming) + a manual game + another team's import."""
        from events.models import Attendance, Event
        from teams.models import Team

        self.import_rows([rbihf_row("1", days=5), rbihf_row("2", days=6), rbihf_row("3", days=7), rbihf_row("4", days=8), rbihf_row("5", days=9)])
        games = {e.external_game_id: e for e in Event.objects.filter(external_game_id__in=["1", "2", "3", "4", "5"])}
        Event.objects.filter(pk=games["2"].pk).update(start=timezone.now() - timedelta(days=2))
        Event.objects.filter(pk=games["3"].pk).update(score_for=2, score_against=1)
        Attendance.objects.create(event=games["4"], member=self.coach, status="present")
        manual = Event.objects.create(title="Own game", kind=Event.EventKind.GAME, start=timezone.now() + timedelta(days=3))
        manual.teams.add(self.team)
        other = Team.objects.create(name="Other Team", short_name="OT")
        self.import_rows([rbihf_row("1", days=5)], team=other)
        self.store(team=other)
        source_id = fixture_previews.entry_id(self.store())
        return games, manual, other, source_id

    def test_remove_with_games_deletes_only_unplayed_games_of_that_source(self):
        from events.models import Event

        _games, manual, other, source_id = self._delete_setup()
        self.assertEqual(self.call("get", f"{self.path}/{source_id}/games").json(), {"deletable": 2, "played": 2, "with_data": 1})

        response = self.call("delete", f"{self.path}/{source_id}?delete_games=true")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"deleted": 2, "kept_played": 2, "kept_with_data": 1})
        self.assertEqual(self.stored(), [])
        self.assertEqual(set(Event.objects.filter(teams=self.team).values_list("external_game_id", flat=True)), {"2", "3", "4", ""})
        self.assertTrue(Event.objects.filter(pk=manual.pk).exists())
        self.assertEqual(Event.objects.filter(teams=other).count(), 1)
        self.assertEqual(len(self.stored(other)), 1)
        self.assertEqual(AuditEntry.objects.filter(action="event.deleted").count(), 2)
        self.assertTrue(AuditEntry.objects.filter(action="events.fixture_source_removed").exists())

    def test_remove_with_games_needs_the_settings_permission_and_changes_nothing_otherwise(self):
        from events.models import Event

        _games, _manual, _other, source_id = self._delete_setup()
        before = Event.objects.count()

        self.assertEqual(self.call("delete", f"{self.path}/{source_id}?delete_games=true", self.coach_user).status_code, 403)
        self.assertEqual(self.call("get", f"{self.path}/{source_id}/games", self.coach_user).status_code, 403)

        self.assertEqual(Event.objects.count(), before)
        self.assertEqual(len(self.stored()), 1)

    def test_delete_team_with_games_removes_unplayed_games_of_its_sources(self):
        from events.models import Event
        from teams.models import Team

        _games, manual, other, _source_id = self._delete_setup()
        StaffAssignment.objects.filter(team=self.team).delete()

        self.assertEqual(self.call("get", f"/teams/{self.team.pk}/fixture-games").json(), {"deletable": 2, "played": 2, "with_data": 1})
        self.assertEqual(self.call("delete", f"/teams/{self.team.pk}?delete_games=true", self.coach_user).status_code, 403)
        response = self.call("delete", f"/teams/{self.team.pk}?delete_games=true")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"deleted": 2, "kept_played": 2, "kept_with_data": 1})
        self.assertFalse(Team.objects.filter(pk=self.team.pk).exists())
        self.assertEqual(set(Event.objects.filter(external_game_id__in=["1", "2", "3", "4", "5"]).exclude(teams=other).values_list("external_game_id", flat=True)), {"2", "3", "4"})
        self.assertTrue(Event.objects.filter(pk=manual.pk).exists())
        self.assertEqual(Event.objects.filter(teams=other).count(), 1)

    def test_delete_team_without_games_keeps_every_game(self):
        from events.models import Event

        _games, _manual, _other, _source_id = self._delete_setup()
        StaffAssignment.objects.filter(team=self.team).delete()
        before = Event.objects.count()

        self.assertEqual(self.call("delete", f"/teams/{self.team.pk}").status_code, 204)

        self.assertEqual(Event.objects.count(), before)

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
