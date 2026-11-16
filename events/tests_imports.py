"""Fixture imports: RBIHF / DFEL / IJNL scrapers and the shared diff/apply core, the JSON play-day import, the stored
import sources (Team.import_sources) and the weekly re-check task. No network: every fetch is mocked."""

import datetime
import json
from datetime import UTC, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

import requests
from django.core.cache import cache
from django.db import connection
from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from django_tenants.utils import schema_context

from club.models import Club, Domain
from club.services import create_club, grant_login
from controlpanel.tests import make_user
from events.models import Event, Location, Opponent, Season
from events.services import dfel_import, ijnl_import, import_sources, json_import
from events.services.rbihf_import import RBIHFImportError, apply_plan, build_plan, extract_team_id, parse_fixtures, suggested_location, suggested_opponent
from events.tasks import refresh_fixtures
from features.services import set_override
from members.models import AuditEntry, Member
from members.services.errors import Invalid, NotAllowed
from notifications.models import InboxNotification
from teams.models import Team

# -- shared scaffolding (also used by tests_competitions / tests_standings) -------------------------------------------


class ClubTestBase(TestCase):
    """One club (owner = its admin), one current season, one team ("First Team" / "1st"). Every test runs inside the
    club's schema. Competition features are off unless a test calls ``enable``."""

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        connection.set_schema_to_public()

    @classmethod
    def setUpTestData(cls):
        cls.owner = make_user(f"owner@{cls.__name__.lower()}.test", first="Olivia", last="Owner")
        public = Club(schema_name="public", name="Platform", owner=cls.owner)
        public.auto_create_schema = False
        public.save()
        Domain.objects.create(domain="testserver", tenant=public, is_primary=True)
        cls.club = create_club("Import FC", cls.owner)
        today = datetime.date.today()
        with schema_context(cls.club.schema_name):
            cls.season = Season.objects.create(start_date=today - timedelta(days=30), end_date=today + timedelta(days=300))
            cls.team = Team.objects.create(name="First Team", short_name="1st")
        cls.future = timezone.now() + timedelta(days=7)

    def setUp(self):
        self.enterContext(schema_context(self.club.schema_name))
        cache.clear()
        self.addCleanup(cache.clear)

    def enable(self, *codes, club=None):
        for code in codes:
            set_override(club or self.club, code, enabled=True)

    def disable(self, *codes, club=None):
        for code in codes:
            set_override(club or self.club, code, enabled=False)

    def make_event(self, **kwargs):
        kwargs.setdefault("title", "Game")
        kwargs.setdefault("start", self.future)
        return Event.objects.create(**kwargs)


RBIHF_TEAM_ID = "4460"
RBIHF_TEAM_NAME = "Sportoase Antwerp Phantoms"


def rbihf_sample_html(rows):
    """A trimmed stand-in for an RBIHF team page (https://www.rbihf.be/league/team/<id>). ``rows``: dicts with game_id,
    date ("YYYY-MM-DD"), hour ("HH:MM"), venue, home_id, home_name, visit_id, visit_name."""
    row_html = "".join(
        f'<tr><td class="game-nr"><a href="/game/{r["game_id"]}" title="Game {r["game_id"]}">{r["game_id"]}</a></td>'
        f'<td class="date">{r["date"]}</td><td class="hour">{r["hour"]}</td><td>{r["venue"]}</td>'
        f'<td><a href="/league/team/{r["home_id"]}" title="{r["home_name"]}">{r["home_name"]}</a></td>'
        f'<td><a href="/league/team/{r["visit_id"]}" title="{r["visit_name"]}">{r["visit_name"]}</a></td></tr>'
        for r in rows
    )
    return f"""<html><body>
    <div class="block"><div class="block-header"><h2>{RBIHF_TEAM_NAME}</h2></div></div>
    <div class="block"><div class="block-header"><h2 id="games-upcoming">Upcoming games</h2></div>
    <div class="block-content"><table>
    <tr><th class="game-nr">#</th><th class="date">Date</th><th class="hour">Hour</th><th>Location</th><th>Home</th><th>Visit</th></tr>
    {row_html}
    </table></div></div>
    </body></html>"""


def rbihf_row(game_id="5002", *, days=7, date=None, hour="12:15", venue="Deurne", visit_name="Amsterdam Tigers", home_id=RBIHF_TEAM_ID):
    date = date or (timezone.now() + timedelta(days=days)).strftime("%Y-%m-%d")
    return {"game_id": game_id, "date": date, "hour": hour, "venue": venue, "home_id": home_id, "home_name": RBIHF_TEAM_NAME, "visit_id": "4464", "visit_name": visit_name}


def rbihf_away_row(game_id="5010", venue="Deurne"):
    return {"game_id": game_id, "date": "2026-09-20", "hour": "18:30", "venue": venue, "home_id": "4500", "home_name": "Brussels Bears", "visit_id": RBIHF_TEAM_ID, "visit_name": RBIHF_TEAM_NAME}


DFEL_TEAM_ID = "70779"
DFEL_DIVISION_ID = "21691"
DFEL_SOURCE_ID = "70779/21691"
DFEL_TEAM_NAME = "Sharks Woman Mechelen"
DFEL_TEAM_PAGE_HTML = """<html><body><div id="team"></div><script>
var cfg = JSON.parse('{\\u0022domNode\\u0022: \\u0022#team\\u0022, \\u0022divisionId\\u0022: \\u002221691\\u0022, \\u0022apiKey\\u0022: \\u0022abc123def456\\u0022, \\u0022sport\\u0022: \\u0022icehockey\\u0022}');
</script></body></html>"""


def dfel_row(game_id, *, days=7, home_id=DFEL_TEAM_ID, home_name=DFEL_TEAM_NAME, away_id="70757", away_name="FASS Berlin", home_score=0, away_score=0, status=0, ended=False, city="Dinslaken", longname="Dinslaken - Eishalle"):
    """One hockeydata Schedule row, trimmed to the fields dfel_import reads, ``days`` from today at 18:00 UTC."""
    start = (timezone.now() + timedelta(days=days)).astimezone(UTC).replace(hour=18, minute=0, second=0, microsecond=0)
    address = json.dumps({"headline1": "Eishalle", "street1": "Some Straße 1", "zipcode": "46535", "city": city, "country": "GER"}) if city is not None else ""
    return {
        "id": game_id,
        "divisionId": int(DFEL_DIVISION_ID),
        "scheduledGameStart": timezone.localtime(start).strftime("%Y-%m-%dT%H:%M:%S"),
        "gameUtcTimestamp": int(start.timestamp() * 1000),
        "homeTeamId": int(home_id),
        "homeTeamLongName": home_name,
        "awayTeamId": int(away_id),
        "awayTeamLongName": away_name,
        "homeTeamScore": home_score,
        "awayTeamScore": away_score,
        "gameStatus": status,
        "gameHasEnded": ended,
        "labels": ["LIVE"] if status == 1 else [],
        "location": {"id": 1, "longname": longname, "shortname": "Eishalle", "address": address},
        "dateIsToBeDetermined": False,
    }


