import base64
import datetime
from unittest import mock

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from django.db import connection
from django.test import Client, TestCase
from django.utils import timezone
from django_tenants.utils import schema_context

from club.models import Club, Domain
from club.services import create_club, ensure_member_for_user
from controlpanel.tests import PASSWORD, login_session, make_user
from events.models import Attendance, Event
from members.models import Family, FamilyMembership, Member
from notifications.models import WebPushSubscription
from sponsoring.models import Sponsor


class ParentPortalTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        owner = make_user("owner@portal.test")
        platform = Club(schema_name="public", name="Platform", owner=owner)
        platform.auto_create_schema = False
        platform.save()
        Domain.objects.create(domain="testserver", tenant=platform, is_primary=True)
        cls.club = create_club("Portal FC", owner)
        cls.host = Domain.objects.get(tenant=cls.club, is_primary=True).domain
        cls.parent = make_user("parent@portal.test", password=f"  {PASSWORD} ")
        cls.outsider = make_user("outsider@portal.test")
        cls.club.add_user(cls.parent)
        cls.viewer = ensure_member_for_user(cls.club, cls.parent)
        with schema_context(cls.club.schema_name):
            cls.child = Member.objects.create(first_name="My", last_name="Child")
            cls.other = Member.objects.create(first_name="Other", last_name="Child")
            family = Family.objects.create(name="Family")
            FamilyMembership.objects.create(family=family, member=cls.viewer, role=FamilyMembership.FamilyRole.PARENT)
            FamilyMembership.objects.create(family=family, member=cls.child, role=FamilyMembership.FamilyRole.CHILD)
            event = Event.objects.create(title="Training", kind=Event.EventKind.TRAINING, start=timezone.now() + datetime.timedelta(days=1))
            event.invited_members.add(cls.child, cls.other)
            cls.child_reply, _ = Attendance.objects.update_or_create(event=event, member=cls.child, defaults={"coach_note": "Private"})
            cls.other_reply, _ = Attendance.objects.get_or_create(event=event, member=cls.other)

    def tearDown(self):
        connection.set_schema_to_public()

    def call(self, method, path, token, data=None):
        return getattr(Client(), method)(f"/api/v1{path}", data=data, content_type="application/json", HTTP_HOST=self.host, HTTP_X_SESSION_TOKEN=token)

    def test_parent_password_and_family_permissions(self):
        login = Client().post(
            "/_allauth/app/v1/auth/login", data={"email": self.parent.email, "password": f"  {PASSWORD} "},
            content_type="application/json", HTTP_HOST=self.host,
        )
        self.assertEqual(login.status_code, 200, login.content)
        token = login.json()["meta"]["session_token"]
        self.assertEqual(self.call("get", "/me", token).json()["code"], "no_access")
        self.assertIs(self.call("get", "/portal/access", token).json()["can_manage"], False)

        with schema_context(self.club.schema_name):
            Sponsor.objects.create(name="Current sponsor", url="https://sponsor.example")
            Sponsor.objects.create(name="Future sponsor", start_date=timezone.localdate() + datetime.timedelta(days=1))
            Sponsor.objects.create(name="Former sponsor", start_date=timezone.localdate() - datetime.timedelta(days=2), end_date=timezone.localdate() - datetime.timedelta(days=1))
        home = self.call("get", "/portal/home", token)
        self.assertEqual(home.status_code, 200, home.content)
        self.assertEqual({person["id"] for person in home.json()["people"]}, {str(self.viewer.pk), str(self.child.pk)})
        [event] = home.json()["events"]
        self.assertEqual(event["id"], str(self.child_reply.pk))
        self.assertNotIn("coach_note", event)
        self.assertEqual([sponsor["name"] for sponsor in home.json()["sponsors"]], ["Current sponsor"])
        self.assertEqual(self.call("post", f"/portal/attendance/{self.other_reply.pk}", token, {"status": "present"}).status_code, 404)
        reply = self.call("post", f"/portal/attendance/{self.child_reply.pk}", token, {"status": "present"})
        self.assertEqual(reply.status_code, 200, reply.content)
        self.assertEqual(reply.json()["status"], "present")
        self.assertEqual(self.call("get", "/portal/coach", token).status_code, 403)
        connection.set_schema_to_public()
        denied = self.call("get", "/portal/home", login_session(self.outsider))
        self.assertIn(denied.status_code, (401, 403))

    def test_web_push_subscription_belongs_to_club_and_user(self):
        token = login_session(self.parent)
        public = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)

        def key(value):
            return base64.urlsafe_b64encode(value).decode().rstrip("=")

        endpoint = "https://fcm.googleapis.com/fcm/send/portal-test"
        body = {"endpoint": endpoint, "keys": {"p256dh": key(public), "auth": key(b"0" * 16)}}
        with mock.patch("notifications.gateways.webpush_public_key", return_value=key(public)):
            self.assertEqual(self.call("get", "/web-push/config", token).json()["public_key"], key(public))
            self.assertEqual(self.call("post", "/web-push/subscriptions", token, {**body, "endpoint": "https://127.0.0.1/push"}).status_code, 400)
            self.assertEqual(self.call("post", "/web-push/subscriptions", token, body).status_code, 204)
        self.assertTrue(WebPushSubscription.objects.filter(user=self.parent, club=self.club, endpoint=endpoint).exists())
        self.assertTrue(self.call("post", "/web-push/status", token, {"endpoint": endpoint}).json()["subscribed"])
        self.assertEqual(self.call("delete", "/web-push/subscriptions", token, {"endpoint": endpoint}).status_code, 204)
        self.assertFalse(WebPushSubscription.objects.filter(endpoint=endpoint).exists())



