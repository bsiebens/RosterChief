"""Game statistics (events.services.game_stats): flattening timelines into GameStat rows, roster matching, the team
analytics sums, a member's own numbers and the manual strength corrections."""

from datetime import timedelta

from django.utils import timezone
from django_tenants.utils import schema_context

from club.models import Sport
from club.services import grant_login
from events.models import Event, GameStat, GameTimeline
from events.services.game_stats import (
    analytics_supported,
    clear_strength_override,
    ensure_game_stats,
    member_stats,
    played_games,
    set_strength_override,
    sync_game_stats,
    team_analytics,
    team_leaders,
)
from events.tests_timelines import GameTestBase
from members.models import AuditEntry, Member
from members.services.errors import Invalid, NotAllowed
from teams.models import Position, StaffAssignment, Team, TeamMembership

# Our side is "away" (no location).
DOCUMENT = {
    "events": [
        {"id": "g1", "type": "goal", "period": "1", "seconds": 60, "side": "away", "goal": {"scorer": {"name": "Sam Scorer", "number": 78}, "assists": [{"name": "Al Assist", "number": 9}], "strength": "PP1", "game_winning": True}},
        {"id": "g2", "type": "goal", "period": "2", "seconds": 90, "side": "home", "goal": {"scorer": {"name": "Them Scorer", "number": 5}, "assists": []}},
        {"id": "p1", "type": "penalty", "period": "2", "seconds": 200, "side": "away", "penalty": {"player": {"name": "Pat Penalty", "number": 3}, "minutes": 4}},
        {"id": "p2", "type": "penalty", "period": "3", "seconds": 300, "side": "home", "penalty": {"player": {"name": "Other", "number": 8}, "minutes": 2}},
    ]
}

# The opponent's minor runs from 1:40 to 3:40, so our goal at 2:10 is a power-play goal recorded as even strength.
FLAGGED = {
    "events": [
        {"id": "p1", "type": "penalty", "period": "1", "seconds": 100, "time": "01:40", "side": "home", "penalty": {"player": {"name": "Them Penalty", "number": 4}, "kind": "minor", "minutes": 2}},
        {"id": "g1", "type": "goal", "period": "1", "seconds": 130, "time": "02:10", "side": "away", "goal": {"scorer": {"name": "Sam Scorer", "number": 78}, "assists": [], "strength": "EQ"}},
    ]
}