def dfel_schedule(rows):
    return json.dumps({"statusId": 1, "statusMsg": "Ok", "data": {"rows": rows}})


def http_response(text, status_code=200):
    return mock.Mock(status_code=status_code, text=text)


IJNL_TEAM_ID = "64414"
IJNL_SEASON = "2026/27"
IJNL_SOURCE_ID = f"{IJNL_TEAM_ID}/{IJNL_SEASON}"
IJNL_TEAM_NAME = "Sharks Ladies Mechelen"
IJNL_URL = "https://www.ijshockey.nl/competities/team/64414?seizoen=2026%2F27"


def ijnl_game(game_id, *, days=7, time_text="16:00", home_id=IJNL_TEAM_ID, home_name=IJNL_TEAM_NAME, away_id="32609", away_name="Amsterdam Tigers Vrouwen", undated=False, time_unknown=False, state="gepland"):
    """One game in an ijshockey.nl team ``/data`` response, ``days`` from now."""
    return {
        "gameGuid": game_id,
        "datum": None if undated else (timezone.now() + timedelta(days=days)).strftime("%Y-%m-%d"),
        "tijd": None if time_unknown else time_text,
        "datumOnbekend": undated,
        "tijdOnbekend": time_unknown,
        "staat": state,
        "competitie": {"league": "Vrouwen", "onderdeelId": 21808, "onderdeel": "Vrouwen Eredivisie"},
        "thuis": {"teamId": int(home_id), "naam": home_name, "score": None},
        "uit": {"teamId": int(away_id), "naam": away_name, "score": None},
        "locatie": None,
    }


def ijnl_team_data(programma, uitslagen=(), season=IJNL_SEASON):
    return json.dumps(
        {
            "team": {"teamId": int(IJNL_TEAM_ID), "naam": IJNL_TEAM_NAME, "korteNaam": IJNL_TEAM_NAME, "club": IJNL_TEAM_NAME},
            "seizoen": season,
            "onderdelen": [{"onderdeelId": 21808, "onderdeel": "Vrouwen Eredivisie"}],
            "programma": list(programma),
            "uitslagen": list(uitslagen),
        }
    )


def ijnl_game_data(state="live", home="2", away="1"):
    """An ijshockey.nl game ``/data?part=live`` response."""
    scoreboard = f'<section class="mp-scoreboard"><div class="mp-result"><span class="mp-status">Periode 2</span><div class="mp-score" aria-label="{home} tegen {away}"><span>{home}</span><span>{away}</span></div></div></section>'
    return json.dumps({"title": "Sharks - Tigers", "state": state, "refreshSeconds": 60, "sections": {"scoreboard": scoreboard}})


# -- RBIHF ---------------------------------------------------------------------------------------------------------------


class RBIHFPastGamesTests(SimpleTestCase):
    """The played-games table of a team page is only read with include_past."""

    def page(self):
        played = (
            '<div class="block"><h2 id="games-past">Played games</h2><table>'
            "<tr><th>#</th><th>Date</th><th>Hour</th><th>Location</th><th>Home</th><th>Score</th><th>Visit</th></tr>"
            f'<tr><td><a href="/game/4001">4001</a></td><td>2020-01-05</td><td>10:00</td><td>Deurne</td>'
            f'<td><a href="/league/team/{RBIHF_TEAM_ID}" title="{RBIHF_TEAM_NAME}">x</a></td><td>3-1</td>'
            '<td><a href="/league/team/4464" title="Amsterdam Tigers">y</a></td></tr></table></div>'
        )
        return rbihf_sample_html([rbihf_row("5002")]).replace("</body>", played + "</body>")

    def test_only_upcoming_by_default(self):
        from events.services.rbihf_import import parse_fixtures

        _, fixtures = parse_fixtures(self.page(), RBIHF_TEAM_ID)
        self.assertEqual([f.external_game_id for f in fixtures], ["5002"])

    def test_include_past_adds_the_played_games(self):
        from events.services.rbihf_import import parse_fixtures

        _, fixtures = parse_fixtures(self.page(), RBIHF_TEAM_ID, include_past=True)
        self.assertEqual(sorted(f.external_game_id for f in fixtures), ["4001", "5002"])
        played = next(f for f in fixtures if f.external_game_id == "4001")
        self.assertTrue(played.is_home)
        self.assertEqual(played.opponent_name, "Amsterdam Tigers")


