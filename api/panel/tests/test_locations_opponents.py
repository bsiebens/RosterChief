import shutil
import tempfile
import uuid

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils import timezone

from api.protected.tests import CLUB_HOST, AppApiTestCase
from club.models import ClubRole
from events.models import Event, Location, Opponent
from management.tests import ONE_PIXEL_PNG
from rosterchief.test_support import make_club
from teams.models import Position, StaffAssignment, Team

MEDIA = tempfile.mkdtemp(prefix="rc-test-media-")


def png(name="crest.png", content_type="image/png"):
    return SimpleUploadedFile(name, ONE_PIXEL_PNG, content_type=content_type)


class _Base(AppApiTestCase):
    base_url = ""

    def setUp(self):
        super().setUp()
        self.team = Team.objects.create(club=self.club, name="U12", short_name="U12")
        self.manager_pos = Position.objects.create(club=self.club, name="Head coach", short_name="HC", staff_position=True, management_position=True)
        self.coach_pos = Position.objects.create(club=self.club, name="Assistant", short_name="AC", staff_position=True)

    def as_admin(self):
        ClubRole.objects.update_or_create(club=self.club, member=self.member, defaults={"role": ClubRole.Roles.ADMIN})
        return self.login_with_totp()

    def as_position(self, position):
        StaffAssignment.objects.create(team=self.team, member=self.member, season=self.season, position=position)
        return self.login_with_totp()

    def call(self, method, path="", token=None, data=None, host=CLUB_HOST, **headers):
        return self.api(method, f"{self.base_url}{path}", token=token, host=host, data=data, **headers)

    def multipart(self, method, path, token, data, host=CLUB_HOST, **headers):
        return self.client.post(f"/api/v1{self.base_url}{path}", data, HTTP_HOST=host, HTTP_AUTHORIZATION=f"Bearer {token}", **headers)

    def check_gate(self, payload=None, multipart=False):
        send = (lambda m, t: self.multipart(m, "", t, payload)) if multipart else (lambda m, t: self.call(m, token=t, data=payload))
        self.assertError(self.call("get"), 401, "unauthenticated")
        self.assertError(self.call("post", data=payload), 401, "unauthenticated")
        self.assertError(self.call("get", token=self.login()), 403, "forbidden")
        coach = self.as_position(self.coach_pos)
        self.assertError(self.call("get", token=coach), 403, "forbidden")
        self.assertError(send("post", coach), 403, "forbidden")


@override_settings(MEDIA_ROOT=MEDIA)
class PanelLocationTests(_Base):
    base_url = "/manage/locations"

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def payload(self, **kwargs):
        return {"name": "Main pitch", "address": "Kerkstraat 1", "city": "Gent", "zip_code": "9000", "country": "BE", **kwargs}

    def test_gate(self):
        self.check_gate(self.payload())

    def test_coach_manager_allowed(self):
        token = self.as_position(self.manager_pos)
        self.assertEqual(self.call("get", token=token).status_code, 200)
        self.assertEqual(self.call("post", token=token, data=self.payload()).status_code, 201)

    def test_crud(self):
        token = self.as_admin()
        created = self.call("post", token=token, data=self.payload())
        self.assertEqual(created.status_code, 201, created.content)
        body = created.json()
        self.assertEqual((body["name"], body["country"], body["is_home"]), ("Main pitch", "BE", False))
        self.assertEqual(Location.objects.get(pk=body["id"]).club, self.club)

        self.assertEqual(self.call("get", f"/{body['id']}", token=token).json()["city"], "Gent")
        listed = self.call("get", token=token).json()["items"]
        self.assertEqual([item["name"] for item in listed], ["Main pitch"])

        updated = self.call("put", f"/{body['id']}", token=token, data=self.payload(city="Brugge", country="NL"))
        self.assertEqual(updated.status_code, 200, updated.content)
        self.assertEqual((updated.json()["city"], updated.json()["country"]), ("Brugge", "NL"))

        self.assertEqual(self.call("delete", f"/{body['id']}", token=token).status_code, 204)
        self.assertFalse(Location.objects.filter(pk=body["id"]).exists())

    def test_update_keeps_is_home(self):
        token = self.as_admin()
        home = Location.objects.create(club=self.club, name="Home", address="a", city="c", zip_code="1", country="BE", is_home=True)
        response = self.call("put", f"/{home.pk}", token=token, data=self.payload(name="Home ground"))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()["is_home"])

    def test_validation(self):
        token = self.as_admin()
        response = self.call("post", token=token, data=self.payload(country="ZZZ", name=""))
        self.assertError(response, 422, "validation_error")
        self.assertEqual(self.call("post", token=token, data={"name": "x"}).status_code, 422)

    def test_other_club_is_404(self):
        token = self.as_admin()
        other = make_club(name="Rival FC", slug="rival-fc")
        foreign = Location.objects.create(club=other, name="Theirs", address="a", city="c", zip_code="1", country="BE")
        self.assertEqual(self.call("get", token=token).json()["items"], [])
        for method in ("get", "delete"):
            self.assertError(self.call(method, f"/{foreign.pk}", token=token), 404, "not_found")
        self.assertError(self.call("put", f"/{foreign.pk}", token=token, data=self.payload()), 404, "not_found")
        self.assertError(self.call("get", f"/{uuid.uuid4()}", token=token), 404, "not_found")

    def test_create_is_idempotent(self):
        token = self.as_admin()
        first = self.call("post", token=token, data=self.payload(), HTTP_IDEMPOTENCY_KEY="loc-1")
        second = self.call("post", token=token, data=self.payload(), HTTP_IDEMPOTENCY_KEY="loc-1")
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.json()["id"], first.json()["id"])
        self.assertEqual(Location.objects.filter(club=self.club).count(), 1)

    def test_delete_keeps_events(self):
        token = self.as_admin()
        location = Location.objects.create(club=self.club, name="L", address="a", city="c", zip_code="1", country="BE")
        event = Event.objects.create(club=self.club, title="Game", kind="social", start=timezone.now(), location=location)
        self.assertEqual(self.call("delete", f"/{location.pk}", token=token).status_code, 204)
        event.refresh_from_db()
        self.assertIsNone(event.location)

    def test_picker_still_works(self):
        token = self.as_admin()
        Location.objects.create(club=self.club, name="L", address="a", city="c", zip_code="1", country="BE")
        response = self.api("get", "/manage/events/locations", token=token, host=CLUB_HOST)
        self.assertEqual(len(response.json()["items"]), 1)


