import datetime
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from events.models import Event, Location, Opponent
from members.models import ClubMembership
from news.tests import NewsClubMixin
from sponsoring.models import Sponsor

BASE = "/api/v1"


class LegacyPublicApiTests(NewsClubMixin, TestCase):
    def get(self, path, host=None):
        return self.client.get(BASE + path, HTTP_HOST=host or self.host, HTTP_ORIGIN="https://club-website.example")

    def test_public_paths_leave_management_api_authenticated(self):
        item = self.news("Club news", title_en="Club news EN", teams=[self.team])
        self.news("Internal news", visibility="internal")

        response = self.get("/news/?limit=0&offset=-1")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.headers["Access-Control-Allow-Origin"], "*")
        body = response.json()
        self.assertEqual((body["count"], body["limit"], body["offset"]), (1, 1, 0))
        (row,) = body["results"]
        self.assertEqual(row["id"], str(item.pk))
        self.assertEqual(row["title_en"], row["translations"]["en"]["title"])
        self.assertEqual(row["body_nl"], row["translations"]["nl"]["body"])
        self.assertEqual(self.get(f"/news/{item.slug}/").json(), row)
        self.assertEqual(self.get("/news/internal-news/").status_code, 404)
        self.assertEqual(self.get("/news/", host="testserver").status_code, 404)

        for path in ("/news", "/teams"):
            with self.subTest(path=path):
                protected = self.get(path)
                self.assertEqual(protected.status_code, 401)
                self.assertNotIn("Access-Control-Allow-Origin", protected.headers)
        preflight = self.client.options(BASE + "/news/", HTTP_HOST=self.host, HTTP_ORIGIN="https://club-website.example", HTTP_ACCESS_CONTROL_REQUEST_METHOD="GET")
        self.assertEqual(preflight.status_code, 204)
        self.assertEqual(preflight.headers["Access-Control-Allow-Methods"], "GET, OPTIONS")

    def test_roster_uses_legacy_season_label_and_player_consent(self):
        self.ann.player_photo = "members/player.jpg"
        self.ann.photo_public_consent = True
        self.ann.save(update_fields=["player_photo", "photo_public_consent"])
        ClubMembership.objects.filter(member=self.kid, season=self.season).update(status=ClubMembership.StatusChoices.PENDING)

        response = self.get(f"/teams/{self.team.pk}/roster/")
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["season"], f"{self.season.start_date:%y}-{self.season.end_date:%y}")
        players = {player["id"]: player for group in body["players"] for player in group["players"]}
        self.assertIn(f"/members/{self.ann.pk}/player-photo/", players[str(self.ann.pk)]["photo_url"])
        self.assertIsNone(players[str(self.cara.pk)]["photo_url"])
        self.assertNotIn(str(self.kid.pk), players)

        self.assertEqual(self.get("/teams/").headers["Cache-Control"], "public, max-age=60")
        self.assertEqual(self.get(f"/teams/{self.team.pk}/standings/").status_code, 200)

    def test_games_and_sponsors_return_legacy_ids(self):
        location = Location.objects.create(name="Ice rink", is_home=True)
        opponent = Opponent.objects.create(name="Visitors")
        game = Event.objects.create(title="Home game", kind=Event.EventKind.GAME, start=timezone.now() + datetime.timedelta(hours=1), location=location, opponent=opponent, season=self.season)
        game.teams.set([self.team])
        sponsor = Sponsor.objects.create(name="Active", url="https://sponsor.example")
        Sponsor.objects.create(name="Future", start_date=timezone.localdate() + datetime.timedelta(days=1))

        (listed,) = self.get("/games/upcoming/?count=0").json()
        self.assertEqual(listed["id"], str(game.pk))
        self.assertEqual(listed["home_team"]["id"], str(self.team.pk))
        self.assertEqual(listed["away_team"]["id"], str(opponent.pk))
        self.assertEqual(self.get(f"/games/{game.pk}/").json(), listed)
        self.assertEqual(self.get(f"/teams/{self.team.pk}/games/").json(), [listed])

        (listed_sponsor,) = self.get("/sponsors/?randomize=true").json()
        self.assertEqual(listed_sponsor["id"], str(sponsor.pk))
        self.assertEqual(listed_sponsor["url"], sponsor.url)
        self.assertEqual(set(listed_sponsor), {"id", "name", "url", "logo_url", "logo_width", "logo_height", "start_date", "end_date"})

    def test_public_sponsors_randomization_is_optional(self):
        Sponsor.objects.create(name="Alpha")
        Sponsor.objects.create(name="Beta")
        with patch("api.legacy.sponsors.random.shuffle", side_effect=list.reverse) as shuffle:
            default = self.get("/sponsors/")
            self.assertEqual(default.status_code, 200, default.content)
            self.assertEqual([row["name"] for row in default.json()], ["Alpha", "Beta"])
            shuffle.assert_not_called()
            randomized = self.get("/sponsors/?randomize=true")
            self.assertEqual([row["name"] for row in randomized.json()], ["Beta", "Alpha"])
            shuffle.assert_called_once()
