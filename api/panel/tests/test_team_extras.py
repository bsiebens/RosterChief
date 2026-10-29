import datetime
import io
import tempfile
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils import timezone
from PIL import Image
from waffle import get_waffle_flag_model

from api.models import Job
from api.protected.tests import CLUB_HOST, OTHER_HOST, AppApiTestCase
from club.models import Club, ClubMembership, ClubRole, Season
from events.models import Event, GameTimeline
from events.services import import_sources
from events.services.game_stats import sync_game_stats
from members.models import Member
from rosterchief.test_support import make_club
from teams.models import Position, StaffAssignment, Team, TeamMembership, TeamPhoto

TEAMS = "/manage/teams"
RBIHF_URL = "https://www.rbihf.be/league/team/4460"


def png(name="team.png"):
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


def enable(name, club):
    flag, _created = get_waffle_flag_model().objects.get_or_create(name=name)
    flag.clubs.add(club)


class TeamExtrasTestCase(AppApiTestCase):
    def setUp(self):
        super().setUp()
        self.team = Team.objects.create(club=self.club, name="U12", short_name="U12", ordering=2)
        self.other_team = Team.objects.create(club=self.club, name="U14", short_name="U14", ordering=1)
        self.third = Team.objects.create(club=self.club, name="U16", short_name="U16")
        self.forward = Position.objects.create(club=self.club, name="Forward", short_name="F")
        self.coach_pos = Position.objects.create(club=self.club, name="Assistant", short_name="AC", staff_position=True)
        self.head_pos = Position.objects.create(club=self.club, name="Head coach", short_name="HC", staff_position=True, management_position=True)

    def as_role(self, role):
        ClubRole.objects.update_or_create(club=self.club, member=self.member, defaults={"role": role})
        return self.login_with_totp()

    def as_admin(self):
        return self.as_role(ClubRole.Roles.ADMIN)

    def as_manager(self, team=None):
        StaffAssignment.objects.create(team=team or self.team, member=self.member, season=self.season, position=self.head_pos)
        return self.login_with_totp()

    def as_coach(self, team=None):
        StaffAssignment.objects.create(team=team or self.team, member=self.member, season=self.season, position=self.coach_pos)
        return self.login()

    def call(self, method, path, token=None, data=None, host=CLUB_HOST, **headers):
        return self.api(method, path, token=token, host=host, data=data, **headers)

    def upload(self, path, token, files, data=None):
        return self.client.post(f"/api/v1{path}", data={**(data or {}), **files}, HTTP_HOST=CLUB_HOST, HTTP_AUTHORIZATION=f"Bearer {token}")

    def eligible(self, first="Sam", last="Smith"):
        member = Member.objects.create(first_name=first, last_name=last)
        ClubMembership.objects.create(club=self.club, member=member, season=self.season, status=ClubMembership.StatusChoices.ACTIVE)
        return member


class ReorderTests(TeamExtrasTestCase):
    def ids(self):
        return [str(pk) for pk in Team.objects.filter(club=self.club).order_by("ordering", "name").values_list("pk", flat=True)]

    def test_auth(self):
        self.assertError(self.call("post", f"{TEAMS}/reorder", data={"ids": []}), 401, "unauthenticated")
        self.assertError(self.call("post", f"{TEAMS}/reorder", self.login(), data={"ids": []}), 403, "forbidden")
        self.assertError(self.call("post", f"{TEAMS}/reorder", self.as_coach(), data={"ids": []}), 403, "forbidden")

    def test_reorders_and_numbers_every_team(self):
        token = self.as_admin()
        response = self.call("post", f"{TEAMS}/reorder", token, data={"ids": [str(self.team.pk), str(self.other_team.pk)]})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["ids"], [str(self.team.pk), str(self.other_team.pk), str(self.third.pk)])
        self.team.refresh_from_db(), self.other_team.refresh_from_db(), self.third.refresh_from_db()
        self.assertEqual((self.team.ordering, self.other_team.ordering, self.third.ordering), (1, 2, 3))

    def test_other_club_team_duplicate_and_garbage_are_422(self):
        token = self.as_admin()
        foreign = Team.objects.create(club=make_club(name="Rival FC", slug="rival-fc"), name="Foreign", short_name="F")
        for ids in ([str(foreign.pk)], [str(self.team.pk), str(self.team.pk)]):
            response = self.call("post", f"{TEAMS}/reorder", token, data={"ids": ids})
            self.assertError(response, 422, "validation_error")
            self.assertIn("ids", response.json()["error"]["fields"])
        self.assertError(self.call("post", f"{TEAMS}/reorder", token, data={"ids": ["nope"]}), 422, "validation_error")
        self.assertEqual(Team.objects.get(pk=foreign.pk).ordering, None)


