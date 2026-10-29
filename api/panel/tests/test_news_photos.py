import shutil
import tempfile
import uuid

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils import timezone

from api.protected.tests import CLUB_HOST, AppApiTestCase
from club.models import ClubRole
from management.tests import ONE_PIXEL_PNG
from news.models import News, NewsPhoto
from rosterchief.test_support import make_club
from teams.models import Position, StaffAssignment, Team

MEDIA = tempfile.mkdtemp(prefix="rc-test-media-")


def png(name="a.png", content_type="image/png", content=ONE_PIXEL_PNG):
    return SimpleUploadedFile(name, content, content_type=content_type)


@override_settings(MEDIA_ROOT=MEDIA)
class PanelNewsPhotoTests(AppApiTestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def setUp(self):
        super().setUp()
        self.item = News.objects.create(club=self.club, title="Hello", body="Body")

    def as_role(self, role):
        ClubRole.objects.update_or_create(club=self.club, member=self.member, defaults={"role": role})
        return self.login_with_totp()

    def url(self, path="", news=None):
        return f"/manage/news/{(news or self.item).pk}/photos{path}"

    def upload(self, token, files, news=None, host=CLUB_HOST):
        return self.client.post(f"/api/v1{self.url(news=news)}", {"photos": files}, HTTP_HOST=host, HTTP_AUTHORIZATION=f"Bearer {token}")

    def call(self, method, path="", token=None, data=None, news=None):
        return self.api(method, self.url(path, news), token=token, host=CLUB_HOST, data=data)

    def photo(self, **kwargs):
        photo = NewsPhoto.objects.create(news_item=self.item, image="news/x.png", **kwargs)
        return photo

    # --- auth ---------------------------------------------------------------

    def test_requires_token(self):
        self.assertError(self.call("get"), 401, "unauthenticated")
        self.assertError(self.call("post"), 401, "unauthenticated")

    def test_plain_member_is_forbidden(self):
        token = self.login()
        self.assertError(self.call("get", token=token), 403, "forbidden")
        self.assertError(self.upload(token, [png()]), 403, "forbidden")

    def test_editing_rule_follows_status(self):
        """A draft is open to a coach manager; once published only an editor/admin may touch it (can_edit_news)."""
        head = Position.objects.create(club=self.club, name="Head coach", short_name="HC", staff_position=True, management_position=True)
        team = Team.objects.create(club=self.club, name="U12", short_name="U12")
        StaffAssignment.objects.create(team=team, member=self.member, season=self.season, position=head)
        token = self.login_with_totp()
        self.assertEqual(self.call("get", token=token).status_code, 200)
        News.objects.filter(pk=self.item.pk).update(status=News.Status.PUBLISHED, published_at=timezone.now())
        self.assertError(self.call("get", token=token), 403, "forbidden")
        ClubRole.objects.update_or_create(club=self.club, member=self.member, defaults={"role": ClubRole.Roles.EDITOR})
        self.assertEqual(self.call("get", token=token).status_code, 200)

    def test_other_club_is_404(self):
        token = self.as_role(ClubRole.Roles.ADMIN)
        other = make_club(name="Rival FC", slug="rival-fc")
        foreign = News.objects.create(club=other, title="Secret", body="x")
        self.assertError(self.call("get", token=token, news=foreign), 404, "not_found")
        self.assertError(self.upload(token, [png()], news=foreign), 404, "not_found")
        self.assertError(self.call("get", token=token, news=type("N", (), {"pk": uuid.uuid4()})), 404, "not_found")

    # --- upload -------------------------------------------------------------

    def test_upload_first_becomes_main(self):
        token = self.as_role(ClubRole.Roles.EDITOR)
        response = self.upload(token, [png("a.png"), png("b.png")])
        self.assertEqual(response.status_code, 201, response.content)
        items = response.json()["items"]
        self.assertEqual([i["is_main"] for i in items], [True, False])
        self.assertEqual(self.item.photos.count(), 2)
        for photo in self.item.photos.all():
            self.addCleanup(photo.image.delete, save=False)
        again = self.upload(token, [png("c.png")]).json()["items"]
        self.assertFalse(again[0]["is_main"])
        for photo in self.item.photos.all():
            self.addCleanup(photo.image.delete, save=False)
        self.assertEqual(len(self.call("get", token=token).json()["items"]), 3)
        self.assertEqual(len(self.api("get", f"/manage/news/{self.item.pk}", token=token, host=CLUB_HOST).json()["photos"]), 3)

    def test_upload_validation(self):
        token = self.as_role(ClubRole.Roles.EDITOR)
        self.assertError(self.upload(token, [png("a.txt", "text/plain")]), 415, "unsupported_media_type")
        self.assertError(self.upload(token, [png("a.png", content=b"nope")]), 422, "validation_error")
        self.assertError(self.upload(token, [png("a.png", content=b"")]), 422, "validation_error")
        response = self.client.post(f"/api/v1{self.url()}", {}, HTTP_HOST=CLUB_HOST, HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertError(response, 422, "validation_error")
        self.assertEqual(self.item.photos.count(), 0)

    # --- main / focal point / delete ----------------------------------------

    def test_set_main(self):
        token = self.as_role(ClubRole.Roles.EDITOR)
        first, second = self.photo(is_main=True), self.photo()
        response = self.call("post", f"/{second.pk}/set-main", token=token)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()["is_main"])
        first.refresh_from_db()
        self.assertFalse(first.is_main)

    def test_focal_point_is_clamped(self):
        token = self.as_role(ClubRole.Roles.EDITOR)
        photo = self.photo()
        response = self.call("put", f"/{photo.pk}/focal-point", token=token, data={"focal_x": 20, "focal_y": 250})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual((response.json()["focal_x"], response.json()["focal_y"]), (20, 100))
        self.assertEqual(self.call("put", f"/{photo.pk}/focal-point", token=token, data={"focal_x": "a"}).status_code, 422)

    def test_delete_promotes_next_photo(self):
        token = self.as_role(ClubRole.Roles.EDITOR)
        main, other = self.photo(is_main=True), self.photo()
        self.assertEqual(self.call("delete", f"/{main.pk}", token=token).status_code, 204)
        other.refresh_from_db()
        self.assertTrue(other.is_main)
        self.assertEqual(self.call("delete", f"/{other.pk}", token=token).status_code, 204)
        self.assertEqual(self.item.photos.count(), 0)

    def test_photo_of_another_item_is_404(self):
        token = self.as_role(ClubRole.Roles.EDITOR)
        elsewhere = News.objects.create(club=self.club, title="Other", body="x")
        photo = NewsPhoto.objects.create(news_item=elsewhere, image="news/y.png")
        self.assertError(self.call("post", f"/{photo.pk}/set-main", token=token), 404, "not_found")
        self.assertError(self.call("delete", f"/{photo.pk}", token=token), 404, "not_found")
        self.assertError(self.call("put", f"/{photo.pk}/focal-point", token=token, data={"focal_x": 1, "focal_y": 1}), 404, "not_found")
