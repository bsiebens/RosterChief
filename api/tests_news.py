"""News API (``/news``): members write drafts and see their own, publishers see everything and publish."""

import datetime
import io
import json
import shutil
import tempfile
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from django_tenants.utils import schema_context
from PIL import Image

from club.models import Club, Domain
from club.services import create_club, grant_login
from controlpanel.tests import login_session, make_user
from members.models import Member
from news.models import News, NewsPhoto
from news.services import render_body_html
from teams.models import Team

BASE = "/api/v1"
MEDIA = tempfile.mkdtemp()


def image_file(fmt="PNG", name="p.png"):
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), "#336699").save(buf, format=fmt)
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/png")


@override_settings(MEDIA_ROOT=MEDIA)
class NewsApiTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls._mfa = mock.patch("authorization.middleware.is_local_development", return_value=True)
        cls._mfa.start()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        connection.set_schema_to_public()
        cls._mfa.stop()
        shutil.rmtree(MEDIA, ignore_errors=True)

    @classmethod
    def setUpTestData(cls):
        cls.owner = make_user("owner@news.test", first="Olivia", last="Owner")
        public = Club(schema_name="public", name="Platform", owner=cls.owner)
        public.auto_create_schema = False
        public.save()
        Domain.objects.create(domain="testserver", tenant=public, is_primary=True)
        cls.club = create_club("News FC", cls.owner)
        cls.host = Domain.objects.get(tenant=cls.club, is_primary=True).domain
        with schema_context(cls.club.schema_name):
            cls.team = Team.objects.create(name="U12", short_name="U12")
            cls.ann = Member.objects.create(first_name="Ann", last_name="Author", email="ann@news.test")
            cls.bob = Member.objects.create(first_name="Bob", last_name="Other", email="bob@news.test")
        cls.ann_user, _ = grant_login(cls.club, cls.ann)
        cls.bob_user, _ = grant_login(cls.club, cls.bob)
        cls.owner = type(cls.owner).objects.get(pk=cls.owner.pk)

    def setUp(self):
        door = mock.patch("members.services.access.can_access_management", return_value=True)
        door.start()
        self.addCleanup(door.stop)

    def call(self, method, path, user, data=None, files=None):
        headers = {"HTTP_HOST": self.host, "HTTP_X_SESSION_TOKEN": login_session(user)}
        if files is not None:
            return getattr(Client(), method)(BASE + path, data=files, **headers)
        body = {"data": json.dumps(data), "content_type": "application/json"} if data is not None else {}
        return getattr(Client(), method)(BASE + path, **body, **headers)

    def write(self, user, title="Big win", **extra):
        r = self.call("post", "/news", user, {"title": title, "body": "Hello **world**", "visibility": "both", "team_ids": [str(self.team.pk)], **extra})
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()

    def test_member_creates_a_draft(self):
        item = self.write(self.ann_user)
        self.assertEqual((item["status"], item["slug"], item["created_by"]["name"], item["can_edit"], item["can_publish"], item["can_delete"]), ("draft", "big-win", "Ann Author", True, False, True))
        self.assertEqual(item["team_ids"], [str(self.team.pk)])
        self.assertIn("<strong>world</strong>", item["body_html"])
        # a status in the body does not make it published
        self.assertEqual(self.write(self.ann_user, status="published")["status"], "draft")
        self.assertEqual(self.call("post", "/news", self.ann_user, {"title": " ", "body": "x"}).status_code, 422)

    def test_slug_is_unique(self):
        slugs = [self.write(self.ann_user)["slug"] for _ in range(3)]
        self.assertEqual(slugs, ["big-win", "big-win-2", "big-win-3"])

    def test_own_versus_all_visibility(self):
        mine = self.write(self.ann_user, "Mine")
        theirs = self.write(self.bob_user, "Theirs")
        titles = lambda user, q="": sorted(i["title"] for i in self.call("get", "/news" + q, user).json()["results"])  # noqa: E731
        self.assertEqual(titles(self.ann_user), ["Mine"])
        self.assertEqual(titles(self.owner), ["Mine", "Theirs"])
        self.assertEqual(titles(self.owner, "?mine=true"), [])
        self.assertEqual(titles(self.owner, "?q=thei&status=draft"), ["Theirs"])
        listing = self.call("get", "/news", self.owner).json()
        self.assertEqual(listing["count"], 2)
        self.assertEqual(listing["results"][0]["teams"], [{"id": str(self.team.pk), "name": self.team.name}])
        self.assertEqual(self.call("get", f"/news/{theirs['id']}", self.ann_user).status_code, 404)
        self.assertEqual(self.call("patch", f"/news/{theirs['id']}", self.ann_user, {"title": "x"}).status_code, 404)
        self.assertEqual(self.call("delete", f"/news/{theirs['id']}", self.ann_user).status_code, 404)
        self.assertEqual(self.call("get", f"/news/{mine['id']}", self.ann_user).status_code, 200)
        # once published (and live) everyone sees it, but only its author and publishers may change it
        with mock.patch("news.services.notifications.dispatch_publish_notification"), self.captureOnCommitCallbacks(execute=True):
            self.call("post", f"/news/{theirs['id']}/publish", self.owner, {"notify": False})
        self.assertEqual(titles(self.ann_user), ["Mine", "Theirs"])
        self.assertEqual(self.call("get", f"/news/{theirs['id']}", self.ann_user).status_code, 200)
        self.assertEqual(self.call("get", f"/news/{theirs['id']}", self.ann_user).json()["can_edit"], False)
        self.assertEqual(self.call("patch", f"/news/{theirs['id']}", self.ann_user, {"title": "x"}).status_code, 403)
        self.assertEqual(self.call("delete", f"/news/{theirs['id']}", self.ann_user).status_code, 403)
        self.assertEqual(titles(self.ann_user, "?live=true"), ["Theirs"])
        # a publisher edits someone else's item
        r = self.call("patch", f"/news/{theirs['id']}", self.owner, {"title": "Edited", "team_ids": []})
        self.assertEqual((r.status_code, r.json()["title"], r.json()["team_ids"]), (200, "Edited", []))

    def test_review_workflow_and_lock(self):
        item = self.write(self.ann_user)
        nid = item["id"]
        self.assertEqual(self.call("post", f"/news/{nid}/publish", self.ann_user, {}).status_code, 403)
        self.assertEqual(
            self.call(
                "post",
                f"/news/{nid}/send-back",
                self.owner,
            ).status_code,
            422,
        )
        with mock.patch("news.services.notifications.dispatch_notify_editors_of_pending_review") as hook, self.captureOnCommitCallbacks(execute=True):
            r = self.call("post", f"/news/{nid}/submit-for-review", self.ann_user)
        self.assertEqual((r.status_code, r.json()["status"]), (200, "pending_review"))
        hook.assert_called_once()
        self.assertEqual(self.call("post", f"/news/{nid}/submit-for-review", self.ann_user).status_code, 422)
        self.assertEqual(self.call("patch", f"/news/{nid}", self.ann_user, {"title": "Still editable"}).status_code, 200)
        self.assertEqual(self.call("post", f"/news/{nid}/send-back", self.ann_user).status_code, 403)
        with mock.patch("news.services.notifications.notify_author_of_send_back") as told, self.captureOnCommitCallbacks(execute=True):
            back = self.call("post", f"/news/{nid}/send-back?note=Add a photo, please", self.owner).json()
        self.assertEqual((back["status"], back["review_note"]), ("draft", "Add a photo, please"))
        self.assertIsNotNone(back["sent_back_at"])
        told.assert_called_once()
        self.assertEqual(self.call("get", f"/news/{nid}", self.ann_user).json()["review_note"], "Add a photo, please")  # the author sees why
        resubmitted = self.call("post", f"/news/{nid}/submit-for-review", self.ann_user).json()
        self.assertEqual(self.call("get", f"/news/{nid}", self.ann_user).json()["review_note"], "")  # cleared once it is submitted again
        # approve = publish
        with mock.patch("news.services.notifications.dispatch_publish_notification") as hook, self.captureOnCommitCallbacks(execute=True):
            r = self.call("post", f"/news/{nid}/publish", self.owner, {})
        self.assertEqual((r.status_code, r.json()["status"], r.json()["is_scheduled"]), (200, "published", False))
        hook.assert_called_once()
        # locked for the author, still visible
        self.assertEqual(self.call("get", f"/news/{nid}", self.ann_user).json()["can_edit"], False)
        self.assertEqual(self.call("patch", f"/news/{nid}", self.ann_user, {"title": "x"}).status_code, 403)
        self.assertEqual(self.call("delete", f"/news/{nid}", self.ann_user).status_code, 403)
        self.assertEqual(self.call("post", f"/news/{nid}/photos", self.ann_user, files={"image": image_file()}).status_code, 403)
        self.assertEqual(self.call("post", f"/news/{nid}/unpublish", self.ann_user).status_code, 403)
        # unpublish returns it to draft, editable again
        r = self.call("post", f"/news/{nid}/unpublish", self.owner)
        self.assertEqual((r.json()["status"], r.json()["published_at"]), ("draft", None))
        self.assertEqual(self.call("patch", f"/news/{nid}", self.ann_user, {"title": "Again"}).status_code, 200)
        self.assertEqual(self.call("post", f"/news/{nid}/unpublish", self.owner).status_code, 422)

    def test_schedule_and_silent_publish(self):
        nid = self.write(self.ann_user)["id"]
        later = (timezone.now() + datetime.timedelta(days=2)).replace(microsecond=0)
        with mock.patch("news.services.notifications.dispatch_publish_notification") as hook, self.captureOnCommitCallbacks(execute=True):
            r = self.call("post", f"/news/{nid}/publish", self.owner, {"published_at": later.isoformat()})
        self.assertEqual((r.json()["status"], r.json()["is_scheduled"], r.json()["notified_at"]), ("published", True, None))
        hook.assert_not_called()  # the sweep sends it when due
        with mock.patch("news.services.notifications.dispatch_publish_notification") as hook, self.captureOnCommitCallbacks(execute=True):
            r = self.call("post", f"/news/{nid}/publish", self.owner, {"notify": False})
        self.assertEqual((r.json()["is_scheduled"], r.json()["notified_at"] is not None), (False, True))
        hook.assert_not_called()

    def test_live_filter_orders_by_publish_date_and_skips_scheduled(self):
        now = timezone.now()
        ids = {name: self.write(self.ann_user, name)["id"] for name in ("Older", "Newer", "Later", "Draft")}
        with mock.patch("news.services.notifications.dispatch_publish_notification"), self.captureOnCommitCallbacks(execute=True):
            for name, at in (("Older", now), ("Newer", now), ("Later", now + datetime.timedelta(days=2))):
                self.call("post", f"/news/{ids[name]}/publish", self.owner, {"published_at": at.isoformat(), "notify": False})
        r = self.call("get", "/news?live=true", self.owner).json()
        self.assertEqual([i["title"] for i in r["results"]], ["Newer", "Older"])  # newest publish date first (a past date means "publish now")
        self.assertEqual(r["count"], 2)

    def test_delete_own_draft(self):
        nid = self.write(self.ann_user)["id"]
        self.assertEqual(self.call("delete", f"/news/{nid}", self.ann_user).status_code, 204)
        with schema_context(self.club.schema_name):
            self.assertFalse(News.objects.exists())

    def test_photos(self):
        nid = self.write(self.ann_user)["id"]
        first = self.call("post", f"/news/{nid}/photos", self.ann_user, files={"image": image_file()})
        self.assertEqual(first.status_code, 201, first.content)
        second = self.call("post", f"/news/{nid}/photos", self.ann_user, files={"image": image_file()}).json()
        first = first.json()
        self.assertEqual((first["is_main"], second["is_main"], second["ordering"]), (True, False, 1))
        self.assertTrue(first["url"].endswith(".png"))
        detail = self.call("get", f"/news/{nid}", self.ann_user).json()
        self.assertEqual(detail["main_photo_url"], first["url"])
        # someone else cannot touch them
        other = self.call("post", f"/news/{nid}/photos", self.bob_user, files={"image": image_file()})
        self.assertEqual(other.status_code, 404)
        # single main
        r = self.call("post", f"/news/{nid}/photos/{second['id']}/set-main", self.ann_user)
        self.assertEqual(sorted((p["id"], p["is_main"]) for p in r.json()["photos"]), sorted([(first["id"], False), (second["id"], True)]))
        with schema_context(self.club.schema_name):
            self.assertEqual(NewsPhoto.objects.filter(is_main=True).count(), 1)
        # focal point
        r = self.call("patch", f"/news/{nid}/photos/{second['id']}", self.ann_user, {"focal_x": 10, "focal_y": 90})
        self.assertEqual((r.json()["focal_x"], r.json()["focal_y"]), (10, 90))
        self.assertEqual(self.call("patch", f"/news/{nid}/photos/{second['id']}", self.ann_user, {"focal_x": 101, "focal_y": 0}).status_code, 422)
        # deleting the main one promotes the other
        self.assertEqual(self.call("delete", f"/news/{nid}/photos/{second['id']}", self.ann_user).status_code, 204)
        photos = self.call("get", f"/news/{nid}", self.ann_user).json()["photos"]
        self.assertEqual([(p["id"], p["is_main"]) for p in photos], [(first["id"], True)])
        # not an image
        bad = SimpleUploadedFile("x.png", b"not an image", content_type="image/png")
        self.assertEqual(self.call("post", f"/news/{nid}/photos", self.ann_user, files={"image": bad}).status_code, 422)
        gif = self.call("post", f"/news/{nid}/photos", self.ann_user, files={"image": image_file("GIF", "x.gif")})
        self.assertEqual(gif.status_code, 422)

    def test_me_flags(self):
        me = lambda user: self.call("get", "/me", user).json()  # noqa: E731
        self.assertEqual((me(self.owner)["can_publish_news"], me(self.owner)["can_add_news"]), (True, True))
        self.assertEqual((me(self.ann_user)["can_publish_news"], me(self.ann_user)["can_add_news"]), (False, True))

    def test_render_body_is_sanitized(self):
        html = render_body_html("hi <script>alert(1)</script> [x](javascript:alert(1))")
        self.assertNotIn("<script", html)
        self.assertNotIn("javascript:", html)
