"""Live game timelines: parsers (against saved real payloads, no network), storing and re-parsing, the strength check,
moments, polling and settling (events.services.timeline_parsers / game_timeline / strength_check / game_moments).
``GameTestBase`` is shared with tests_game_stats and tests_game_push."""

import json
from datetime import timedelta
from pathlib import Path
from unittest import mock

import requests
from bs4 import BeautifulSoup
from django.core.cache import cache
from django.db import connection
from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from django_tenants.utils import schema_context

from club.models import Club, Domain
from club.services import create_club
from controlpanel.tests import make_user
from events.models import Event, GameMoment, GameStat, GameTimeline, Location, Opponent, Season
from events.services import game_moments, game_timeline, strength_check, timeline_parsers
from teams.models import Team

TIMELINE_FIXTURES = Path(__file__).resolve().parent / "test_fixtures" / "game_timeline"


def timeline_fixture(name):
    """A real per-game payload (players pseudonymised) -- see events/test_fixtures/game_timeline."""
    return (TIMELINE_FIXTURES / name).read_text()


def rbihf_game_data():
    return timeline_parsers.rbihf_extract_gd(timeline_fixture("rbihf_game_137.html"))


def rbihf_fetch():
    raw = timeline_fixture("rbihf_timeline_137.json")
    return game_timeline.TimelineFetch(document=timeline_parsers.parse_rbihf(raw, rbihf_game_data()), raw=raw)


class GameTestBase(TestCase):
    """One club (own schema) with a current season, team "First Team" (1st), a home ground and opponent "Olympia Heist".
    Every test runs inside the club's schema."""

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
        cls.club = create_club("Games FC", cls.owner)
        today = timezone.localdate()
        with schema_context(cls.club.schema_name):
            cls.season = Season.objects.create(start_date=today - timedelta(days=30), end_date=today + timedelta(days=300))
            cls.team = Team.objects.create(name="First Team", short_name="First Team")
            cls.home_location = Location.objects.create(name="Home Arena", address="1 St", city="Hasselt", zip_code="3500", is_home=True)
            cls.opponent = Opponent.objects.create(name="Olympia Heist")

    def setUp(self):
        ctx = schema_context(self.club.schema_name)
        ctx.__enter__()
        self.addCleanup(ctx.__exit__, None, None, None)
        cache.clear()
        self.addCleanup(cache.clear)

    def make_game(self, **kwargs):
        kwargs.setdefault("kind", Event.EventKind.GAME)
        kwargs.setdefault("title", "Game")
        kwargs.setdefault("competition", "RBIHF")
        kwargs.setdefault("external_game_id", "137")
        kwargs.setdefault("location", self.home_location)
        kwargs.setdefault("opponent", self.opponent)
        kwargs.setdefault("season", self.season)
        kwargs.setdefault("start", timezone.now() - timedelta(hours=1))
        game = Event.objects.create(**kwargs)
        game.teams.add(self.team)
        return game

    def rbihf_fetch(self):
        return rbihf_fetch()


