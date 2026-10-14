"""Followed games and the game timeline in the PWA (iOS parity): the follow toggle on the event
sheet, the timeline card (polling only while live), Home's followed-game cards and the per-game
unfollow on Me > Live game updates. Rules live in events.services.game_push / game_timeline."""

import datetime

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from waffle import get_waffle_flag_model

from club.models import Club
from events.models import Event, EventFollow, GameFollow, GameTimeline, Opponent

from .test_member_screens import HOST, MemberScreenFixture, setUpModule, tearDownModule  # noqa: F401

DOCUMENT = {
    "version": 1,
    "status": "live",
    "period": "2",
    "clock": "04:31",
    "score": {"home": 2, "away": 1},
    "periods": [{"label": "1", "home": 1, "away": 1}, {"label": "2", "home": 1, "away": 0}],
    "events": [
        {"id": "a", "type": "period_start", "period": "1", "seconds": 0, "time": "00:00"},
        {
            "id": "b",
            "type": "goal",
            "period": "1",
            "seconds": 473,
            "time": "07:53",
            "side": "home",
            "goal": {
                "scorer": {"name": "Julia Malejka", "number": 88},
                "assists": [{"name": "Zoe Kallenbach", "number": 33}],
                "strength": "PP1",
                "empty_net": True,
                "score_after": {"home": 1, "away": 0},
            },
        },
        {"id": "c", "type": "penalty", "period": "1", "seconds": 600, "time": "10:00", "side": "away", "penalty": {"player": {"number": 16}, "code": "TRIP", "description": "Tripping", "minutes": 2, "kind": "minor"}},
        {"id": "d", "type": "goalie_change", "period": "2", "seconds": 1300, "time": "21:40", "side": "home", "goalie_change": {"player": {"name": "Eva Goal", "number": 30}, "action": "in"}},
    ],
}


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "testserver"])
class GameFollowTestCase(MemberScreenFixture, TestCase):
    def setUp(self):
        get_waffle_flag_model().objects.get_or_create(name="game_timeline")[0].clubs.add(self.club)
        cache.clear()
        self.client.force_login(self.user)
        self.opponent = Opponent.objects.create(club=self.club, name="Rivals HC")

    def game(self, hours=2, **kwargs):
        kwargs.setdefault("title", "Game")
        game = self.make_event(kind=Event.EventKind.GAME, start=timezone.now() + datetime.timedelta(hours=hours), opponent=self.opponent, **kwargs)
        game.teams.add(self.team)
        return game

    def timeline(self, game, status="live", **document):
        return GameTimeline.objects.create(event=game, source="RBIHF", status=status, data={**DOCUMENT, "status": status, **document})


class FollowToggleTests(GameFollowTestCase):
    def test_sheet_offers_to_follow_and_following_is_idempotent(self):
        game = self.game()
        self.assertContains(self.get(reverse("mobile:event_detail", args=[game.pk]) + "?sheet=1"), "Get live score pushes for this game.")

        url = reverse("mobile:game_follow", args=[game.pk])
        response = self.post(url, {"follow": "1"}, HTTP_HX_REQUEST="true")
        self.post(url, {"follow": "1"}, HTTP_HX_REQUEST="true")

        self.assertContains(response, "Live score pushes for this game.")
        self.assertEqual(EventFollow.objects.filter(user=self.user, event=game).count(), 1)

        self.post(url, {"follow": "0"}, HTTP_HX_REQUEST="true")
        self.assertFalse(EventFollow.objects.exists())

    def test_followed_through_a_team_is_read_only(self):
        game = self.game()
        GameFollow.objects.create(club=self.club, user=self.user, team=self.team)

        response = self.get(reverse("mobile:event_detail", args=[game.pk]) + "?sheet=1")

        self.assertContains(response, "Following via U12 Girls.")
        self.assertContains(response, "disabled")
        self.post(reverse("mobile:game_follow", args=[game.pk]), {"follow": "0"}, HTTP_HX_REQUEST="true")
        self.assertTrue(GameFollow.objects.exists())

    def test_non_games_and_other_clubs_cannot_be_followed(self):
        practice = self.make_event()
        self.assertEqual(self.post(reverse("mobile:game_follow", args=[practice.pk]), {"follow": "1"}).status_code, 400)
        other = Club.objects.create(name="Rival FC", slug="rival-fc")
        foreign = Event.objects.create(club=other, title="Away", kind=Event.EventKind.GAME, start=timezone.now() + datetime.timedelta(hours=3))
        self.assertEqual(self.post(reverse("mobile:game_follow", args=[foreign.pk]), {"follow": "1"}).status_code, 404)
        self.assertEqual(self.get(reverse("mobile:game_timeline", args=[foreign.pk])).status_code, 404)

    def test_everything_needs_the_club_flag(self):
        game = self.game()
        get_waffle_flag_model().objects.get(name="game_timeline").clubs.remove(self.club)
        cache.clear()

        self.assertEqual(self.post(reverse("mobile:game_follow", args=[game.pk]), {"follow": "1"}).status_code, 404)
        self.assertEqual(self.get(reverse("mobile:game_timeline", args=[game.pk])).status_code, 404)
        self.assertEqual(self.get(reverse("mobile:followed_games")).status_code, 404)
        self.assertNotContains(self.get(reverse("mobile:event_detail", args=[game.pk]) + "?sheet=1"), "Follow this game")


