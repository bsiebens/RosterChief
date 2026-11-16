"""Event subtypes API: everyone reads, the settings permission writes; the event form options and events carry them."""

import datetime
import json
from unittest import mock

from django.db import connection
from django.test import Client, TestCase
from django.utils import timezone
from django_tenants.utils import schema_context

from club.models import Club, Domain
from club.services import create_club, grant_login
from controlpanel.tests import login_session, make_user
from events.models import Event, EventSubtype, Season
from members.models import AuditEntry, Member
from teams.models import Team

BASE = "/api/v1"


class EventSubtypeApiTests(TestCase):
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
        cls.owner = make_user("owner@sub.test", first="Olivia", last="Owner")
        public = Club(schema_name="public", name="Platform", owner=cls.owner)
        public.auto_create_schema = False
        public.save()
        Domain.objects.create(domain="testserver", tenant=public, is_primary=True)
        cls.club = create_club("Subtype FC", cls.owner)
        cls.host = Domain.objects.get(tenant=cls.club, is_primary=True).domain
        today = datetime.date.today()
        with schema_context(cls.club.schema_name):
            cls.team = Team.objects.create(name="U12", short_name="U12")
            Season.objects.filter(start_date__lte=today, end_date__gte=today).first() or Season.objects.create(start_date=today - datetime.timedelta(days=30), end_date=today + datetime.timedelta(days=300))
            cls.coach = Member.objects.create(first_name="Coach", last_name="Test", email="coach@sub.test")
        cls.coach_user, _ = grant_login(cls.club, cls.coach)
        cls.owner = type(cls.owner).objects.get(pk=cls.owner.pk)

    def setUp(self):
        door = mock.patch("members.services.access.can_access_management", return_value=True)
        door.start()
        self.addCleanup(door.stop)

    def call(self, method, path, user=None, data=None):
        headers = {"HTTP_HOST": self.host}
        if user is not None:
            headers["HTTP_X_SESSION_TOKEN"] = login_session(user)
        body = {"data": json.dumps(data), "content_type": "application/json"} if data is not None else {}
        return getattr(Client(), method)(BASE + path, **body, **headers)

    def admin(self, method, path, data=None):
        return self.call(method, path, self.owner, data)

    def test_crud_and_permissions(self):
        r = self.admin("post", "/event-subtypes", {"kind": "training", "name": "Fitness", "title_template": "Fitness {teams}"})
        self.assertEqual(r.status_code, 201, r.content)
        sub = r.json()
        self.assertEqual((sub["kind_label"], sub["in_use_count"]), ("Training", 0))
        # reading is open to anyone who may use the app; writing is not
        self.assertEqual(self.call("get", "/event-subtypes", self.coach_user).status_code, 200)
        for method, path, data in [("post", "/event-subtypes", {"kind": "training", "name": "X"}), ("patch", f"/event-subtypes/{sub['id']}", {"name": "X"}), ("delete", f"/event-subtypes/{sub['id']}", None)]:
            self.assertEqual(self.call(method, path, self.coach_user, data).status_code, 403, path)
        self.assertEqual(self.admin("post", "/event-subtypes", {"kind": "training", "name": "fitness"}).status_code, 422)
        r = self.admin("patch", f"/event-subtypes/{sub['id']}", {"title_template": "Strength {teams}"})
        self.assertEqual(r.json()["title_template"], "Strength {teams}")
        self.assertEqual(self.admin("delete", f"/event-subtypes/{sub['id']}").status_code, 204)
        with schema_context(self.club.schema_name):
            self.assertEqual(sorted(AuditEntry.objects.filter(action__startswith="event_subtype.").values_list("action", flat=True)), ["event_subtype.created", "event_subtype.deleted", "event_subtype.updated"])

    def test_events_and_options_carry_the_subtype(self):
        sub = self.admin("post", "/event-subtypes", {"kind": "training", "name": "Fitness", "title_template": "Fitness {teams}"}).json()
        options = self.admin("get", "/events/options").json()
        self.assertEqual([(s["name"], s["kind"], s["title_template"]) for s in options["subtypes"]], [("Fitness", "training", "Fitness {teams}")])
        start = (timezone.now() + datetime.timedelta(days=2)).replace(microsecond=0).isoformat()
        r = self.admin("post", "/events", {"kind": "training", "subtype_id": sub["id"], "start": start, "team_ids": [str(self.team.pk)]})
        self.assertEqual(r.status_code, 201, r.content)
        event = r.json()
        self.assertEqual((event["title"], event["subtype_name"], event["subtype_id"]), ("Fitness U12", "Fitness", sub["id"]))
        self.assertEqual(self.admin("get", f"/events/{event['id']}/form").json()["event"]["subtype_id"], sub["id"])
        self.assertEqual(self.admin("get", "/event-subtypes").json()[0]["in_use_count"], 1)
        bad = self.admin("post", "/events", {"kind": "meeting", "title": "x", "subtype_id": sub["id"], "start": start})
        self.assertEqual(bad.status_code, 422)
        with schema_context(self.club.schema_name):
            self.assertEqual(Event.objects.filter(subtype_id=sub["id"]).count(), 1)
            self.assertEqual(EventSubtype.objects.count(), 1)

    def test_reorder_per_kind(self):
        ids = {n: self.admin("post", "/event-subtypes", {"kind": k, "name": n}).json()["id"] for n, k in [("A", "training"), ("B", "training"), ("C", "game"), ("D", "training")]}
        self.assertEqual([s["ordering"] for s in self.admin("get", "/event-subtypes").json() if s["kind"] == "training"], [1, 2, 3])  # new ones go to the end of their kind
        r = self.admin("post", "/event-subtypes/reorder", {"ids": [ids["D"], ids["A"]]})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual([(s["name"], s["ordering"]) for s in r.json() if s["kind"] == "training"], [("D", 1), ("A", 2), ("B", 3)])
        self.assertEqual(self.call("post", "/event-subtypes/reorder", self.coach_user, {"ids": []}).status_code, 403)
        self.assertEqual(self.admin("post", "/event-subtypes/reorder", {"ids": [ids["A"], ids["A"]]}).status_code, 422)
