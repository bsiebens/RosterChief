"""The personal calendar feed: switching it on/off/replacing it, and the public .ics (content, scope, 404s)."""

import datetime

from django.db import connection
from django.test import Client, TestCase
from django.utils import timezone
from django_tenants.utils import schema_context

from club.models import Club, Domain
from club.services import create_club, ensure_member_for_user
from controlpanel.tests import PASSWORD, make_user
from events.models import Attendance, Event, Location
from events.services.calendar_feed import _fold
from members.models import AuditEntry, Family, FamilyMembership, Member

Role = FamilyMembership.FamilyRole


class CalendarFeedTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        owner = make_user("owner@feed.test")
        platform = Club(schema_name="public", name="Platform", owner=owner)
        platform.auto_create_schema = False
        platform.save()
        Domain.objects.create(domain="testserver", tenant=platform, is_primary=True)
        cls.club = create_club("Feed FC", owner)
        cls.host = Domain.objects.get(tenant=cls.club, is_primary=True).domain
        cls.parent = make_user("parent@feed.test")
        cls.club.add_user(cls.parent)
        cls.viewer = ensure_member_for_user(cls.club, cls.parent)
        cls.stranger = make_user("stranger@feed.test")
        cls.club.add_user(cls.stranger)
        cls.stranger_member = ensure_member_for_user(cls.club, cls.stranger)
        with schema_context(cls.club.schema_name):
            cls.child = Member.objects.create(first_name="Kiddo", last_name="Player")
            cls.other = Member.objects.create(first_name="Someone", last_name="Else")
            family = Family.objects.create(name="Family")
            FamilyMembership.objects.create(family=family, member=cls.viewer, role=Role.PARENT)
            FamilyMembership.objects.create(family=family, member=cls.child, role=Role.CHILD)
            place = Location.objects.create(name="Sporthal", address="Straat 1", city="Gent", zip_code="9000", country="BE")
            now = timezone.now()
            own = Event.objects.create(title="Own, training; evening", kind="training", start=now + datetime.timedelta(days=2), gathering=now + datetime.timedelta(days=2) - datetime.timedelta(minutes=30), location=place, notes="Bring water\nand a towel")
            kid = Event.objects.create(title="Kid game", kind="game", start=now + datetime.timedelta(days=3))
            gone = Event.objects.create(title="Cancelled one", kind="training", start=now + datetime.timedelta(days=4), end=now + datetime.timedelta(days=4, hours=1), cancelled=True)
            hidden = Event.objects.create(title="Not for this family", kind="training", start=now + datetime.timedelta(days=5))
            old = Event.objects.create(title="Long ago", kind="training", start=now - datetime.timedelta(days=90))
            for event, member in ((own, cls.viewer), (kid, cls.child), (gone, cls.child), (hidden, cls.other), (old, cls.viewer)):
                Attendance.objects.update_or_create(event=event, member=member, defaults={"status": "present" if event is own else "no_response"})
            cls.kid_event = kid

    def tearDown(self):
        connection.set_schema_to_public()

    def token(self, user):
        login = Client().post("/_allauth/app/v1/auth/login", data={"email": user.email, "password": PASSWORD}, content_type="application/json", HTTP_HOST=self.host)
        return login.json()["meta"]["session_token"]

    def call(self, method, path, user):
        return getattr(Client(), method)(f"/api/v1{path}", HTTP_HOST=self.host, HTTP_X_SESSION_TOKEN=self.token(user))

    def feed(self, token, host=None):
        return Client().get(f"/api/v1/public/calendar/{token}.ics", HTTP_HOST=host or self.host)

    def test_switch_on_replace_and_off(self):
        self.assertEqual(self.call("get", "/portal/me/calendar-feed", self.parent).json(), {"enabled": False, "url_token": None})
        first = self.call("put", "/portal/me/calendar-feed", self.parent).json()
        self.assertTrue(first["enabled"])
        self.assertEqual(self.call("put", "/portal/me/calendar-feed", self.parent).json()["url_token"], first["url_token"])  # no change without rotate
        second = self.call("put", "/portal/me/calendar-feed?rotate=true", self.parent).json()
        self.assertNotEqual(first["url_token"], second["url_token"])
        self.assertEqual(self.feed(first["url_token"]).status_code, 404)
        self.assertEqual(self.feed(second["url_token"]).status_code, 200)
        self.assertEqual(self.call("delete", "/portal/me/calendar-feed", self.parent).json(), {"enabled": False, "url_token": None})
        self.assertEqual(self.feed(second["url_token"]).status_code, 404)
        with schema_context(self.club.schema_name):
            self.assertEqual(list(AuditEntry.objects.filter(action__startswith="calendar_feed.").order_by("created").values_list("action", flat=True)), ["calendar_feed.enabled", "calendar_feed.rotated", "calendar_feed.disabled"])

    def test_unknown_token_and_platform_host_are_404(self):
        self.assertEqual(self.feed("nope").status_code, 404)
        token = self.call("put", "/portal/me/calendar-feed", self.parent).json()["url_token"]
        self.assertEqual(self.feed(token, host="testserver").status_code, 404)
        self.assertEqual(Client().put("/api/v1/portal/me/calendar-feed", HTTP_HOST=self.host).status_code, 401)

    def test_feed_has_own_and_childrens_events_only(self):
        token = self.call("put", "/portal/me/calendar-feed", self.parent).json()["url_token"]
        response = self.feed(token)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "private, max-age=300")
        self.assertTrue(response["Content-Type"].startswith("text/calendar"))
        text = response.content.decode().replace("\r\n ", "")
        self.assertTrue(text.startswith("BEGIN:VCALENDAR\r\n") and text.endswith("END:VCALENDAR\r\n"))
        self.assertEqual(text.count("BEGIN:VEVENT"), 3)
        self.assertIn("SUMMARY:Own\\, training\\; evening\r\n", text)  # own events carry no name suffix
        self.assertIn("SUMMARY:Kid game (Kiddo)\r\n", text)
        self.assertIn("LOCATION:Sporthal\\, Straat 1\\, 9000 Gent\r\n", text)
        self.assertIn("Bring water\\nand a towel", text)
        self.assertIn("Answer: Present", text)
        self.assertIn("Gathering: ", text)
        self.assertIn("TRIGGER:-PT90M", text)  # 60 minutes before a gathering that is itself 30 minutes before the start
        self.assertIn("TRIGGER:-PT60M", text)  # 60 minutes before the start of an event without gathering time
        self.assertIn("STATUS:CANCELLED", text)
        self.assertEqual(text.count("BEGIN:VALARM"), 2)  # none for the cancelled one
        self.assertNotIn("Not for this family", text)
        self.assertNotIn("Long ago", text)
        self.assertIn(f"UID:{self.kid_event.pk}-{self.child.pk}@", text)
        kid = text.split("SUMMARY:Kid game (Kiddo)")[0].split("BEGIN:VEVENT")[-1]
        start = [line for line in kid.split("\r\n") if line.startswith("DTSTART:")][0][8:]
        end = [line for line in kid.split("\r\n") if line.startswith("DTEND:")][0][6:]
        self.assertEqual(datetime.datetime.strptime(end, "%Y%m%dT%H%M%SZ") - datetime.datetime.strptime(start, "%Y%m%dT%H%M%SZ"), datetime.timedelta(hours=2))

    def test_another_account_sees_none_of_it(self):
        token = self.call("put", "/portal/me/calendar-feed", self.stranger).json()["url_token"]
        text = self.feed(token).content.decode()
        self.assertEqual(text.count("BEGIN:VEVENT"), 0)

    def test_long_lines_are_folded_within_75_octets(self):
        folded = _fold("DESCRIPTION:" + "é" * 80)
        self.assertTrue(all(len(line.encode()) <= 75 for line in folded.split("\r\n")))
        self.assertEqual(folded.replace("\r\n ", ""), "DESCRIPTION:" + "é" * 80)