class RBIHFParseTests(SimpleTestCase):
    """Pure parsing: no network, no database."""

    def test_parses_team_name_and_fixtures(self):
        team_name, fixtures = parse_fixtures(rbihf_sample_html([rbihf_row(date="2026-09-12"), rbihf_away_row(venue="Antwerp Ice Rink")]), RBIHF_TEAM_ID)

        self.assertEqual(team_name, RBIHF_TEAM_NAME)
        self.assertEqual(len(fixtures), 2)

    def test_home_and_away_fixtures(self):
        _name, [home, away] = parse_fixtures(rbihf_sample_html([rbihf_row(), rbihf_away_row()]), RBIHF_TEAM_ID)

        self.assertEqual((home.is_home, home.opponent_name, home.venue_text, home.external_game_id), (True, "Amsterdam Tigers", "Deurne", "5002"))
        self.assertEqual((away.is_home, away.opponent_name), (False, "Brussels Bears"))
        self.assertTrue(timezone.is_aware(home.start))

    def test_a_row_where_neither_side_matches_is_skipped(self):
        row = {"game_id": "9999", "date": "2026-09-20", "hour": "18:30", "venue": "Elsewhere", "home_id": "1", "home_name": "One", "visit_id": "2", "visit_name": "Two"}

        self.assertEqual(parse_fixtures(rbihf_sample_html([row]), RBIHF_TEAM_ID)[1], [])

    def test_missing_games_table_raises(self):
        with self.assertRaises(RBIHFImportError):
            parse_fixtures("<html><body><h2>Some Team</h2></body></html>", RBIHF_TEAM_ID)

    def test_extract_team_id(self):
        self.assertEqual(extract_team_id("https://www.rbihf.be/league/team/4460"), "4460")
        self.assertEqual(extract_team_id("https://rbihf.be/league/team/4460/"), "4460")
        for url in ["https://evil.example.com/league/team/4460", "https://www.rbihf.be/leagues", ""]:
            with self.subTest(url=url), self.assertRaises(RBIHFImportError):
                extract_team_id(url)

    def test_fetch_html_errors(self):
        from events.services.rbihf_import import fetch_html

        with mock.patch("events.services.rbihf_import.requests.get", side_effect=requests.ConnectionError("boom")), self.assertRaises(RBIHFImportError):
            fetch_html("https://www.rbihf.be/league/team/4460")
        with mock.patch("events.services.rbihf_import.requests.get", return_value=http_response("", 503)), self.assertRaises(RBIHFImportError):
            fetch_html("https://www.rbihf.be/league/team/4460")
        with mock.patch("events.services.rbihf_import.requests.get", return_value=http_response("<html/>")) as get:
            self.assertEqual(fetch_html("https://www.rbihf.be/league/team/4460"), "<html/>")
        self.assertEqual(get.call_args.kwargs["timeout"], 15)


