"""Competitions: the seeded club.Competition rows, which clubs may use them (features), resolving a game to its data
source class, and the per-game score refresh of the ice hockey sources. No network: every request is mocked."""

import importlib
from types import SimpleNamespace
from unittest import mock

import requests
from django.contrib.admin.sites import AdminSite
from django.db import connection
from django.test import RequestFactory
from django_tenants.utils import schema_context

from club.models import Competition, Sport
from events.admin import EventAdmin
from events.competition.base import CompetitionBaseClass
from events.competition.hockey import CEHL, DFEL, IJNL, RBIHF, season_code
from events.models import Event, Location
from events.services import dfel_import
from events.services.competitions import CompetitionFetchError, available_competitions, competition_allowed, competition_class, competition_for, fetch_game_info
from features.entitlements import Entitlements
from features.models import Feature

from .tests_imports import DFEL_SOURCE_ID, DFEL_TEAM_ID, DFEL_TEAM_PAGE_HTML, IJNL_SOURCE_ID, ClubTestBase, dfel_row, dfel_schedule, http_response, ijnl_game_data

SEED = importlib.import_module("club.migrations.0010_seed_competitions")


class SeededCompetitionTests(ClubTestBase):
    def test_the_migration_seeded_one_row_per_old_competition(self):
        rows = {competition.name: competition for competition in Competition.objects.select_related("sport", "feature")}

        self.assertTrue({"RBIHF", "CEHL", "DFEL", "IJNL"} <= set(rows))
        for name, code in [("RBIHF", "competition_rbihf"), ("DFEL", "competition_dfel"), ("IJNL", "competition_ijnl"), ("CEHL", None)]:
            with self.subTest(name=name):
                row = rows[name]
                self.assertEqual((row.module, row.sport.name), ("events.competition.hockey", "Ice hockey"))
                self.assertEqual(row.feature.code if row.feature else None, code)
                self.assertTrue(issubclass(competition_class(row), CompetitionBaseClass))

    def test_the_features_match_the_catalog(self):
        from features.catalog import CATALOG

        for code in ("competition_rbihf", "competition_dfel", "competition_ijnl"):
            feature = Feature.objects.get(code=code)
            self.assertEqual((feature.name, feature.description), (CATALOG[code].name, CATALOG[code].description))

    def test_seeding_is_idempotent_and_reversible(self):
        from django.apps import apps

        with schema_context("public"):
            editor = SimpleNamespace(connection=connection)
            SEED.seed(apps, editor)
            self.assertEqual(Competition.objects.filter(name__in=SEED.COMPETITIONS).count(), 4)
            self.assertEqual(Sport.objects.filter(name="Ice hockey").count(), 1)

            SEED.unseed(apps, editor)
            self.assertFalse(Competition.objects.filter(name__in=SEED.COMPETITIONS).exists())
            SEED.seed(apps, editor)
            self.assertEqual(Competition.objects.filter(name__in=SEED.COMPETITIONS).count(), 4)

    def test_seeding_is_skipped_in_a_club_schema(self):
        from django.apps import apps

        Competition.objects.filter(name="CEHL").delete()
        SEED.seed(apps, SimpleNamespace(connection=connection))  # we are in the club schema here
        self.assertFalse(Competition.objects.filter(name="CEHL").exists())


class AvailableCompetitionsTests(ClubTestBase):
    def names(self, club_or_features=None):
        return list(available_competitions(club_or_features).values_list("name", flat=True))

    def test_only_competitions_whose_feature_the_club_has(self):
        self.assertEqual(self.names(), [])
        self.enable("competition_dfel")

        self.assertEqual(self.names(), ["DFEL"])
        self.assertEqual(self.names(self.club), ["DFEL"])
        self.assertTrue(competition_allowed("DFEL"))
        self.assertFalse(competition_allowed("RBIHF"))

    def test_a_competition_without_a_feature_is_offered_to_nobody(self):
        Competition.objects.filter(name="CEHL").update(feature=None)
        self.assertNotIn("CEHL", self.names(Entitlements(bypass=True)))
        self.assertIn("RBIHF", self.names(Entitlements(bypass=True)))

    def test_a_revoked_feature_hides_it_again(self):
        self.enable("competition_rbihf")
        self.disable("competition_rbihf")

        self.assertEqual(self.names(), [])

    def test_the_event_admin_dropdown_follows_the_features(self):
        self.enable("competition_ijnl")
        request = RequestFactory().get("/")
        request.tenant = self.club

        field = EventAdmin(Event, AdminSite()).formfield_for_dbfield(Event._meta.get_field("competition"), request)

        self.assertEqual([value for value, _label in field.choices], ["", "IJNL"])


