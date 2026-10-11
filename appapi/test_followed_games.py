"""Following games: one game directly (`PUT /events/{id}/follow`) or all of a team's games
(`PUT /game-follows`), and the Home cards for the games an account follows."""

import datetime

from django.core.cache import cache
from django.utils import timezone
from waffle import get_waffle_flag_model

from events.models import Event, EventFollow, GameFollow, GameTimeline, Location, Opponent
from events.services import game_push
from teams.models import Team

from .test_member import ClubApiTestCase


class FollowedGamesTestCase(ClubApiTestCase):
    def setUp(self):
        super().setUp()
        get_waffle_flag_model().objects.get_or_create(name="game_timeline")[0].clubs.add(self.club)
        cache.clear()
        self.team = Team.objects.create(club=self.club, name="Sharks Seniors", short_name="SEN")
        self.other_team = Team.objects.create(club=self.club, name="Sharks U12", short_name="U12")
        self.home_ground = Location.objects.create(club=self.club, name="Home Ice", address="1 St", city="Town", zip_code="1000", country="BE", is_home=True)
        self.rivals = Opponent.objects.create(club=self.club, name="Rivals HC")
        self.now = timezone.now()

    def game(self, team=None, *, hours=2, title="Game", **kwargs):
        kwargs.setdefault("opponent", self.rivals)
        kwargs.setdefault("location", self.home_ground)
        game = Event.objects.create(club=self.club, title=title, kind=Event.EventKind.GAME, start=self.now + datetime.timedelta(hours=hours), season=self.season, **kwargs)
        game.teams.add(team or self.team)
        return game

    def timeline(self, game, status="live", **document):
        data = {"version": 1, "status": status, **document}
        return GameTimeline.objects.create(event=game, source="RBIHF", status=status, data=data)

    def cards(self):
        return self.get("/home").json()["followed_games"]


class FollowEndpointTests(FollowedGamesTestCase):
    def test_following_a_game_is_idempotent_and_reported(self):
        game = self.game()

        first = self.send("put", f"/events/{game.pk}/follow")
        again = self.send("put", f"/events/{game.pk}/follow")

        self.assertEqual(first.json(), {"directly": True, "via_team": False})
        self.assertEqual(again.json(), {"directly": True, "via_team": False})
        self.assertEqual(EventFollow.objects.filter(user=self.user, event=game).count(), 1)

    def test_unfollowing_is_idempotent(self):
        game = self.game()
        self.send("put", f"/events/{game.pk}/follow")

        self.assertEqual(self.send("delete", f"/events/{game.pk}/follow").json(), {"directly": False, "via_team": False})
        self.assertEqual(self.send("delete", f"/events/{game.pk}/follow").status_code, 200)
        self.assertFalse(EventFollow.objects.exists())

    def test_unfollowing_a_game_of_a_followed_team_says_it_is_still_followed_through_the_team(self):
        game = self.game()
        GameFollow.objects.create(club=self.club, user=self.user, team=self.team)
        self.send("put", f"/events/{game.pk}/follow")

        self.assertEqual(self.send("delete", f"/events/{game.pk}/follow").json(), {"directly": False, "via_team": True})

    def test_only_games_can_be_followed(self):
        training = Event.objects.create(club=self.club, title="Practice", kind=Event.EventKind.TRAINING, start=self.now + datetime.timedelta(days=1))

        self.assertError(self.send("put", f"/events/{training.pk}/follow"), 422, "not_a_game")

    def test_it_needs_game_timelines(self):
        get_waffle_flag_model().objects.get(name="game_timeline").clubs.remove(self.club)
        cache.clear()
        game = self.game()

        self.assertError(self.send("put", f"/events/{game.pk}/follow"), 404, "feature_disabled")

    def test_another_clubs_game_is_not_found(self):
        from club.models import Club

        other = Club.objects.create(name="Rival FC", slug="rival-fc")
        foreign = Event.objects.create(club=other, title="Away", kind=Event.EventKind.GAME, start=self.now + datetime.timedelta(hours=3))

        self.assertError(self.send("put", f"/events/{foreign.pk}/follow"), 404, "not_found")

    def test_event_detail_says_how_the_game_is_followed(self):
        game = self.game()
        self.assertEqual(self.get(f"/events/{game.pk}").json()["follow"], {"directly": False, "via_team": False})

        self.send("put", f"/events/{game.pk}/follow")
        GameFollow.objects.create(club=self.club, user=self.user, team=self.team)

        self.assertEqual(self.get(f"/events/{game.pk}").json()["follow"], {"directly": True, "via_team": True})

    def test_game_follows_lists_the_directly_followed_games(self):
        coming = self.game(hours=5, title="Coming")
        self.game(hours=6, title="Not followed")
        long_ago = self.game(hours=-48, title="Long ago")
        EventFollow.objects.create(club=self.club, user=self.user, event=coming)
        EventFollow.objects.create(club=self.club, user=self.user, event=long_ago)

        body = self.get("/game-follows").json()

        self.assertEqual([game["title"] for game in body["games"]], ["Coming"])
        self.assertEqual(sorted(team["name"] for team in body["teams"]), ["Sharks Seniors", "Sharks U12"])