class PortalProfileTests(ParentPortalTests):
    def token(self):
        login = Client().post(
            "/_allauth/app/v1/auth/login", data={"email": self.parent.email, "password": f"  {PASSWORD} "},
            content_type="application/json", HTTP_HOST=self.host,
        )
        return login.json()["meta"]["session_token"]

    def test_edit_own_and_childs_details_but_nobody_elses(self):
        token = self.token()
        profile = self.call("get", "/portal/me/profile", token).json()
        self.assertEqual({p["id"] for p in profile["people"]}, {str(self.viewer.pk), str(self.child.pk)})
        saved = self.call("patch", f"/portal/me/profile/{self.child.pk}", token, {"first_name": "Kid", "phone": "+32 470 12 34 56", "date_of_birth": "2015-04-02"})
        self.assertEqual(saved.status_code, 200, saved.content)
        self.assertEqual((saved.json()["first_name"], saved.json()["date_of_birth"]), ("Kid", "2015-04-02"))
        self.assertEqual(self.call("patch", f"/portal/me/profile/{self.other.pk}", token, {"first_name": "Nope"}).status_code, 404)
        self.assertEqual(self.call("patch", f"/portal/me/profile/{self.child.pk}", token, {"first_name": " "}).status_code, 422)
        self.assertEqual(self.call("patch", f"/portal/me/profile/{self.child.pk}", token, {"email": "x@y.test"}).status_code, 200)  # unknown fields are ignored by the schema
        with schema_context(self.club.schema_name):
            self.assertEqual(Member.objects.get(pk=self.child.pk).email, "")

    def test_notification_switches_round_trip(self):
        token = self.token()
        before = self.call("get", "/portal/me/notifications", token).json()
        self.assertTrue(all(row["push"] for row in before["categories"]))
        after = self.call("put", "/portal/me/notifications", token, {"master": {"email": False}, "categories": {"news": {"push": False}}, "quiet_hours": {"enabled": True, "start": "22:00", "end": "07:00", "timezone": "Europe/Brussels"}})
        self.assertEqual(after.status_code, 200, after.content)
        data = after.json()
        self.assertIs(data["master"]["email"], False)
        self.assertIs(next(row for row in data["categories"] if row["key"] == "news")["push"], False)
        self.assertEqual(data["quiet_hours"]["start"], "22:00")
        self.assertEqual(self.call("put", "/portal/me/notifications", token, {"categories": {"admin": {"push": False}}}).status_code, 422)