class TimelineParserTests(SimpleTestCase):
    """events.services.timeline_parsers against saved real payloads -- the expected totals are the real results."""

    def test_hockeydata_period_markers_follow_where_the_game_is(self):
        def markers(**game):
            report = json.loads(timeline_fixture("hockeydata_report.json"))
            report["data"]["gameData"].update(game)
            document = timeline_parsers.parse_hockeydata(json.dumps(report))
            return [(event["type"], event["period"], event["seconds"]) for event in document["events"] if event["type"].startswith("period")]

        self.assertEqual(markers(), [("period_start", "1", 0), ("period_end", "1", 1200), ("period_start", "2", 1200), ("period_end", "2", 2400), ("period_start", "3", 2400), ("period_end", "3", 3600)])
        live = markers(gameStatus=1, endTime="", liveTime=1217, liveTimePeriod="2")
        self.assertEqual(live, [("period_start", "1", 0), ("period_end", "1", 1200), ("period_start", "2", 1200)])
        self.assertEqual(markers(gameStatus=0, endTime="", liveTime=0, liveTimePeriod="1"), [])

    def parse_all(self):
        return {
            "rbihf": timeline_parsers.finalize_document(timeline_parsers.parse_rbihf(timeline_fixture("rbihf_timeline_137.json"), rbihf_game_data())),
            "cehl": timeline_parsers.finalize_document(timeline_parsers.parse_cehl(timeline_fixture("cehl_timeline_7101.json"))),
            "dfel": timeline_parsers.finalize_document(timeline_parsers.parse_hockeydata(timeline_fixture("hockeydata_report.json"))),
            "ijnl": timeline_parsers.finalize_document(timeline_parsers.parse_ijnl(timeline_fixture("ijnl_game_920c9dc5.json"))),
        }

    def test_every_source_yields_the_real_score_with_unique_ids_in_order(self):
        expected = {"rbihf": ({"home": 5, "away": 2}, 7), "cehl": ({"home": 1, "away": 4}, 5), "dfel": ({"home": 5, "away": 1}, 6), "ijnl": ({"home": 4, "away": 0}, 4)}
        for source, document in self.parse_all().items():
            with self.subTest(source=source):
                score, goals = expected[source]
                events = document["events"]
                self.assertEqual(document["score"], score)
                self.assertEqual(sum(event["type"] == "goal" for event in events), goals)
                self.assertEqual(len({event["id"] for event in events}), len(events))
                seconds = [event["seconds"] for event in events]
                self.assertEqual(seconds, sorted(seconds))
                self.assertEqual(document["version"], timeline_parsers.VERSION)
                self.assertTrue(all(event["goal"]["scorer"]["name"] for event in events if event["type"] == "goal"))

    def test_period_scores(self):
        documents = self.parse_all()
        self.assertEqual([(p["label"], p["home"], p["away"]) for p in documents["rbihf"]["periods"]], [("1", 0, 0), ("2", 3, 2), ("3", 2, 0)])
        self.assertEqual([(p["label"], p["home"], p["away"]) for p in documents["ijnl"]["periods"]], [("1", 3, 0), ("2", 1, 0), ("3", 0, 0)])

    def test_a_completed_ijnl_game_gets_assist_numbers_from_the_lineups_and_the_team_statistics(self):
        document = timeline_parsers.finalize_document(timeline_parsers.parse_ijnl(timeline_fixture("ijnl_game_49a66dbb.json")))
        goals = [event["goal"] for event in document["events"] if event["type"] == "goal"]
        self.assertEqual((document["score"], document["status"], len(goals)), ({"home": 3, "away": 4}, "final", 7))
        self.assertEqual([[assist["number"] for assist in goal["assists"]] for goal in goals], [[62, 87], [], [19, 4], [87], [], [4], []])
        self.assertEqual(document["team_stats"], {"home": {"pp_goals": 1, "pp_opportunities": 3, "penalty_minutes": 6}, "away": {"pp_goals": 1, "pp_opportunities": 3, "penalty_minutes": 6}})

    def test_ijnl_goalies_who_played_come_from_the_lineups_and_a_listed_backup_is_left_out(self):
        document = timeline_parsers.parse_ijnl(timeline_fixture("ijnl_game_49a66dbb.json"))
        self.assertEqual(
            document["goalies"],
            {"home": [{"player": {"name": "Player 55", "number": 55}, "seconds": 3600, "against": 4}], "away": [{"player": {"name": "Player 30", "number": 30}, "seconds": 3600, "against": 3}]},
        )

    def test_an_ijnl_game_without_goalies_entered_yet_has_none(self):
        payload = json.loads(timeline_fixture("ijnl_game_49a66dbb.json"))
        payload["sections"]["lineups"] = payload["sections"]["lineups"].replace("60:00", "0:00")
        payload["sections"]["lineups"] = payload["sections"]["lineups"].replace("<td>4</td>", "<td>0</td>").replace("<td>3</td>", "<td>0</td>")
        self.assertNotIn("goalies", timeline_parsers.parse_ijnl(json.dumps(payload)))

    def test_ijnl_assists_without_a_lineup_keep_their_name_only(self):
        payload = json.loads(timeline_fixture("ijnl_game_49a66dbb.json"))
        payload["sections"].pop("lineups")
        document = timeline_parsers.parse_ijnl(json.dumps(payload))
        [first] = [event["goal"] for event in document["events"] if event["type"] == "goal"][:1]
        self.assertEqual(first["assists"], [{"name": "Player 62"}, {"name": "Player 87"}])

    def test_penalty_kinds_are_normalized_and_fill_in_minutes(self):
        self.assertEqual(timeline_parsers.normalize_penalty_kind("game miscodunct"), "game_misconduct")
        self.assertEqual(timeline_parsers.normalize_penalty_kind("Double Minor"), "double_minor")
        self.assertEqual(timeline_parsers.normalize_penalty_kind("Tripping"), "")
        self.assertEqual(timeline_parsers.finish_penalty({}, "major"), {"kind": "major", "minutes": 5})
        self.assertEqual(timeline_parsers.finish_penalty({"minutes": 10}), {"kind": "misconduct", "minutes": 10})
        self.assertEqual(timeline_parsers.finish_penalty({"minutes": 3}), {"minutes": 3})

    def test_an_rbihf_game_misconduct_is_recognised(self):
        note = '<span>Max (#17)</span> was given a <span class="highlight">game miscodunct</span> penalty for Checking To The Head.'
        row = f'<tr class="penalty"><td class="time">52:16</td><td class="event">Penalty<br />CHHE</td><td class="team">COP</td><td class="note">{note}</td></tr>'
        [penalty] = timeline_parsers.parse_rbihf(json.dumps({"success": True, "data": f"<table>{row}</table>"}), {"visit": {"code": "COP"}})["events"]
        self.assertEqual((penalty["side"], penalty["penalty"]["kind"], penalty["penalty"]["minutes"], penalty["penalty"]["description"]), ("away", "game_misconduct", 20, "Checking To The Head"))

    def test_a_penalty_shot_and_its_team_level_penalty(self):
        game_data = {"home": {"code": "CHL", "name": "Charleroi Red Roosters"}, "visit": {"code": "COP", "name": "Sharks Mechelen"}}
        shot = '<tr class="penalty"><td class="time">45:55</td><td class="event">Penalty shot</td><td class="team">COP</td><td class="note"><span>Sharks Mechelen</span> was awarded a penalty shot.</td></tr>'
        foul_note = '<span>Charleroi Red Roosters</span> was given a <span class="highlight"></span> penalty for Tripping.'
        foul = f'<tr class="penalty"><td class="time">45:55</td><td class="event">Penalty<br />TRIP</td><td class="team">CHL</td><td class="note">{foul_note}</td></tr>'
        events = timeline_parsers.parse_rbihf(json.dumps({"success": True, "data": f"<table>{shot}{foul}</table>"}), game_data)["events"]
        self.assertEqual([(event["type"], event["side"]) for event in events], [("penalty", "home"), ("penalty_shot", "away")])
        self.assertIsNone(events[0]["penalty"]["player"])
        self.assertEqual(events[0]["penalty"]["description"], "Tripping")

    def test_a_cehl_penalty_shot(self):
        html = '<div class="timeline-event penalty team-a"><div class="timeline-event-content"><span><b>45:55</b></span> <b>HC Example</b> was awarded a penalty shot</div></div>'
        [event] = timeline_parsers.parse_cehl(json.dumps({"live": 0, "html": html}))["events"]
        self.assertEqual((event["type"], event["side"]), ("penalty_shot", "home"))

    def test_period_markers_carry_their_own_period_in_order(self):
        documents = self.parse_all()
        for source in ("rbihf", "cehl"):
            with self.subTest(source=source):
                markers = [(event["time"], event["type"], event["period"]) for event in documents[source]["events"] if event["type"] in ("period_start", "period_end")]
                self.assertIn(("20:00", "period_end", "1"), markers)
                self.assertEqual(markers[markers.index(("20:00", "period_end", "1")) + 1], ("20:00", "period_start", "2"))

    def test_rbihf_goal_and_penalty_details(self):
        events = self.parse_all()["rbihf"]["events"]
        goal = next(event for event in events if event["type"] == "goal")
        penalty = next(event for event in events if event["type"] == "penalty")
        self.assertEqual((goal["side"], goal["period"], goal["goal"]["strength"], goal["goal"]["score_after"], len(goal["goal"]["assists"])), ("away", "2", "EQ", {"home": 0, "away": 1}, 2))
        self.assertIsInstance(goal["goal"]["scorer"]["number"], int)
        self.assertEqual((penalty["penalty"]["code"], penalty["penalty"]["description"], penalty["penalty"]["minutes"]), ("HOOK", "Hooking", 2))
        self.assertEqual({event["type"] for event in events}, {"goal", "penalty", "goalie_change", "timeout", "period_start", "period_end"})

    def test_rbihf_without_the_roster_still_parses_but_has_no_names(self):
        row = '<tr class="goal"><td class="time">01:00</td><td class="event">Goal<br />EQ</td><td class="team"></td><td class="note"><span> (#)</span> scored the <span class="highlight">1-0</span>, no assists.</td></tr>'
        [goal] = timeline_parsers.parse_rbihf(json.dumps({"success": True, "live": False, "data": f"<table>{row}</table>"}), {})["events"]
        self.assertEqual((goal["goal"]["scorer"], goal["side"]), ({"name": ""}, None))

    def test_hockeydata_ids_strength_and_hyphenated_codes(self):
        events = self.parse_all()["dfel"]["events"]
        powerplay = next(event for event in events if event["type"] == "goal" and event["goal"]["strength"] == "PP1")
        hyphenated = next(event for event in events if event["type"] == "penalty" and event["penalty"]["code"] == "HI-ST")
        self.assertTrue(powerplay["id"].startswith("hd:"))
        self.assertEqual(hyphenated["penalty"]["description"], "High Sticking")
        self.assertEqual(self.parse_all()["dfel"]["team_stats"]["home"]["pp_goals"], 2)

    def test_ijnl_state_badges_and_keys(self):
        document = self.parse_all()["ijnl"]
        winner = next(event for event in document["events"] if event["type"] == "goal" and event["goal"]["game_winning"])
        self.assertEqual(document["status"], "final")
        self.assertTrue(winner["id"].startswith("ijnl:goal:home:13:39"))
        self.assertEqual(next(event for event in document["events"] if event["type"] == "penalty")["penalty"]["minutes"], 2)

    def test_a_malformed_payload_is_a_parse_error(self):
        for parse, payload in [(timeline_parsers.parse_cehl, "<html>"), (timeline_parsers.parse_hockeydata, json.dumps({"statusId": 0, "statusMsg": "Invalid apiKey"})), (timeline_parsers.parse_ijnl, "nope")]:
            with self.subTest(parse=parse.__name__), self.assertRaises(timeline_parsers.TimelineParseError):
                parse(payload)
        with self.assertRaises(timeline_parsers.TimelineParseError):
            timeline_parsers.rbihf_extract_gd("<html></html>")

    def test_rbihf_form_fields_nest_like_jquery(self):
        fields = dict(timeline_parsers.rbihf_form_fields({"gameNr": "137", "home": {"team": {"18438": {"nr": 35}}}}))
        self.assertEqual(fields, {"gd[gameNr]": "137", "gd[home][team][18438][nr]": "35"})


