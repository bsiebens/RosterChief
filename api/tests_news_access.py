"""News through the REAL auth path (no mocked management door), /news/team-options and the public feed."""

import datetime
import json

from django.test import Client, TestCase
from django.utils import timezone

from controlpanel.tests import login_session, make_user
from news.models import News
from news.tests import NewsClubMixin

BASE = "/api/v1"


class MemberNewsAccessTests(NewsClubMixin, TestCase):
    def call(self, method, path, user=None, data=None, host=None):
        headers = {"HTTP_HOST": host or self.host}
        if user is not None:
            headers["HTTP_X_SESSION_TOKEN"] = login_session(user)
        body = {"data": json.dumps(data), "content_type": "application/json"} if data is not None else {}
        return getattr(Client(), method)(BASE + path, **body, **headers)

    def draft(self, user, title="Mine"):
        r = self.call("post", "/news", user, {"title": title, "body": "Body", "visibility": "internal"})
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()

    def test_publisher_cycle_through_the_real_door(self):
        item = self.draft(self.owner)
        nid = item["id"]
        self.assertEqual(self.call("get", "/news?mine=true", self.owner).json()["count"], 1)
        r = self.call("patch", f"/news/{nid}", self.owner, {"title": "Edited"})
        self.assertEqual((r.status_code, r.json()["title"]), (200, "Edited"))
        r = self.call("post", f"/news/{nid}/publish", self.owner, {"notify": False})
        self.assertEqual((r.status_code, r.json()["status"]), (200, "published"))

    def test_team_options(self):
        r = self.call("get", "/news/team-options", self.owner)
        self.assertEqual(r.status_code, 200)
        self.assertEqual([(t["name"], t["short_name"]) for t in r.json()], [("Under 12", "U12"), ("Under 14", "U14")])
        self.assertEqual(set(r.json()[0]), {"id", "name", "short_name"})
        self.assertEqual(self.call("get", "/news/team-options").status_code, 401)

    def test_plain_members_stay_out_with_the_management_door(self):
        for path in ("/news", "/news/team-options", "/me"):
            r = self.call("get", path, self.ann_user)
            self.assertEqual((r.status_code, r.json().get("code")), (403, "no_access"), path)
        self.assertEqual(self.call("post", "/news", self.ann_user, {"title": "x", "body": "y"}).status_code, 403)

    def test_login_without_member_record_and_anonymous(self):
        stranger = make_user("stranger@news.test")
        self.club.add_user(stranger)
        for path in ("/news", "/news/team-options"):
            r = self.call("get", path, stranger)
            self.assertEqual((r.status_code, r.json().get("code")), (403, "no_access"), path)
        self.assertEqual(self.call("get", "/news").status_code, 401)
        self.assertEqual(self.call("post", "/news", None, {"title": "x", "body": "y"}).status_code, 401)
        outsider = make_user("outsider@news.test")
        self.assertIn(self.call("get", "/news", outsider).status_code, (401, 403))


class PublicNewsTests(NewsClubMixin, TestCase):
    def get(self, path, host=None):
        return Client().get(BASE + "/public/news" + path, HTTP_HOST=host or self.host)

    def test_list_and_item(self):
        both = self.news("Both", visibility="both", title_en="Both EN", body="Dutch **text**", teams=[self.team])
        self.news("External", visibility="external", published_at=timezone.now() - datetime.timedelta(hours=2))
        self.news("Internal", visibility="internal")
        self.news("Scheduled", visibility="both", published_at=timezone.now() + datetime.timedelta(days=1))
        self.news("Draft", visibility="both", status=News.Status.DRAFT)
        r = self.get("")
        self.assertEqual(r.status_code, 200, r.content)
        data = r.json()
        self.assertEqual((data["count"], data["limit"], data["offset"]), (2, 20, 0))
        self.assertEqual([i["slug"] for i in data["results"]], ["both", "external"])
        first = data["results"][0]
        self.assertEqual(first["teams"], ["Under 12"])
        self.assertEqual(first["translations"]["en"]["title"], "Both EN")
        self.assertEqual(first["translations"]["nl"]["title"], "Both")
        self.assertIn("<strong>text</strong>", first["translations"]["nl"]["body"])
        self.assertEqual(first["translations"]["en"]["body"], first["translations"]["nl"]["body"])  # untranslated: falls back
        self.assertEqual(first["translations"]["nl"]["excerpt"], "Dutch text")
        self.assertEqual(len(self.get("?limit=1&offset=1").json()["results"]), 1)
        self.assertEqual(self.get("?limit=0").status_code, 422)
        item = self.get("/" + both.slug)
        self.assertEqual((item.status_code, item.json()["id"]), (200, str(both.pk)))
        self.assertEqual(self.get("/internal").status_code, 404)
        self.assertEqual(self.get("/scheduled").status_code, 404)
        self.assertEqual(self.get("/draft").status_code, 404)

    def test_photos_are_absolute_with_focal_point(self):
        from news.models import NewsPhoto

        item = self.news("Pic", visibility="external")
        NewsPhoto.objects.create(news_item=item, image="news/x/p.png", is_main=True, focal_x=20, focal_y=80)
        (photo,) = self.get("/pic").json()["photos"]
        self.assertTrue(photo["url"].startswith("http://"))
        self.assertEqual((photo["focal_x"], photo["focal_y"], photo["object_position"], photo["is_main"]), (20, 80, "20% 80%", True))

    def test_platform_host_has_no_news(self):
        self.assertEqual(self.get("", host="testserver").status_code, 404)
