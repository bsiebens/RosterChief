"""Calendar data: locations and opponents."""

import datetime
import io
import json
import tempfile
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from django_tenants.utils import schema_context

from club.models import Club, Domain
from club.services import create_club, grant_login
from controlpanel.tests import login_session, make_user
from events.models import Event, Location, Opponent, Season
from members.models import AuditEntry, Member
from teams.models import Position, StaffAssignment, Team

BASE = "/api/v1"


class CalendarDataTests(TestCase):
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
        cls.owner = make_user("owner@cal.test", first="Olivia", last="Owner")
        public = Club(schema_name="public", name="Platform", owner=cls.owner)
        public.auto_create_schema = False
        public.save()
        Domain.objects.create(domain="testserver", tenant=public, is_primary=True)
        cls.club = create_club("Calendar FC", cls.owner)
        cls.host = Domain.objects.get(tenant=cls.club, is_primary=True).domain
        today = datetime.date.today()
        with schema_context(cls.club.schema_name):
            season = Season.objects.create(start_date=today - datetime.timedelta(days=100), end_date=today + datetime.timedelta(days=200))
            team = Team.objects.create(name="U12", short_name="U12")
            head = Position.objects.create(name="Head coach", short_name="HC", staff_position=True, management_position=True)
            assistant = Position.objects.create(name="Assistant", short_name="AC", staff_position=True)
            cls.head_m = Member.objects.create(first_name="Head", last_name="Coach", email="head@cal.test")
            cls.assistant_m = Member.objects.create(first_name="Assi", last_name="Stant", email="assi@cal.test")
            StaffAssignment.objects.create(team=team, member=cls.head_m, season=season, position=head)
            StaffAssignment.objects.create(team=team, member=cls.assistant_m, season=season, position=assistant)
            cls.home = Location.objects.create(name="Home Rink", address="1 Ice Road", city="Mechelen", zip_code="2800", country="BE", is_home=True)
        cls.head_user, _ = grant_login(cls.club, cls.head_m)
        cls.assistant_user, _ = grant_login(cls.club, cls.assistant_m)
        cls.owner = type(cls.owner).objects.get(pk=cls.owner.pk)

    def call(self, method, path, user=None, data=None, files=None):
        headers = {"HTTP_HOST": self.host, "HTTP_X_SESSION_TOKEN": login_session(user or self.owner)}
        if files is not None:
            return getattr(Client(), method)(BASE + path, files, **headers)
        body = {"data": json.dumps(data), "content_type": "application/json"} if data is not None else {}
        return getattr(Client(), method)(BASE + path, **body, **headers)

    LOC = {"name": "Ice Palace", "address": "5 Cold St", "city": "Antwerp", "zip_code": "2000", "country": "BE"}

    # -- locations --

    def test_location_crud_and_rules(self):
        r = self.call("post", "/locations", data=self.LOC)
        self.assertEqual(r.status_code, 201, r.content)
        loc = r.json()
        self.assertEqual((loc["country_name"], loc["is_home"], loc["event_count"]), ("Belgium", False, 0))
        self.assertEqual(self.call("post", "/locations", data=self.LOC).status_code, 422)  # same name
        bad = self.call("post", "/locations", data={**self.LOC, "name": "X", "country": "ZZ", "latitude": 51.0})
        self.assertEqual(bad.status_code, 422)
        self.assertEqual(set(bad.json()["errors"]), {"country", "longitude"})
        r = self.call("patch", f"/locations/{loc['id']}", data={"city": "Gent", "latitude": 51.05, "longitude": 3.72})
        self.assertEqual((r.json()["city"], r.json()["latitude"]), ("Gent", 51.05))
        self.assertTrue(AuditEntry.objects.filter(action="club.location_updated").exists())
        self.assertEqual([x["name"] for x in self.call("get", "/locations?q=gent").json()], ["Ice Palace"])
        self.assertEqual(self.call("delete", f"/locations/{loc['id']}").status_code, 204)
        # the home location is managed elsewhere
        self.assertEqual(self.call("delete", f"/locations/{self.home.pk}").status_code, 422)
        self.assertTrue(next(x for x in self.call("get", "/locations").json() if x["name"] == "Home Rink")["is_home"])

    def test_deleting_a_location_in_use_unlinks_events(self):
        loc = self.call("post", "/locations", data=self.LOC).json()
        with schema_context(self.club.schema_name):
            ev = Event.objects.create(title="Game", start=timezone.now(), location=Location.objects.get(pk=loc["id"]))
        self.assertEqual(next(x for x in self.call("get", "/locations").json() if x["id"] == loc["id"])["event_count"], 1)
        self.assertEqual(self.call("delete", f"/locations/{loc['id']}").status_code, 204)
        with schema_context(self.club.schema_name):
            ev.refresh_from_db()
            self.assertIsNone(ev.location)

    # -- opponents --

    def test_opponent_crud_names_and_logo(self):
        r = self.call("post", "/opponents", data={"name": "Bulldogs", "alternative_names": ["Bulldogs Liège", " bulldogs ", ""]})
        self.assertEqual(r.status_code, 201, r.content)
        o = r.json()
        self.assertEqual(o["alternative_names"], ["Bulldogs Liège"])  # blanks and repeats of the name are dropped
        self.assertEqual(self.call("post", "/opponents", data={"name": "bulldogs liège"}).status_code, 422)  # an alternative name of another opponent
        r2 = self.call("post", "/opponents", data={"name": "Lions"})
        self.assertEqual(self.call("patch", f"/opponents/{r2.json()['id']}", data={"alternative_names": ["Bulldogs"]}).status_code, 422)
        self.assertEqual([x["name"] for x in self.call("get", "/opponents?q=liège").json()], ["Bulldogs"])  # search includes alternative names
        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media):
            from PIL import Image

            buf = io.BytesIO()
            Image.new("RGB", (4, 4), "blue").save(buf, "PNG")
            up = lambda data: self.call("post", f"/opponents/{o['id']}/logo", files={"file": SimpleUploadedFile("l.png", data)})  # noqa: E731
            self.assertEqual(up(b"nope").status_code, 422)
            r = up(buf.getvalue())
            self.assertEqual(r.status_code, 200, r.content)
            self.assertTrue(r.json()["logo_url"].startswith("/media/"))
            self.assertIsNone(self.call("delete", f"/opponents/{o['id']}/logo").json()["logo_url"])
        self.assertEqual(self.call("delete", f"/opponents/{o['id']}").status_code, 204)
        self.assertFalse(Opponent.objects.filter(pk=o["id"]).exists())

    # -- permissions --

    def test_who_may_change_them(self):
        with mock.patch("members.services.access.can_access_management", return_value=True):
            for user in (self.assistant_user, self.head_user):
                self.assertEqual(self.call("get", "/locations", user).status_code, 200)
                self.assertEqual(self.call("get", "/opponents", user).status_code, 200)
            self.assertEqual(self.call("post", "/locations", self.assistant_user, self.LOC).status_code, 403)
            self.assertEqual(self.call("post", "/opponents", self.assistant_user, {"name": "Nope"}).status_code, 403)
            self.assertEqual(self.call("post", "/locations", self.head_user, self.LOC).status_code, 201)  # a team manager schedules events
            self.assertEqual(self.call("post", "/opponents", self.head_user, {"name": "Yes"}).status_code, 201)