def penalty_event(seconds, side, kind="minor", minutes=None):
    return {
        "id": f"p{seconds}{side}",
        "type": "penalty",
        "period": "1",
        "seconds": seconds,
        "time": f"{seconds // 60:02d}:{seconds % 60:02d}",
        "side": side,
        "penalty": {"player": {"name": "Pat", "number": 9}, "kind": kind, "minutes": minutes or {"minor": 2, "major": 5, "misconduct": 10}.get(kind, 2)},
    }


def goal_event(seconds, side, strength="EQ", **goal):
    return {"id": f"g{seconds}{side}", "type": "goal", "period": "1" if seconds <= 1200 else "2", "seconds": seconds, "side": side, "goal": {"scorer": {"name": "Sam", "number": 7}, "assists": [], "strength": strength, **goal}}


class StrengthCheckTests(SimpleTestCase):
    """events.services.strength_check: a goal's recorded strength against the penalties in the game."""

    def conflicts(self, *events, overrides=None):
        return {goal: found["expected"] for goal, found in strength_check.strength_conflicts({"events": list(events)}, overrides).items()}

    def test_the_real_ijnl_game_flags_the_goal_scored_during_a_running_minor(self):
        conflicts = strength_check.strength_conflicts(timeline_parsers.parse_ijnl(timeline_fixture("ijnl_game_49a66dbb.json")))
        self.assertEqual({goal: found["expected"] for goal, found in conflicts.items()}, {"ijnl:goal:home:18:09::/clubs-en-spelers/speler/p54": "PP1"})
        [found] = conflicts.values()
        self.assertEqual([(penalty["side"], penalty["time"], penalty["kind"]) for penalty in found["penalties"]], [("away", "17:40", "minor")])

    def test_a_minor_costs_two_minutes_of_power_play(self):
        self.assertEqual(self.conflicts(penalty_event(100, "away"), goal_event(219, "home", "EQ")), {"g219home": "PP1"})
        self.assertEqual(self.conflicts(penalty_event(100, "away"), goal_event(221, "home", "EQ")), {})
        self.assertEqual(self.conflicts(penalty_event(100, "away"), goal_event(150, "home", "PP1")), {})

    def test_a_goal_on_the_power_play_ends_a_minor_but_not_a_major(self):
        self.assertEqual(self.conflicts(penalty_event(100, "away"), goal_event(130, "home", "PP1"), goal_event(150, "home", "PP1")), {"g150home": "EQ"})
        self.assertEqual(self.conflicts(penalty_event(100, "away", "major"), goal_event(130, "home", "PP1"), goal_event(150, "home", "EQ")), {"g150home": "PP1"})

    def test_a_misconduct_does_not_put_the_team_a_skater_down(self):
        self.assertEqual(self.conflicts(penalty_event(100, "away", "misconduct"), goal_event(130, "home", "PP1")), {"g130home": "EQ"})
        self.assertEqual(self.conflicts(penalty_event(100, "away", "misconduct"), goal_event(130, "home", "EQ")), {})

    def test_two_minors_make_a_five_on_three_and_a_short_handed_goal_is_not_even_strength(self):
        two_down = [penalty_event(100, "away"), penalty_event(110, "away")]
        self.assertEqual(self.conflicts(*two_down, goal_event(130, "home", "EQ")), {"g130home": "PP2"})
        self.assertEqual(self.conflicts(*two_down, goal_event(130, "home", "PP1")), {})
        self.assertEqual(self.conflicts(penalty_event(100, "away"), goal_event(130, "away", "EQ")), {"g130away": "SH1"})

    def test_a_goal_only_cancels_one_of_two_minors_and_a_double_minor_keeps_its_second_half(self):
        self.assertEqual(self.conflicts(penalty_event(100, "away"), penalty_event(110, "away"), goal_event(130, "home", "PP2"), goal_event(150, "home", "PP1")), {})
        double = penalty_event(0, "away", "double_minor", 4)
        self.assertEqual(self.conflicts(double, goal_event(60, "home", "PP1"), goal_event(150, "home", "PP1"), goal_event(190, "home", "EQ")), {})
        self.assertEqual(self.conflicts(double, goal_event(60, "home", "PP1"), goal_event(190, "home", "PP1")), {"g190home": "EQ"})

    def test_two_penalties_for_the_same_player_at_the_same_time_are_served_one_after_the_other(self):
        twice = [penalty_event(100, "away"), {**penalty_event(100, "away"), "id": "p100away2"}]
        self.assertEqual(self.conflicts(*twice, goal_event(130, "home", "EQ")), {"g130home": "PP1"})
        self.assertEqual(self.conflicts(*twice, goal_event(130, "home", "PP2")), {})
        self.assertEqual(self.conflicts(*twice, goal_event(330, "home", "PP1")), {})
        self.assertEqual(self.conflicts(*twice, goal_event(345, "home", "PP1")), {"g345home": "EQ"})
        self.assertEqual(self.conflicts(*twice, goal_event(130, "home", "PP1"), goal_event(240, "home", "PP1"), goal_event(260, "home", "EQ")), {})
        other = {**penalty_event(100, "away"), "id": "p100away2", "penalty": {**penalty_event(100, "away")["penalty"], "player": {"name": "Other", "number": 12}}}
        self.assertEqual(self.conflicts(penalty_event(100, "away"), other, goal_event(130, "home", "EQ")), {"g130home": "PP2"})

    def test_coincidental_minors_are_even_strength(self):
        self.assertEqual(self.conflicts(penalty_event(100, "away"), penalty_event(100, "home"), goal_event(130, "home", "EQ")), {})

    def test_goals_the_timeline_cannot_judge_are_left_alone(self):
        minor = penalty_event(100, "away")
        self.assertEqual(self.conflicts(minor, goal_event(100, "home", "EQ")), {})
        self.assertEqual(self.conflicts(minor, goal_event(220, "home", "EQ")), {})
        self.assertEqual(self.conflicts(minor, goal_event(130, "home", "EQ", empty_net=True)), {})
        self.assertEqual(self.conflicts(minor, goal_event(130, "home", "EQ", penalty_shot=True)), {})
        self.assertEqual(self.conflicts(minor, goal_event(130, "home", "PS")), {})
        self.assertEqual(self.conflicts(penalty_event(3500, "away"), goal_event(3650, "home", "EQ")), {})
        self.assertEqual(self.conflicts({**goal_event(130, "home", "EQ"), "seconds": None}), {})

    def test_a_penalty_the_other_side_scored_on_at_the_same_second_is_waived(self):
        self.assertEqual(self.conflicts(goal_event(100, "home", "EQ"), penalty_event(100, "away"), goal_event(150, "away", "EQ")), {})

    def test_a_corrected_or_confirmed_strength_stops_being_flagged(self):
        events = [penalty_event(100, "away"), goal_event(130, "home", "EQ")]
        self.assertEqual(self.conflicts(*events), {"g130home": "PP1"})
        self.assertEqual(self.conflicts(*events, overrides={"g130home": "PP1"}), {})
        self.assertEqual(self.conflicts(*events, overrides={"g130home": "EQ"}), {})


