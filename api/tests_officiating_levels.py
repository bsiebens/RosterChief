"""Referee / official levels API (``/officiating/{referee,official}-levels``): feature gate, admin only, CRUD,
validation, loop prevention and delete protection."""

import datetime
import json
import uuid
from unittest import mock

from django.db import connection
from django.test import Client, TestCase
from django_tenants.utils import schema_context

from club.models import Club, Domain
from club.services import create_club, grant_login
from controlpanel.tests import login_session, make_user
from features.entitlements import Entitlement, Entitlements
from members.models import AuditEntry, Member
from teams.models import OfficialLevel, OfficialProfile, RefereeLevel, RefereeProfile, Team

BASE = "/api/v1/officiating"
WITH_FEATURE = Entitlements({"referee_officials": Entitlement(True)})
KINDS = ("referee", "official")


class LevelsApiTests(TestCase):
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
        cls.owner = make_user("owner@lvl.test", first="Olivia", last="Owner")
        public = Club(schema_name="public", name="Platform", owner=cls.owner)
        public.auto_create_schema = False
        public.save()
        Domain.objects.create(domain="testserver", tenant=public, is_primary=True)
        cls.club = create_club("Level FC", cls.owner)
        cls.host = Domain.objects.get(tenant=cls.club, is_primary=True).domain
        with schema_context(cls.club.schema_name):
            cls.u12 = Team.objects.create(name="U12", short_name="U12")
            cls.u14 = Team.objects.create(name="U14", short_name="U14")
            cls.person = Member.objects.create(first_name="Rae", last_name="Ref", email="rae@lvl.test")
            cls.coach = Member.objects.create(first_name="Coach", last_name="Test", email="coach@lvl.test")
        cls.coach_user, _ = grant_login(cls.club, cls.coach)
        cls.owner = type(cls.owner).objects.get(pk=cls.owner.pk)

    def setUp(self):
        self.features = WITH_FEATURE
        patcher = mock.patch("features.middleware.entitlements_for_request", side_effect=lambda request: self.features)
        patcher.start()
        self.addCleanup(patcher.stop)

    def call(self, method, path, user=None, data=None):
        headers = {"HTTP_HOST": self.host}
        if user is not None:
            headers["HTTP_X_SESSION_TOKEN"] = login_session(user)
        body = {"data": json.dumps(data), "content_type": "application/json"} if data is not None else {}
        return getattr(Client(), method)(BASE + path, **body, **headers)

    def admin(self, method, path, data=None):
        return self.call(method, path, self.owner, data)

    def create(self, kind, name="Regional", teams=(), parent=None):
        r = self.admin("post", f"/{kind}-levels", {"name": name, "team_ids": [str(t.pk) for t in teams], "inherits_from_id": str(parent) if parent else None})
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()

    def audit(self, action):
        with schema_context(self.club.schema_name):
            return list(AuditEntry.objects.filter(action=action))

    # -- gate --

    def test_admin_only_checked_before_feature(self):
        for kind in KINDS:
            for method, path, data in [("get", f"/{kind}-levels", None), ("post", f"/{kind}-levels", {"name": "X"}), ("patch", f"/{kind}-levels/{uuid.uuid4()}", {"name": "X"}), ("delete", f"/{kind}-levels/{uuid.uuid4()}", None)]:
                self.assertEqual(self.call(method, path, self.coach_user, data).status_code, 403, (method, path))
                self.assertIn(self.call(method, path, None, data).status_code, (401, 403))
                self.features = Entitlements({})
                self.assertEqual(self.call(method, path, self.coach_user, data).status_code, 403)  # 403 even without the feature
                self.features = WITH_FEATURE

    def test_hidden_without_the_feature(self):
        level = self.create("referee")
        self.features = Entitlements({})
        for kind in KINDS:
            for method, path, data in [("get", f"/{kind}-levels", None), ("post", f"/{kind}-levels", {"name": "X"}), ("patch", f"/{kind}-levels/{level['id']}", {"name": "X"}), ("delete", f"/{kind}-levels/{level['id']}", None)]:
                r = self.admin(method, path, data)
                self.assertEqual(r.status_code, 404, (method, path))
                self.assertEqual(r.json()["detail"], "Not found.")

    # -- CRUD, both kinds --

    def test_crud(self):
        for kind, model in (("referee", RefereeLevel), ("official", OfficialLevel)):
            self.assertEqual(self.admin("get", f"/{kind}-levels").json(), [])
            base = self.create(kind, "Regional", [self.u12, self.u14])
            self.assertEqual(sorted(base["team_ids"]), sorted([str(self.u12.pk), str(self.u14.pk)]))
            self.assertEqual((base["member_count"], base["inherited_by_count"], base["inherits_from_id"]), (0, 0, None))
            top = self.create(kind, "National", [], base["id"])
            self.assertEqual(top["inherits_from_id"], base["id"])
            listing = self.admin("get", f"/{kind}-levels").json()
            self.assertEqual([x["name"] for x in listing], ["National", "Regional"])
            self.assertEqual({x["name"]: x["inherited_by_count"] for x in listing}, {"National": 0, "Regional": 1})
            r = self.admin("patch", f"/{kind}-levels/{top['id']}", {"name": "Nationwide", "team_ids": [str(self.u12.pk)], "inherits_from_id": None})
            self.assertEqual(r.status_code, 200, r.content)
            self.assertEqual((r.json()["name"], r.json()["team_ids"], r.json()["inherits_from_id"]), ("Nationwide", [str(self.u12.pk)], None))
            self.assertEqual(len(self.audit(f"club.{kind}_level_created")), 2)
            (entry,) = self.audit(f"club.{kind}_level_updated")
            self.assertEqual(entry.changes["name"], {"from": "National", "to": "Nationwide"})
            self.assertEqual(self.admin("delete", f"/{kind}-levels/{top['id']}").status_code, 204)
            self.assertEqual(self.admin("delete", f"/{kind}-levels/{base['id']}").status_code, 204)
            self.assertEqual(len(self.audit(f"club.{kind}_level_deleted")), 2)
            self.assertEqual(self.admin("get", f"/{kind}-levels").json(), [])

    def test_unknown_level_is_404(self):
        for kind in KINDS:
            self.assertEqual(self.admin("patch", f"/{kind}-levels/{uuid.uuid4()}", {"name": "X"}).status_code, 404)
            self.assertEqual(self.admin("delete", f"/{kind}-levels/{uuid.uuid4()}").status_code, 404)

    def test_kinds_are_separate(self):
        ref = self.create("referee", "Shared name")
        self.create("official", "Shared name")  # unique per kind only
        self.assertEqual(self.admin("patch", f"/official-levels/{ref['id']}", {"name": "X"}).status_code, 404)
        r = self.admin("post", "/official-levels", {"name": "Child", "team_ids": [], "inherits_from_id": ref["id"]})
        self.assertEqual(r.status_code, 422)
        self.assertIn("inherits_from_id", r.json()["errors"])

    # -- validation --

    def test_name_required_and_unique(self):
        for kind in KINDS:
            r = self.admin("post", f"/{kind}-levels", {"name": "  "})
            self.assertEqual(r.status_code, 422)
            self.assertIn("name", r.json()["errors"])
            self.create(kind, "Regional")
            r = self.admin("post", f"/{kind}-levels", {"name": "regional"})
            self.assertEqual(r.status_code, 422)
            self.assertIn("name", r.json()["errors"])
            other = self.create(kind, "Other")
            r = self.admin("patch", f"/{kind}-levels/{other['id']}", {"name": "Regional"})
            self.assertEqual(r.status_code, 422)
            self.assertIn("name", r.json()["errors"])
            self.assertEqual(self.admin("patch", f"/{kind}-levels/{other['id']}", {"name": "Other"}).status_code, 200)  # own name is fine

    def test_unknown_team_or_parent_is_422(self):
        r = self.admin("post", "/referee-levels", {"name": "A", "team_ids": [str(uuid.uuid4())], "inherits_from_id": str(uuid.uuid4())})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(set(r.json()["errors"]), {"team_ids", "inherits_from_id"})
        self.assertFalse(RefereeLevel.objects.exists())

    def test_loops_are_refused(self):
        for kind in KINDS:
            a = self.create(kind, "A")
            b = self.create(kind, "B", parent=a["id"])
            c = self.create(kind, "C", parent=b["id"])
            for target, parent in ((a, a), (a, c), (b, c)):
                r = self.admin("patch", f"/{kind}-levels/{target['id']}", {"name": target["name"], "team_ids": [], "inherits_from_id": parent["id"]})
                self.assertEqual(r.status_code, 422, (kind, target["name"], parent["name"]))
                self.assertIn("inherits_from_id", r.json()["errors"])
            self.assertIsNone(self.admin("get", f"/{kind}-levels").json()[0]["inherits_from_id"])  # A unchanged

    # -- delete protection --

    def test_delete_refused_when_held_or_inherited(self):
        ref = self.create("referee", "Regional")
        child = self.create("referee", "National", parent=ref["id"])
        r = self.admin("delete", f"/referee-levels/{ref['id']}")
        self.assertEqual(r.status_code, 422)
        self.assertIn("National", r.json()["detail"])
        with schema_context(self.club.schema_name):
            RefereeProfile.objects.create(member=self.person, level=RefereeLevel.objects.get(pk=child["id"]), valid_until=datetime.date.today())
        r = self.admin("delete", f"/referee-levels/{child['id']}")
        self.assertEqual(r.status_code, 422)
        self.assertIn("1 member", r.json()["detail"])
        self.assertEqual(self.admin("get", "/referee-levels").json()[0]["member_count"], 1)
        with schema_context(self.club.schema_name):
            self.assertEqual(RefereeLevel.objects.count(), 2)
            RefereeProfile.objects.all().delete()
        self.assertEqual(self.admin("delete", f"/referee-levels/{child['id']}").status_code, 204)
        self.assertEqual(self.admin("delete", f"/referee-levels/{ref['id']}").status_code, 204)

    def test_official_delete_refused_when_held(self):
        lvl = self.create("official", "Bench")
        with schema_context(self.club.schema_name):
            OfficialProfile.objects.create(member=self.person, level=OfficialLevel.objects.get(pk=lvl["id"]), valid_until=datetime.date.today())
        r = self.admin("delete", f"/official-levels/{lvl['id']}")
        self.assertEqual(r.status_code, 422)
        self.assertIn("member", r.json()["detail"])
