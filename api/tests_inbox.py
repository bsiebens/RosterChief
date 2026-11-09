"""The in-app inbox API: always the caller's own notifications."""

from unittest import mock

from django.db import connection
from django.test import Client, TestCase

from club.models import Club, Domain
from club.services import create_club
from controlpanel.tests import login_session, make_user
from notifications import inbox
from notifications.models import InboxNotification

BASE = "/api/v1/notifications"


class InboxApiTests(TestCase):
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

    @classmethod
    def setUpTestData(cls):
        cls.owner = make_user("owner@inbox.test", first="Olivia", last="Owner")
        public = Club(schema_name="public", name="Platform", owner=cls.owner)
        public.auto_create_schema = False
        public.save()
        Domain.objects.create(domain="testserver", tenant=public, is_primary=True)
        cls.club = create_club("Inbox FC", cls.owner)
        cls.other_club = create_club("Other FC", cls.owner)
        cls.host = Domain.objects.get(tenant=cls.club, is_primary=True).domain
        cls.other = make_user("other@inbox.test", first="Otto", last="Other")
        cls.owner = type(cls.owner).objects.get(pk=cls.owner.pk)

    def call(self, method, path="", user=None):
        headers = {"HTTP_HOST": self.host}
        if user is not None:
            headers["HTTP_X_SESSION_TOKEN"] = login_session(user)
        return getattr(Client(), method)(BASE + path, **headers)

    def test_list_is_mine_newest_first_and_scoped_to_the_club(self):
        a = inbox.notify(self.owner, self.club, title="First", body="b1", href="/members")
        b = inbox.notify(self.owner, self.club, title="Second", kind="warning")
        inbox.notify(self.owner, self.other_club, title="Elsewhere")
        inbox.notify(self.other, self.club, title="Not mine")
        rows = self.call("get", user=self.owner).json()
        self.assertEqual([r["title"] for r in rows], ["Second", "First"])
        self.assertEqual((rows[0]["kind"], rows[0]["href"], rows[0]["read"]), ("warning", None, False))
        self.assertEqual(rows[1]["href"], "/members")
        self.assertEqual({a.pk, b.pk}, {r["id"] and type(a.pk)(r["id"]) for r in rows})

    def test_read_one_and_all(self):
        a = inbox.notify(self.owner, self.club, title="A")
        inbox.notify(self.owner, self.club, title="B")
        self.assertEqual(self.call("post", f"/{a.pk}/read", self.owner).status_code, 204)
        self.assertEqual(self.call("post", f"/{a.pk}/read", self.owner).status_code, 204)  # idempotent
        self.assertEqual(sorted(r["read"] for r in self.call("get", user=self.owner).json()), [False, True])
        self.assertEqual(self.call("post", "/read-all", self.owner).status_code, 204)
        self.assertTrue(all(r["read"] for r in self.call("get", user=self.owner).json()))

    def test_clear_all_only_clears_mine_here(self):
        inbox.notify(self.owner, self.club, title="Mine")
        inbox.notify(self.owner, self.other_club, title="Other club")
        inbox.notify(self.other, self.club, title="Someone else's")
        self.assertEqual(self.call("delete", user=self.owner).status_code, 204)
        self.assertEqual(self.call("get", user=self.owner).json(), [])
        self.assertEqual(InboxNotification.objects.count(), 2)

    def test_cannot_touch_someone_elses(self):
        theirs = inbox.notify(self.other, self.club, title="Theirs")
        self.assertEqual(self.call("post", f"/{theirs.pk}/read", self.owner).status_code, 404)
        theirs.refresh_from_db()
        self.assertIsNone(theirs.read_at)

    def test_push_is_queued_only_when_asked_and_carries_the_category(self):
        from notifications.models import OutboxEntry

        before = OutboxEntry.objects.count()
        inbox.notify(self.owner, self.club, title="Quiet")
        self.assertEqual(OutboxEntry.objects.count(), before)  # inbox only by default
        item = inbox.notify(self.owner, self.club, title="Loud", body="b", href="/calendar", push=True, category="event")
        self.assertEqual(OutboxEntry.objects.count(), before + 1)
        payload = OutboxEntry.objects.order_by("-created").first().payload
        self.assertEqual((payload["title"], payload["category"], payload["data"]["href"], payload["data"]["inbox_id"]), ("Loud", "event", "/calendar", str(item.pk)))
        self.assertEqual(payload["user_id"], str(self.owner.pk))

    def test_anonymous_and_platform_host(self):
        self.assertIn(self.call("get").status_code, (401, 403))
        headers = {"HTTP_HOST": "testserver", "HTTP_X_SESSION_TOKEN": login_session(self.owner)}
        self.assertEqual(Client().get(BASE, **headers).status_code, 404)
