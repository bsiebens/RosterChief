"""Live game pushes (events.services.game_push / game_texts) and following teams and games (events.services.follows)."""

from datetime import timedelta
from unittest import mock

from django.utils import timezone

from controlpanel.tests import make_user
from events.models import EventFollow, GameFollow, GameMoment, Location
from events.services import follows, game_push, game_timeline
from events.tests_timelines import GameTestBase, goal_item
from members.services.errors import Invalid
from notifications.models import InboxNotification, OutboxEntry
from teams.models import Team


class GamePushTests(GameTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.fan = make_user("fan@push.test", first="Fay", last="Fan")
        cls.game_fan = make_user("gamefan@push.test", first="Gil", last="Gamefan")
        cls.stranger = make_user("stranger@push.test", first="Stu", last="Stranger")

    def moment(self, game, key, kind=GameMoment.Kind.GOAL_FOR, seconds=60, notifiable=True, **payload):
        return GameMoment.objects.create(event=game, key=key, kind=kind, game_seconds=seconds, notifiable=notifiable, payload={"scorer": {"name": "Sam", "number": 9}, "score": {"for": 1, "against": 0}, **payload})

    def deliver(self, game, moments):
        with mock.patch("events.services.game_push.inbox.notify") as notify:
            reached = game_push.deliver_moments(game.pk, [moment.pk for moment in moments], club=self.club)
        return reached, notify

    def texts(self, game, moments):
        follows.follow_team(self.fan, self.team)
        _reached, notify = self.deliver(game, moments)
        return notify.call_args.kwargs["title"], notify.call_args.kwargs["body"]

    def test_followers_get_one_coalesced_inbox_push_and_others_none(self):
        game = self.make_game(is_live=True)
        follows.follow_team(self.fan, self.team)
        goal = self.moment(game, "goal_for:g1")
        penalty = self.moment(game, "penalty_against:p1", kind=GameMoment.Kind.PENALTY_AGAINST, seconds=90, player={"name": "Rex"}, description="Tripping")

        reached, notify = self.deliver(game, [penalty, goal])

        self.assertEqual(reached, 1)
        notify.assert_called_once()
        self.assertEqual(notify.call_args.args, (self.fan, self.club))
        kwargs = notify.call_args.kwargs
        self.assertEqual((kwargs["push"], kwargs["category"], kwargs["href"]), (True, "game", f"/calendar/events/{game.pk}"))
        self.assertEqual(kwargs["body"].splitlines(), ["GOAL First Team · Sam (#9)", "PENALTY Olympia Heist · Rex · Tripping"])
        self.assertEqual(kwargs["title"], "First Team 1–0 Olympia Heist")
        self.assertEqual(GameMoment.objects.filter(event=game, delivered_at__isnull=False).count(), 2)

    def test_the_real_inbox_gets_the_entry_and_a_push_is_queued(self):
        game = self.make_game(is_live=True)
        follows.follow_event(self.game_fan, game)
        goal = self.moment(game, "goal_for:g1")

        self.assertEqual(game_push.deliver_moments(game.pk, [goal.pk], club=self.club), 1)

        entry = InboxNotification.objects.get(user=self.game_fan)
        self.assertEqual((entry.title, entry.body), ("First Team 1–0 Olympia Heist", "GOAL First Team · Sam (#9)"))
        self.assertTrue(OutboxEntry.objects.filter(payload__user_id=str(self.game_fan.pk), payload__category="game").exists())

    def test_the_push_names_the_assists_on_our_goals_only(self):
        game = self.make_game(is_live=True)
        ours = self.moment(game, "goal_for:g1", assists=[{"name": "Ann", "number": 7}], seconds=60)
        theirs = self.moment(game, "goal_against:g2", kind=GameMoment.Kind.GOAL_AGAINST, assists=[{"name": "Bo", "number": 3}], seconds=120)
        _title, body = self.texts(game, [ours, theirs])
        self.assertEqual(body.splitlines(), ["GOAL First Team · Sam (#9) · assist Ann (#7)", "GOAL Olympia Heist · Sam (#9)"])

    def test_an_away_game_leads_with_the_opponent_and_flips_the_score(self):
        away = Location.objects.create(name="Away Ground", address="2 St", city="Town", zip_code="1000")
        game = self.make_game(is_live=True, location=away)
        title, _body = self.texts(game, [self.moment(game, "goal_for:g1", score={"for": 3, "against": 1})])
        self.assertEqual(title, "Olympia Heist 1–3 First Team")

    def test_the_final_names_the_winner(self):
        game = self.make_game(is_live=True)
        for score, expected in (({"for": 4, "against": 2}, "FINAL · First Team win"), ({"for": 2, "against": 4}, "FINAL · Olympia Heist win"), ({"for": 3, "against": 3}, "FINAL · Draw")):
            GameMoment.objects.filter(event=game).delete()
            final = GameMoment.objects.create(event=game, key="final", kind=GameMoment.Kind.GAME_FINAL, game_seconds=3600, payload={"score": score})
            title, body = self.texts(game, [final])
            self.assertEqual((title, body), (f"First Team {score['for']}–{score['against']} Olympia Heist", expected))

    def test_a_penalty_shows_the_current_score_and_the_clock(self):
        game = self.make_game(is_live=True)
        self.moment(game, "goal_for:earlier", seconds=30, score={"for": 2, "against": 1})
        payload = {"player": {"name": "Brais Stoffels", "number": 2}, "description": "Roughing", "minutes": 2, "period": "1", "time": "15:00"}
        penalty = GameMoment.objects.create(event=game, key="p1", kind=GameMoment.Kind.PENALTY_AGAINST, game_seconds=900, payload=payload)
        self.assertEqual(self.texts(game, [penalty]), ("First Team 2–1 Olympia Heist", "PENALTY Olympia Heist · Brais Stoffels (#2) · Roughing · 2 min · P1 15:00"))

    def test_a_game_start_reads_as_a_goalless_scoreboard(self):
        game = self.make_game(is_live=True)
        started = GameMoment.objects.create(event=game, key="start", kind=GameMoment.Kind.GAME_STARTED, game_seconds=0, payload={})
        self.assertEqual(self.texts(game, [started]), ("First Team 0–0 Olympia Heist", "GAME STARTED"))

    def test_catch_ups_are_never_pushed_and_a_moment_is_pushed_once(self):
        game = self.make_game(is_live=True)
        follows.follow_team(self.fan, self.team)
        late = self.moment(game, "goal_for:old", notifiable=False, scorer={"name": "Old", "number": 1})
        fresh = self.moment(game, "goal_for:new")

        _reached, notify = self.deliver(game, [late, fresh])
        self.assertEqual(notify.call_count, 1)
        self.assertNotIn("Old", notify.call_args.kwargs["body"])

        reached_again, notify_again = self.deliver(game, [fresh])
        self.assertEqual(reached_again, 0)
        notify_again.assert_not_called()

    def test_moments_are_marked_delivered_even_without_followers(self):
        game = self.make_game(is_live=True)
        goal = self.moment(game, "goal_for:g1")
        reached, notify = self.deliver(game, [goal])
        self.assertEqual(reached, 0)
        notify.assert_not_called()
        goal.refresh_from_db()
        self.assertIsNotNone(goal.delivered_at)

    def test_team_and_game_followers_are_each_reached_once(self):
        game = self.make_game(is_live=True)
        both = make_user("both@push.test", first="Bo", last="Both")
        follows.follow_team(self.fan, self.team)
        follows.follow_event(self.game_fan, game)
        follows.follow_team(both, self.team)
        follows.follow_event(both, game)
        self.assertEqual({user.email for user in follows.followers(game)}, {"fan@push.test", "gamefan@push.test", "both@push.test"})
        reached, _notify = self.deliver(game, [self.moment(game, "goal_for:g1")])
        self.assertEqual(reached, 3)

    def test_there_is_no_cap_per_game(self):
        game = self.make_game(is_live=True)
        follows.follow_team(self.fan, self.team)
        for number in range(15):
            reached, _notify = self.deliver(game, [self.moment(game, f"goal_for:g{number}")])
            self.assertEqual(reached, 1)

    def test_a_changed_timeline_pushes_its_moments_after_commit(self):
        game = self.make_game(is_live=True)
        follows.follow_team(self.fan, self.team)
        game_timeline.store(game, game_timeline.TimelineFetch(document={"events": []}))  # first fetch: silent

        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            game_timeline.store(game, game_timeline.TimelineFetch(document={"events": [goal_item("g1", "home", 60, 1, 0)]}))

        self.assertTrue(callbacks)
        [moment] = GameMoment.objects.filter(event=game)
        self.assertEqual(moment.kind, GameMoment.Kind.GOAL_FOR)
        self.assertIsNotNone(moment.delivered_at)
        self.assertEqual(InboxNotification.objects.get(user=self.fan).body, "GOAL First Team · Sam (#9) · P1 01:00")

    def test_a_failing_delivery_never_breaks_the_timeline_update(self):
        game = self.make_game(is_live=True)
        game_timeline.store(game, game_timeline.TimelineFetch(document={"events": []}))
        with mock.patch("events.services.game_push.deliver_moments", side_effect=RuntimeError("down")), self.captureOnCommitCallbacks(execute=True):
            _timeline, changed = game_timeline.store(game, game_timeline.TimelineFetch(document={"events": [goal_item("g1", "home", 60, 1, 0)]}))
        self.assertTrue(changed)
        self.assertIsNone(GameMoment.objects.get(event=game).delivered_at)


class FollowTests(GameTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.fan = make_user("fan@follow.test", first="Fay", last="Fan")

    def test_following_teams_is_idempotent_and_set_replaces_the_set(self):
        second = Team.objects.create(name="Second", short_name="2nd")
        follows.follow_team(self.fan, self.team)
        follows.follow_team(self.fan, self.team)
        self.assertEqual(GameFollow.objects.filter(user=self.fan).count(), 1)
        self.assertEqual(follows.followed_teams(self.fan), [self.team])

        import uuid

        self.assertEqual(follows.set_followed_teams(self.fan, [second.pk, uuid.uuid4()]), {second.pk})
        self.assertEqual(follows.followed_team_ids(self.fan), {second.pk})
        follows.unfollow_team(self.fan, second)
        follows.unfollow_team(self.fan, second)
        self.assertEqual(follows.followed_team_ids(self.fan), set())

    def test_following_a_game_and_its_state(self):
        game = self.make_game()
        self.assertEqual(follows.follow_state(self.fan, game), {"directly": False, "via_team": False})
        follows.follow_event(self.fan, game)
        follows.follow_event(self.fan, game)
        follows.follow_team(self.fan, self.team)
        self.assertEqual(EventFollow.objects.filter(user=self.fan).count(), 1)
        self.assertEqual(follows.followed_event_ids(self.fan), {game.pk})
        self.assertEqual(follows.follow_state(self.fan, game), {"directly": True, "via_team": True})
        follows.unfollow_event(self.fan, game)
        self.assertEqual(follows.follow_state(self.fan, game), {"directly": False, "via_team": True})

    def test_only_games_can_be_followed(self):
        training = self.make_game(kind="training")
        with self.assertRaises(Invalid):
            follows.follow_event(self.fan, training)

    def test_followed_games_lists_live_then_upcoming_then_just_finished(self):
        now = timezone.now()
        finished = self.make_game(external_game_id="1", start=now - timedelta(hours=3))
        live = self.make_game(external_game_id="2", start=now - timedelta(hours=1), is_live=True)
        soon = self.make_game(external_game_id="3", start=now + timedelta(hours=2))
        self.make_game(external_game_id="4", start=now + timedelta(days=3))  # too far ahead
        self.make_game(external_game_id="5", start=now - timedelta(days=1))  # too long ago
        other_team = Team.objects.create(name="Other", short_name="O")
        direct = self.make_game(external_game_id="6", start=now + timedelta(hours=5))
        direct.teams.set([other_team])
        self.assertEqual(follows.followed_games(self.fan), [])

        follows.follow_team(self.fan, self.team)
        follows.follow_event(self.fan, direct)
        follows.follow_event(self.fan, soon)
        rows = follows.followed_games(self.fan, now=now)

        self.assertEqual([row["event"] for row in rows], [live, soon, direct, finished])
        self.assertEqual([row["via"] for row in rows], [["team"], ["game", "team"], ["game"], ["team"]])
        self.assertEqual(len(follows.followed_games(self.fan, now=now, limit=2)), 2)