class BulkAddTests(TeamExtrasTestCase):
    def url(self, team=None):
        return f"{TEAMS}/{(team or self.team).pk}/roster/bulk-add"

    def body(self, *rows, **extra):
        return {"rows": [{"member_id": str(m.pk), "position_id": str(p.pk), **kw} for m, p, kw in rows], **extra}

    def test_auth(self):
        sam = self.eligible()
        body = self.body((sam, self.forward, {}))
        self.assertError(self.call("post", self.url(), data=body), 401, "unauthenticated")
        self.assertError(self.call("post", self.url(), self.login(), data=body), 403, "forbidden")
        self.assertError(self.call("post", self.url(), self.as_coach(), data=body), 403, "forbidden")

    def test_manager_of_another_team_is_forbidden_and_unknown_team_404(self):
        sam = self.eligible()
        token = self.as_manager(self.other_team)
        self.assertError(self.call("post", self.url(), token, data=self.body((sam, self.forward, {}))), 403, "forbidden")
        foreign = Team.objects.create(club=make_club(name="Rival FC", slug="rival-fc"), name="Foreign", short_name="F")
        self.assertError(self.call("post", self.url(foreign), token, data=self.body((sam, self.forward, {}))), 404, "not_found")

    def test_adds_players_and_staff(self):
        sam, lee = self.eligible(), self.eligible("Lee", "Jones")
        token = self.as_manager()
        body = self.body((sam, self.forward, {"jersey_number": 9, "is_captain": True}), (lee, self.coach_pos, {}))
        response = self.call("post", self.url(), token, data=body)
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json(), {"players_added": 1, "staff_added": 1})
        membership = TeamMembership.objects.get(team=self.team, member=sam, season=self.season)
        self.assertEqual((membership.jersey_number, membership.is_captain), (9, True))
        self.assertTrue(StaffAssignment.objects.filter(team=self.team, member=lee, season=self.season).exists())

    def test_idempotency_key_replays(self):
        sam = self.eligible()
        token = self.as_admin()
        body = self.body((sam, self.forward, {}))
        first = self.call("post", self.url(), token, data=body, HTTP_IDEMPOTENCY_KEY="k1")
        second = self.call("post", self.url(), token, data=body, HTTP_IDEMPOTENCY_KEY="k1")
        self.assertEqual((first.status_code, second.status_code), (201, 201))
        self.assertEqual(second.json(), first.json())
        self.assertEqual(TeamMembership.objects.filter(team=self.team).count(), 1)

    def test_bad_row_saves_nothing_and_is_keyed_by_row(self):
        sam, lee = self.eligible(), self.eligible("Lee", "Jones")
        TeamMembership.objects.create(team=self.team, member=lee, season=self.season, position=self.forward, jersey_number=9)
        token = self.as_admin()
        body = self.body((sam, self.forward, {"jersey_number": 5}), (lee, self.coach_pos, {"jersey_number": 3}))
        response = self.call("post", self.url(), token, data=body)
        self.assertError(response, 422, "validation_error")
        self.assertIn("rows.1.jersey_number", response.json()["error"]["fields"])
        self.assertFalse(TeamMembership.objects.filter(member=sam).exists())

    def test_duplicate_member_in_batch_and_ineligible_member(self):
        sam = self.eligible()
        token = self.as_admin()
        response = self.call("post", self.url(), token, data=self.body((sam, self.forward, {}), (sam, self.forward, {})))
        self.assertError(response, 422, "validation_error")
        self.assertTrue(any(key.startswith("rows.") for key in response.json()["error"]["fields"]))
        stranger = Member.objects.create(first_name="No", last_name="Club")
        response = self.call("post", self.url(), token, data=self.body((stranger, self.forward, {})))
        self.assertIn("rows.0.member_id", response.json()["error"]["fields"])

    def test_empty_rows_is_422(self):
        self.assertError(self.call("post", self.url(), self.as_admin(), data={"rows": []}), 422, "validation_error")

    def test_unknown_season_404(self):
        sam = self.eligible()
        other = Season.objects.create(club=make_club(name="Rival FC", slug="rival-fc"), start_date=datetime.date.today(), end_date=datetime.date.today() + datetime.timedelta(days=5))
        self.assertError(self.call("post", self.url(), self.as_admin(), data=self.body((sam, self.forward, {}), season_id=str(other.pk))), 404, "not_found")