class GameTimelineStoreTests(GameTestBase):
    def test_store_saves_the_document_and_skips_an_unchanged_one(self):
        game = self.make_game()
        timeline, changed = game_timeline.store(game, self.rbihf_fetch())
        first_changed_at = timeline.changed_at
        timeline_again, changed_again = game_timeline.store(game, self.rbihf_fetch())

        self.assertTrue(changed)
        self.assertFalse(changed_again)
        self.assertEqual(timeline_again.changed_at, first_changed_at)
        self.assertEqual((timeline.source, timeline.status, timeline.data["score"]), ("RBIHF", GameTimeline.Status.FINAL, {"home": 5, "away": 2}))
        self.assertTrue(timeline.raw)

    def test_status_follows_the_game(self):
        live = self.make_game(is_live=True)
        upcoming = self.make_game(external_game_id="140", start=timezone.now() + timedelta(hours=2))
        self.assertEqual(game_timeline.store(live, self.rbihf_fetch())[0].status, GameTimeline.Status.LIVE)
        self.assertEqual(game_timeline.store(upcoming, game_timeline.TimelineFetch(document={"events": []}))[0].status, GameTimeline.Status.SCHEDULED)
        self.assertEqual(game_timeline.store(upcoming, game_timeline.TimelineFetch(document={"status": "final", "events": []}))[0].status, GameTimeline.Status.FINAL)

    def test_a_failed_fetch_keeps_the_previous_data_and_records_the_error(self):
        game = self.make_game()
        game_timeline.store(game, self.rbihf_fetch())
        competition = mock.Mock()
        competition.fetch_timeline.side_effect = requests.ConnectionError("RBIHF is down")

        self.assertEqual(game_timeline.fetch_and_store(game, competition), game_timeline.FAILED)
        timeline = GameTimeline.objects.get(event=game)
        self.assertEqual(timeline.last_error, "RBIHF is down")
        self.assertEqual(len(timeline.data["events"]), 27)
        competition.fetch_timeline.side_effect, competition.fetch_timeline.return_value = None, None
        self.assertEqual(game_timeline.fetch_and_store(game, competition), game_timeline.UNAVAILABLE)

    def test_display_timeline_is_from_our_side(self):
        home_game = self.make_game()
        away_game = self.make_game(external_game_id="138", location=None)
        for game in (home_game, away_game):
            game_timeline.store(game, self.rbihf_fetch())

        home = game_timeline.display_timeline(home_game.timeline, home_game)
        away = game_timeline.display_timeline(away_game.timeline, away_game)
        home_goal = next(row for period in home["periods"] for row in period["rows"] if row["type"] == "goal")
        away_goal = next(row for period in away["periods"] for row in period["rows"] if row["type"] == "goal")

        self.assertEqual((home["score"], away["score"]), ("5–2", "2–5"))
        self.assertEqual((home_goal["ours"], home_goal["team"], home_goal["score"]), (False, "Olympia Heist", "0–1"))
        self.assertEqual((away_goal["ours"], away_goal["team"], away_goal["score"]), (True, "First Team", "1–0"))
        self.assertTrue(home_goal["who"].startswith("#"))
        self.assertTrue(home_goal["detail"].startswith("Assists: "))

    def test_serious_penalties_are_tagged(self):
        game = self.make_game()
        major = {"id": "p1", "type": "penalty", "side": "away", "period": "3", "seconds": 3136, "time": "52:16", "penalty": {"player": {"name": "Max", "number": 17}, "description": "Checking To The Head", "kind": "game_misconduct", "minutes": 20}}
        minor = {"id": "p2", "type": "penalty", "side": "home", "period": "3", "seconds": 3200, "time": "53:20", "penalty": {"player": {"name": "Kim"}, "description": "Tripping", "kind": "minor", "minutes": 2}}
        game_timeline.store(game, game_timeline.TimelineFetch(document={"events": [major, minor]}))

        serious, plain = game_timeline.display_timeline(game.timeline, game)["periods"][0]["rows"]

        self.assertEqual((serious["serious"], serious["tag"], serious["detail"]), (True, "Game misconduct", "Checking To The Head · 20 min"))
        self.assertEqual((plain["serious"], plain["tag"]), (False, ""))

    def test_a_penalty_shot_gets_its_own_row_and_a_team_penalty_names_the_team(self):
        game = self.make_game()
        shot = {"id": "s1", "type": "penalty_shot", "side": "home", "period": "3", "seconds": 2755, "time": "45:55"}
        foul = {"id": "f1", "type": "penalty", "side": "away", "period": "3", "seconds": 2755, "time": "45:55", "penalty": {"player": None, "code": "TRIP", "description": "Tripping"}}
        game_timeline.store(game, game_timeline.TimelineFetch(document={"events": [foul, shot]}))

        foul_row, shot_row = game_timeline.display_timeline(game.timeline, game)["periods"][0]["rows"]

        self.assertEqual((foul_row["who"], foul_row["ours"]), ("Olympia Heist", False))
        self.assertEqual((shot_row["type"], shot_row["icon"], shot_row["ours"], shot_row["who"]), ("penalty_shot", "target", True, "First Team"))

    def test_display_timeline_groups_by_period_without_marker_rows(self):
        game = self.make_game()
        game_timeline.store(game, self.rbihf_fetch())
        view = game_timeline.display_timeline(game.timeline, game)
        self.assertEqual([(period["title"], period["score"]) for period in view["periods"]], [("Period 1", "0–0"), ("Period 2", "3–2"), ("Period 3", "2–0")])
        self.assertFalse(any(row["type"].startswith("period") for period in view["periods"] for row in period["rows"]))

    def test_timeline_for_api_resolves_our_side_and_hides_names_unless_asked(self):
        game = self.make_game(location=None)  # away: our side is "away"
        timeline, _changed = game_timeline.store(game, self.rbihf_fetch())

        shown = game_timeline.timeline_for_api(timeline, game, show_names=True)
        hidden = game_timeline.timeline_for_api(timeline, game, show_names=False)

        from events.timeline_schemas import TimelineOut

        TimelineOut.model_validate(shown)
        goal = next(item for item in shown["events"] if item["type"] == "goal")
        self.assertEqual((shown["our_side"], shown["decided_in"], goal["ours"]), ("away", "regulation", True))
        self.assertIn("name", goal["goal"]["scorer"])
        self.assertNotIn("name", next(item for item in hidden["events"] if item["type"] == "goal")["goal"]["scorer"])