class HomeFollowedGamesTests(FollowedGamesTestCase):
    def test_nothing_followed_means_no_cards(self):
        self.game()

        self.assertEqual(self.cards(), [])

    def test_a_game_followed_directly_gets_a_card_with_both_teams_the_location_and_the_score(self):
        game = self.game(hours=3, score_for=None)
        self.send("put", f"/events/{game.pk}/follow")

        (card,) = self.cards()

        self.assertEqual(card["via"], ["game"])
        self.assertEqual(card["status"], "scheduled")
        self.assertEqual(card["event"]["location"]["name"], "Home Ice")
        self.assertEqual((card["home"], card["away"]), ({"name": "Sharks Seniors", "score": None}, {"name": "Rivals HC", "score": None}))
        self.assertEqual((card["period"], card["clock"], card["decided_in"]), (None, None, None))

    def test_all_games_of_a_followed_team_get_cards(self):
        GameFollow.objects.create(club=self.club, user=self.user, team=self.team)
        mine = self.game(self.team, hours=4, title="Seniors game")
        self.game(self.other_team, hours=4, title="U12 game")

        (card,) = self.cards()

        self.assertEqual((card["event"]["id"], card["via"]), (str(mine.pk), ["team"]))

    def test_a_game_followed_both_ways_shows_once(self):
        GameFollow.objects.create(club=self.club, user=self.user, team=self.team)
        game = self.game(hours=4)
        self.send("put", f"/events/{game.pk}/follow")

        (card,) = self.cards()

        self.assertEqual(card["via"], ["game", "team"])

    def test_a_live_game_carries_the_score_period_and_clock_from_the_timeline(self):
        game = self.game(hours=-1, is_live=True)
        self.timeline(game, status="live", period="3", clock="04:31", score={"home": 5, "away": 2})
        self.send("put", f"/events/{game.pk}/follow")

        (card,) = self.cards()

        self.assertEqual(card["status"], "live")
        self.assertEqual((card["home"]["score"], card["away"]["score"]), (5, 2))
        self.assertEqual((card["period"], card["clock"]), ("3", "04:31"))

    def test_away_games_put_the_opponent_first(self):
        away = Location.objects.create(club=self.club, name="Their Rink", address="2 St", city="City", zip_code="2000", country="BE")
        game = self.game(hours=-1, is_live=True, location=away)
        self.timeline(game, period="1", clock="10:00", score={"home": 1, "away": 3})
        self.send("put", f"/events/{game.pk}/follow")

        (card,) = self.cards()

        self.assertEqual((card["home"], card["away"]), ({"name": "Rivals HC", "score": 1}, {"name": "Sharks Seniors", "score": 3}))

    def test_without_a_timeline_the_events_own_score_is_used_oriented_to_home_and_away(self):
        game = self.game(hours=-3, score_for=4, score_against=1)
        self.send("put", f"/events/{game.pk}/follow")

        (card,) = self.cards()

        self.assertEqual((card["home"]["score"], card["away"]["score"]), (4, 1))
        self.assertEqual(card["status"], "finished")

    def test_a_finished_game_says_how_it_was_decided_and_has_no_clock(self):
        game = self.game(hours=-3, score_for=3, score_against=2)
        self.timeline(game, status="final", period="3", clock="00:00", score={"home": 3, "away": 2}, decided_in="overtime")
        self.send("put", f"/events/{game.pk}/follow")

        (card,) = self.cards()

        self.assertEqual((card["status"], card["decided_in"], card["period"], card["clock"]), ("finished", "overtime", None, None))

    def test_cards_are_ordered_live_then_upcoming_then_just_finished(self):
        finished = self.game(hours=-4, title="Finished", score_for=1, score_against=0)
        soon = self.game(hours=2, title="Soon")
        later = self.game(hours=9, title="Later")
        live = self.game(hours=-1, title="Live", is_live=True)
        for game in (finished, soon, later, live):
            self.send("put", f"/events/{game.pk}/follow")

        self.assertEqual([card["event"]["title"] for card in self.cards()], ["Live", "Soon", "Later", "Finished"])

    def test_games_outside_the_window_and_cancelled_ones_get_no_card(self):
        for hours, title, extra in ((72, "Too far ahead", {}), (-30, "Long over", {}), (3, "Cancelled", {"cancelled": True})):
            self.send("put", f"/events/{self.game(hours=hours, title=title, **extra).pk}/follow")

        self.assertEqual(self.cards(), [])

    def test_at_most_five_cards(self):
        for number in range(7):
            self.send("put", f"/events/{self.game(hours=number + 1, title=f'Game {number}').pk}/follow")

        self.assertEqual([card["event"]["title"] for card in self.cards()], [f"Game {number}" for number in range(5)])

    def test_without_game_timelines_home_has_no_followed_games(self):
        game = self.game()
        self.send("put", f"/events/{game.pk}/follow")
        get_waffle_flag_model().objects.get(name="game_timeline").clubs.remove(self.club)
        cache.clear()

        self.assertEqual(self.cards(), [])

    def test_following_is_per_account(self):
        from django.contrib.auth import get_user_model

        game = self.game()
        someone_else = get_user_model().objects.create_user(email="fan@example.com", password="pw-secret-123")
        EventFollow.objects.create(club=self.club, user=someone_else, event=game)
        GameFollow.objects.create(club=self.club, user=someone_else, team=self.team)

        # Another account's follows put nothing on this account's Home...
        self.assertEqual(self.cards(), [])
        self.assertEqual(self.get("/game-follows").json()["games"], [])
        self.assertEqual(self.get(f"/events/{game.pk}").json()["follow"], {"directly": False, "via_team": False})

        # ...and following it too does not touch theirs.
        self.send("put", f"/events/{game.pk}/follow")
        self.send("delete", f"/events/{game.pk}/follow")
        self.assertTrue(EventFollow.objects.filter(user=someone_else, event=game).exists())


class DirectFollowersGetThePushesTests(FollowedGamesTestCase):
    def test_followers_are_team_followers_and_direct_followers_once_each(self):
        from django.contrib.auth import get_user_model

        user_model = get_user_model()
        by_team = user_model.objects.create_user(email="team@example.com", password="pw-secret-123")
        by_game = user_model.objects.create_user(email="game@example.com", password="pw-secret-123")
        both = user_model.objects.create_user(email="both@example.com", password="pw-secret-123")
        neither = user_model.objects.create_user(email="neither@example.com", password="pw-secret-123")
        game = self.game()
        for user in (by_team, both):
            GameFollow.objects.create(club=self.club, user=user, team=self.team)
        for user in (by_game, both):
            EventFollow.objects.create(club=self.club, user=user, event=game)
        other_game = self.game(title="Other game")
        EventFollow.objects.create(club=self.club, user=neither, event=other_game)

        self.assertEqual({user.email for user in game_push.followers(game)}, {"team@example.com", "game@example.com", "both@example.com"})