class GameStatsTests(GameTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        with schema_context(cls.club.schema_name):
            cls.other_team = Team.objects.create(name="Second", short_name="2nd")
            cls.scorer = Member.objects.create(first_name="Sam", last_name="Scorer")
            cls.renamed = Member.objects.create(first_name="Kowalski", last_name="Jan")
            TeamMembership.objects.create(team=cls.team, member=cls.scorer, season=cls.season, jersey_number=78)
            TeamMembership.objects.create(team=cls.team, member=cls.renamed, season=cls.season, jersey_number=12)
            cls.coach_position = Position.objects.create(name="Coach", short_name="C", staff_position=True, management_position=True)
            cls.coach = Member.objects.create(first_name="Cy", last_name="Coach", email="cy@stats.test")
            cls.other_coach = Member.objects.create(first_name="Oz", last_name="Other", email="oz@stats.test")
            StaffAssignment.objects.create(team=cls.team, member=cls.coach, season=cls.season, position=cls.coach_position)
            StaffAssignment.objects.create(team=cls.other_team, member=cls.other_coach, season=cls.season, position=cls.coach_position)
        cls.coach_user, _ = grant_login(cls.club, cls.coach)
        cls.other_user, _ = grant_login(cls.club, cls.other_coach)

    def setUp(self):
        super().setUp()
        self.game = self.make_game(location=None, start=timezone.now() - timedelta(days=2), score_for=3, score_against=1)

    def test_sync_flattens_the_timeline_and_matches_the_roster(self):
        sync_game_stats(self.game, DOCUMENT)

        goal = GameStat.objects.get(event=self.game, stat_type=GameStat.StatType.GOAL, is_ours=True)
        self.assertEqual((goal.member, goal.season, goal.meta["strength"]), (self.scorer, self.season, "PP1"))
        self.assertEqual(GameStat.objects.filter(stat_type=GameStat.StatType.ASSIST).count(), 1)
        self.assertEqual(GameStat.objects.get(stat_type=GameStat.StatType.PENALTY_MINUTES, is_ours=False).value, 2)
        self.assertIsNone(GameStat.objects.get(stat_type=GameStat.StatType.GOAL, is_ours=False).member)  # theirs is never matched

    def test_names_match_in_any_order_and_a_contradicting_number_does_not(self):
        document = {
            "events": [
                {"id": "a", "type": "goal", "period": "1", "seconds": 60, "side": "away", "goal": {"scorer": {"name": "Jan Kowalski", "number": 99}, "assists": [{"name": "Somebody Else", "number": 78}]}},
            ]
        }
        sync_game_stats(self.game, document)
        self.assertEqual(GameStat.objects.get(stat_type=GameStat.StatType.GOAL).member, self.renamed)  # the name, despite the number
        self.assertIsNone(GameStat.objects.get(stat_type=GameStat.StatType.ASSIST).member)  # #78 is Sam, but the name says otherwise

    def test_resync_replaces_rows_and_ensure_repairs_only_missing_or_stale_ones(self):
        sync_game_stats(self.game, DOCUMENT)
        sync_game_stats(self.game, DOCUMENT)
        self.assertEqual(GameStat.objects.filter(event=self.game).count(), 7)

        self.assertFalse(ensure_game_stats(self.game, DOCUMENT))
        GameStat.objects.filter(event=self.game, stat_type=GameStat.StatType.ASSIST).delete()
        self.assertTrue(ensure_game_stats(self.game, DOCUMENT))
        self.assertEqual(GameStat.objects.filter(event=self.game).count(), 7)

    def test_a_new_roster_spot_queues_a_relink_that_picks_up_the_player(self):
        from unittest import mock

        from events.models import GameTimeline, StatsRefresh
        from events.services import stats_refresh

        # The game was stored before the player joined: the goal is unmatched.
        newcomer = Member.objects.create(first_name="Nina", last_name="Newcomer", email="nina@stats.test")
        document = {"events": [{"id": "g9", "type": "goal", "period": "1", "seconds": 60, "side": "home" if self.game.is_home_game else "away", "goal": {"scorer": {"name": "Nina Newcomer", "number": 55}, "assists": []}}]}
        GameTimeline.objects.create(event=self.game, source="RBIHF", data=document, data_hash="x", status="final")
        sync_game_stats(self.game, document)
        self.assertIsNone(GameStat.objects.get(stat_type=GameStat.StatType.GOAL, is_ours=True).member)

        with mock.patch("events.tasks.refresh_member_stats.delay") as delay, self.captureOnCommitCallbacks(execute=True):
            membership = TeamMembership.objects.create(team=self.team, member=newcomer, season=self.season, jersey_number=55)
        job = StatsRefresh.objects.get(member=newcomer)
        delay.assert_called_once_with(str(job.pk))
        self.assertTrue(stats_refresh.in_progress(newcomer))

        stats_refresh.run(job)
        job.refresh_from_db()
        self.assertEqual((job.status, job.updated), ("done", 1))
        self.assertFalse(stats_refresh.in_progress(newcomer))
        self.assertEqual(GameStat.objects.get(stat_type=GameStat.StatType.GOAL, is_ours=True).member, newcomer)
        self.assertIsNotNone(membership.pk)

    def test_lineups_count_as_games_played_and_parse_from_the_game_page(self):
        from events.services.timeline_parsers import rbihf_extract_lineups

        page = (
            '<div id="game-home"><table><tr><th>Pos</th><th>Nr</th><th>Lic</th><th>Name</th></tr>'
            '<tr><td class="pos">DF</td><td class="nr">78</td><td class="lic">111</td><td class="name">Scorer Sam</td></tr></table></div>'
            '<div id="game-visit"><table><tr><td class="pos">GO</td><td class="nr">1</td><td class="lic">9</td><td class="name">Keeper Kim</td></tr></table></div>'
        )
        lineups = rbihf_extract_lineups(page)
        self.assertEqual(lineups["home"], [{"pos": "DF", "number": 78, "license": "111", "name": "Scorer Sam"}])
        self.assertEqual(lineups["away"][0]["pos"], "GO")

        side = "home" if self.game.is_home_game else "away"
        document = {"events": [], "lineups": {side: [{"pos": "DF", "number": 78, "license": "111", "name": "Sam Scorer"}]}}
        sync_game_stats(self.game, document)
        row = GameStat.objects.get(event=self.game, stat_type=GameStat.StatType.APPEARANCE)
        self.assertEqual((row.member, row.meta["pos"]), (self.scorer, "DF"))
        self.assertEqual(member_stats(self.scorer)["games"], 1)  # played without a single recorded stat

    def test_analytics_sums_goals_penalties_and_leaders(self):
        sync_game_stats(self.game, DOCUMENT)

        analytics = team_analytics(self.team, self.season)

        self.assertEqual((analytics["games"], analytics["record"], analytics["points_table"]), (1, {"won": 1, "drawn": 0, "lost": 0}, 3))
        self.assertEqual((analytics["goals_for"], analytics["goals_against"]), (3, 1))
        self.assertEqual((analytics["pim_for"], analytics["pim_against"]), (4, 2))
        self.assertEqual((analytics["pp_goals_for"], analytics["game_winning_goals"]), (1, 1))
        self.assertEqual(analytics["top_scorers"][0]["name"], "Sam Scorer")
        self.assertEqual(analytics["top_scorers"][0]["member_id"], self.scorer.pk)
        self.assertEqual(analytics["top_offenders"][0]["name"], "Pat Penalty")
        self.assertEqual([row["result"] for row in analytics["form"]], ["W"])
        self.assertEqual(analytics["split"]["away"]["games"], 1)

    def test_games_filters_scope_friendlies_and_competition(self):
        Event.objects.filter(pk=self.game.pk).update(is_friendly=True, competition_label="Cup")
        live = self.make_game(external_game_id="x", score_for=1, score_against=0, is_live=True)  # not finished
        self.assertNotIn(live, played_games(self.team, self.season))
        self.assertEqual(team_analytics(self.team, self.season, friendlies=False)["games"], 0)
        self.assertEqual(team_analytics(self.team, self.season, competition="Cup")["competitions"], ["Cup"])
        self.assertEqual(team_analytics(self.team, self.season, scope="home")["games"], 0)
        self.assertEqual(team_analytics(self.team, self.season, scope="away")["games"], 1)

    def test_goalie_time_in_net_comes_from_the_goalie_changes(self):
        starter, backup = {"name": "Gus Goalie", "number": 1}, {"name": "Bo Backup", "number": 30}
        document = {
            "events": [
                {"id": "gc1", "type": "goalie_change", "period": "1", "seconds": 0, "side": "away", "goalie_change": {"player": starter, "action": "in"}},
                {"id": "g2", "type": "goal", "period": "1", "seconds": 600, "side": "home", "goal": {"scorer": {"name": "Them Scorer", "number": 5}, "assists": []}},
                {"id": "gc2", "type": "goalie_change", "period": "2", "seconds": 1800, "side": "away", "goalie_change": {"player": starter, "action": "out"}},
                {"id": "gc3", "type": "goalie_change", "period": "2", "seconds": 1800, "side": "away", "goalie_change": {"player": backup, "action": "in"}},
                {"id": "pe3", "type": "period_end", "period": "3", "seconds": 3600, "side": "home"},
            ]
        }
        sync_game_stats(self.game, document)

        goalies = {row["name"]: row for row in team_analytics(self.team, self.season)["goalies"]}

        self.assertEqual((goalies["Gus Goalie"]["minutes"], goalies["Gus Goalie"]["total"], goalies["Gus Goalie"]["gaa"]), (30, 1, 2.0))
        self.assertEqual((goalies["Bo Backup"]["minutes"], goalies["Bo Backup"]["total"], goalies["Bo Backup"]["gaa"]), (30, 0, 0.0))

    def test_average_penalty_minutes_is_also_given_without_game_misconducts(self):
        penalties = [
            {"id": "m1", "type": "penalty", "period": "1", "seconds": 100, "side": "away", "penalty": {"player": {"name": "Pat Penalty", "number": 3}, "kind": "minor", "minutes": 2}},
            {"id": "m2", "type": "penalty", "period": "2", "seconds": 1500, "side": "away", "penalty": {"player": {"name": "Pat Penalty", "number": 3}, "kind": "game_misconduct", "minutes": 20}},
        ]
        sync_game_stats(self.game, {"events": penalties})
        analytics = team_analytics(self.team, self.season)
        self.assertEqual((analytics["pim_for"], analytics["avg_pim"], analytics["game_misconduct_minutes"], analytics["avg_pim_excl_game_misconducts"]), (22, 22.0, 20, 2.0))

        sync_game_stats(self.game, DOCUMENT)
        analytics = team_analytics(self.team, self.season)
        self.assertEqual((analytics["game_misconduct_minutes"], analytics["avg_pim_excl_game_misconducts"]), (0, None))

    def test_extra_metrics_from_shots_power_plays_goalies_and_minutes(self):
        document = {
            "events": [
                {"id": "gi", "type": "goalie_change", "period": "1", "seconds": 0, "side": "away", "goalie_change": {"player": {"name": "Gus Goalie", "number": 1}, "action": "in"}},
                {"id": "p2", "type": "penalty", "period": "1", "seconds": 100, "side": "away", "penalty": {"player": {"name": "Pat Penalty", "number": 3}, "minutes": 2, "kind": "minor"}},
                {"id": "g2", "type": "goal", "period": "1", "seconds": 130, "side": "home", "goal": {"scorer": {"name": "Them Scorer", "number": 5}, "assists": [], "strength": "PP1"}},
                {"id": "g3", "type": "goal", "period": "3", "seconds": 3000, "side": "away", "goal": {"scorer": {"name": "Sam Scorer", "number": 78}, "assists": []}},
            ],
            "team_stats": {"home": {"shots_on_goal": 10, "pp_opportunities": 1}, "away": {"shots_on_goal": 30}},
        }
        sync_game_stats(self.game, document)

        analytics = team_analytics(self.team, self.season)

        self.assertEqual((analytics["shots_for"], analytics["shots_against"]), (30, 10))
        self.assertEqual(analytics["save_pct"], 90.0)
        self.assertEqual((analytics["pk_opportunities"], analytics["pk_pct"], analytics["pp_opportunities"]), (1, 0.0, 0))
        self.assertEqual(analytics["goalies"][0]["name"], "Gus Goalie")
        self.assertEqual((analytics["goal_minutes"][4]["for"], analytics["goal_minutes"][10]["for"]), (0, 1))
        self.assertEqual(analytics["opponents"][0]["games"], 1)
        self.assertEqual(analytics["game_rows"][0]["pim_for"], 2)
        self.assertIsNotNone(analytics["trend"])

    def test_member_stats_and_team_leaders(self):
        sync_game_stats(self.game, DOCUMENT)
        second = self.make_game(external_game_id="2", location=None, start=timezone.now() - timedelta(days=1), score_for=1, score_against=0)
        sync_game_stats(second, {"events": [{"id": "x", "type": "goal", "period": "1", "seconds": 10, "side": "away", "goal": {"scorer": {"name": "Sam Scorer", "number": 78}, "assists": []}}]})

        stats = member_stats(self.scorer, self.season)

        self.assertEqual((stats["games"], stats["goals"], stats["assists"], stats["points"], stats["pp_goals"], stats["game_winning_goals"], stats["goalie"]), (2, 2, 0, 2, 1, 1, None))
        [row] = stats["teams"]
        self.assertEqual((row["team"], row["season"], row["goals"]), (self.team, self.season, 2))
        self.assertEqual(member_stats(self.scorer)["goals"], 2)
        leaders = team_leaders(self.team, self.season)
        self.assertEqual((leaders["top_scorers"][0]["name"], leaders["top_scorers"][0]["total"]), ("Sam Scorer", 2))

    def test_analytics_need_a_hockey_club_with_the_analytics_feature(self):
        from features.entitlements import Entitlement, Entitlements

        on, off = Entitlements({"analytics": Entitlement(True)}), Entitlements({})
        self.assertFalse(analytics_supported(None))
        self.club.sport = None
        self.assertFalse(analytics_supported(self.club, on))
        self.club.sport = Sport(name="Ice Hockey", slug="ice-hockey")
        self.assertTrue(analytics_supported(self.club, on))
        self.assertFalse(analytics_supported(self.club, off))  # the right sport, but the feature is not on
        self.assertTrue(analytics_supported(self.club, Entitlements(bypass=True)))  # the platform owner's bypass
        self.club.sport = Sport(name="Football", slug="football")
        self.assertFalse(analytics_supported(self.club, on))

    # -- strength corrections -----------------------------------------------------------------------------------------

    def flagged_game(self):
        GameTimeline.objects.create(event=self.game, source="test", data=FLAGGED, status=GameTimeline.Status.FINAL)
        sync_game_stats(self.game, FLAGGED)

    def test_a_goal_that_conflicts_with_the_penalties_is_flagged(self):
        self.flagged_game()
        analytics = team_analytics(self.team, self.season)
        [warning] = analytics["strength_warnings"]
        self.assertEqual((warning["goal"], warning["recorded"], warning["expected"], warning["ours"], warning["time"]), ("g1", "EQ", "PP1", True, "02:10"))
        self.assertEqual(analytics["pp_goals_for"], 0)

    def test_fixing_the_strength_counts_the_goal_as_power_play_and_clears_the_warning(self):
        self.flagged_game()

        set_strength_override(self.coach_user, self.game, "g1", "PP1")

        analytics = team_analytics(self.team, self.season)
        self.assertEqual((analytics["pp_goals_for"], analytics["strength_warnings"]), (1, []))
        self.assertEqual(GameTimeline.objects.get(event=self.game).strength_overrides, {"g1": "PP1"})
        goal = GameStat.objects.get(event=self.game, stat_type=GameStat.StatType.GOAL, source_key="g1")
        self.assertEqual((goal.meta["strength"], goal.meta["strength_source"]), ("PP1", "EQ"))
        self.assertTrue(AuditEntry.objects.filter(action="game.strength_override").exists())
        # survives a rebuild, and can be undone
        self.assertFalse(ensure_game_stats(self.game, FLAGGED))
        sync_game_stats(self.game, FLAGGED)
        self.assertEqual(team_analytics(self.team, self.season)["pp_goals_for"], 1)
        clear_strength_override(self.owner, self.game, "g1")
        self.assertEqual(len(team_analytics(self.team, self.season)["strength_warnings"]), 1)

    def test_keeping_the_recorded_strength_clears_the_warning_without_changing_the_count(self):
        self.flagged_game()
        set_strength_override(self.owner, self.game, "g1", "EQ")
        analytics = team_analytics(self.team, self.season)
        self.assertEqual((analytics["pp_goals_for"], analytics["strength_warnings"]), (0, []))

    def test_fixing_rejects_unknown_goals_strengths_and_other_teams_coaches(self):
        self.flagged_game()
        for goal, strength in (("nope", "PP1"), ("p1", "PP1"), ("g1", "DP")):
            with self.subTest(goal=goal, strength=strength), self.assertRaises(Invalid):
                set_strength_override(self.owner, self.game, goal, strength)
        with self.assertRaises(NotAllowed):
            set_strength_override(self.other_user, self.game, "g1", "PP1")
        no_timeline = self.make_game(external_game_id="nt")
        with self.assertRaises(Invalid):
            set_strength_override(self.owner, no_timeline, "g1", "PP1")
        self.assertEqual(GameTimeline.objects.get(event=self.game).strength_overrides, {})
