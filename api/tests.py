import datetime
import io

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from PIL import Image

from club.models import Club, ClubMembership, Season, Sponsor
from events.models import Event, Location, Opponent
from members.models import Member
from news.models import News, NewsPhoto
from teams.models import Position, StaffAssignment, Team, TeamMembership, TeamPhoto


@override_settings(
    ROSTERCHIEF_BASE_DOMAIN="rosterchief.app",
    ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "rival-fc.rosterchief.app", "testserver"],
)
class ApiTestBase(TestCase):
    def setUp(self):
        self.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        today = timezone.localdate()
        self.season = Season.objects.create(club=self.club, start_date=today - datetime.timedelta(days=30), end_date=today + datetime.timedelta(days=300))
        self.team = Team.objects.create(club=self.club, name="First Team", short_name="1st")

    def api_get(self, path, **params):
        return self.client.get(f"/api/v1{path}", params, HTTP_HOST="ajax-united.rosterchief.app")

    def api_get_base_domain(self, path, **params):
        return self.client.get(f"/api/v1{path}", params, HTTP_HOST="rosterchief.app")


class NewsApiTests(ApiTestBase):
    def make_news(self, **overrides):
        defaults = {"club": self.club, "title": "News", "body": "body", "status": News.Status.PUBLISHED, "published_at": timezone.now() - datetime.timedelta(hours=1), "visibility": News.Visibility.EXTERNAL}
        defaults.update(overrides)
        return News.objects.create(**defaults)

    def test_a_draft_is_excluded(self):
        self.make_news(status=News.Status.DRAFT, published_at=None)

        self.assertEqual(self.api_get("/news/").json()["count"], 0)

    def test_a_scheduled_but_not_yet_released_item_is_excluded(self):
        self.make_news(published_at=timezone.now() + datetime.timedelta(days=1))

        self.assertEqual(self.api_get("/news/").json()["count"], 0)

    def test_an_internal_only_item_is_excluded(self):
        self.make_news(visibility=News.Visibility.INTERNAL)

        self.assertEqual(self.api_get("/news/").json()["count"], 0)

    def test_an_external_item_is_included(self):
        item = self.make_news(visibility=News.Visibility.EXTERNAL)

        data = self.api_get("/news/").json()

        self.assertEqual(data["count"], 1)
        self.assertEqual(data["results"][0]["id"], str(item.pk))

    def test_a_both_visibility_item_is_included(self):
        self.make_news(visibility=News.Visibility.BOTH)

        self.assertEqual(self.api_get("/news/").json()["count"], 1)

    def test_newest_first(self):
        older = self.make_news(title="Older", published_at=timezone.now() - datetime.timedelta(days=2))
        newer = self.make_news(title="Newer", published_at=timezone.now() - datetime.timedelta(hours=1))

        results = self.api_get("/news/").json()["results"]

        self.assertEqual([r["id"] for r in results], [str(newer.pk), str(older.pk)])

    def test_photos_get_absolute_urls(self):
        item = self.make_news()
        NewsPhoto.objects.create(news_item=item, image="clubs/ajax-united/news/x/pic.jpg", is_main=True)

        photo = self.api_get("/news/").json()["results"][0]["photos"][0]

        self.assertTrue(photo["url"].startswith("http://ajax-united.rosterchief.app/media/"))
        self.assertTrue(photo["is_main"])

    def test_pagination_limit_and_offset(self):
        for i in range(3):
            self.make_news(title=f"Item {i}", published_at=timezone.now() - datetime.timedelta(hours=1, minutes=i))

        data = self.api_get("/news/", limit=1, offset=1).json()

        self.assertEqual(data["count"], 3)
        self.assertEqual(len(data["results"]), 1)

    def test_limit_is_capped(self):
        self.assertEqual(self.api_get("/news/", limit=1000).json()["limit"], 100)

    def test_no_news_is_an_empty_list_not_an_error(self):
        response = self.api_get("/news/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["results"], [])

    def test_excerpt_is_a_truncated_prefix_of_the_body(self):
        self.make_news(body=" ".join(f"word{i}" for i in range(80)))

        excerpt = self.api_get("/news/").json()["results"][0]["excerpt"]

        self.assertTrue(excerpt.startswith("word0 word1"))
        self.assertTrue(excerpt.endswith("…"))
        self.assertLess(len(excerpt.split()), 80)

    def test_excerpt_is_unchanged_when_the_body_is_already_short(self):
        item = self.make_news(body="Short body.")

        excerpt = self.api_get("/news/").json()["results"][0]["excerpt"]

        self.assertEqual(excerpt, item.body)

    def test_slug_is_auto_populated_from_the_title(self):
        item = self.make_news(title="Big Win This Weekend")

        self.assertEqual(item.slug, "big-win-this-weekend")

    def test_get_single_news_item_by_slug(self):
        item = self.make_news(title="Big Win This Weekend")

        response = self.api_get(f"/news/{item.slug}/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["id"], str(item.pk))

    def test_get_single_news_item_404s_for_an_unknown_slug(self):
        response = self.api_get("/news/no-such-item/")

        self.assertEqual(response.status_code, 404)

    def test_get_single_news_item_respects_visibility(self):
        item = self.make_news(visibility=News.Visibility.INTERNAL)

        response = self.api_get(f"/news/{item.slug}/")

        self.assertEqual(response.status_code, 404)

    def test_body_markdown_is_rendered_to_html(self):
        self.make_news(body="## Big win\n\nWe beat **Rivals FC** 4-2. [Full report](https://example.com).")

        body = self.api_get("/news/").json()["results"][0]["body"]

        self.assertIn("<h2>Big win</h2>", body)
        self.assertIn("<strong>Rivals FC</strong>", body)
        self.assertIn('href="https://example.com"', body)
        self.assertIn(">Full report</a>", body)

    def test_body_markdown_a_single_newline_becomes_a_line_break(self):
        self.make_news(body="Line one\nLine two")

        body = self.api_get("/news/").json()["results"][0]["body"]

        self.assertIn("Line one<br", body)

    def test_body_html_strips_a_script_tag(self):
        self.make_news(body="Hello<script>alert('xss')</script>world")

        body = self.api_get("/news/").json()["results"][0]["body"]

        self.assertNotIn("<script", body)
        self.assertNotIn("alert(", body)

    def test_body_html_strips_an_event_handler_attribute(self):
        self.make_news(body='<img src="x" onerror="alert(1)">')

        body = self.api_get("/news/").json()["results"][0]["body"]

        self.assertNotIn("onerror", body)

    def test_body_html_strips_a_javascript_url(self):
        self.make_news(body="[click me](javascript:alert(1))")

        body = self.api_get("/news/").json()["results"][0]["body"]

        self.assertNotIn("javascript:", body)

    def test_excerpt_strips_markdown_syntax(self):
        self.make_news(body="**Bold** and a [link](https://example.com) and # not a heading here")

        excerpt = self.api_get("/news/").json()["results"][0]["excerpt"]

        self.assertNotIn("**", excerpt)
        self.assertNotIn("[link]", excerpt)
        self.assertNotIn("<", excerpt)


class TeamsApiTests(ApiTestBase):
    def setUp(self):
        super().setUp()
        self.forward = Position.objects.create(club=self.club, name="Forward", short_name="FW", ordering=1)
        self.defense = Position.objects.create(club=self.club, name="Defense", short_name="DF", ordering=2)
        self.coach_position = Position.objects.create(club=self.club, name="Head Coach", short_name="HC", staff_position=True, management_position=True)

    def test_list_teams(self):
        data = self.api_get("/teams/").json()

        self.assertEqual(data, [{"id": str(self.team.pk), "name": "First Team", "short_name": "1st", "photo_url": None}])

    def test_roster_groups_players_by_position(self):
        alice = Member.objects.create(first_name="Alice", last_name="Ash")
        bob = Member.objects.create(first_name="Bob", last_name="Birch")
        carol = Member.objects.create(first_name="Carol", last_name="Cedar")
        # Two forwards (ordering 1) at different jersey numbers, one defense (ordering 2).
        TeamMembership.objects.create(team=self.team, member=bob, season=self.season, position=self.forward, jersey_number=9)
        TeamMembership.objects.create(team=self.team, member=alice, season=self.season, position=self.forward, jersey_number=2)
        TeamMembership.objects.create(team=self.team, member=carol, season=self.season, position=self.defense, jersey_number=1)

        groups = self.api_get(f"/teams/{self.team.pk}/roster/").json()["players"]

        # Groups in Position.ordering order (Forward before Defense); within a
        # group, sorted by jersey number.
        self.assertEqual([g["position"] for g in groups], ["Forward", "Defense"])
        self.assertEqual([p["first_name"] for p in groups[0]["players"]], ["Alice", "Bob"])
        self.assertEqual([p["first_name"] for p in groups[1]["players"]], ["Carol"])

    def test_a_player_entry_no_longer_repeats_its_position(self):
        # The position is now the group key, not a per-player field.
        alice = Member.objects.create(first_name="Alice", last_name="Ash")
        TeamMembership.objects.create(team=self.team, member=alice, season=self.season, position=self.forward, jersey_number=2)

        player = self.api_get(f"/teams/{self.team.pk}/roster/").json()["players"][0]["players"][0]

        self.assertNotIn("position", player)

    def test_roster_includes_staff(self):
        dana = Member.objects.create(first_name="Dana", last_name="Dean")
        StaffAssignment.objects.create(team=self.team, member=dana, season=self.season, position=self.coach_position)

        staff = self.api_get(f"/teams/{self.team.pk}/roster/").json()["staff"]

        self.assertEqual(staff, [{"id": str(dana.pk), "first_name": "Dana", "last_name": "Dean", "position": "Head Coach"}])

    def test_roster_is_current_season_only(self):
        other_season = Season.objects.create(club=self.club, start_date=datetime.date(2000, 1, 1), end_date=datetime.date(2000, 12, 31))
        eve = Member.objects.create(first_name="Eve", last_name="Elm")
        TeamMembership.objects.create(team=self.team, member=eve, season=other_season, position=self.forward, jersey_number=1)

        players = self.api_get(f"/teams/{self.team.pk}/roster/").json()["players"]

        self.assertEqual(players, [])

    def test_roster_is_empty_with_no_current_season(self):
        self.season.delete()
        team_without_season = self.team

        data = self.api_get(f"/teams/{team_without_season.pk}/roster/").json()

        self.assertEqual(data["season"], None)
        self.assertEqual(data["players"], [])
        self.assertEqual(data["staff"], [])

    def test_list_teams_includes_the_current_seasons_photo(self):
        TeamPhoto.objects.create(team=self.team, season=self.season, image="clubs/ajax-united/teams/x/26-27/pic.jpg")

        photo_url = self.api_get("/teams/").json()[0]["photo_url"]

        self.assertTrue(photo_url.startswith("http://ajax-united.rosterchief.app/media/"))

    def test_roster_includes_the_current_seasons_photo(self):
        TeamPhoto.objects.create(team=self.team, season=self.season, image="clubs/ajax-united/teams/x/26-27/pic.jpg")

        photo_url = self.api_get(f"/teams/{self.team.pk}/roster/").json()["team"]["photo_url"]

        self.assertTrue(photo_url.startswith("http://ajax-united.rosterchief.app/media/"))

    def test_a_photo_from_a_different_season_does_not_leak(self):
        other_season = Season.objects.create(club=self.club, start_date=datetime.date(2000, 1, 1), end_date=datetime.date(2000, 12, 31))
        TeamPhoto.objects.create(team=self.team, season=other_season, image="clubs/ajax-united/teams/x/00-00/pic.jpg")

        self.assertIsNone(self.api_get("/teams/").json()[0]["photo_url"])
        self.assertIsNone(self.api_get(f"/teams/{self.team.pk}/roster/").json()["team"]["photo_url"])

    def test_a_team_from_another_club_404s(self):
        other_club = Club.objects.create(name="Rival FC", slug="rival-fc")
        other_team = Team.objects.create(club=other_club, name="Rival Team", short_name="RIV")

        response = self.api_get(f"/teams/{other_team.pk}/roster/")

        self.assertEqual(response.status_code, 404)

    def test_a_players_license_comes_from_their_club_membership(self):
        alice = Member.objects.create(first_name="Alice", last_name="Ash")
        TeamMembership.objects.create(team=self.team, member=alice, season=self.season, position=self.forward, jersey_number=2)
        ClubMembership.objects.create(club=self.club, member=alice, season=self.season, license="BE-12345")

        player = self.api_get(f"/teams/{self.team.pk}/roster/").json()["players"][0]["players"][0]

        self.assertEqual(player["license"], "BE-12345")

    def test_a_players_license_is_null_without_a_club_membership(self):
        alice = Member.objects.create(first_name="Alice", last_name="Ash")
        TeamMembership.objects.create(team=self.team, member=alice, season=self.season, position=self.forward, jersey_number=2)

        player = self.api_get(f"/teams/{self.team.pk}/roster/").json()["players"][0]["players"][0]

        self.assertIsNone(player["license"])


class GamesApiTests(ApiTestBase):
    def setUp(self):
        super().setUp()
        self.home_location = Location.objects.create(club=self.club, name="Home Arena", address="1 St", city="Town", zip_code="1000", country="BE", is_home=True)
        self.opponent = Opponent.objects.create(club=self.club, name="Rivals FC")

    def make_game(self, **overrides):
        defaults = {"club": self.club, "title": "Game", "kind": Event.EventKind.GAME, "start": timezone.now() + datetime.timedelta(days=1), "opponent": self.opponent}
        defaults.update(overrides)
        event = Event.objects.create(**defaults)
        event.teams.add(self.team)
        return event

    def test_upcoming_games_are_listed_with_location_and_teams(self):
        self.make_game(location=self.home_location)

        games = self.api_get("/games/upcoming/").json()

        self.assertEqual(len(games), 1)
        self.assertEqual(games[0]["home_team"]["name"], "First Team")
        self.assertEqual(games[0]["away_team"]["name"], "Rivals FC")
        self.assertEqual(games[0]["location"]["name"], "Home Arena")
        self.assertEqual(games[0]["status"], "upcoming")

    def test_upcoming_excludes_cancelled_games(self):
        self.make_game(cancelled=True)

        self.assertEqual(self.api_get("/games/upcoming/").json(), [])

    def test_upcoming_excludes_past_games(self):
        self.make_game(start=timezone.now() - datetime.timedelta(days=1))

        self.assertEqual(self.api_get("/games/upcoming/").json(), [])

    def test_upcoming_includes_tournaments(self):
        self.make_game(kind=Event.EventKind.TOURNAMENT)

        self.assertEqual(len(self.api_get("/games/upcoming/").json()), 1)

    def test_upcoming_excludes_other_kinds(self):
        self.make_game(kind=Event.EventKind.TRAINING)
        self.make_game(kind=Event.EventKind.SOCIAL)

        self.assertEqual(self.api_get("/games/upcoming/").json(), [])

    def test_live_endpoint_stays_game_only(self):
        # is_live/scores are game-specific -- a tournament wouldn't have
        # anything meaningful to show here even if flagged live.
        self.make_game(kind=Event.EventKind.TOURNAMENT, start=timezone.now() - datetime.timedelta(minutes=10), is_live=True)

        self.assertEqual(self.api_get("/games/live/").json(), [])

    def test_count_is_respected(self):
        for i in range(3):
            self.make_game(start=timezone.now() + datetime.timedelta(days=i + 1))

        self.assertEqual(len(self.api_get("/games/upcoming/", count=2).json()), 2)

    def test_count_is_capped(self):
        for i in range(3):
            self.make_game(start=timezone.now() + datetime.timedelta(days=i + 1))

        # Cap is 50, well above the 3 created -- just confirm an oversized
        # request doesn't error and doesn't somehow exceed what exists.
        response = self.api_get("/games/upcoming/", count=1000)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 3)

    def test_live_games_are_listed_with_scores(self):
        self.make_game(start=timezone.now() - datetime.timedelta(minutes=10), is_live=True, score_for=2, score_against=1, location=self.home_location)

        games = self.api_get("/games/live/").json()

        self.assertEqual(len(games), 1)
        self.assertEqual(games[0]["status"], "live")
        self.assertEqual(games[0]["home_score"], 2)
        self.assertEqual(games[0]["away_score"], 1)

    def test_live_excludes_cancelled_games(self):
        self.make_game(is_live=True, cancelled=True)

        self.assertEqual(self.api_get("/games/live/").json(), [])

    def test_non_live_games_are_excluded_from_live_endpoint(self):
        self.make_game()

        self.assertEqual(self.api_get("/games/live/").json(), [])

    def test_score_relabelling_for_an_away_game(self):
        # No location (or a non-home one) -- is_home_game is False, so our
        # team's score_for/score_against map to the away side.
        self.make_game(start=timezone.now() - datetime.timedelta(days=1), score_for=4, score_against=3)

        games = self.api_get(f"/teams/{self.team.pk}/games/").json()

        self.assertEqual(games[0]["home_team"]["name"], "Rivals FC")
        self.assertEqual(games[0]["away_team"]["name"], "First Team")
        self.assertEqual(games[0]["home_score"], 3)
        self.assertEqual(games[0]["away_score"], 4)
        self.assertEqual(games[0]["status"], "finished")

    def test_score_relabelling_for_a_home_game(self):
        self.make_game(start=timezone.now() - datetime.timedelta(days=1), score_for=4, score_against=3, location=self.home_location)

        games = self.api_get(f"/teams/{self.team.pk}/games/").json()

        self.assertEqual(games[0]["home_team"]["name"], "First Team")
        self.assertEqual(games[0]["away_team"]["name"], "Rivals FC")
        self.assertEqual(games[0]["home_score"], 4)
        self.assertEqual(games[0]["away_score"], 3)

    def test_team_games_includes_past_and_upcoming_for_the_current_season(self):
        self.make_game(title="Past", start=timezone.now() - datetime.timedelta(days=1), score_for=1, score_against=0)
        self.make_game(title="Future", start=timezone.now() + datetime.timedelta(days=1))

        games = self.api_get(f"/teams/{self.team.pk}/games/").json()

        self.assertEqual(len(games), 2)

    def test_team_games_excludes_a_different_season(self):
        other_season = Season.objects.create(club=self.club, start_date=datetime.date(2000, 1, 1), end_date=datetime.date(2000, 12, 31))
        self.make_game(start=datetime.datetime(2000, 6, 1, tzinfo=datetime.UTC), season=other_season)

        self.assertEqual(self.api_get(f"/teams/{self.team.pk}/games/").json(), [])

    def test_team_games_excludes_cancelled(self):
        self.make_game(cancelled=True)

        self.assertEqual(self.api_get(f"/teams/{self.team.pk}/games/").json(), [])

    def test_team_games_is_empty_with_no_current_season(self):
        self.make_game()
        self.season.delete()

        self.assertEqual(self.api_get(f"/teams/{self.team.pk}/games/").json(), [])

    def test_a_team_from_another_club_404s_on_games(self):
        other_club = Club.objects.create(name="Rival FC", slug="rival-fc")
        other_team = Team.objects.create(club=other_club, name="Rival Team", short_name="RIV")

        response = self.api_get(f"/teams/{other_team.pk}/games/")

        self.assertEqual(response.status_code, 404)

    def test_home_team_links_to_the_actual_team_and_the_clubs_logo(self):
        # Our own teams have no logo of their own -- they're shown under the club's badge.
        self.club.logo = "clubs/ajax-united/logo.png"
        self.club.save()
        self.make_game(location=self.home_location)

        home_team = self.api_get("/games/upcoming/").json()[0]["home_team"]

        self.assertEqual(home_team["id"], str(self.team.pk))
        self.assertEqual(home_team["name"], "First Team")
        self.assertTrue(home_team["logo_url"].startswith("http://ajax-united.rosterchief.app/media/"))

    def test_away_team_links_to_the_opponent_and_its_own_logo(self):
        self.opponent.logo = "opponents/rivals.png"
        self.opponent.save()
        self.make_game(location=self.home_location)

        away_team = self.api_get("/games/upcoming/").json()[0]["away_team"]

        self.assertEqual(away_team["id"], str(self.opponent.pk))
        self.assertEqual(away_team["name"], "Rivals FC")
        self.assertTrue(away_team["logo_url"].startswith("http://ajax-united.rosterchief.app/media/"))

    def test_team_logo_url_is_null_without_a_club_logo(self):
        self.make_game(location=self.home_location)

        home_team = self.api_get("/games/upcoming/").json()[0]["home_team"]

        self.assertIsNone(home_team["logo_url"])


class TenancyAndCorsTests(ApiTestBase):
    def test_the_base_domain_404s(self):
        response = self.api_get_base_domain("/news/")

        self.assertEqual(response.status_code, 404)

    def test_a_get_response_carries_the_cors_header(self):
        response = self.api_get("/news/")

        self.assertEqual(response["Access-Control-Allow-Origin"], "*")

    def test_an_options_preflight_gets_a_204_with_cors_headers(self):
        response = self.client.options("/api/v1/news/", HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.status_code, 204)
        self.assertEqual(response["Access-Control-Allow-Origin"], "*")
        self.assertIn("GET", response["Access-Control-Allow-Methods"])

    def test_cors_headers_are_not_added_outside_the_api(self):
        response = self.client.get("/", HTTP_HOST="ajax-united.rosterchief.app")

        self.assertNotIn("Access-Control-Allow-Origin", response)

    def test_docs_page_resolves(self):
        self.assertEqual(self.api_get("/docs").status_code, 200)

    def test_openapi_schema_resolves(self):
        self.assertEqual(self.api_get("/openapi.json").status_code, 200)


class SponsorApiTests(ApiTestBase):
    def make_sponsor(self, **overrides):
        today = timezone.localdate()
        defaults = {"club": self.club, "name": "Acme Corp", "start_date": today - datetime.timedelta(days=10), "end_date": today + datetime.timedelta(days=10)}
        defaults.update(overrides)
        return Sponsor.objects.create(**defaults)

    def test_a_sponsor_covering_today_is_included(self):
        self.make_sponsor()

        data = self.api_get("/sponsors/").json()

        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["name"], "Acme Corp")

    def test_a_sponsor_starting_in_the_future_is_excluded(self):
        today = timezone.localdate()
        self.make_sponsor(start_date=today + datetime.timedelta(days=1), end_date=None)

        self.assertEqual(self.api_get("/sponsors/").json(), [])

    def test_a_sponsor_that_already_ended_is_excluded(self):
        today = timezone.localdate()
        self.make_sponsor(start_date=today - datetime.timedelta(days=20), end_date=today - datetime.timedelta(days=1))

        self.assertEqual(self.api_get("/sponsors/").json(), [])

    def test_a_sponsor_with_no_end_date_and_a_past_start_is_included(self):
        today = timezone.localdate()
        self.make_sponsor(start_date=today - datetime.timedelta(days=100), end_date=None)

        self.assertEqual(len(self.api_get("/sponsors/").json()), 1)

    def test_a_sponsor_starting_today_is_included(self):
        today = timezone.localdate()
        self.make_sponsor(start_date=today, end_date=None)

        self.assertEqual(len(self.api_get("/sponsors/").json()), 1)

    def test_a_sponsor_ending_today_is_included(self):
        today = timezone.localdate()
        self.make_sponsor(start_date=today - datetime.timedelta(days=10), end_date=today)

        self.assertEqual(len(self.api_get("/sponsors/").json()), 1)

    def test_another_clubs_sponsor_never_leaks_in(self):
        other_club = Club.objects.create(name="Rival FC", slug="rival-fc")
        self.make_sponsor(club=other_club)

        self.assertEqual(self.api_get("/sponsors/").json(), [])

    def test_logo_url_is_absolute_when_set(self):
        self.make_sponsor(logo="clubs/ajax-united/sponsors/x/logo.png")

        logo_url = self.api_get("/sponsors/").json()[0]["logo_url"]

        self.assertTrue(logo_url.startswith("http://ajax-united.rosterchief.app/media/"))

    def test_logo_url_is_null_when_not_set(self):
        self.make_sponsor()

        self.assertIsNone(self.api_get("/sponsors/").json()[0]["logo_url"])

    def test_logo_dimensions_are_computed_for_a_raster_image(self):
        buffer = io.BytesIO()
        Image.new("RGB", (300, 150)).save(buffer, format="PNG")
        logo = SimpleUploadedFile("logo.png", buffer.getvalue(), content_type="image/png")

        self.make_sponsor(logo=logo)

        sponsor = self.api_get("/sponsors/").json()[0]
        self.assertEqual(sponsor["logo_width"], 300)
        self.assertEqual(sponsor["logo_height"], 150)

    def test_logo_dimensions_are_computed_for_an_svg_with_width_and_height(self):
        svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="120" height="80"></svg>'
        logo = SimpleUploadedFile("logo.svg", svg, content_type="image/svg+xml")

        self.make_sponsor(logo=logo)

        sponsor = self.api_get("/sponsors/").json()[0]
        self.assertEqual(sponsor["logo_width"], 120)
        self.assertEqual(sponsor["logo_height"], 80)

    def test_logo_dimensions_fall_back_to_an_svg_viewbox(self):
        svg = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 32"></svg>'
        logo = SimpleUploadedFile("logo.svg", svg, content_type="image/svg+xml")

        self.make_sponsor(logo=logo)

        sponsor = self.api_get("/sponsors/").json()[0]
        self.assertEqual(sponsor["logo_width"], 64)
        self.assertEqual(sponsor["logo_height"], 32)

    def test_logo_dimensions_are_null_without_a_logo(self):
        self.make_sponsor()

        sponsor = self.api_get("/sponsors/").json()[0]
        self.assertIsNone(sponsor["logo_width"])
        self.assertIsNone(sponsor["logo_height"])

    def test_randomize_returns_the_same_set_of_sponsors(self):
        for i in range(5):
            self.make_sponsor(name=f"Sponsor {i}")

        stable = {s["id"] for s in self.api_get("/sponsors/").json()}
        randomized = {s["id"] for s in self.api_get("/sponsors/", randomize="true").json()}

        self.assertEqual(stable, randomized)
        self.assertEqual(len(stable), 5)

    def test_default_order_is_stable_and_alphabetical(self):
        self.make_sponsor(name="Zulu Corp")
        self.make_sponsor(name="Acme Corp")

        names = [s["name"] for s in self.api_get("/sponsors/").json()]

        self.assertEqual(names, ["Acme Corp", "Zulu Corp"])
