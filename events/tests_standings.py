"""RBIHF league standings (events.services.rbihf_standings): parsing a league page, computing the table, storing it on
the team's import source, and the refresh triggers (a finished game, the nightly task). No network."""

from datetime import date, timedelta
from unittest import mock

from django.core.cache import cache
from django.test import TestCase
from django_tenants.utils import schema_context

from club.services import create_club
from events.models import Event, Season
from events.services import import_sources, rbihf_standings
from events.services.rbihf_import import RBIHFImportError
from events.tasks import refresh_standings
from teams.models import Team

from .tests_imports import ClubTestBase


def rbihf_league_html(played=(), upcoming=(), rule="Won: 2 points | Draw: 1 point | Lost: 0 points", friendly_played=()):
    """A cut-down RBIHF league page: a ranked "Regular Competition" tab (type-513) and an unranked "Friendly Games" tab
    (type-498). ``played`` rows are (game_id, home_id, home_name, away_id, away_name, score); ``upcoming`` the same
    without the score."""

    def team_cell(team_id, name):
        return f'<td><a href="/league/team/{team_id}" title="{name}">{name}</a></td>'

    def played_table(rows):
        body = "".join(
            f'<tr><td class="game-nr"><a href="/game/{game_id}">{game_id}</a></td><td class="date">27 Sep 2026</td><td class="hour">20:00</td><td>Deurne</td>{team_cell(home_id, home)}{team_cell(away_id, away)}<td class="score">{score}</td></tr>'
            for game_id, home_id, home, away_id, away, score in rows
        )
        return f"<h2>Played games</h2><table><tr><th>#</th><th>Date</th><th>Hour</th><th>Location</th><th>Home</th><th>Visit</th><th>Score</th></tr>{body}</table>"

    def upcoming_table(rows):
        body = "".join(f'<tr><td><a href="/game/{game_id}">{game_id}</a></td><td>04 Oct 2026</td><td>18:00</td><td>Mechelen</td>{team_cell(home_id, home)}{team_cell(away_id, away)}</tr>' for game_id, home_id, home, away_id, away in rows)
        return f"<h2>Upcoming games</h2><table><tr><th>#</th><th>Date</th><th>Hour</th><th>Location</th><th>Home</th><th>Visit</th></tr>{body}</table>"

    return f"""<html><head><title>RBIHF :: Under 18</title></head><body>
    <ul><li><a href="#type-all">Summary</a></li><li><a href="#type-498">Friendly Games</a></li><li><a href="#type-513">Regular Competition</a></li></ul>
    <div id="type-all" class="tab">{played_table(played)}</div>
    <div id="type-498" class="tab">{played_table(friendly_played)}</div>
    <div id="type-513" class="tab">{upcoming_table(upcoming)}{played_table(played)}
      <div class="node page ranking"><div class="content"><img src="/images/league/ranking/513" /></div><div class="meta">{rule}<br /><a href="/league/u18/stats">More statistics</a>.</div></div>
    </div></body></html>"""


RBIHF_OT_RULE = "Won: 3 points | Won after overtime or penaltyshots: 2 points | Lost after overtime or penaltyshots: 1 point | Lost: 0 points"
RBIHF_TEAM_PAGE_HTML = '<html><body><a href="/league/u18" title="Under 18">Under 18</a><a href="/league/team/4448">Antwerp</a></body></html>'


def standings_rows(standings, competition=0):
    return [(row["position"], row["team_name"], row["played"], row["points"]) for row in standings["competitions"][competition]["rows"]]