class PhotoTests(TeamExtrasTestCase):
    def url(self, team=None):
        return f"{TEAMS}/{(team or self.team).pk}/photo"

    def setUp(self):
        super().setUp()
        self.enterContext(override_settings(MEDIA_ROOT=self.enterContext(tempfile.TemporaryDirectory())))

    def test_auth(self):
        self.assertError(self.call("get", self.url()), 401, "unauthenticated")
        self.assertError(self.call("get", self.url(), self.login()), 403, "forbidden")
        self.assertError(self.call("delete", self.url(), self.as_coach(self.other_team)), 403, "forbidden")
        self.assertEqual(self.upload(self.url(), self.login(), {"photo": png()}).status_code, 403)

    def test_manager_of_other_team_forbidden_and_foreign_team_404(self):
        token = self.as_manager(self.other_team)
        self.assertEqual(self.upload(self.url(), token, {"photo": png()}).status_code, 403)
        foreign = Team.objects.create(club=make_club(name="Rival FC", slug="rival-fc"), name="Foreign", short_name="F")
        self.assertEqual(self.upload(self.url(foreign), token, {"photo": png()}).status_code, 404)
        self.assertError(self.call("delete", self.url(foreign), token), 404, "not_found")

    def test_upload_replace_get_delete(self):
        token = self.as_manager()
        self.assertIsNone(self.call("get", self.url(), token).json()["url"])
        response = self.upload(self.url(), token, {"photo": png()})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()["url"])
        self.assertEqual(response.json()["season_id"], str(self.season.pk))
        self.assertEqual(self.upload(self.url(), token, {"photo": png("again.png")}).status_code, 200)
        self.assertEqual(TeamPhoto.objects.filter(team=self.team, season=self.season).count(), 1)
        self.assertEqual(self.call("get", self.url(), token).json()["url"], TeamPhoto.objects.get().image.url)
        self.assertEqual(self.call("delete", self.url(), token).status_code, 204)
        self.assertFalse(TeamPhoto.objects.exists())
        self.assertEqual(self.call("delete", self.url(), token).status_code, 204)

    def test_validation(self):
        token = self.as_admin()
        response = self.client.post(f"/api/v1{self.url()}", data={}, HTTP_HOST=CLUB_HOST, HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(response.status_code, 422)
        wrong_type = SimpleUploadedFile("a.txt", b"hello", content_type="text/plain")
        self.assertError(self.upload(self.url(), token, {"photo": wrong_type}), 415, "unsupported_media_type")
        empty = SimpleUploadedFile("a.png", b"", content_type="image/png")
        self.assertError(self.upload(self.url(), token, {"photo": empty}), 422, "validation_error")
        fake = SimpleUploadedFile("a.png", b"not really an image", content_type="image/png")
        response = self.upload(self.url(), token, {"photo": fake})
        self.assertError(response, 422, "validation_error")
        self.assertIn("photo", response.json()["error"]["fields"])
        with override_settings(API_UPLOAD_MAX_BYTES=10):
            big = png()
            self.assertGreater(big.size, 10)
            with mock.patch("api.panel.routers.team_extras.PHOTO_MAX_BYTES", 10):
                self.assertError(self.upload(self.url(), token, {"photo": big}), 413, "file_too_large")
        self.assertFalse(TeamPhoto.objects.exists())

    def test_unknown_season_404(self):
        other = Season.objects.create(club=make_club(name="Rival FC", slug="rival-fc"), start_date=datetime.date.today(), end_date=datetime.date.today() + datetime.timedelta(days=5))
        self.assertEqual(self.upload(self.url(), self.as_admin(), {"photo": png()}, {"season_id": str(other.pk)}).status_code, 404)


class AnalyticsTests(TeamExtrasTestCase):
    DOCUMENT = {
        "events": [
            {"id": "g1", "type": "goal", "period": "1", "seconds": 60, "side": "away", "goal": {"scorer": {"name": "Sam Scorer", "number": 78}, "assists": [{"name": "Al Assist", "number": 9}], "strength": "PP1", "game_winning": True}},
            {"id": "g2", "type": "goal", "period": "2", "seconds": 90, "side": "home", "goal": {"scorer": {"name": "Them Scorer", "number": 5}, "assists": []}},
            {"id": "p1", "type": "penalty", "period": "2", "seconds": 200, "side": "away", "penalty": {"player": {"name": "Pat Penalty", "number": 3}, "minutes": 4}},
        ]
    }

    def setUp(self):
        super().setUp()
        Club.objects.filter(pk=self.club.pk).update(sport_type=Club.SportType.ICE_HOCKEY)
        self.club.refresh_from_db()
        enable("analytics", self.club)
        self.game = Event.objects.create(club=self.club, title="Game", kind=Event.EventKind.GAME, season=self.season, start=timezone.now() - datetime.timedelta(days=2), score_for=3, score_against=1)
        self.game.teams.add(self.team)
        GameTimeline.objects.create(event=self.game, source="test", data=self.DOCUMENT, status=GameTimeline.Status.FINAL)
        sync_game_stats(self.game, self.DOCUMENT)

    def url(self, team=None, suffix=""):
        return f"{TEAMS}/{(team or self.team).pk}/analytics{suffix}"

    def test_auth(self):
        self.assertError(self.call("get", self.url()), 401, "unauthenticated")
        self.assertError(self.call("get", self.url(), self.login()), 403, "forbidden")
        self.assertError(self.call("post", self.url(suffix="/strength"), self.login(), data={"event_id": str(self.game.pk), "goal": "g1", "strength": "EQ"}), 403, "forbidden")

    def test_flag_off_or_other_sport_is_404(self):
        token = self.as_admin()
        Club.objects.filter(pk=self.club.pk).update(sport_type="other")
        self.assertError(self.call("get", self.url(), token), 404, "not_found")
        Club.objects.filter(pk=self.club.pk).update(sport_type=Club.SportType.ICE_HOCKEY)
        get_waffle_flag_model().objects.get(name="analytics").clubs.clear()
        self.assertError(self.call("get", self.url(), token), 404, "not_found")

    def test_numbers_match_the_web_service(self):
        token = self.as_admin()
        response = self.call("get", self.url(), token)
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual((body["scope"], body["friendlies"], body["season_id"]), ("all", True, str(self.season.pk)))
        analytics = body["analytics"]
        self.assertEqual((analytics["goals_for"], analytics["goals_against"], analytics["games"]), (3, 1, 1))
        self.assertEqual((analytics["pim_for"], analytics["pim_against"]), (4, 0))
        self.assertEqual(analytics["record"], {"won": 1, "drawn": 0, "lost": 0})
        self.assertEqual(analytics["top_scorers"][0]["name"], "Sam Scorer")
        self.assertEqual(analytics["game_rows"][0]["event"]["id"], str(self.game.pk))

    def test_filters(self):
        token = self.as_admin()
        body = self.call("get", self.url() + "?games=home", token).json()
        self.assertEqual((body["scope"], body["analytics"]["games"]), ("home", 0))
        self.assertEqual(self.call("get", self.url() + "?games=bogus", token).json()["scope"], "all")
        body = self.call("get", self.url() + "?competition=Nope", token).json()
        self.assertEqual((body["competition"], body["analytics"]["games"]), ("", 1))

    def test_visibility_follows_the_team_page(self):
        self.assertEqual(self.call("get", self.url(), self.as_coach()).status_code, 200)
        self.assertError(self.call("get", self.url(self.other_team), self.login()), 404, "not_found")
        foreign = Team.objects.create(club=make_club(name="Rival FC", slug="rival-fc"), name="Foreign", short_name="F")
        self.assertError(self.call("get", self.url(foreign), self.login()), 404, "not_found")

    def test_unknown_season_404(self):
        other = Season.objects.create(club=make_club(name="Rival FC", slug="rival-fc"), start_date=datetime.date.today(), end_date=datetime.date.today() + datetime.timedelta(days=5))
        self.assertError(self.call("get", self.url() + f"?season_id={other.pk}", self.as_admin()), 404, "not_found")

    def test_set_strength(self):
        token = self.as_admin()
        url = self.url(suffix="/strength")
        ok = self.call("post", url, token, data={"event_id": str(self.game.pk), "goal": "g1", "strength": "EQ"})
        self.assertEqual(ok.status_code, 200, ok.content)
        self.assertEqual(GameTimeline.objects.get().strength_overrides, {"g1": "EQ"})
        self.assertIn("goal", self.call("post", url, token, data={"event_id": str(self.game.pk), "goal": "nope", "strength": "EQ"}).json()["error"]["fields"])
        self.assertIn("strength", self.call("post", url, token, data={"event_id": str(self.game.pk), "goal": "g1", "strength": "ZZ"}).json()["error"]["fields"])
        self.assertError(self.call("post", url, token, data={"event_id": str(Event.objects.create(club=self.club, title="x", kind=Event.EventKind.GAME, season=self.season, start=timezone.now()).pk), "goal": "g1", "strength": "EQ"}), 404, "not_found")


class FixtureSourceTests(TeamExtrasTestCase):
    def setUp(self):
        super().setUp()
        enable("RBIHF", self.club)

    def url(self, suffix="", team=None):
        return f"{TEAMS}/{(team or self.team).pk}/fixture-sources{suffix}"

    def payload(self, **extra):
        return {"source": "RBIHF", "url": RBIHF_URL, "season_id": str(self.season.pk), "competition_label": "Division 1", **extra}

    def test_auth(self):
        for method, suffix in (("get", ""), ("post", ""), ("post", "/sync"), ("delete", "/abc")):
            self.assertError(self.call(method, self.url(suffix)), 401, "unauthenticated")
        for token in (self.login(), self.as_coach()):
            self.assertError(self.call("get", self.url(), token), 403, "forbidden")
            self.assertError(self.call("post", self.url(), token, data=self.payload()), 403, "forbidden")

    def test_member_admin_is_forbidden(self):
        token = self.as_role(ClubRole.Roles.MEMBER_ADMIN)
        self.assertError(self.call("get", self.url(), token), 403, "forbidden")

    def test_feature_off_and_foreign_team_are_404(self):
        token = self.as_admin()
        foreign = Team.objects.create(club=make_club(name="Rival FC", slug="rival-fc"), name="Foreign", short_name="F")
        self.assertError(self.call("get", self.url(team=foreign), token), 404, "not_found")
        get_waffle_flag_model().objects.get(name="RBIHF").clubs.clear()
        self.assertError(self.call("get", self.url(), token), 404, "not_found")

    def test_other_club_host_refuses_a_non_member(self):
        token = self.as_admin()
        make_club(name="Rival FC", slug="rival-fc")
        self.assertError(self.call("get", self.url(), token, host=OTHER_HOST), 403, "not_a_member")

    def test_crud(self):
        token = self.as_admin()
        created = self.call("post", self.url(), token, data=self.payload())
        self.assertEqual(created.status_code, 201, created.content)
        source = created.json()
        self.assertEqual((source["source"], source["url"], source["competition_label"], source["season_exists"]), ("RBIHF", RBIHF_URL, "Division 1", True))

        listed = self.call("get", self.url(), token).json()["items"]
        self.assertEqual([item["id"] for item in listed], [source["id"]])
        self.assertEqual(self.call("get", self.url() + f"?season_id={self.season.pk}", token).json()["items"][0]["id"], source["id"])

        again = self.call("post", self.url(), token, data=self.payload(url=RBIHF_URL + "/"))
        self.assertEqual(again.status_code, 201)
        self.assertEqual(len(self.call("get", self.url(), token).json()["items"]), 1)

        patched = self.call("patch", self.url(f"/{source['id']}"), token, data={"url": "https://rbihf.be/league/team/999", "competition_label": "Cup"})
        self.assertEqual(patched.status_code, 200, patched.content)
        self.assertEqual((patched.json()["url"], patched.json()["competition_label"]), ("https://rbihf.be/league/team/999", "Cup"))
        items = self.call("get", self.url(), token).json()["items"]
        self.assertEqual(len(items), 1)
        self.assertNotEqual(items[0]["id"], source["id"])
        self.assertError(self.call("patch", self.url(f"/{source['id']}"), token, data={"url": RBIHF_URL}), 404, "not_found")

        self.assertEqual(self.call("delete", self.url(f"/{items[0]['id']}"), token).status_code, 204)
        self.assertEqual(self.call("get", self.url(), token).json()["items"], [])
        self.assertError(self.call("delete", self.url(f"/{items[0]['id']}"), token), 404, "not_found")

    def test_create_validation(self):
        token = self.as_admin()
        self.assertIn("url", self.call("post", self.url(), token, data=self.payload(url="https://example.com/x")).json()["error"]["fields"])
        self.assertIn("source", self.call("post", self.url(), token, data=self.payload(source="DFEL")).json()["error"]["fields"])
        self.assertError(self.call("post", self.url(), token, data=self.payload(source="NOPE")), 422, "validation_error")
        other = Season.objects.create(club=make_club(name="Rival FC", slug="rival-fc"), start_date=datetime.date.today(), end_date=datetime.date.today() + datetime.timedelta(days=5))
        self.assertError(self.call("post", self.url(), token, data=self.payload(season_id=str(other.pk))), 404, "not_found")
        self.assertEqual(self.team.__class__.objects.get(pk=self.team.pk).import_sources, [])

    def test_patch_bad_url_is_422(self):
        token = self.as_admin()
        source = self.call("post", self.url(), token, data=self.payload()).json()
        response = self.call("patch", self.url(f"/{source['id']}"), token, data={"url": "nonsense"})
        self.assertIn("url", response.json()["error"]["fields"])
        self.assertEqual(self.call("get", self.url(), token).json()["items"][0]["id"], source["id"])

    def test_sync_job_checks_without_changing_games(self):
        token = self.as_admin()
        source = self.call("post", self.url(), token, data=self.payload()).json()
        plan = mock.Mock(to_update=[], to_create=[mock.Mock(fixture=mock.Mock(external_game_id="1", start=timezone.now(), opponent_name="X"))], to_delete=[])
        with (
            mock.patch.object(import_sources, "fetch", return_value="<html>"),
            mock.patch.object(import_sources, "build_plan_for", return_value=plan),
            self.captureOnCommitCallbacks(execute=True),
        ):
            response = self.call("post", self.url("/sync"), token, data={})
        self.assertEqual(response.status_code, 202, response.content)
        job = self.call("get", f"/manage/jobs/{response.json()['id']}", token).json()
        self.assertEqual(job["status"], "succeeded", job)
        self.assertEqual((job["result"]["sources"], job["result"]["new_games"], job["result"]["failed"], job["result"]["created"]), (1, 1, 0, 0))
        self.assertEqual(Event.objects.filter(club=self.club).count(), 0)
        listed = self.call("get", self.url(), token).json()["items"][0]
        self.assertEqual(listed["id"], source["id"])
        self.assertEqual(listed["last_check"]["new_games"], 1)
        self.assertIsNotNone(listed["last_checked_at"])

    def test_sync_job_apply_and_failure(self):
        token = self.as_admin()
        self.call("post", self.url(), token, data=self.payload())
        plan = mock.Mock()
        with (
            mock.patch.object(import_sources, "fetch", return_value="<html>"),
            mock.patch.object(import_sources, "build_plan_for", return_value=plan),
            mock.patch.object(import_sources.rbihf_import, "apply_plan", return_value={"created": 2, "updated": 1, "deleted": 0}) as apply_plan,
            self.captureOnCommitCallbacks(execute=True),
        ):
            response = self.call("post", self.url("/sync"), token, data={"apply": True})
        apply_plan.assert_called_once_with(plan, {}, {})
        result = self.call("get", f"/manage/jobs/{response.json()['id']}", token).json()["result"]
        self.assertEqual((result["created"], result["updated"], result["sources"]), (2, 1, 1))

        with mock.patch.object(import_sources, "fetch", side_effect=import_sources.ImportSourceError("down")), self.captureOnCommitCallbacks(execute=True):
            response = self.call("post", self.url("/sync"), token, data={"apply": True})
        self.assertEqual(self.call("get", f"/manage/jobs/{response.json()['id']}", token).json()["result"]["failed"], 1)

    def test_sync_idempotency_key_starts_one_job(self):
        token = self.as_admin()
        self.call("post", self.url(), token, data=self.payload())
        with mock.patch.object(import_sources, "fetch", return_value="x"), mock.patch.object(import_sources, "build_plan_for", return_value=mock.Mock(to_update=[], to_create=[], to_delete=[])), self.captureOnCommitCallbacks(execute=True):
            first = self.call("post", self.url("/sync"), token, data={}, HTTP_IDEMPOTENCY_KEY="s1")
            second = self.call("post", self.url("/sync"), token, data={}, HTTP_IDEMPOTENCY_KEY="s1")
        self.assertEqual(first.json()["id"], second.json()["id"])
        self.assertEqual(Job.objects.count(), 1)

    def test_sync_without_season_is_409_and_foreign_season_404(self):
        token = self.as_admin()
        other = Season.objects.create(club=make_club(name="Rival FC", slug="rival-fc"), start_date=datetime.date.today(), end_date=datetime.date.today() + datetime.timedelta(days=5))
        self.assertError(self.call("post", self.url("/sync"), token, data={"season_id": str(other.pk)}), 404, "not_found")