@override_settings(MEDIA_ROOT=MEDIA)
class PanelOpponentTests(_Base):
    base_url = "/manage/opponents"

    def test_gate(self):
        self.check_gate({"name": "Rivals"}, multipart=True)

    def test_coach_manager_allowed(self):
        token = self.as_position(self.manager_pos)
        self.assertEqual(self.multipart("post", "", token, {"name": "Rivals"}).status_code, 201)

    def test_crud_with_logo(self):
        token = self.as_admin()
        created = self.multipart("post", "", token, {"name": "Rivals", "logo": png()})
        self.assertEqual(created.status_code, 201, created.content)
        body = created.json()
        self.assertTrue(body["logo_url"])
        opponent = Opponent.objects.get(pk=body["id"])
        self.assertEqual(opponent.club, self.club)
        self.addCleanup(opponent.logo.delete, save=False)

        self.assertEqual(self.call("get", f"/{body['id']}", token=token).json()["name"], "Rivals")
        self.assertEqual([o["name"] for o in self.call("get", token=token).json()["items"]], ["Rivals"])

        renamed = self.multipart("post", f"/{body['id']}", token, {"name": "Rivals B"})
        self.assertEqual(renamed.status_code, 200, renamed.content)
        self.assertEqual(renamed.json()["name"], "Rivals B")
        self.assertTrue(renamed.json()["logo_url"])

        cleared = self.multipart("post", f"/{body['id']}", token, {"name": "Rivals B", "clear_logo": "true"})
        self.assertEqual(cleared.status_code, 200, cleared.content)
        self.assertIsNone(cleared.json()["logo_url"])

        self.assertEqual(self.call("delete", f"/{body['id']}", token=token).status_code, 204)
        self.assertFalse(Opponent.objects.filter(pk=body["id"]).exists())

    def test_upload_limits(self):
        token = self.as_admin()
        self.assertError(self.multipart("post", "", token, {"name": "X", "logo": png("c.txt", "text/plain")}), 415, "unsupported_media_type")
        with override_settings(API_UPLOAD_MAX_BYTES=10):
            self.assertError(self.multipart("post", "", token, {"name": "X", "logo": png()}), 413, "file_too_large")
        fake = SimpleUploadedFile("c.png", b"not an image", content_type="image/png")
        self.assertError(self.multipart("post", "", token, {"name": "X", "logo": fake}), 422, "validation_error")
        self.assertError(self.multipart("post", "", token, {"name": ""}), 422, "validation_error")
        self.assertFalse(Opponent.objects.exists())

    def test_other_club_is_404(self):
        token = self.as_admin()
        other = make_club(name="Rival FC", slug="rival-fc")
        foreign = Opponent.objects.create(club=other, name="Theirs")
        self.assertEqual(self.call("get", token=token).json()["items"], [])
        self.assertError(self.call("get", f"/{foreign.pk}", token=token), 404, "not_found")
        self.assertError(self.call("delete", f"/{foreign.pk}", token=token), 404, "not_found")
        self.assertError(self.multipart("post", f"/{foreign.pk}", token, {"name": "x"}), 404, "not_found")

    def test_create_is_idempotent(self):
        token = self.as_admin()
        first = self.multipart("post", "", token, {"name": "Once"}, HTTP_IDEMPOTENCY_KEY="opp-1")
        second = self.multipart("post", "", token, {"name": "Once"}, HTTP_IDEMPOTENCY_KEY="opp-1")
        self.assertEqual(second.json()["id"], first.json()["id"])
        self.assertEqual(Opponent.objects.filter(club=self.club).count(), 1)

    def test_picker_still_works(self):
        token = self.as_admin()
        Opponent.objects.create(club=self.club, name="O")
        self.assertEqual(len(self.api("get", "/manage/events/opponents", token=token, host=CLUB_HOST).json()["items"]), 1)
