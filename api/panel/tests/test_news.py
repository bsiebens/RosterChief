import datetime
import uuid
from unittest import mock

from django.utils import timezone

from api.protected.tests import CLUB_HOST, OTHER_HOST, AppApiTestCase
from club.models import ClubRole
from news.models import News
from rosterchief.test_support import make_club
from teams.models import Team

BASE_URL = "/manage/news"


class PanelNewsTests(AppApiTestCase):
    def setUp(self):
        super().setUp()
        self.team = Team.objects.create(club=self.club, name="U12", short_name="U12")

    def as_role(self, role):
        ClubRole.objects.update_or_create(club=self.club, member=self.member, defaults={"role": role})
        return self.login_with_totp()

    def call(self, method, path="", token=None, data=None, host=CLUB_HOST, **headers):
        return self.api(method, f"{BASE_URL}{path}", token=token, host=host, data=data, **headers)

    def item(self, **kwargs):
        defaults = {"club": self.club, "title": "Hello", "body": "Body **bold**"}
        return News.objects.create(**{**defaults, **kwargs})

    def payload(self, **kwargs):
        return {"title": "Season kickoff", "body": "See you there", "teams": [str(self.team.pk)], "visibility": "both", **kwargs}

    # --- auth ---------------------------------------------------------------

    def test_requires_token(self):
        self.assertError(self.call("get"), 401, "unauthenticated")
        self.assertError(self.call("post", data=self.payload()), 401, "unauthenticated")

    def test_plain_member_is_forbidden(self):
        token = self.login()
        self.assertError(self.call("get", token=token), 403, "forbidden")
        self.assertError(self.call("post", token=token, data=self.payload()), 403, "forbidden")

    def test_other_club_host_is_refused(self):
        other = make_club(name="Rival FC", slug="rival-fc")
        self.assertEqual(other.slug, "rival-fc")
        token = self.as_role(ClubRole.Roles.ADMIN)
        self.assertEqual(self.call("get", token=token, host=OTHER_HOST).status_code, 403)

    # --- read ---------------------------------------------------------------

    def test_list_filters_and_paginates(self):
        token = self.as_role(ClubRole.Roles.EDITOR)
        now = timezone.now()
        self.item(title="Draft")
        self.item(title="Pending", status=News.Status.PENDING_REVIEW)
        self.item(title="Live", status=News.Status.PUBLISHED, published_at=now - datetime.timedelta(days=1))
        self.item(title="Soon", status=News.Status.PUBLISHED, published_at=now + datetime.timedelta(days=1))

        titles = lambda response: sorted(item["title"] for item in response.json()["items"])  # noqa: E731
        self.assertEqual(len(self.call("get", token=token).json()["items"]), 4)
        self.assertEqual(titles(self.call("get", "?status=draft", token=token)), ["Draft", "Pending"])
        self.assertEqual(titles(self.call("get", "?status=published", token=token)), ["Live"])
        self.assertEqual(titles(self.call("get", "?status=scheduled", token=token)), ["Soon"])
        self.assertError(self.call("get", "?status=bogus", token=token), 422, "validation_error")

        first = self.call("get", "?limit=3", token=token).json()
        self.assertEqual(len(first["items"]), 3)
        second = self.call("get", f"?limit=3&cursor={first['next_cursor']}", token=token).json()
        self.assertEqual(len(second["items"]), 1)
        self.assertIsNone(second["next_cursor"])

        counts = self.call("get", "/counts", token=token).json()
        self.assertEqual(counts, {"all": 4, "draft": 2, "scheduled": 1, "published": 1})

    def test_list_excludes_other_clubs(self):
        token = self.as_role(ClubRole.Roles.ADMIN)
        other = make_club(name="Rival FC", slug="rival-fc")
        News.objects.create(club=other, title="Secret", body="x")
        self.assertEqual(self.call("get", token=token).json()["items"], [])

    def test_detail_and_404(self):
        token = self.as_role(ClubRole.Roles.EDITOR)
        item = self.item()
        body = self.call("get", f"/{item.pk}", token=token).json()
        self.assertEqual(body["title"], "Hello")
        self.assertIn("<strong>bold</strong>", body["body_html"])
        self.assertTrue(body["can_edit"])
        self.assertTrue(body["can_publish"])

        other = make_club(name="Rival FC", slug="rival-fc")
        foreign = News.objects.create(club=other, title="Secret", body="x")
        self.assertError(self.call("get", f"/{foreign.pk}", token=token), 404, "not_found")
        self.assertError(self.call("get", f"/{uuid.uuid4()}", token=token), 404, "not_found")

    # --- write --------------------------------------------------------------

    def test_create_draft(self):
        token = self.as_role(ClubRole.Roles.EDITOR)
        response = self.call("post", token=token, data=self.payload())
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual(body["status"], "draft")
        self.assertEqual(body["visibility"], "both")
        self.assertEqual([team["name"] for team in body["teams"]], ["U12"])
        item = News.objects.get(pk=body["id"])
        self.assertEqual(item.club, self.club)
        self.assertEqual(item.created_by, self.member)

    def test_create_validation_and_foreign_team(self):
        token = self.as_role(ClubRole.Roles.EDITOR)
        response = self.call("post", token=token, data=self.payload(title=""))
        self.assertError(response, 422, "validation_error")
        self.assertIn("title", response.json()["error"]["fields"])

        other = make_club(name="Rival FC", slug="rival-fc")
        foreign_team = Team.objects.create(club=other, name="X", short_name="X")
        response = self.call("post", token=token, data=self.payload(teams=[str(foreign_team.pk)]))
        self.assertError(response, 422, "validation_error")
        self.assertIn("teams", response.json()["error"]["fields"])

    def test_create_is_idempotent(self):
        token = self.as_role(ClubRole.Roles.EDITOR)
        first = self.call("post", token=token, data=self.payload(), HTTP_IDEMPOTENCY_KEY="k1")
        second = self.call("post", token=token, data=self.payload(), HTTP_IDEMPOTENCY_KEY="k1")
        self.assertEqual(first.json()["id"], second.json()["id"])
        self.assertEqual(News.objects.count(), 1)

    def test_update(self):
        token = self.as_role(ClubRole.Roles.EDITOR)
        item = self.item()
        response = self.call("put", f"/{item.pk}", token=token, data=self.payload(title="Renamed"))
        self.assertEqual(response.status_code, 200, response.content)
        item.refresh_from_db()
        self.assertEqual(item.title, "Renamed")
        self.assertEqual(item.status, News.Status.DRAFT)
        self.assertError(self.call("put", f"/{item.pk}", token=token, data=self.payload(body="")), 422, "validation_error")

    def test_published_item_only_editable_by_publishers(self):
        item = self.item(status=News.Status.PUBLISHED, published_at=timezone.now())
        token = self.as_role(ClubRole.Roles.ADMIN)
        self.assertEqual(self.call("put", f"/{item.pk}", token=token, data=self.payload()).status_code, 200)

    def test_staff_without_news_rights_is_forbidden(self):
        """A member admin has management access but may neither author nor publish news."""
        token = self.as_role(ClubRole.Roles.MEMBER_ADMIN)
        item = self.item()
        self.assertError(self.call("post", token=token, data=self.payload()), 403, "forbidden")
        self.assertError(self.call("put", f"/{item.pk}", token=token, data=self.payload()), 403, "forbidden")
        self.assertError(self.call("delete", f"/{item.pk}", token=token), 403, "forbidden")
        self.assertError(self.call("post", f"/{item.pk}/publish", token=token, data={}), 403, "forbidden")
        self.assertError(self.call("post", f"/{item.pk}/unpublish", token=token), 403, "forbidden")

    def test_write_other_club_is_404(self):
        token = self.as_role(ClubRole.Roles.ADMIN)
        other = make_club(name="Rival FC", slug="rival-fc")
        foreign = News.objects.create(club=other, title="Secret", body="x")
        self.assertError(self.call("put", f"/{foreign.pk}", token=token, data=self.payload()), 404, "not_found")
        self.assertError(self.call("delete", f"/{foreign.pk}", token=token), 404, "not_found")
        self.assertError(self.call("post", f"/{foreign.pk}/publish", token=token, data={}), 404, "not_found")
        self.assertTrue(News.objects.filter(pk=foreign.pk).exists())

    def test_delete(self):
        token = self.as_role(ClubRole.Roles.EDITOR)
        item = self.item()
        self.assertEqual(self.call("delete", f"/{item.pk}", token=token).status_code, 204)
        self.assertFalse(News.objects.filter(pk=item.pk).exists())

    # --- publishing ---------------------------------------------------------

    def test_publish_now_with_notification(self):
        token = self.as_role(ClubRole.Roles.EDITOR)
        item = self.item()
        with mock.patch("news.services.dispatch_send_publish_notification") as send:
            response = self.call("post", f"/{item.pk}/publish", token=token, data={"notify_members": True})
        self.assertEqual(response.status_code, 200, response.content)
        send.assert_called_once_with(str(item.pk))
        item.refresh_from_db()
        self.assertEqual(item.status, News.Status.PUBLISHED)
        self.assertIsNotNone(item.published_at)
        self.assertIsNone(item.notified_at)

    def test_publish_without_notification_marks_notified(self):
        token = self.as_role(ClubRole.Roles.ADMIN)
        item = self.item()
        with mock.patch("news.services.dispatch_send_publish_notification") as send:
            self.call("post", f"/{item.pk}/publish", token=token, data={})
        send.assert_not_called()
        item.refresh_from_db()
        self.assertIsNotNone(item.notified_at)

    def test_publish_scheduled_defers_notification(self):
        token = self.as_role(ClubRole.Roles.ADMIN)
        item = self.item()
        when = timezone.now() + datetime.timedelta(days=2)
        with mock.patch("news.services.dispatch_send_publish_notification") as send:
            response = self.call("post", f"/{item.pk}/publish", token=token, data={"published_at": when.isoformat(), "notify_members": True})
        self.assertTrue(response.json()["is_scheduled"])
        send.assert_not_called()

    def test_unpublish(self):
        token = self.as_role(ClubRole.Roles.EDITOR)
        item = self.item(status=News.Status.PUBLISHED, published_at=timezone.now())
        response = self.call("post", f"/{item.pk}/unpublish", token=token)
        self.assertEqual(response.json()["status"], "draft")
        item.refresh_from_db()
        self.assertIsNone(item.published_at)

    def test_submit_for_review(self):
        token = self.as_role(ClubRole.Roles.EDITOR)
        item = self.item()
        with mock.patch("news.services.dispatch_notify_editors_of_pending_review") as notify:
            response = self.call("post", f"/{item.pk}/submit-for-review", token=token)
        self.assertEqual(response.json()["status"], "pending_review")
        notify.assert_called_once_with(item.pk)