class ReparseStoredTimelinesTests(GameTestBase):
    def stored_ijnl_game(self):
        game = self.make_game(competition="IJNL", external_game_id="49a66dbb")
        payload = json.loads(timeline_fixture("ijnl_game_49a66dbb.json"))
        old = {**payload, "sections": {key: value for key, value in payload["sections"].items() if key != "lineups"}}
        document = timeline_parsers.finalize_document(timeline_parsers.parse_ijnl(json.dumps(old)))
        timeline = GameTimeline.objects.create(event=game, source="IJNL", data=document, data_hash=game_timeline.document_hash(document), raw=timeline_fixture("ijnl_game_49a66dbb.json"), status=GameTimeline.Status.FINAL)
        return game, timeline

    def assist_numbers(self, timeline):
        return [[assist.get("number") for assist in event["goal"]["assists"]] for event in timeline.data["events"] if event["type"] == "goal"]

    def test_reparse_applies_the_current_parser_to_the_stored_payload_without_any_signals(self):
        _game, timeline = self.stored_ijnl_game()
        self.assertEqual(self.assist_numbers(timeline)[0], [None, None])

        self.assertTrue(game_timeline.reparse(timeline))

        timeline.refresh_from_db()
        self.assertEqual(self.assist_numbers(timeline)[0], [62, 87])
        self.assertEqual(timeline.data["team_stats"]["home"]["pp_opportunities"], 3)
        self.assertEqual(GameMoment.objects.count(), 0)
        self.assertFalse(game_timeline.reparse(timeline))

    def test_reparse_leaves_sources_and_timelines_it_cannot_re_run_alone(self):
        _game, timeline = self.stored_ijnl_game()
        timeline.source = "RBIHF"
        self.assertFalse(game_timeline.reparse(timeline))
        timeline.source, timeline.raw = "IJNL", ""
        self.assertFalse(game_timeline.reparse(timeline))