class RBIHFStandingsParsingTests(TestCase):
    PLAYED = [
        ("816", "4448", "Antwerp", "4450", "Sharks", "3-6"),
        ("823", "4445", "Haskey", "4443", "Bulldogs", "9-1"),
        ("900", "4443", "Bulldogs", "4448", "Antwerp", "Unfinished"),
    ]
    UPCOMING = [("821", "4445", "Haskey", "4447", "Leuven")]

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def test_points_rules_are_parsed(self):
        simple = rbihf_standings.parse_points_rule("Won: 2 points | Draw: 1 point | Lost: 0 points More statistics.")
        self.assertEqual((simple.win, simple.draw, simple.loss, simple.has_overtime), (2, 1, 0, False))

        overtime = rbihf_standings.parse_points_rule(RBIHF_OT_RULE)
        self.assertEqual((overtime.win, overtime.ot_win, overtime.ot_loss, overtime.loss, overtime.has_overtime), (3, 2, 1, 0, True))

    def test_only_ranked_tabs_are_parsed_with_every_team_and_finished_games(self):
        [competition] = rbihf_standings.parse_league(rbihf_league_html(self.PLAYED, self.UPCOMING, friendly_played=[("2004", "4450", "Sharks", "4445", "Haskey", "1-5")]))

        self.assertEqual((competition.type_id, competition.name), ("513", "Regular Competition"))
        self.assertEqual(set(competition.teams), {"4448", "4450", "4445", "4443", "4447"})
        self.assertEqual([game.game_id for game in competition.games], ["816", "823"])  # "Unfinished" doesn't count
        self.assertEqual(rbihf_standings.league_name(rbihf_league_html()), "Under 18")

    def test_the_table_is_ordered_by_points_then_goal_difference_then_name(self):
        [competition] = rbihf_standings.parse_league(rbihf_league_html(self.PLAYED, self.UPCOMING))
        rows = rbihf_standings.compute_table(competition)

        self.assertEqual([(row["position"], row["team_name"], row["points"], row["goal_difference"]) for row in rows], [(1, "Haskey", 2, 8), (2, "Sharks", 2, 3), (3, "Leuven", 0, 0), (4, "Antwerp", 0, -3), (5, "Bulldogs", 0, -8)])
        sharks = rows[1]
        self.assertEqual((sharks["played"], sharks["wins"], sharks["losses"], sharks["goals_for"], sharks["goals_against"]), (1, 1, 0, 6, 3))

    def test_a_draw_scores_the_draw_points(self):
        [competition] = rbihf_standings.parse_league(rbihf_league_html([("1", "4448", "Antwerp", "4450", "Sharks", "2-2")]))

        self.assertEqual([(row["draws"], row["points"]) for row in rbihf_standings.compute_table(competition)], [(1, 1), (1, 1)])

    def test_a_one_goal_game_decided_after_regulation_earns_overtime_points(self):
        played = [("1", "4448", "Antwerp", "4450", "Sharks", "3-2"), ("2", "4445", "Haskey", "4443", "Bulldogs", "4-3"), ("3", "4447", "Leuven", "4442", "Lions", "5-1")]
        [competition] = rbihf_standings.parse_league(rbihf_league_html(played, rule=RBIHF_OT_RULE))
        checked = []

        def overtime_check(game):
            checked.append(game.game_id)
            return game.game_id == "1"

        rows = {row["team_name"]: row for row in rbihf_standings.compute_table(competition, overtime_check=overtime_check)}

        self.assertEqual(checked, ["1", "2"])  # a three-goal margin can't have gone to overtime
        self.assertEqual((rows["Antwerp"]["ot_wins"], rows["Antwerp"]["points"]), (1, 2))
        self.assertEqual((rows["Sharks"]["ot_losses"], rows["Sharks"]["points"]), (1, 1))
        self.assertEqual((rows["Haskey"]["wins"], rows["Haskey"]["points"]), (1, 3))
        self.assertEqual(rows["Leuven"]["points"], 3)

    def test_a_score_marked_ot_or_ps_earns_overtime_points_without_asking_the_overview(self):
        played = [("207", "4448", "Antwerp", "4450", "Sharks", "3-2(PS)"), ("208", "4445", "Haskey", "4443", "Bulldogs", "4-3 ( OT )")]
        [competition] = rbihf_standings.parse_league(rbihf_league_html(played, rule=RBIHF_OT_RULE))
        overtime_check = mock.Mock()

        rows = {row["team_name"]: row for row in rbihf_standings.compute_table(competition, overtime_check=overtime_check)}

        overtime_check.assert_not_called()
        self.assertEqual((rows["Antwerp"]["played"], rows["Antwerp"]["ot_wins"], rows["Antwerp"]["points"]), (1, 1, 2))
        self.assertEqual((rows["Sharks"]["ot_losses"], rows["Sharks"]["points"]), (1, 1))
        self.assertEqual((rows["Haskey"]["ot_wins"], rows["Bulldogs"]["ot_losses"]), (1, 1))

    def test_a_league_without_overtime_points_never_asks_for_the_overview(self):
        [competition] = rbihf_standings.parse_league(rbihf_league_html([("1", "4448", "Antwerp", "4450", "Sharks", "3-2")]))
        overtime_check = mock.Mock()

        rbihf_standings.compute_table(competition, overtime_check=overtime_check)

        overtime_check.assert_not_called()

    def test_the_overview_tells_whether_regulation_ended_level(self):
        def overview(home, away):
            def cells(values):
                return "".join(f"<td>{value}</td>" for value in values)

            home_row = f'<tr><td rowspan="2" class="goals">Goals</td><td class="title">A</td>{cells(home)}<td>{sum(home)}</td></tr>'
            away_row = f'<tr><td class="title">B</td>{cells(away)}<td>{sum(away)}</td></tr>'
            return {"success": True, "data": f'<table><tr><th colspan="2">Per Period</th></tr>{home_row}{away_row}</table>'}

        self.assertFalse(rbihf_standings.parse_overview(overview([0, 3, 2], [0, 2, 0])))
        self.assertTrue(rbihf_standings.parse_overview(overview([1, 1, 0, 1], [0, 2, 0, 0])))
        self.assertIsNone(rbihf_standings.parse_overview({"success": False, "data": ""}))

    def test_decided_after_regulation_asks_rbihf_once_and_caches(self):
        game = rbihf_standings.PlayedGame(game_id="137", date=date(2026, 9, 27), home_id="1", away_id="2", home_score=3, away_score=2)
        response = mock.Mock(status_code=200)
        response.json.return_value = {"success": True, "data": '<table><tr><td class="goals">G</td><td>A</td><td>1</td><td>1</td><td>0</td><td>1</td><td>3</td></tr><tr><td>B</td><td>0</td><td>2</td><td>0</td><td>0</td><td>2</td></tr></table>'}
        with mock.patch("events.services.rbihf_standings.requests.get", return_value=response) as get:
            self.assertTrue(rbihf_standings.decided_after_regulation(game))
            self.assertTrue(rbihf_standings.decided_after_regulation(game))
        get.assert_called_once()
        self.assertIn("gameNr=137&season=2627", get.call_args.args[0])

    def test_season_code_runs_summer_to_summer(self):
        self.assertEqual(rbihf_standings.season_code(date(2026, 9, 27)), "2627")
        self.assertEqual(rbihf_standings.season_code(date(2027, 3, 1)), "2627")