class CompetitionResolutionTests(ClubTestBase):
    def game(self, competition="DFEL", **kwargs):
        return self.make_event(kind=Event.EventKind.GAME, competition=competition, external_game_id=kwargs.pop("external_game_id", "g1"), **kwargs)

    def test_competition_for_is_gated_on_the_feature(self):
        self.assertIsNone(competition_for(self.game()))
        self.enable("competition_dfel")
        self.assertIsInstance(competition_for(self.game()), DFEL)
        self.assertIsNone(competition_for(self.game(competition="")))

    def test_a_full_class_path_in_module_also_resolves(self):
        self.assertIs(competition_class(Competition(name="RBIHF", module="events.competition.hockey.RBIHF")), RBIHF)

    def test_a_module_that_resolves_to_nothing_is_a_configuration_error(self):
        with self.assertRaises(CompetitionFetchError):
            competition_class(Competition(name="Nope", module="events.competition.nowhere"))


class HockeyTestBase(ClubTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        with schema_context(cls.club.schema_name):
            cls.home_location = Location.objects.create(name="Home Arena", address="1 St", city="Mechelen", zip_code="2800", country="BE", is_home=True)

    def setUp(self):
        super().setUp()
        # The timeline half of fetch_game_info belongs to events.services.game_timeline (tested there).
        patcher = mock.patch("events.services.game_timeline.refresh_timeline", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)


class RBIHFAndCEHLTests(HockeyTestBase):
    def game(self, competition, **kwargs):
        return self.make_event(kind=Event.EventKind.GAME, competition=competition, external_game_id="1234", **kwargs)

    def test_season_code(self):
        game = self.game("RBIHF")
        self.assertEqual(season_code(game), f"{self.season.start_date:%y}{self.season.end_date:%y}")

    def test_rbihf_maps_home_and_away_scores(self):
        response = mock.Mock(status_code=200)
        response.json.return_value = {"live": True, "scoreA": 4, "scoreB": 1}
        home, away = self.game("RBIHF", location=self.home_location), self.game("RBIHF")
        with mock.patch("events.competition.hockey.requests.get", return_value=response) as get:
            RBIHF().update_game_information(home)
            RBIHF().update_game_information(away)

        home.refresh_from_db()
        away.refresh_from_db()
        self.assertEqual((home.score_for, home.score_against, home.is_live), (4, 1, True))
        self.assertEqual((away.score_for, away.score_against), (1, 4))
        self.assertEqual(get.call_args.kwargs["params"]["gameNr"], "1234")

    def test_cehl_reads_live_from_the_timeline_and_scores_from_score_php(self):
        timeline, score = mock.Mock(status_code=200, text="{}"), mock.Mock(status_code=200)
        timeline.json.return_value = {"live": 1}
        score.json.return_value = {"scoreA": 2, "scoreB": 3}
        game = self.game("CEHL", location=self.home_location)
        with mock.patch("events.competition.hockey.requests.get", side_effect=[timeline, score]):
            source = CEHL()
            source.update_game_information(game)

        game.refresh_from_db()
        self.assertEqual((game.score_for, game.score_against, game.is_live), (2, 3, True))
        self.assertEqual(source.timeline_payload, (game.pk, "{}"))

    def test_every_request_passes_a_timeout_and_a_timeout_is_a_fetch_error(self):
        self.enable("competition_rbihf")
        with mock.patch("events.competition.hockey.requests.get", return_value=mock.Mock(status_code=500)) as get:
            self.assertTrue(fetch_game_info(self.game("RBIHF")))
        for call in get.call_args_list:
            self.assertEqual(call.kwargs.get("timeout"), dfel_import.REQUEST_TIMEOUT_SECONDS)

        with mock.patch("events.competition.hockey.requests.get", side_effect=requests.Timeout("read timed out")), self.assertRaises(CompetitionFetchError):
            fetch_game_info(self.game("RBIHF"))


class DFELGameInfoTests(HockeyTestBase):
    def game(self, **kwargs):
        kwargs.setdefault("kind", Event.EventKind.GAME)
        kwargs.setdefault("competition", "DFEL")
        kwargs.setdefault("external_game_id", "g1")
        kwargs.setdefault("external_source_id", DFEL_SOURCE_ID)
        return self.make_event(**kwargs)

    def patch_fetch(self, rows):
        return mock.patch("events.services.dfel_import.requests.get", side_effect=[http_response(DFEL_TEAM_PAGE_HTML), http_response(f"rosterchief({dfel_schedule(rows)})")])

    def test_scores_and_live_state(self):
        home = self.game(location=self.home_location)
        with self.patch_fetch([dfel_row("g1", days=0, home_score=4, away_score=1, status=1)]):
            DFEL().update_game_information(home)
        home.refresh_from_db()
        self.assertEqual((home.score_for, home.score_against, home.is_live), (4, 1, True))

        away = self.game(is_live=True)
        with self.patch_fetch([dfel_row("g1", days=0, home_id="70774", away_id=DFEL_TEAM_ID, home_score=4, away_score=1, status=1, ended=True)]):
            DFEL().update_game_information(away)
        away.refresh_from_db()
        self.assertEqual((away.score_for, away.score_against, away.is_live), (1, 4, False))

    def test_missing_ids_or_game_are_errors(self):
        with self.patch_fetch([dfel_row("g1")]), self.assertRaises(ValueError):
            DFEL().update_game_information(self.game(external_game_id="gone"))
        for kwargs in [{"external_source_id": ""}, {"external_source_id": "70779"}, {"external_game_id": ""}]:
            with self.subTest(**kwargs), mock.patch("events.services.dfel_import.requests.get") as get, self.assertRaises(ValueError):
                DFEL().update_game_information(self.game(**kwargs))
            get.assert_not_called()

    def test_fetch_game_info_is_gated_on_the_feature_and_reports_failures(self):
        game = self.game(location=self.home_location)
        with mock.patch("events.services.dfel_import.requests.get") as get:
            self.assertFalse(fetch_game_info(game))
        get.assert_not_called()

        self.enable("competition_dfel")
        with self.patch_fetch([dfel_row("g1", days=0, home_score=3, away_score=0, status=1)]):
            self.assertTrue(fetch_game_info(game))
        game.refresh_from_db()
        self.assertEqual((game.score_for, game.score_against, game.is_live), (3, 0, True))

        with self.patch_fetch([dfel_row("g1")]), self.assertRaises(CompetitionFetchError):
            fetch_game_info(self.game(external_game_id="gone"))


class IJNLGameInfoTests(HockeyTestBase):
    GAME_ID = "49a66dbb-eaa2-4527-9c8b-31ee324d736b"

    def game(self, **kwargs):
        kwargs.setdefault("kind", Event.EventKind.GAME)
        kwargs.setdefault("competition", "IJNL")
        kwargs.setdefault("external_game_id", self.GAME_ID)
        kwargs.setdefault("external_source_id", IJNL_SOURCE_ID)
        return self.make_event(**kwargs)

    def patch_fetch(self, body):
        return mock.patch("events.services.ijnl_import.requests.get", return_value=http_response(body))

    def test_scores_and_live_state(self):
        home = self.game(location=self.home_location)
        with self.patch_fetch(ijnl_game_data(state="live", home="3", away="1")) as get:
            IJNL().update_game_information(home)
        home.refresh_from_db()
        self.assertEqual((home.score_for, home.score_against, home.is_live), (3, 1, True))
        self.assertEqual(get.call_args.args[0], f"https://www.ijshockey.nl/competities/wedstrijd/{self.GAME_ID}/data")
        self.assertEqual(get.call_args.kwargs["params"], {"part": "live"})

        away = self.game(is_live=True)
        with self.patch_fetch(ijnl_game_data(state="definitief", home="4", away="0")):
            IJNL().update_game_information(away)
        away.refresh_from_db()
        self.assertEqual((away.score_for, away.score_against, away.is_live), (0, 4, False))

    def test_a_missing_game_id_is_an_error_without_fetching(self):
        with mock.patch("events.services.ijnl_import.requests.get") as get, self.assertRaises(ValueError):
            IJNL().update_game_information(self.game(external_game_id=""))
        get.assert_not_called()

    def test_fetch_game_info_is_gated_on_the_feature(self):
        game = self.game(location=self.home_location)
        with mock.patch("events.services.ijnl_import.requests.get") as get:
            self.assertFalse(fetch_game_info(game))
        get.assert_not_called()

        self.enable("competition_ijnl")
        with self.patch_fetch(ijnl_game_data(state="live", home="1", away="0")):
            self.assertTrue(fetch_game_info(game))
        game.refresh_from_db()
        self.assertEqual((game.score_for, game.score_against), (1, 0))