class FakeCompetition:
    """Stands in for an events.competition class: sets the score/live flag it is told to, returns a fixed timeline."""

    def __init__(self, *, live=False, score=(5, 2), timeline=None, fail=False):
        self.live, self.score, self.timeline, self.fail = live, score, timeline, fail
        self.calls = []

    def update_game_information(self, event):
        self.calls.append(event.pk)
        if self.fail:
            raise requests.ConnectionError("down")
        event.is_live = self.live
        event.score_for, event.score_against = self.score
        event.save(update_fields=["is_live", "score_for", "score_against"])

    def fetch_timeline(self, event):
        return self.timeline() if callable(self.timeline) else self.timeline


class PollLiveGamesTests(GameTestBase):
    """game_timeline.poll_live_games / the events.poll_live_games task, with a fake competition."""

    def poll(self, competition):
        with mock.patch("events.services.game_timeline.competition_for", return_value=competition):
            return game_timeline.poll_live_games()

    def test_only_games_in_the_window_with_a_game_id_are_polled(self):
        soon = self.make_game(start=timezone.now() + timedelta(minutes=10))
        self.make_game(external_game_id="200", start=timezone.now() + timedelta(hours=3))  # too early
        self.make_game(external_game_id="201", start=timezone.now() - timedelta(hours=6))  # long over
        self.make_game(external_game_id="")  # no id
        self.make_game(external_game_id="202", cancelled=True)
        done = self.make_game(external_game_id="203")
        Event.objects.filter(pk=done.pk).update(live_score_polling_done_at=timezone.now())
        late_live = self.make_game(external_game_id="204", start=timezone.now() - timedelta(hours=5), is_live=True)  # still live: polled however late
        competition = FakeCompetition(live=True, timeline=lambda: self.rbihf_fetch())

        counts = self.poll(competition)

        self.assertEqual(set(competition.calls), {soon.pk, late_live.pk})
        self.assertEqual((counts["checked"], counts["fetched"]), (2, 2))
        self.assertEqual(GameTimeline.objects.get(event=soon).status, GameTimeline.Status.LIVE)
        soon.refresh_from_db()
        self.assertEqual((soon.score_for, soon.score_against, soon.is_live), (5, 2, True))

    def test_a_live_game_ends_after_three_not_live_reads_and_stops_being_polled(self):
        game = self.make_game(is_live=True)
        competition = FakeCompetition(live=False, timeline=lambda: self.rbihf_fetch())

        for _read in range(2):
            self.assertEqual(self.poll(competition)["finished"], 0)
            game.refresh_from_db()
            self.assertTrue(game.is_live)
        self.assertEqual(self.poll(competition)["finished"], 1)

        game.refresh_from_db()
        self.assertFalse(game.is_live)
        self.assertIsNotNone(game.live_score_polling_done_at)
        self.assertEqual(self.poll(competition)["checked"], 0)

    def test_a_failing_game_is_counted_and_the_sweep_goes_on(self):
        self.make_game()
        self.assertEqual(self.poll(FakeCompetition(fail=True))["failed"], 1)
        self.assertEqual(self.poll(None)["fetched"], 0)  # no data source this club may use

    def test_the_task_runs_every_club(self):
        from events.tasks import poll_live_games

        self.make_game()
        with mock.patch("events.services.game_timeline.competition_for", return_value=FakeCompetition(timeline=lambda: self.rbihf_fetch())):
            summary = poll_live_games()
        self.assertIn("checked 1 game(s); fetched 1", summary)

    def test_without_a_competitions_service_the_club_competition_is_resolved_by_its_feature(self):
        from club.models import Competition

        game = self.make_game(competition="FakeComp")
        Competition.objects.create(name="FakeCompetition", module="events.tests_timelines")
        self.assertIsNone(game_timeline._own_competition_for(game))  # no Competition row with the game's name
        Competition.objects.create(name="FakeComp", module="events.tests_timelines")
        with self.assertRaises(game_timeline.CompetitionFetchError):  # the module has no class called "FakeComp"
            game_timeline._own_competition_for(game)


class SettleGameTimelinesTests(GameTestBase):
    def finished_game(self, **kwargs):
        game = self.make_game(start=timezone.now() - timedelta(hours=5), **kwargs)
        game_timeline.store(game, self.rbihf_fetch())
        return game

    def test_an_unchanged_final_timeline_is_finalized_and_a_changed_one_is_not(self):
        from events.tasks import settle_game_timelines

        stable = self.finished_game()
        corrected = self.finished_game(external_game_id="138")
        changed_document = self.rbihf_fetch()
        changed_document.document["events"] = changed_document.document["events"][:-1]
        competition = mock.Mock()
        competition.fetch_timeline.side_effect = lambda event: changed_document if event.pk == corrected.pk else self.rbihf_fetch()

        with mock.patch("events.services.game_timeline.competition_for", return_value=competition):
            result = settle_game_timelines()

        stable.timeline.refresh_from_db()
        corrected.timeline.refresh_from_db()
        self.assertIsNotNone(stable.timeline.finalized_at)
        self.assertIsNone(corrected.timeline.finalized_at)
        self.assertIn("1 finalized, 1 still changing", result)

    def test_too_recent_games_and_games_without_a_source_are_left_alone(self):
        recent = self.make_game(external_game_id="139", start=timezone.now() - timedelta(minutes=30))
        game_timeline.store(recent, self.rbihf_fetch())
        self.finished_game()
        competition = mock.Mock()

        with mock.patch("events.services.game_timeline.competition_for", return_value=None):
            self.assertEqual(game_timeline.settle_timelines(), {"skipped": 1})
        competition.fetch_timeline.assert_not_called()

    def test_settle_brings_stats_in_agreement_with_an_unchanged_timeline(self):
        game = self.make_game(start=timezone.now() - timedelta(hours=5))
        document = {"status": "final", "events": [goal_event(60, "home")]}
        timeline = GameTimeline.objects.create(event=game, source="test", data=document, data_hash="x", status=GameTimeline.Status.FINAL)

        with mock.patch("events.services.game_timeline.fetch_and_store", return_value="unchanged"):
            self.assertEqual(game_timeline.settle(timeline, None), "finalized")
        self.assertEqual(GameStat.objects.filter(event=game).count(), 1)

    def test_backfill_fetches_played_games_and_finalizes_old_ones(self):
        old = self.make_game(start=timezone.now() - timedelta(days=20))
        recent = self.make_game(external_game_id="138", start=timezone.now() - timedelta(days=1))
        self.make_game(external_game_id="139", start=timezone.now() + timedelta(days=3))
        competition = FakeCompetition(timeline=lambda: self.rbihf_fetch())

        with mock.patch("events.services.game_timeline.competition_for", return_value=competition):
            self.assertEqual(game_timeline.backfill_timelines(since=timezone.now() - timedelta(days=30), dry_run=True)["games"], 2)
            self.assertFalse(GameTimeline.objects.exists())
            counts = game_timeline.backfill_timelines(since=timezone.now() - timedelta(days=30))

        self.assertEqual((counts["changed"], counts["finalized"]), (2, 1))
        self.assertIsNotNone(GameTimeline.objects.get(event=old).finalized_at)
        self.assertIsNone(GameTimeline.objects.get(event=recent).finalized_at)
        self.assertFalse(GameMoment.objects.exists())  # a first fetch is silent