class TimelineCardTests(GameFollowTestCase):
    def test_live_timeline_shows_events_and_polls(self):
        game = self.game(hours=-1)
        self.timeline(game)

        response = self.get(reverse("mobile:event_detail", args=[game.pk]) + "?sheet=1")

        self.assertContains(response, "Goal · Julia Malejka")
        self.assertContains(response, "Assists: Zoe Kallenbach")
        self.assertContains(response, "Empty net")
        self.assertContains(response, "PP1")
        self.assertContains(response, "#16 (2 min)")
        self.assertContains(response, "Eva Goal in goal")
        self.assertContains(response, "START OF PERIOD 1")
        self.assertContains(response, "every 15s")
        self.assertContains(response, reverse("mobile:game_timeline", args=[game.pk]))

    def test_final_timeline_stops_polling(self):
        game = self.game(hours=-3)
        self.timeline(game, status="final", decided_in="overtime")

        response = self.get(reverse("mobile:game_timeline", args=[game.pk]))

        self.assertContains(response, "Final · OT")
        self.assertNotContains(response, "every 15s")

    def test_game_without_timeline_shows_no_card(self):
        game = self.game()

        response = self.get(reverse("mobile:game_timeline", args=[game.pk]))

        self.assertNotContains(response, "m-card")


class HomeFollowedGamesTests(GameFollowTestCase):
    def test_live_followed_game_leads_home_and_polls(self):
        game = self.game(hours=-1, is_live=True)
        self.timeline(game)
        upcoming = self.game(hours=5, title="Later")
        GameFollow.objects.create(club=self.club, user=self.user, team=self.team)
        EventFollow.objects.create(club=self.club, user=self.user, event=upcoming)

        response = self.get(reverse("mobile:home"))

        self.assertContains(response, "Live &middot; P2 &middot; 04:31", html=False)
        self.assertContains(response, "2 &ndash; 1")
        self.assertContains(response, "every 20s")
        self.assertContains(response, reverse("mobile:event_detail", args=[upcoming.pk]))
        self.assertLess(response.content.decode().index("followed-live"), response.content.decode().index("followed-rest"))

    def test_poll_fragment_updates_both_slots_out_of_band(self):
        self.game(hours=-1, is_live=True)
        GameFollow.objects.create(club=self.club, user=self.user, team=self.team)

        response = self.get(reverse("mobile:followed_games"))

        self.assertContains(response, 'id="followed-live"')
        self.assertContains(response, 'id="followed-rest"')
        self.assertContains(response, 'hx-swap-oob="true"', count=2)

    def test_unfollowed_games_are_not_on_home(self):
        self.game(hours=-1, is_live=True)

        self.assertNotContains(self.get(reverse("mobile:home")), "every 20s")


class IndividualGamesTests(GameFollowTestCase):
    def test_live_game_updates_lists_followed_games_and_unfollows_one(self):
        game = self.game()
        EventFollow.objects.create(club=self.club, user=self.user, event=game)

        page = self.get(reverse("mobile:live_game_updates"))
        self.assertContains(page, "Individual games")
        self.assertContains(page, game.title)

        self.post(reverse("mobile:live_game_updates"), {"unfollow_game": str(game.pk)})
        self.assertFalse(EventFollow.objects.exists())

    def test_unfollow_ignores_garbage_and_other_clubs(self):
        self.assertEqual(self.post(reverse("mobile:live_game_updates"), {"unfollow_game": "nope"}).status_code, 302)
        other = Club.objects.create(name="Rival FC", slug="rival-fc")
        foreign = Event.objects.create(club=other, title="Away", kind=Event.EventKind.GAME, start=timezone.now() + datetime.timedelta(hours=3))
        EventFollow.objects.create(club=other, user=self.user, event=foreign)
        self.post(reverse("mobile:live_game_updates"), {"unfollow_game": str(foreign.pk)})
        self.assertTrue(EventFollow.objects.filter(event=foreign).exists())