class RBIHFImportPlanTests(ClubTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        with schema_context(cls.club.schema_name):
            cls.home_location = Location.objects.create(name="Home Arena", address="1 St", city="Antwerp", zip_code="1000", country="BE", is_home=True)
            cls.away_location = Location.objects.create(name="Deurne Ice Hall", address="2 St", city="Deurne", zip_code="2100", country="BE")

    def plan(self, rows, team=None, source_id=RBIHF_TEAM_ID, label=""):
        return build_plan(team or self.team, source_id, rbihf_sample_html(rows), label)

    def test_suggested_location(self):
        _name, [home, away] = parse_fixtures(rbihf_sample_html([rbihf_row(), rbihf_away_row()]), RBIHF_TEAM_ID)
        _name, [nowhere] = parse_fixtures(rbihf_sample_html([rbihf_away_row(venue="Nowhere Familiar")]), RBIHF_TEAM_ID)

        self.assertEqual(suggested_location(home), self.home_location)
        self.assertEqual(suggested_location(away), self.away_location)
        self.assertIsNone(suggested_location(nowhere))

    def test_suggested_opponent_matches_case_insensitively_or_by_alternative_name(self):
        _name, [fixture] = parse_fixtures(rbihf_sample_html([rbihf_row()]), RBIHF_TEAM_ID)
        self.assertIsNone(suggested_opponent(fixture))

        shouty = Opponent.objects.create(name="AMSTERDAM TIGERS")
        self.assertEqual(suggested_opponent(fixture), shouty)
        shouty.delete()

        renamed = Opponent.objects.create(name="Tigers HC", alternative_names=["Amsterdam Tigers"])
        self.assertEqual(suggested_opponent(fixture), renamed)

    def test_new_identical_and_changed_fixtures(self):
        plan = self.plan([rbihf_row()])
        self.assertEqual([(p.fixture.external_game_id, p.suggested_location) for p in plan.to_create], [("5002", self.home_location)])
        apply_plan(plan)

        again = self.plan([rbihf_row()])
        self.assertEqual((again.to_create, again.to_update, again.unchanged_count), ([], [], 1))

        moved = self.plan([rbihf_row(hour="20:00")])
        self.assertEqual(len(moved.to_update), 1)
        self.assertIn("start", moved.to_update[0].changes)

    def test_a_future_fixture_no_longer_listed_is_deleted_a_past_one_kept(self):
        apply_plan(self.plan([rbihf_row(), rbihf_row("4001", date="2020-01-01")]))

        plan = self.plan([])

        self.assertEqual([event.external_game_id for event in plan.to_delete], ["5002"])
        self.assertEqual(apply_plan(plan), {"created": 0, "updated": 0, "deleted": 1})
        self.assertEqual(list(Event.objects.values_list("external_game_id", flat=True)), ["4001"])

    def test_another_competition_page_never_deletes_these_games(self):
        apply_plan(self.plan([rbihf_row("5002")], label="Division 1"))

        cup_plan = self.plan([rbihf_row("7001", home_id="9999")], source_id="9999", label="Cup")
        self.assertEqual(cup_plan.to_delete, [])
        apply_plan(cup_plan)
        self.assertEqual(self.plan([], label="Division 1").to_delete[0].external_game_id, "5002")  # only its own
        self.assertEqual(build_plan(self.team, "9999", rbihf_sample_html([rbihf_row("7001", home_id="9999")]), "Cup").to_delete, [])

        division = Event.objects.get(external_game_id="5002")
        cup = Event.objects.get(external_game_id="7001")
        self.assertEqual((division.external_source_id, division.competition_label), (RBIHF_TEAM_ID, "Division 1"))
        self.assertEqual((cup.external_source_id, cup.competition_label), ("9999", "Cup"))

    def test_a_legacy_game_without_source_id_is_matched_and_healed(self):
        apply_plan(self.plan([rbihf_row()]))
        Event.objects.filter(external_game_id="5002").update(external_source_id="")

        plan = self.plan([rbihf_row()], label="Division 1")
        self.assertEqual((plan.to_create, len(plan.to_update)), ([], 1))
        apply_plan(plan)

        healed = Event.objects.get(external_game_id="5002")
        self.assertEqual((healed.external_source_id, healed.competition_label), (RBIHF_TEAM_ID, "Division 1"))

    def test_another_teams_games_are_not_touched(self):
        other = Team.objects.create(name="Second Team", short_name="2nd")
        apply_plan(self.plan([rbihf_row()], team=other))

        self.assertEqual(len(self.plan([rbihf_row()]).to_create), 1)

    def test_apply_creates_games_with_the_expected_fields(self):
        result = apply_plan(self.plan([rbihf_row()]), {"5002": str(self.home_location.pk)})

        self.assertEqual(result, {"created": 1, "updated": 0, "deleted": 0})
        event = Event.objects.get(external_game_id="5002")
        self.assertEqual((event.kind, event.competition, event.external_source_id, event.opponent.name, event.location), (Event.EventKind.GAME, "RBIHF", RBIHF_TEAM_ID, "Amsterdam Tigers", self.home_location))
        self.assertEqual(list(event.teams.all()), [self.team])
        self.assertTrue(event.is_home_game)
        self.assertEqual(event.title, "1st vs Amsterdam Tigers")
        self.assertIsNotNone(event.end)

    def test_an_unknown_location_or_opponent_id_is_ignored(self):
        apply_plan(self.plan([rbihf_row()]), {"5002": "00000000-0000-0000-0000-000000000000"}, {"5002": "not-a-pk"})

        event = Event.objects.get(external_game_id="5002")
        self.assertIsNone(event.location)
        self.assertEqual(event.opponent.name, "Amsterdam Tigers")

    def test_a_chosen_opponent_is_used_and_the_spelling_remembered(self):
        renamed = Opponent.objects.create(name="Amsterdam Tigers HC")
        apply_plan(self.plan([rbihf_row()]), {}, {"5002": str(renamed.pk)})

        self.assertEqual(Event.objects.get(external_game_id="5002").opponent, renamed)
        self.assertFalse(Opponent.objects.filter(name="Amsterdam Tigers").exists())
        renamed.refresh_from_db()
        self.assertEqual(renamed.alternative_names, ["Amsterdam Tigers"])
        again = self.plan([rbihf_row()])
        self.assertEqual((again.to_update, again.unchanged_count), ([], 1))

        moved = self.plan([rbihf_row(hour="20:00")])
        self.assertEqual(moved.to_update[0].suggested_opponent, renamed)

    def test_an_update_only_touches_start_opponent_location_and_label(self):
        apply_plan(self.plan([rbihf_row()]))
        event = Event.objects.get(external_game_id="5002")
        event.gathering, event.score_for = self.future, 3
        event.save()

        apply_plan(self.plan([rbihf_row(hour="20:00")]))

        event.refresh_from_db()
        self.assertEqual(timezone.localtime(event.start).strftime("%H:%M"), "20:00")
        self.assertIsNotNone(event.gathering)
        self.assertEqual(event.score_for, 3)


# -- DFEL ---------------------------------------------------------------------------------------------------------------


class DFELTests(SimpleTestCase):
    def test_extract_ids(self):
        for url in ["https://ehv-nrw.de/leagues/team/70779/21691/", "https://www.ehv-nrw.de/leagues/team/70779/21691", "  https://ehv-nrw.de/leagues/team/70779/21691/  "]:
            with self.subTest(url=url):
                self.assertEqual(dfel_import.extract_ids(url), ("70779", "21691"))
        for url in ["https://ehv-nrw.de/leagues/team/70779/", "https://evil.example.com/leagues/team/70779/21691/", "https://www.rbihf.be/league/team/4460", ""]:
            with self.subTest(url=url), self.assertRaises(dfel_import.DFELImportError):
                dfel_import.extract_ids(url)
        self.assertEqual(dfel_import.source_id("70779", "21691"), DFEL_SOURCE_ID)

    def test_api_key_extraction(self):
        self.assertEqual(dfel_import.extract_api_key(DFEL_TEAM_PAGE_HTML), "abc123def456")
        self.assertEqual(dfel_import.extract_api_key('{"apiKey": "f4b71c87"}'), "f4b71c87")
        with self.assertRaises(dfel_import.DFELImportError):
            dfel_import.extract_api_key("<html></html>")

    def test_fetch_schedule_unwraps_the_jsonp_callback(self):
        schedule = dfel_schedule([dfel_row("g1")])
        with mock.patch("events.services.dfel_import.requests.get", side_effect=[http_response(DFEL_TEAM_PAGE_HTML), http_response(f"rosterchief({schedule});")]) as get:
            raw_json = dfel_import.fetch_schedule(DFEL_TEAM_ID, DFEL_DIVISION_ID)

        self.assertEqual(json.loads(raw_json), json.loads(schedule))
        page_call, schedule_call = get.call_args_list
        self.assertEqual(page_call.args[0], "https://ehv-nrw.de/leagues/team/70779/21691/")
        self.assertEqual(schedule_call.args[0], dfel_import.SCHEDULE_URL)
        self.assertEqual((schedule_call.kwargs["params"]["apiKey"], schedule_call.kwargs["params"]["divisionId"]), ("abc123def456", DFEL_DIVISION_ID))

    def test_fetch_errors_are_dfel_import_errors(self):
        cases = [
            requests.ConnectionError("boom"),
            [http_response(DFEL_TEAM_PAGE_HTML), http_response("nope", status_code=500)],
            [http_response(DFEL_TEAM_PAGE_HTML), http_response("rosterchief(<html>oops</html>)")],
        ]
        for side_effect in cases:
            with self.subTest(side_effect=side_effect), mock.patch("events.services.dfel_import.requests.get", side_effect=side_effect), self.assertRaises(dfel_import.DFELImportError):
                dfel_import.fetch_schedule(DFEL_TEAM_ID, DFEL_DIVISION_ID)
        body = json.dumps({"statusId": 0, "statusMsg": "Invalid apiKey"})
        with mock.patch("events.services.dfel_import.requests.get", side_effect=[http_response(DFEL_TEAM_PAGE_HTML), http_response(f"rosterchief({body})")]), self.assertRaisesMessage(dfel_import.DFELImportError, "Invalid apiKey"):
            dfel_import.fetch_schedule(DFEL_TEAM_ID, DFEL_DIVISION_ID)

    def test_parse_fixtures(self):
        rows = [dfel_row("home"), dfel_row("away", days=14, home_id="70774", home_name='KEC "Die Haie"', away_id=DFEL_TEAM_ID, away_name=DFEL_TEAM_NAME, city="Köln"), dfel_row("past", days=-3), dfel_row("theirs", home_id="70774", away_id="70757")]
        team_name, [home, away] = dfel_import.parse_fixtures(dfel_schedule(rows), DFEL_TEAM_ID)

        self.assertEqual(team_name, DFEL_TEAM_NAME)
        self.assertEqual((home.external_game_id, home.is_home, home.opponent_name, home.venue_text), ("home", True, "FASS Berlin", "Dinslaken"))
        self.assertEqual((away.external_game_id, away.is_home, away.opponent_name, away.venue_text), ("away", False, 'KEC "Die Haie"', "Köln"))
        self.assertEqual(home.start, datetime.datetime.fromtimestamp(rows[0]["gameUtcTimestamp"] / 1000, tz=UTC))

    def test_venue_fallback_team_name_from_a_past_game_and_unknown_team(self):
        _name, [fixture] = dfel_import.parse_fixtures(dfel_schedule([dfel_row("g1", city=None)]), DFEL_TEAM_ID)
        self.assertEqual(fixture.venue_text, "Dinslaken - Eishalle")
        self.assertEqual(dfel_import.parse_fixtures(dfel_schedule([dfel_row("past", days=-3)]), DFEL_TEAM_ID), (DFEL_TEAM_NAME, []))
        with self.assertRaises(dfel_import.DFELImportError):
            dfel_import.parse_fixtures(dfel_schedule([dfel_row("g1")]), "99999")

    def test_find_game(self):
        raw_json = dfel_schedule([dfel_row("a"), dfel_row("b")])

        self.assertEqual(dfel_import.find_game(raw_json, "b")["id"], "b")
        self.assertIsNone(dfel_import.find_game(raw_json, "zzz"))


class DFELImportPlanTests(ClubTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        with schema_context(cls.club.schema_name):
            cls.home_location = Location.objects.create(name="Home Arena", address="1 St", city="Mechelen", zip_code="2800", country="BE", is_home=True)
            cls.away_location = Location.objects.create(name="Eishalle", address="2 St", city="Köln", zip_code="50679", country="DE")

    def plan(self, rows, source_id=DFEL_SOURCE_ID, label=""):
        return dfel_import.build_plan(self.team, source_id, dfel_schedule(rows), label)

    def test_creates_with_suggested_locations_and_applies(self):
        away = dfel_row("a1", days=10, home_id="70774", home_name="KEC", away_id=DFEL_TEAM_ID, away_name=DFEL_TEAM_NAME, city="köln")
        plan = self.plan([dfel_row("h1"), away], label="DFEL2")

        self.assertEqual((plan.competition, plan.source_id, plan.scraped_team_name), ("DFEL", DFEL_SOURCE_ID, DFEL_TEAM_NAME))
        self.assertEqual([(p.fixture.external_game_id, p.suggested_location) for p in plan.to_create], [("h1", self.home_location), ("a1", self.away_location)])
        self.assertEqual(dfel_import.apply_plan(plan, {"h1": str(self.home_location.pk)}), {"created": 2, "updated": 0, "deleted": 0})
        event = Event.objects.get(external_game_id="h1")
        self.assertEqual((event.competition, event.external_source_id, event.competition_label, event.opponent.name), ("DFEL", DFEL_SOURCE_ID, "DFEL2", "FASS Berlin"))
        self.assertTrue(event.title.startswith("1st "))

    def test_reimport_moves_and_deletes_scoped_to_its_division(self):
        dfel_import.apply_plan(self.plan([dfel_row("h1"), dfel_row("h2", days=21), dfel_row("h3", days=30)]))
        dfel_import.apply_plan(self.plan([dfel_row("cup1")], source_id=dfel_import.source_id(DFEL_TEAM_ID, "99999")))

        plan = self.plan([dfel_row("h1", days=8), dfel_row("h2", days=21)])

        self.assertEqual([p.fixture.external_game_id for p in plan.to_update], ["h1"])
        self.assertEqual(plan.unchanged_count, 1)
        self.assertEqual([event.external_game_id for event in plan.to_delete], ["h3"])

    def test_dfel_and_rbihf_imports_never_delete_each_other(self):
        rbihf_game = self.make_event(kind=Event.EventKind.GAME, competition="RBIHF", external_game_id="5002", external_source_id=RBIHF_TEAM_ID)
        rbihf_game.teams.add(self.team)
        self.assertEqual(self.plan([dfel_row("h1")]).to_delete, [])

        dfel_import.apply_plan(self.plan([dfel_row("h1")]))
        self.assertEqual(build_plan(self.team, RBIHF_TEAM_ID, rbihf_sample_html([]), "").to_delete, [rbihf_game])


# -- IJNL ---------------------------------------------------------------------------------------------------------------


class IJNLTests(SimpleTestCase):
    def test_extract_ids(self):
        for url in [IJNL_URL, "https://ijshockey.nl/competities/team/64414/?seizoen=2026/27", f"  {IJNL_URL}  "]:
            with self.subTest(url=url):
                self.assertEqual(ijnl_import.extract_ids(url), (IJNL_TEAM_ID, IJNL_SEASON))
        self.assertEqual(ijnl_import.extract_ids("https://www.ijshockey.nl/competities/team/64414"), (IJNL_TEAM_ID, ""))
        for url in [
            "https://www.ijshockey.nl/competities/wedstrijd/49a66dbb-eaa2-4527-9c8b-31ee324d736b",
            "https://evil.example.com/competities/team/64414",
            "https://www.ijshockey.nl.evil.example.com/competities/team/64414",
            "https://www.ijshockey.nl/competities/team/64414?seizoen=next",
            "",
        ]:
            with self.subTest(url=url), self.assertRaises(ijnl_import.IJNLImportError):
                ijnl_import.extract_ids(url)
        self.assertEqual(ijnl_import.source_id(IJNL_TEAM_ID, IJNL_SEASON), IJNL_SOURCE_ID)
        self.assertEqual(ijnl_import.team_page_url(IJNL_TEAM_ID, IJNL_SEASON), IJNL_URL)

    def test_fetch_team_data(self):
        with mock.patch("events.services.ijnl_import.requests.get", return_value=http_response(ijnl_team_data([]))) as get:
            raw_json = ijnl_import.fetch_team_data(IJNL_TEAM_ID, IJNL_SEASON)

        self.assertEqual(ijnl_import.season_of(raw_json), IJNL_SEASON)
        self.assertEqual(get.call_args.args[0], "https://www.ijshockey.nl/competities/team/64414/data")
        self.assertEqual(get.call_args.kwargs["params"], {"seizoen": IJNL_SEASON})

    def test_fetch_errors_are_ijnl_import_errors(self):
        for side_effect in [requests.ConnectionError("boom"), [http_response("nope", status_code=500)], [http_response("<html>oops</html>")], [http_response(json.dumps({"team": {}}))], [http_response("[]")]]:
            with self.subTest(side_effect=side_effect), mock.patch("events.services.ijnl_import.requests.get", side_effect=side_effect), self.assertRaises(ijnl_import.IJNLImportError):
                ijnl_import.fetch_team_data(IJNL_TEAM_ID, IJNL_SEASON)

    def test_parse_fixtures(self):
        games = [ijnl_game("home", time_text="16:45"), ijnl_game("away", days=14, home_id="32610", home_name="Dordrecht Lady Lions", away_id=IJNL_TEAM_ID, away_name=IJNL_TEAM_NAME)]
        games += [ijnl_game("noon", time_unknown=True), ijnl_game("tbd", undated=True)]
        team_name, [home, away, noon], undated = ijnl_import.parse_fixtures(ijnl_team_data(games, uitslagen=[ijnl_game("past", days=-3, state="definitief")]), IJNL_TEAM_ID)

        self.assertEqual((team_name, undated), (IJNL_TEAM_NAME, ["tbd"]))
        self.assertEqual((home.is_home, home.opponent_name), (True, "Amsterdam Tigers Vrouwen"))
        self.assertEqual((away.is_home, away.opponent_name), (False, "Dordrecht Lady Lions"))
        local = home.start.astimezone(ZoneInfo("Europe/Amsterdam"))
        self.assertEqual((local.strftime("%Y-%m-%d"), local.hour, local.minute), (games[0]["datum"], 16, 45))
        self.assertEqual(noon.start.astimezone(ZoneInfo("Europe/Amsterdam")).hour, 12)

    def test_parse_score(self):
        self.assertEqual(ijnl_import.parse_score(json.loads(ijnl_game_data(home="4", away="0"))), (4, 0))
        self.assertEqual(ijnl_import.parse_score(json.loads(ijnl_game_data(state="gepland", home="-", away="-"))), (None, None))
        self.assertEqual(ijnl_import.parse_score({"state": "gepland"}), (None, None))


class IJNLImportPlanTests(ClubTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        with schema_context(cls.club.schema_name):
            cls.home_location = Location.objects.create(name="Home Arena", address="1 St", city="Mechelen", zip_code="2800", country="BE", is_home=True)

    def plan(self, games, source_id=IJNL_SOURCE_ID, label=""):
        return ijnl_import.build_plan(self.team, source_id, ijnl_team_data(games), label)

    def test_creates_ijnl_games(self):
        plan = self.plan([ijnl_game("h1")], label="Eredivisie")

        self.assertEqual((plan.competition, plan.source_id, plan.scraped_team_name), ("IJNL", IJNL_SOURCE_ID, IJNL_TEAM_NAME))
        self.assertEqual(plan.to_create[0].suggested_location, self.home_location)
        self.assertEqual(ijnl_import.apply_plan(plan, {"h1": str(self.home_location.pk)}), {"created": 1, "updated": 0, "deleted": 0})
        event = Event.objects.get(external_game_id="h1")
        self.assertEqual((event.competition, event.external_source_id, event.competition_label, event.opponent.name), ("IJNL", IJNL_SOURCE_ID, "Eredivisie", "Amsterdam Tigers Vrouwen"))

    def test_reimport_moves_deletes_and_keeps_undated_games(self):
        ijnl_import.apply_plan(self.plan([ijnl_game("h1"), ijnl_game("h2", days=21), ijnl_game("h3")]))

        plan = self.plan([ijnl_game("h1", days=8), ijnl_game("h3", undated=True)])

        self.assertEqual([p.fixture.external_game_id for p in plan.to_update], ["h1"])
        self.assertEqual([event.external_game_id for event in plan.to_delete], ["h2"])

    def test_another_seasons_import_never_deletes_these_games(self):
        ijnl_import.apply_plan(self.plan([ijnl_game("h1")]))

        self.assertEqual(self.plan([ijnl_game("next1")], source_id=ijnl_import.source_id(IJNL_TEAM_ID, "2027/28")).to_delete, [])


# -- JSON play days -----------------------------------------------------------------------------------------------------


class JSONImportTests(ClubTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        with schema_context(cls.club.schema_name):
            cls.u10 = Team.objects.create(name="Sharks U10", short_name="U10")
            cls.u12 = Team.objects.create(name="Sharks U12", short_name="U12")
            cls.home = Location.objects.create(name="Home Rink", address="1 St", city="Antwerp", zip_code="2000", country="BE", is_home=True)
            cls.mechelen = Location.objects.create(name="Ice Mechelen", address="2 St", city="Mechelen", zip_code="2800", country="BE")

    def payload(self, games, **extra):
        return json.dumps({"source": "Kalender Promo", "our_code": "COP", "games": games, **extra})

    def game(self, game_id="g1", *, teams=("U10",), city="Mechelen", start="11:45", hosted=False, **extra):
        return {"id": game_id, "date": "2026-10-18", "start": start, "end": "13:45", "city": city, "teams": list(teams), "hosted_by_us": hosted, "participants": ["COP", "IHC"], "ice": "cross-ice", **extra}

    def test_load_rejects_bad_files(self):
        for raw in ["not json", json.dumps([]), json.dumps({"games": []}), json.dumps({"source": "x"})]:
            with self.subTest(raw=raw), self.assertRaises(json_import.JSONImportError):
                json_import.load(raw)

    def test_resolve_team_by_name_short_name_or_whole_word(self):
        self.assertEqual(json_import.resolve_team("Sharks U10"), self.u10)
        self.assertEqual(json_import.resolve_team("u12"), self.u12)
        self.assertIsNone(json_import.resolve_team("U1"))

    def test_rows_on_the_same_day_time_and_city_merge_into_one_play_day(self):
        plan = json_import.build_plan(self.payload([self.game("g1"), self.game("g2", teams=["U12"])]))

        [planned] = plan.to_create
        self.assertEqual((planned.external_game_id, planned.teams, planned.suggested_location, planned.label), ("g1", [self.u10, self.u12], self.mechelen, "cross-ice · IHC"))

    def test_apply_and_reimport(self):
        plan = json_import.build_plan(self.payload([self.game(hosted=True)]))
        self.assertEqual(plan.to_create[0].suggested_location, self.home)
        self.assertIsNone(json_import.home_warning(plan.to_create[0], self.home))
        self.assertTrue(json_import.home_warning(plan.to_create[0], self.mechelen))

        self.assertEqual(json_import.apply_plan(plan, {"g1": str(self.home.pk)}), {"created": 1, "updated": 0})
        event = Event.objects.get(external_game_id="g1")
        self.assertEqual((event.competition, event.external_source_id, event.title, event.location, list(event.teams.all())), ("Development", "Kalender Promo", "Development game", self.home, [self.u10]))

        again = json_import.build_plan(self.payload([self.game(hosted=True, start="12:00")]))
        self.assertEqual((again.to_create, len(again.to_update), again.to_update[0].suggested_location), ([], 1, self.home))
        json_import.apply_plan(again, {"g1": str(self.home.pk)})
        self.assertEqual(timezone.localtime(Event.objects.get(external_game_id="g1").start).strftime("%H:%M"), "12:00")

    def test_unknown_teams_are_reported(self):
        with self.assertRaisesMessage(json_import.JSONImportError, "Nobody"):
            json_import.build_plan(self.payload([self.game(teams=["Nobody"])]))


# -- stored import sources, preview/confirm and the weekly check -----------------------------------------------------


class ImportSourcesTestBase(ClubTestBase):
    RBIHF_URL = f"https://www.rbihf.be/league/team/{RBIHF_TEAM_ID}"
    DFEL_URL = f"https://ehv-nrw.de/leagues/team/{DFEL_TEAM_ID}/{DFEL_DIVISION_ID}/"

    def import_rows(self, rows, team=None, label=""):
        apply_plan(build_plan(team or self.team, RBIHF_TEAM_ID, rbihf_sample_html(rows), label))

    def store(self, team=None, *, source=import_sources.SOURCE_RBIHF, url=None, season=None, label=""):
        url = url or {import_sources.SOURCE_RBIHF: self.RBIHF_URL, import_sources.SOURCE_DFEL: self.DFEL_URL, import_sources.SOURCE_IJNL: IJNL_URL}[source]
        return import_sources.record_source(team or self.team, source=source, url=url, season=season or self.season, competition_label=label)

    def patch_fetch(self, payload):
        """Patches the network half of the checks: RBIHF HTML for every entry, an exception, or a callable(entry)."""
        if isinstance(payload, Exception) or callable(payload):
            return mock.patch("events.services.import_sources.fetch", side_effect=payload)
        return mock.patch("events.services.import_sources.fetch", return_value=payload)

    def stored(self, team=None):
        team = team or self.team
        team.refresh_from_db()
        return team.import_sources


class RecordSourceTests(ImportSourcesTestBase):
    def test_replaces_the_same_season_source_and_label_case_insensitively(self):
        self.store(label="Division 1", url="https://www.rbihf.be/league/team/1111")
        self.store(label="  division 1 ", url="https://www.rbihf.be/league/team/2222")

        [entry] = self.stored()
        self.assertEqual((entry["url"], entry["competition_label"], entry["season_name"]), ("https://www.rbihf.be/league/team/2222", "division 1", self.season.name))

    def test_appends_a_different_label_source_or_season(self):
        next_season = Season.objects.create(start_date=self.season.end_date + timedelta(days=1), end_date=self.season.end_date + timedelta(days=365))
        self.store(label="Division 1")
        self.store(label="Cup")
        self.store(source=import_sources.SOURCE_DFEL, label="Division 1")
        self.store(label="Division 1", season=next_season)

        self.assertEqual(len(self.stored()), 4)
        self.assertEqual(len(import_sources.sources_for(self.team, self.season)), 3)
        self.assertEqual([row["season_exists"] for row in import_sources.sources_for_display(self.team)], [True] * 4)

    def test_rejects_an_unknown_source_and_can_forget_one(self):
        with self.assertRaises(ValueError):
            import_sources.record_source(self.team, source="NOPE", url="https://example.com", season=self.season)
        entry = self.store()
        self.assertTrue(import_sources.remove_entry(self.team, entry))
        self.assertEqual(self.stored(), [])

    def test_source_ids_and_fetch_per_source(self):
        self.assertEqual(import_sources.source_id_for(self.store()), RBIHF_TEAM_ID)
        self.assertEqual(import_sources.source_id_for(self.store(source=import_sources.SOURCE_DFEL)), DFEL_SOURCE_ID)
        ijnl = self.store(source=import_sources.SOURCE_IJNL)
        with mock.patch("events.services.ijnl_import.requests.get", return_value=http_response(ijnl_team_data([ijnl_game("h1")]))) as get:
            raw = import_sources.fetch(ijnl)
        self.assertEqual(get.call_args.kwargs["params"], {"seizoen": IJNL_SEASON})
        plan = import_sources.build_plan_for(self.team, ijnl, raw)
        self.assertEqual((plan.competition, plan.source_id), ("IJNL", IJNL_SOURCE_ID))
        with self.assertRaises(import_sources.ImportSourceError):
            import_sources.source_id_for(self.store(source=import_sources.SOURCE_IJNL, url="https://www.ijshockey.nl/competities/team/64414", label="x"))


class CheckEntryTests(ImportSourcesTestBase):
    def check(self, payload):
        entry = self.store()
        with self.patch_fetch(payload):
            return import_sources.check_entry(self.team, entry)

    def test_a_moved_hour_is_reported_as_rescheduled(self):
        self.import_rows([rbihf_row()])

        result = self.check(rbihf_sample_html([rbihf_row(hour="20:00")]))

        self.assertEqual([game_id for game_id, _old, _new in result.rescheduled], ["5002"])
        self.assertEqual(result.as_dict(), {"rescheduled": 1, "new_games": 0, "removed_games": 0, "error": ""})

    def test_new_and_removed_games_are_reported_an_opponent_change_is_not(self):
        self.import_rows([rbihf_row(), rbihf_row("5003", days=14)])

        result = self.check(rbihf_sample_html([rbihf_row(visit_name="Amsterdam Lions"), rbihf_row("5004", days=21)]))

        self.assertEqual([game[0] for game in result.new_games], ["5004"])
        self.assertEqual([game[0] for game in result.removed_games], ["5003"])
        self.assertEqual(result.rescheduled, [])
        self.assertFalse(self.check(rbihf_sample_html([rbihf_row(visit_name="Amsterdam Lions"), rbihf_row("5003", days=14)])).has_changes)

    def test_a_fetch_error_is_captured_not_raised(self):
        result = self.check(import_sources.ImportSourceError("RBIHF is down"))

        self.assertEqual((result.error, result.has_changes, result.as_dict()["error"]), ("RBIHF is down", True, "RBIHF is down"))


class ImportFixturesTests(ImportSourcesTestBase):
    """The permission-checked preview/confirm entry points."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        with schema_context(cls.club.schema_name):
            cls.plain = Member.objects.create(first_name="Plain", last_name="Member", email="plain@imports.test")
        cls.plain_user, _ = grant_login(cls.club, cls.plain)

    def test_needs_settings_and_the_feature(self):
        with self.assertRaises(NotAllowed):
            import_sources.preview_import(self.owner, self.team, source="RBIHF", url=self.RBIHF_URL, season=self.season)
        self.enable("competition_rbihf")
        with self.assertRaises(NotAllowed):
            import_sources.preview_import(self.plain_user, self.team, source="RBIHF", url=self.RBIHF_URL, season=self.season)

    def test_preview_then_import_records_the_source_and_audits(self):
        self.enable("competition_rbihf")
        html = rbihf_sample_html([rbihf_row()])
        with mock.patch("events.services.rbihf_import.requests.get", return_value=http_response(html)):
            entry, raw, plan = import_sources.preview_import(self.owner, self.team, source="RBIHF", url=self.RBIHF_URL, season=self.season, competition_label="Division 1")
        self.assertEqual(len(plan.to_create), 1)
        self.assertFalse(Event.objects.exists())

        result = import_sources.import_fixtures(self.owner, self.team, entry, raw)

        self.assertEqual(result, {"created": 1, "updated": 0, "deleted": 0})
        self.assertEqual(Event.objects.get().competition_label, "Division 1")
        [stored] = self.stored()
        self.assertEqual((stored["url"], stored["competition_label"], stored["season_id"]), (self.RBIHF_URL, "Division 1", str(self.season.pk)))
        self.assertTrue(AuditEntry.objects.filter(action="events.fixtures_imported", target_id=str(self.team.pk)).exists())

    def test_a_bad_url_is_a_field_error(self):
        self.enable("competition_dfel")
        with self.assertRaises(Invalid) as caught:
            import_sources.preview_import(self.owner, self.team, source="DFEL", url="https://example.com", season=self.season)
        self.assertIn("url", caught.exception.errors)

    def test_an_ijnl_url_without_season_is_pinned_to_the_fetched_season(self):
        self.enable("competition_ijnl")
        with mock.patch("events.services.ijnl_import.requests.get", return_value=http_response(ijnl_team_data([ijnl_game("h1")]))):
            entry, _raw, plan = import_sources.preview_import(self.owner, self.team, source="IJNL", url="https://www.ijshockey.nl/competities/team/64414", season=self.season)

        self.assertEqual((entry["url"], plan.source_id), (IJNL_URL, IJNL_SOURCE_ID))

    def test_apply_entry_with_suggestions_picks_the_home_location(self):
        home = Location.objects.create(name="Home", address="x", city="Antwerp", zip_code="1", country="BE", is_home=True)
        entry = self.store()

        import_sources.apply_entry(self.team, entry, rbihf_sample_html([rbihf_row()]), use_suggestions=True)

        self.assertEqual(Event.objects.get().location, home)


class RefreshFixturesTaskTests(ImportSourcesTestBase):
    """events.refresh_fixtures: the weekly sweep over every active club's current-season sources, alerting admins once."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        with schema_context(cls.club.schema_name):
            cls.second_team = Team.objects.create(name="Second Team", short_name="2nd")

    def setUp(self):
        super().setUp()
        self.enable("competition_rbihf")

    def inbox(self):
        return InboxNotification.objects.filter(club=self.club, user=self.owner)

    def test_notifies_the_admins_once_with_a_summary(self):
        self.import_rows([rbihf_row(), rbihf_row("5003", days=14)], label="Division 1")
        self.store(label="Division 1")
        self.store(self.second_team, label="Cup")

        with self.patch_fetch(rbihf_sample_html([rbihf_row(hour="20:00"), rbihf_row("5004", days=21)])):
            result = refresh_fixtures()

        [note] = self.inbox()
        self.assertIn("First Team · RBIHF · Division 1: 1 game moved to another date or time, 1 new game, 1 game removed.", note.body)
        self.assertIn("Second Team · RBIHF · Cup: 2 new games.", note.body)
        self.assertEqual((note.kind, note.href), ("warning", "/teams"))
        self.assertIn("checked 2 fixture source(s): 2 with changes", result)
        self.assertFalse(Event.objects.filter(external_game_id="5004").exists())  # checked, never applied

    def test_no_notification_when_nothing_changed_but_bookkeeping_is_written(self):
        self.import_rows([rbihf_row()])
        self.store()

        with self.patch_fetch(rbihf_sample_html([rbihf_row(visit_name="Renamed Opponent")])):
            refresh_fixtures()

        self.assertFalse(self.inbox().exists())
        [entry] = self.stored()
        self.assertEqual(entry["last_check"], {"rescheduled": 0, "new_games": 0, "removed_games": 0, "error": ""})
        self.assertTrue(entry["last_checked_at"])

    def test_skips_sources_without_the_feature_and_other_seasons(self):
        self.store(source=import_sources.SOURCE_DFEL)
        old_season = Season.objects.create(start_date=self.season.start_date - timedelta(days=365), end_date=self.season.start_date - timedelta(days=1))
        self.store(season=old_season)

        with self.patch_fetch(import_sources.ImportSourceError("should not be fetched")) as fetch:
            refresh_fixtures()

        fetch.assert_not_called()
        self.assertFalse(self.inbox().exists())

    def test_archived_clubs_are_skipped(self):
        self.store()
        Club.objects.filter(pk=self.club.pk).update(archived_at=timezone.now())

        with self.patch_fetch(import_sources.ImportSourceError("should not be fetched")) as fetch:
            refresh_fixtures()

        fetch.assert_not_called()

    def test_a_fetch_error_is_alerted_and_one_bad_entry_does_not_stop_the_others(self):
        self.store()
        self.store(self.second_team)
        calls = []

        def fetch(entry):
            calls.append(entry)
            if len(calls) == 1:
                raise RuntimeError("parser bug")
            raise import_sources.ImportSourceError("Could not reach rbihf.be")

        with self.patch_fetch(fetch):
            refresh_fixtures()

        self.assertEqual(len(calls), 2)
        [note] = self.inbox()
        self.assertIn("Second Team · RBIHF: the fixture list could not be fetched (Could not reach rbihf.be).", note.body)
        self.assertTrue(self.stored(self.team)[0]["last_check"]["error"])