class IJNLGoalieTests(GameTestBase):
    """IJNL enters its goalies after the game: they become the goals conceded, and a game isn't settled before they are in."""

    def fetch(self, *, goalies):
        payload = json.loads(timeline_fixture("ijnl_game_49a66dbb.json"))
        if not goalies:
            payload["sections"].pop("lineups")
        return game_timeline.TimelineFetch(document=timeline_parsers.parse_ijnl(json.dumps(payload)), raw=json.dumps(payload))

    def test_the_goalies_become_the_goals_conceded_by_our_side(self):
        game = self.make_game(competition="IJNL", external_game_id="49a66dbb")
        game_timeline.store(game, self.fetch(goalies=True))
        rows = GameStat.objects.filter(event=game, stat_type=GameStat.StatType.GOAL_CONCEDED)
        self.assertEqual((sum(row.value for row in rows), {row.player_name for row in rows}, {row.is_ours for row in rows}), (4, {"Player 55"}, {True}))

    def test_without_goalie_changes_in_the_timeline_the_lineup_totals_are_used(self):
        game = self.make_game(competition="IJNL", external_game_id="49a66dbb")
        payload = json.loads(timeline_fixture("ijnl_game_49a66dbb.json"))
        timeline = BeautifulSoup(payload["sections"]["timeline"], "html.parser")
        for item in timeline.select('li.mp-event[data-event="goalie"]'):
            item.decompose()
        payload["sections"]["timeline"] = str(timeline)

        game_timeline.store(game, game_timeline.TimelineFetch(document=timeline_parsers.parse_ijnl(json.dumps(payload))))

        [conceded] = GameStat.objects.filter(event=game, stat_type=GameStat.StatType.GOAL_CONCEDED)
        self.assertEqual((conceded.player_name, conceded.player_number, conceded.value, conceded.is_ours, conceded.meta), ("Player 55", 55, 4, True, {"seconds_played": 3600}))

    def test_a_game_is_not_settled_until_its_goalies_are_in(self):
        game = self.make_game(competition="IJNL", external_game_id="49a66dbb", start=timezone.now() - timedelta(hours=5))
        game_timeline.store(game, self.fetch(goalies=False))

        def settle(goalies):
            competition = mock.Mock()
            competition.fetch_timeline.side_effect = lambda event: self.fetch(goalies=goalies)
            with mock.patch("events.services.game_timeline.competition_for", return_value=competition):
                game_timeline.settle_timelines()
            game.timeline.refresh_from_db()
            return game.timeline.finalized_at

        self.assertIsNone(settle(goalies=False))
        self.assertIsNone(settle(goalies=True))
        self.assertTrue(GameStat.objects.filter(event=game, stat_type=GameStat.StatType.GOAL_CONCEDED).exists())
        self.assertIsNotNone(settle(goalies=True))


def moment_doc(status, events, home=0, away=0):
    return {"status": status, "score": {"home": home, "away": away}, "events": events}


def goal_item(event_id, side, seconds, home, away, name="Sam"):
    return {
        "id": event_id,
        "type": "goal",
        "side": side,
        "period": "1",
        "seconds": seconds,
        "time": f"{seconds // 60:02d}:{seconds % 60:02d}",
        "goal": {"scorer": {"name": name, "number": 9}, "assists": [], "score_after": {"home": home, "away": away}},
    }


