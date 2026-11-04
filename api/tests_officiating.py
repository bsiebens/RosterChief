"""Referee / official eligibility API (``/members/{id}/officiating``, ``/referee``, ``/official``): feature gate,
admin-only access, set / update / remove, eligibility rules and audit."""

import datetime
import json
import uuid
from unittest import mock

from django.db import connection
from django.test import Client, TestCase
from django.utils import timezone
from django_tenants.utils import schema_context

from club.models import Club, Domain
from club.services import create_club, grant_login
from controlpanel.tests import login_session, make_user
from features.entitlements import Entitlement, Entitlements
from members.models import AuditEntry, Member
from teams.models import OfficialLevel, OfficialProfile, RefereeLevel, RefereeProfile, Team

BASE = "/api/v1/members"
WITH_FEATURE = Entitlements({"referee_officials": Entitlement(True)})
WITHOUT_FEATURE = Entitlements({})


class OfficiatingApiTests(TestCase):
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
        cls.owner = make_user("owner@ref.test", first="Olivia", last="Owner")
        public = Club(schema_name="public", name="Platform", owner=cls.owner)
        public.auto_create_schema = False
        public.save()
        Domain.objects.create(domain="testserver", tenant=public, is_primary=True)
        cls.club = create_club("Ref FC", cls.owner)
        cls.host = Domain.objects.get(tenant=cls.club, is_primary=True).domain
        with schema_context(cls.club.schema_name):
            cls.u12 = Team.objects.create(name="U12", short_name="U12")
            cls.u14 = Team.objects.create(name="U14", short_name="U14")
            cls.u16 = Team.objects.create(name="U16", short_name="U16")
            cls.regional = RefereeLevel.objects.create(name="Regional")
            cls.regional.teams.set([cls.u12, cls.u14])
            cls.national = RefereeLevel.objects.create(name="National", inherits_from=cls.regional)
            cls.national.teams.set([cls.u16])
            cls.bench = OfficialLevel.objects.create(name="Bench 4")
            cls.bench.teams.set([cls.u12])
            cls.person = Member.objects.create(first_name="Rae", last_name="Ref", email="rae@ref.test")
            cls.coach = Member.objects.create(first_name="Coach", last_name="Test", email="coach@ref.test")
        cls.coach_user, _ = grant_login(cls.club, cls.coach)
        cls.owner = type(cls.owner).objects.get(pk=cls.owner.pk)

    def setUp(self):
        self.features = WITH_FEATURE
        patcher = mock.patch("features.middleware.entitlements_for_request", side_effect=lambda request: self.features)
        patcher.start()
        self.addCleanup(patcher.stop)

    # -- helpers --

    def call(self, method, path, user=None, data=None):
        headers = {"HTTP_HOST": self.host}
        if user is not None:
            headers["HTTP_X_SESSION_TOKEN"] = login_session(user)
        body = {"data": json.dumps(data), "content_type": "application/json"} if data is not None else {}
        return getattr(Client(), method)(BASE + path, **body, **headers)

    def admin(self, method, path, data=None):
        return self.call(method, path, self.owner, data)

    def path(self, suffix=""):
        return f"/{self.person.pk}{suffix}"

    def audit(self, action):
        return AuditEntry.objects.filter(member=self.person, action=action)

    def future(self, days=100):
        return (timezone.localdate() + datetime.timedelta(days=days)).isoformat()

    def past(self, days=10):
        return (timezone.localdate() - datetime.timedelta(days=days)).isoformat()

    # -- feature gate and access --

    def test_hidden_without_the_feature(self):
        self.features = WITHOUT_FEATURE
        for method, path, data in [
            ("get", self.path("/officiating"), None),
            ("get", "/officiating/options", None),
            ("put", self.path("/referee"), {"level_id": None}),
            ("put", self.path("/official"), {"level_id": None}),
            ("delete", self.path("/referee"), None),
            ("delete", self.path("/official"), None),
        ]:
            r = self.admin(method, path, data)
            self.assertEqual(r.status_code, 404, (method, path, r.content))
        self.assertFalse(RefereeProfile.objects.exists())

    def test_admin_only(self):
        for method, path, data in [("get", self.path("/officiating"), None), ("get", "/officiating/options", None), ("put", self.path("/referee"), {"level_id": None}), ("delete", self.path("/official"), None)]:
            self.assertEqual(self.call(method, path, self.coach_user, data).status_code, 403, (method, path))
            self.assertIn(self.call(method, path, None, data).status_code, (401, 403), (method, path))

    # -- reading --

    def test_empty_and_options(self):
        self.assertEqual(self.admin("get", self.path("/officiating")).json(), {"referee": None, "official": None})
        opts = self.admin("get", "/officiating/options").json()
        self.assertEqual([x["name"] for x in opts["referee_levels"]], ["National", "Regional"])
        self.assertEqual([x["name"] for x in opts["official_levels"]], ["Bench 4"])
        self.assertEqual([x["name"] for x in opts["teams"]], ["U12", "U14", "U16"])

    # -- referee --

    def test_set_update_and_remove_referee(self):
        r = self.admin("put", self.path("/referee"), {"level_id": str(self.regional.pk), "valid_until": self.future()})
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual(body["level"], {"id": str(self.regional.pk), "name": "Regional"})
        self.assertTrue(body["is_valid"] and body["is_eligible"])
        self.assertEqual([t["name"] for t in body["eligible_teams"]], ["U12", "U14"])
        self.assertEqual(body["excluded_teams"], [])
        self.assertEqual(self.audit("member.referee_updated").count(), 1)

        # update in place: still one profile; National inherits Regional's teams
        r = self.admin("put", self.path("/referee"), {"level_id": str(self.national.pk), "valid_until": self.future(), "excluded_team_ids": [str(self.u14.pk)]})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["id"], body["id"])
        self.assertEqual([t["name"] for t in r.json()["eligible_teams"]], ["U12", "U16"])
        self.assertEqual([t["name"] for t in r.json()["excluded_teams"]], ["U14"])
        with schema_context(self.club.schema_name):
            self.assertEqual(RefereeProfile.objects.filter(member=self.person).count(), 1)
        entry = self.audit("member.referee_updated").first()
        self.assertEqual(entry.changes["level"], {"from": "Regional", "to": "National"})
        self.assertEqual(entry.changes["excluded_teams"], {"from": [], "to": ["U14"]})

        # the same values again change nothing and add no entry
        self.admin("put", self.path("/referee"), {"level_id": str(self.national.pk), "valid_until": self.future(), "excluded_team_ids": [str(self.u14.pk)]})
        self.assertEqual(self.audit("member.referee_updated").count(), 2)

        self.assertEqual(self.admin("get", self.path("/officiating")).json()["referee"]["id"], body["id"])
        self.assertEqual(self.admin("delete", self.path("/referee")).status_code, 204)
        self.assertEqual(self.admin("get", self.path("/officiating")).json()["referee"], None)
        self.assertEqual(self.audit("member.referee_removed").count(), 1)
        self.assertEqual(self.admin("delete", self.path("/referee")).status_code, 204)  # no-op
        self.assertEqual(self.audit("member.referee_removed").count(), 1)

    def test_expired_or_levelless_referee_is_not_eligible(self):
        r = self.admin("put", self.path("/referee"), {"level_id": str(self.regional.pk), "valid_until": self.past()}).json()
        self.assertFalse(r["is_valid"])
        self.assertFalse(r["is_eligible"])
        self.assertEqual(r["eligible_teams"], [])
        r = self.admin("put", self.path("/referee"), {"level_id": None, "valid_until": self.future()}).json()
        self.assertTrue(r["is_valid"])
        self.assertFalse(r["is_eligible"])
        self.assertIsNone(r["level"])
        r = self.admin("put", self.path("/referee"), {"level_id": str(self.regional.pk)}).json()  # no date: not valid
        self.assertEqual((r["valid_until"], r["is_valid"], r["is_eligible"]), (None, False, False))

    def test_unknown_level_or_team_is_422(self):
        r = self.admin("put", self.path("/referee"), {"level_id": str(uuid.uuid4())})
        self.assertEqual(r.status_code, 422)
        self.assertIn("level_id", r.json()["errors"])
        r = self.admin("put", self.path("/referee"), {"level_id": str(self.regional.pk), "excluded_team_ids": [str(uuid.uuid4())]})
        self.assertEqual(r.status_code, 422)
        self.assertIn("excluded_team_ids", r.json()["errors"])
        r = self.admin("put", self.path("/referee"), {"level_id": str(self.bench.pk)})  # an official level is not a referee level
        self.assertEqual(r.status_code, 422)
        self.assertFalse(RefereeProfile.objects.exists())

    def test_unknown_member_is_404(self):
        self.assertEqual(self.admin("get", f"/{uuid.uuid4()}/officiating").status_code, 404)

    # -- official --

    def test_set_update_and_remove_official(self):
        r = self.admin("put", self.path("/official"), {"level_id": str(self.bench.pk), "valid_until": self.future()})
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual(body["level"]["name"], "Bench 4")
        self.assertEqual([t["name"] for t in body["eligible_teams"]], ["U12"])
        self.assertEqual(body["excluded_teams"], [])
        self.assertEqual(self.audit("member.official_updated").count(), 1)
        r = self.admin("put", self.path("/official"), {"level_id": str(self.bench.pk), "valid_until": self.past()}).json()
        self.assertEqual((r["id"], r["is_eligible"], r["eligible_teams"]), (body["id"], False, []))
        with schema_context(self.club.schema_name):
            self.assertEqual(OfficialProfile.objects.filter(member=self.person).count(), 1)
        self.assertEqual(self.admin("delete", self.path("/official")).status_code, 204)
        self.assertEqual(self.admin("get", self.path("/officiating")).json()["official"], None)
        self.assertEqual(self.audit("member.official_removed").count(), 1)