class RBIHFStandingsRefreshTests(ClubTestBase):
    TEAM_URL = "https://www.rbihf.be/league/team/4448"
    PLAYED = [("816", "4448", "Antwerp", "4450", "Sharks", "3-6")]

    def store(self, team=None, *, url=None, label="", season=None):
        return import_sources.record_source(team or self.team, source=import_sources.SOURCE_RBIHF, url=url or self.TEAM_URL, season=season or self.season, competition_label=label)

    def patch_fetch(self, *pages):
        return mock.patch("events.services.rbihf_standings.fetch_html", side_effect=list(pages))

    def stored(self, team=None):
        (team or self.team).refresh_from_db()
        return (team or self.team).import_sources[0]["standings"]

    def test_refresh_finds_the_league_and_stores_the_table(self):
        entry = self.store()
        with self.patch_fetch(RBIHF_TEAM_PAGE_HTML, rbihf_league_html(self.PLAYED)) as fetch:
            self.assertTrue(rbihf_standings.refresh_entry(self.team, entry))

        self.assertEqual([call.args[0] for call in fetch.call_args_list], [self.TEAM_URL, "https://www.rbihf.be/league/u18"])
        standings = self.stored()
        self.assertEqual((standings["league"], standings["league_slug"], standings["rbihf_team_id"], standings["error"]), ("Under 18", "u18", "4448", ""))
        self.assertEqual(standings_rows(standings), [(1, "Sharks", 1, 2), (2, "Antwerp", 1, 0)])

        [competition] = rbihf_standings.standings_for(self.team, self.season)
        self.assertEqual((competition["league"], competition["competition"]), ("Under 18", "Regular Competition"))
        self.assertEqual([row["is_own_team"] for row in competition["rows"]], [False, True])
        self.assertEqual(rbihf_standings.standings_for(self.team, None), [])

    def test_the_league_page_is_cached_and_the_league_remembered(self):
        with self.patch_fetch(RBIHF_TEAM_PAGE_HTML, rbihf_league_html(self.PLAYED)):
            rbihf_standings.refresh_entry(self.team, self.store())

        self.team.refresh_from_db()
        with self.patch_fetch() as fetch:
            self.assertTrue(rbihf_standings.refresh_entry(self.team, self.team.import_sources[0]))
        fetch.assert_not_called()

        with self.patch_fetch(rbihf_league_html([*self.PLAYED, ("817", "4450", "Sharks", "4448", "Antwerp", "1-4")])) as fetch:
            rbihf_standings.refresh_entry(self.team, self.team.import_sources[0], fresh=True)
        fetch.assert_called_once_with("https://www.rbihf.be/league/u18")
        self.assertEqual(standings_rows(self.stored()), [(1, "Antwerp", 2, 2), (2, "Sharks", 2, 2)])

    def test_a_failed_refresh_keeps_the_last_good_table(self):
        with self.patch_fetch(RBIHF_TEAM_PAGE_HTML, rbihf_league_html(self.PLAYED)):
            rbihf_standings.refresh_entry(self.team, self.store())
        cache.clear()

        self.team.refresh_from_db()
        with self.patch_fetch(RBIHFImportError("RBIHF is down")):
            self.assertFalse(rbihf_standings.refresh_entry(self.team, self.team.import_sources[0]))

        standings = self.stored()
        self.assertEqual(standings["error"], "RBIHF is down")
        self.assertEqual(standings_rows(standings), [(1, "Sharks", 1, 2), (2, "Antwerp", 1, 0)])

    def test_a_reimport_keeps_the_standings_only_for_the_same_url(self):
        entry = self.store()
        import_sources.update_entry(self.team, entry, standings={"competitions": []})

        self.store()
        self.assertIn("standings", self.team.import_sources[0])
        self.store(url="https://www.rbihf.be/league/team/9999")
        self.assertNotIn("standings", self.team.import_sources[0])

    def test_a_finished_game_refreshes_its_teams_matching_source_without_the_cache(self):
        self.enable("competition_rbihf")
        self.store()
        self.store(url="https://www.rbihf.be/league/team/5555", label="Cup")
        game = self.make_event(kind=Event.EventKind.GAME, competition="RBIHF", external_game_id="816", external_source_id="4448")
        game.teams.add(self.team)

        with mock.patch("events.services.rbihf_standings.refresh_entry", return_value=True) as refresh:
            rbihf_standings.refresh_for_finished_game(game)

        [call] = refresh.call_args_list
        self.assertEqual((call.args[1]["url"], call.kwargs["fresh"]), (self.TEAM_URL, True))

    def test_a_finished_game_does_nothing_without_the_rbihf_feature(self):
        self.store()
        game = self.make_event(kind=Event.EventKind.GAME, competition="RBIHF", external_game_id="816", external_source_id="4448")
        game.teams.add(self.team)

        with mock.patch("events.services.rbihf_standings.refresh_entry") as refresh:
            rbihf_standings.refresh_for_finished_game(game)

        refresh.assert_not_called()

    def test_the_nightly_task_refreshes_only_clubs_with_the_rbihf_feature(self):
        other = create_club("Rival Standings", self.owner)
        with schema_context(other.schema_name):
            Season.objects.create(start_date=self.season.start_date, end_date=self.season.end_date)
            other_team = Team.objects.create(name="Rival Team", short_name="RT")
            import_sources.record_source(other_team, source=import_sources.SOURCE_RBIHF, url=self.TEAM_URL, season=Season.objects.get())
        self.enable("competition_rbihf")
        self.store()
        old = Season.objects.create(start_date=self.season.start_date - timedelta(days=400), end_date=self.season.start_date - timedelta(days=1))
        self.store(Team.objects.create(name="Old Team", short_name="OT"), season=old)

        with mock.patch("events.services.rbihf_standings.refresh_entry", return_value=True) as refresh:
            result = refresh_standings()

        self.assertEqual([call.args[0] for call in refresh.call_args_list], [self.team])
        self.assertIn("refreshed standings for 1 competition source(s); 0 failed", result)