class DeriveMomentsTests(SimpleTestCase):
    """events.services.game_moments.derive_moments -- the pure rules."""

    def kinds(self, previous, current, our_side="home", first_fetch=False):
        return [moment.kind for moment in game_moments.derive_moments(previous, current, our_side=our_side, first_fetch=first_fetch)]

    def test_the_first_fetch_is_silent(self):
        self.assertEqual(self.kinds(None, moment_doc("live", [goal_item("g1", "home", 60, 1, 0)], home=1), first_fetch=True), [])

    def test_kick_off_goals_from_our_side_and_the_result(self):
        scheduled = moment_doc("scheduled", [])
        live = moment_doc("live", [goal_item("g1", "home", 60, 1, 0)], home=1)
        final = moment_doc("final", [goal_item("g1", "home", 60, 1, 0), goal_item("g2", "away", 120, 1, 1)], home=1, away=1)
        self.assertEqual(self.kinds(scheduled, live), ["game_started", "goal_for"])
        self.assertEqual(self.kinds(live, final), ["goal_against", "game_final"])
        self.assertEqual(self.kinds(live, final, our_side="away"), ["goal_for", "game_final"])

    def test_the_score_is_from_our_side(self):
        [moment] = game_moments.derive_moments(moment_doc("live", []), moment_doc("live", [goal_item("g1", "home", 60, 1, 0)], home=1), our_side="away", first_fetch=False)
        self.assertEqual((moment.kind, moment.payload["score"], moment.key), ("goal_against", {"for": 0, "against": 1}, "goal_against:g1"))

    def test_a_penalty_shot_is_a_moment_from_our_side(self):
        live = moment_doc("live", [])
        current = moment_doc("live", [{"id": "s1", "type": "penalty_shot", "side": "away", "period": "3", "seconds": 2755}])
        self.assertEqual(self.kinds(live, current, our_side="away"), ["penalty_shot_for"])
        self.assertEqual(self.kinds(live, current, our_side="home"), ["penalty_shot_against"])

    def test_penalties_and_period_ends(self):
        current = moment_doc(
            "live",
            [
                {"id": "p1", "type": "penalty", "side": "away", "period": "1", "seconds": 300, "penalty": {"player": {"name": "Rex"}, "description": "Tripping", "minutes": 2}},
                {"id": "pe1", "type": "period_end", "side": None, "period": "1", "seconds": 1200},
            ],
        )
        self.assertEqual(self.kinds(moment_doc("live", []), current), ["penalty_against", "period_ended"])

    def test_corrections_after_the_final_are_silent(self):
        final = moment_doc("final", [goal_item("g1", "home", 60, 1, 0)], home=1)
        corrected = moment_doc("final", [goal_item("g1", "home", 60, 1, 0), goal_item("g2", "home", 3000, 2, 0)], home=2)
        self.assertEqual(self.kinds(final, corrected), [])

    def test_a_vanished_goal_is_cancelled_only_when_the_score_drops(self):
        before = moment_doc("live", [goal_item("g1", "home", 60, 1, 0, name="Sam")], home=1)
        rekeyed = moment_doc("live", [goal_item("g1b", "home", 60, 1, 0, name="Kim")], home=1)
        self.assertEqual(self.kinds(before, rekeyed), ["goal_for"])
        self.assertEqual(self.kinds(before, moment_doc("live", [], home=0)), ["goal_cancelled"])

    def test_a_late_catch_up_is_recorded_but_not_notifiable(self):
        caught_up = moment_doc("live", [goal_item("old", "home", 60, 1, 0), goal_item("new", "home", 1500, 2, 0)], home=2)
        moments = {moment.key: moment for moment in game_moments.derive_moments(moment_doc("live", []), caught_up, our_side="home", first_fetch=False)}
        self.assertFalse(moments["goal_for:old"].notifiable)
        self.assertTrue(moments["goal_for:new"].notifiable)

    def test_pre_registered_goalie_changes_do_not_make_live_moments_look_late(self):
        goalies = [
            {"id": "gk-on", "type": "goalie_change", "side": "home", "period": "1", "seconds": 0},
            {"id": "gk-off", "type": "goalie_change", "side": "home", "period": "3", "seconds": 3600},
        ]
        [moment] = game_moments.derive_moments(moment_doc("live", goalies), moment_doc("live", [*goalies, goal_item("g1", "home", 420, 1, 0)], home=1), our_side="home", first_fetch=False)
        self.assertTrue(moment.notifiable)


class RecordGameMomentsTests(GameTestBase):
    """game_timeline.store -> game_timeline_changed (events/signals_games.py) -> GameMoment rows."""

    def fetch(self, events):
        return game_timeline.TimelineFetch(document={"events": events})

    def test_a_changed_timeline_records_its_moments_once(self):
        game = self.make_game(is_live=True)
        game_timeline.store(game, self.fetch([]))  # first fetch: silent
        game_timeline.store(game, self.fetch([goal_item("g1", "home", 60, 1, 0)]))

        game_moments.game_timeline_changed.send(sender=Event, event=game, previous={"status": "live", "events": [], "score": {"home": 0, "away": 0}}, current=game.timeline.data, first_fetch=False)

        self.assertEqual(list(GameMoment.objects.filter(event=game).values_list("kind", "key")), [("goal_for", "goal_for:g1")])

    def test_the_first_fetch_records_nothing(self):
        game = self.make_game(is_live=True)
        game_timeline.store(game, self.rbihf_fetch())
        self.assertFalse(GameMoment.objects.filter(event=game).exists())

    def describe(self, game, moment_kind, **payload):
        return game_moments.describe_moment(GameMoment.objects.create(event=game, key=f"{moment_kind}:{GameMoment.objects.count()}", kind=moment_kind, payload=payload))

    def test_describe_moment(self):
        game = self.make_game(is_live=True)
        sam, score = {"name": "Sam", "number": 9}, {"for": 2, "against": 1}
        self.assertEqual(self.describe(game, GameMoment.Kind.GOAL_FOR, scorer=sam, score=score), "Goal for us by Sam (#9), 2-1.")
        self.assertEqual(self.describe(game, GameMoment.Kind.GOAL_FOR, scorer=sam, score=score, assists=[{"name": "Ann", "number": 7}]), "Goal for us by Sam (#9) (assist: Ann (#7)), 2-1.")
        self.assertEqual(self.describe(game, GameMoment.Kind.GOAL_FOR, scorer=sam, score=score, assists=[{"name": "Ann", "number": 7}, {"name": "Lee"}]), "Goal for us by Sam (#9) (assists: Ann (#7), Lee), 2-1.")
        self.assertEqual(self.describe(game, GameMoment.Kind.GOAL_FOR, scorer=sam, score=score, assists=[{}, {"name": ""}]), "Goal for us by Sam (#9), 2-1.")
        self.assertEqual(self.describe(game, GameMoment.Kind.GOAL_AGAINST, scorer=sam, score=score, assists=[{"name": "Ann", "number": 7}]), "Goal against by Sam (#9), 2-1.")
        self.assertEqual(
            self.describe(game, GameMoment.Kind.PENALTY_AGAINST, player={"name": "Max", "number": 17}, description="Checking To The Head", kind="game_misconduct"),
            "Penalty for the opponent: Max (#17), Checking To The Head (game misconduct).",
        )
