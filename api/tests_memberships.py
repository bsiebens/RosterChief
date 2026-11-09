"""/api/v1/memberships: access rules, fee and onboarding flows, filters, query counts and audit trail (real HTTP)."""

import datetime
import json
import shutil
import tempfile
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from django_tenants.utils import schema_context

from club.models import Club, Domain
from club.services import create_club, grant_login
from controlpanel.tests import login_session, make_user
from events.models import Season
from members.models import AuditEntry, Member
from teams.models import Team, TeamMembership

BASE = "/api/v1/memberships"


class MembershipApiTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.mkdtemp(prefix="rc-private-test-")
        cls._override = override_settings(PRIVATE_MEDIA_ROOT=cls._tmp)
        cls._override.enable()
        cls._mfa = mock.patch("authorization.middleware.is_local_development", return_value=True)
        cls._mfa.start()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        connection.set_schema_to_public()
        cls._mfa.stop()
        cls._override.disable()
        shutil.rmtree(cls._tmp, ignore_errors=True)

    @classmethod
    def setUpTestData(cls):
        cls.owner = make_user("owner@msapi.test", first="Olivia", last="Owner")
        public = Club(schema_name="public", name="Platform", owner=cls.owner)
        public.auto_create_schema = False
        public.save()
        Domain.objects.create(domain="testserver", tenant=public, is_primary=True)
        cls.club = create_club("Membership API FC", cls.owner)
        cls.host = Domain.objects.get(tenant=cls.club, is_primary=True).domain
        today = timezone.localdate()
        with schema_context(cls.club.schema_name):
            cls.season = Season.objects.create(start_date=today - datetime.timedelta(days=100), end_date=today + datetime.timedelta(days=200))
            cls.old_season = Season.objects.create(start_date=today - datetime.timedelta(days=500), end_date=today - datetime.timedelta(days=200))
            cls.team = Team.objects.create(name="U12", short_name="U12")
            cls.alice = Member.objects.create(first_name="Alice", last_name="Anders", email="alice@msapi.test", license_number="LIC-1")
            cls.bob = Member.objects.create(first_name="Bob", last_name="Baker", email="bob@msapi.test")
            cls.carol = Member.objects.create(first_name="Carol", last_name="Case")
        cls.alice_user, _ = grant_login(cls.club, cls.alice)  # ordinary club user, no permissions
        cls.bob_user, _ = grant_login(cls.club, cls.bob)

    def setUp(self):
        # These tests are about the memberships rules (self scope, admin only); the door has its own test in tests_members.
        door = mock.patch("members.services.access.can_access_management", return_value=True)
        door.start()
        self.addCleanup(door.stop)
        self._ctx = schema_context(self.club.schema_name)
        self._ctx.__enter__()
        self.addCleanup(self._ctx.__exit__, None, None, None)

    # -- helpers --
    def call(self, method, path, data=None, *, user=None, multipart=False):
        client = Client()
        headers = {"HTTP_HOST": self.host}
        if user is not None:
            headers["HTTP_X_SESSION_TOKEN"] = login_session(user)
        kwargs = dict(headers)
        if data is not None:
            if multipart:
                kwargs["data"] = data
            else:
                kwargs.update(data=json.dumps(data), content_type="application/json")
        elif method in ("post", "patch"):
            kwargs.update(data="{}", content_type="application/json")
        return getattr(client, method)(f"{BASE}{path}", **kwargs)

    def admin(self, method, path, data=None, **kw):
        return self.call(method, path, data, user=self.owner, **kw)

    def make(self, member, season=None, fee="100.00", **extra):
        r = self.admin("post", "", {"member_id": str(member.pk), "season_id": str((season or self.season).pk), "fee_amount": fee, **extra})
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()

    # -- access --
    def test_anonymous_rejected(self):
        for method, path in [("get", ""), ("get", "/seasons"), ("get", "/summary"), ("post", ""), ("get", "/requirements")]:
            self.assertEqual(self.call(method, path).status_code, 401, (method, path))

    def test_non_admin_cannot_use_finance_or_onboarding(self):
        m = self.make(self.alice)
        mid = m["id"]
        checks = [
            ("get", ""),
            ("get", "/summary"),
            ("post", ""),
            ("get", "/requirements"),
            ("post", "/requirements"),
            ("patch", f"/{mid}"),
            ("post", f"/{mid}/activate"),
            ("post", f"/{mid}/cancel"),
            ("post", f"/{mid}/waive"),
            ("get", f"/{mid}/payments"),
            ("post", f"/{mid}/payments"),
            ("post", f"/{mid}/mark-paid"),
            ("get", f"/{mid}/checklist"),
            ("get", f"/{mid}/history"),
        ]
        for method, path in checks:
            body = {"member_id": str(self.bob.pk), "season_id": str(self.season.pk), "name": "N", "amount": "1"} if method != "get" else None
            r = self.call(method, path, body, user=self.alice_user)  # alice owns this membership
            self.assertEqual(r.status_code, 403, (method, path, r.content))
            r = self.call(method, path, body, user=self.bob_user)  # bob can't even see it
            self.assertIn(r.status_code, (403, 404), (method, path))
        for method, path in [("get", f"/{mid}/payments"), ("get", f"/{mid}/history"), ("post", f"/{mid}/payments"), ("get", f"/{mid}/checklist")]:
            self.assertEqual(self.call(method, path, {"amount": "1"} if method == "post" else None, user=self.bob_user).status_code, 404, path)

    def test_member_reads_only_own_status_without_fee_data(self):
        mine = self.make(self.alice)
        theirs = self.make(self.bob)
        r = self.call("get", f"/{mine['id']}", user=self.alice_user)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["scope"], "self")
        self.assertEqual(body["status"], "pending")
        for key in ("fee_amount", "amount_paid", "amount_due", "remaining_balance", "fee_status", "member"):
            self.assertNotIn(key, body)
        self.assertEqual(self.call("get", f"/{theirs['id']}", user=self.alice_user).status_code, 404)
        self.assertEqual(self.call("get", "/00000000-0000-0000-0000-000000000000", user=self.alice_user).status_code, 404)

    def test_seasons_for_any_club_user(self):
        r = self.call("get", "/seasons", user=self.alice_user)
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual([s["id"] for s in data], [str(self.season.pk), str(self.old_season.pk)])
        self.assertEqual([s["is_current"] for s in data], [True, False])

    # -- fee flow --
    def test_payment_flow_and_activation(self):
        m = self.make(self.alice, fee="100.00")
        mid = m["id"]
        self.assertEqual(m["fee_status"], "unpaid")
        self.assertIsInstance(m["fee_amount"], str)
        self.assertEqual(m["remaining_balance"], "100.00")

        r = self.admin("post", f"/{mid}/payments", {"amount": "40.00", "method": "cash"})
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()["amount"], "40.00")
        m = self.admin("get", f"/{mid}").json()
        self.assertEqual((m["fee_status"], m["amount_paid"], m["remaining_balance"]), ("partially_paid", "40.00", "60.00"))

        # activation blocked: fee unsettled
        r = self.admin("post", f"/{mid}/activate")
        self.assertEqual(r.status_code, 422)

        self.assertEqual(self.admin("post", f"/{mid}/mark-paid").status_code, 201)
        m = self.admin("get", f"/{mid}").json()
        self.assertEqual((m["fee_status"], m["remaining_balance"]), ("paid", "0.00"))
        self.assertEqual(len(self.admin("get", f"/{mid}/payments").json()), 2)
        self.assertEqual(self.admin("post", f"/{mid}/mark-paid").status_code, 422)  # nothing left

        # onboarding requirements block activation until resolved
        doc_req = self.admin("post", "/requirements", {"name": "Medical certificate", "requires_document": True}).json()
        plain_req = self.admin("post", "/requirements", {"name": "Photo consent"}).json()
        self.assertEqual(self.admin("get", f"/{mid}").json()["open_requirement_count"], 2)
        r = self.admin("post", f"/{mid}/activate")
        self.assertEqual(r.status_code, 422)
        self.assertIn("Medical certificate", r.json()["detail"])

        # a document is required for that one
        r = self.admin("post", f"/{mid}/checklist/{doc_req['id']}/complete", {"note": "x"}, multipart=True)
        self.assertEqual(r.status_code, 422)
        self.assertEqual(self.admin("get", f"/{mid}/checklist/{doc_req['id']}/document").status_code, 404)
        upload = SimpleUploadedFile("cert.pdf", b"%PDF-secret", content_type="application/pdf")
        r = self.admin("post", f"/{mid}/checklist/{doc_req['id']}/complete", {"note": "received", "document": upload}, multipart=True)
        self.assertEqual(r.status_code, 200, r.content)
        item = r.json()
        self.assertEqual((item["state"], item["has_document"], item["note"]), ("complete", True, "received"))
        self.assertNotIn("media", json.dumps(item))  # no storage path or URL in the checklist

        r = self.admin("get", f"/{mid}/checklist/{doc_req['id']}/document")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(b"".join(r.streaming_content), b"%PDF-secret")
        self.assertEqual(self.call("get", f"/{mid}/checklist/{doc_req['id']}/document", user=self.alice_user).status_code, 403)
        self.assertEqual(self.call("get", f"/{mid}/checklist/{doc_req['id']}/document", user=self.bob_user).status_code, 404)
        self.assertEqual(self.call("get", f"/{mid}/checklist/{doc_req['id']}/document").status_code, 401)

        self.assertEqual(self.admin("post", f"/{mid}/activate").status_code, 422)  # one still open
        r = self.admin("post", f"/{mid}/checklist/{plain_req['id']}/bypass", {"note": "not needed"})
        self.assertEqual((r.status_code, r.json()["state"]), (200, "bypassed"))
        checklist = self.admin("get", f"/{mid}/checklist").json()
        self.assertEqual(sorted(i["state"] for i in checklist), ["bypassed", "complete"])

        # reopen then re-resolve
        self.assertEqual(self.admin("post", f"/{mid}/checklist/{plain_req['id']}/reopen").json()["state"], "open")
        self.assertEqual(self.admin("post", f"/{mid}/checklist/{plain_req['id']}/reopen").status_code, 422)
        self.admin("post", f"/{mid}/checklist/{plain_req['id']}/bypass", {})

        r = self.admin("post", f"/{mid}/activate")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["status"], "active")
        self.assertIsNotNone(r.json()["activated_at"])
        # the member now sees "active" without fee data
        self.assertEqual(self.call("get", f"/{mid}", user=self.alice_user).json()["status"], "active")

    def test_overpay_keeps_balance_at_zero(self):
        m = self.make(self.alice, fee="50.00")
        self.admin("post", f"/{m['id']}/payments", {"amount": "80.00"})
        m = self.admin("get", f"/{m['id']}").json()
        self.assertEqual((m["fee_status"], m["amount_paid"], m["remaining_balance"]), ("paid", "80.00", "0.00"))

    def test_waived_refuses_payments_and_waiver_can_be_lifted(self):
        m = self.make(self.alice, fee="50.00")
        mid = m["id"]
        self.assertEqual(self.admin("post", f"/{mid}/waive", {"waived": True}).json()["fee_status"], "waived")
        self.assertEqual(self.admin("post", f"/{mid}/payments", {"amount": "5.00"}).status_code, 422)
        self.assertEqual(self.admin("post", f"/{mid}/mark-paid").status_code, 422)
        self.assertEqual(self.admin("post", f"/{mid}/waive", {"waived": False}).json()["fee_status"], "unpaid")

    def test_validation_and_duplicates(self):
        m = self.make(self.alice)
        dup = self.admin("post", "", {"member_id": str(self.alice.pk), "season_id": str(self.season.pk)})
        self.assertEqual(dup.status_code, 422)
        self.assertEqual(self.admin("post", f"/{m['id']}/payments", {"amount": "0"}).status_code, 422)
        self.assertEqual(self.admin("post", f"/{m['id']}/payments", {"amount": "1", "method": "bitcoin"}).status_code, 422)
        self.assertEqual(self.admin("patch", f"/{m['id']}", {"status": "active"}).status_code, 422)
        r = self.admin("patch", f"/{m['id']}", {"fee_amount": "75.50"})
        self.assertEqual((r.status_code, r.json()["fee_amount"]), (200, "75.50"))
        self.assertEqual(self.admin("post", "", {"member_id": "00000000-0000-0000-0000-000000000000", "season_id": str(self.season.pk)}).status_code, 404)

    def test_cancel_and_delete_payment(self):
        m = self.make(self.alice, fee="30.00")
        mid = m["id"]
        pay = self.admin("post", f"/{mid}/payments", {"amount": "30.00"}).json()
        self.assertEqual(self.admin("get", f"/{mid}").json()["fee_status"], "paid")
        r = self.admin("delete", f"/payments/{pay['id']}")
        self.assertEqual((r.status_code, r.json()["fee_status"]), (200, "unpaid"))
        self.assertEqual(self.call("delete", f"/payments/{pay['id']}", user=self.alice_user).status_code, 403)
        self.assertEqual(self.admin("post", f"/{mid}/cancel").json()["status"], "cancelled")
        self.assertEqual(self.admin("post", f"/{mid}/cancel").status_code, 422)
        self.assertEqual(self.admin("post", f"/{mid}/activate").status_code, 422)

    def test_requirement_crud(self):
        r = self.admin("post", "/requirements", {"name": "Waiver", "blocked_event_kinds": []})
        self.assertEqual(r.status_code, 201)
        rid = r.json()["id"]
        self.assertEqual(self.admin("post", "/requirements", {"name": "waiver"}).status_code, 422)
        self.assertEqual(self.admin("post", "/requirements", {"name": "X", "blocked_event_kinds": ["nope"]}).status_code, 422)
        r = self.admin("patch", f"/requirements/{rid}", {"description": "Signed"})
        self.assertEqual(r.json()["description"], "Signed")
        self.assertEqual([x["name"] for x in self.admin("get", "/requirements").json()], ["Waiver"])
        self.assertEqual(self.admin("delete", f"/requirements/{rid}").status_code, 204)
        self.assertEqual(self.admin("delete", f"/requirements/{rid}").status_code, 404)

    # -- list, summary, history --
    def test_list_filters_summary_and_query_count(self):
        paid = self.make(self.alice, fee="10.00")
        self.admin("post", f"/{paid['id']}/mark-paid")
        part = self.make(self.bob, fee="20.00")
        self.admin("post", f"/{part['id']}/payments", {"amount": "5.00"})
        unpaid = self.make(self.carol, fee="30.00")
        old = self.make(self.alice, season=self.old_season, fee="9.00")
        TeamMembership.objects.create(team=self.team, member=self.bob, season=self.season)
        self.admin("post", "/requirements", {"name": "R1"})

        def ids(qs=""):
            r = self.admin("get", f"?{qs}")
            self.assertEqual(r.status_code, 200, r.content)
            return {i["id"] for i in r.json()["items"]}

        self.assertEqual(ids(), {paid["id"], part["id"], unpaid["id"]})  # default: current season
        self.assertEqual(ids(f"season={self.old_season.pk}"), {old["id"]})
        self.assertEqual(ids("fee_status=not_paid"), {part["id"], unpaid["id"]})
        self.assertEqual(ids("fee_status=paid"), {paid["id"]})
        self.assertEqual(ids("status=pending&fee_status=unpaid"), {unpaid["id"]})
        self.assertEqual(ids(f"team={self.team.pk}"), {part["id"]})
        self.assertEqual(ids("q=alice"), {paid["id"]})
        self.assertEqual(ids("q=LIC-1"), {paid["id"]})
        self.assertEqual(ids("q=bob@msapi"), {part["id"]})
        self.admin("post", f"/{unpaid['id']}/cancel")
        self.assertEqual(ids("fee_status=not_paid"), {part["id"]})  # cancelled excluded

        r = self.admin("get", "?limit=2").json()
        self.assertEqual((r["count"], len(r["items"])), (3, 2))
        row = next(i for i in self.admin("get", "").json()["items"] if i["id"] == part["id"])
        self.assertEqual((row["amount_due"], row["remaining_balance"], row["open_requirement_count"]), ("20.00", "15.00", 1))

        s = self.admin("get", "/summary").json()
        self.assertEqual({k: s[k] for k in ("total", "paid", "partially_paid", "unpaid", "waived")}, {"total": 2, "paid": 1, "partially_paid": 1, "unpaid": 0, "waived": 0})
        self.assertEqual(s["paid_rate"], 0.5)
        self.assertEqual(self.admin("get", f"/summary?season={self.old_season.pk}").json()["total"], 1)

        # no N+1: the query count must not grow with the number of memberships
        client_headers = {"HTTP_HOST": self.host, "HTTP_X_SESSION_TOKEN": login_session(self.owner)}
        with CaptureQueriesContext(connection) as small:
            Client().get(BASE, **client_headers)
        for i in range(8):
            member = Member.objects.create(first_name=f"Extra{i}", last_name="Person")
            m = self.make(member, fee="10.00")
            self.admin("post", f"/{m['id']}/payments", {"amount": "3.00"})
        with CaptureQueriesContext(connection) as big:
            r = Client().get(BASE + "?limit=50", **client_headers)
        self.assertEqual(len(r.json()["items"]), 11)
        self.assertEqual(len(big), len(small), [q["sql"][:120] for q in big.captured_queries])

    def test_history_and_audit_entries(self):
        m = self.make(self.alice, fee="20.00")
        mid = m["id"]
        pay = self.admin("post", f"/{mid}/payments", {"amount": "5.00"}).json()
        self.admin("delete", f"/payments/{pay['id']}")
        other = self.make(self.bob)
        self.admin("post", f"/{other['id']}/payments", {"amount": "1.00"})
        req = self.admin("post", "/requirements", {"name": "Consent"}).json()
        self.admin("post", f"/{mid}/checklist/{req['id']}/bypass", {"note": "n/a"})

        r = self.admin("get", f"/{mid}/history")
        self.assertEqual(r.status_code, 200, r.content)
        actions = [e["action"] for e in r.json()["items"]]
        self.assertCountEqual(actions, ["membership.created", "payment.recorded", "payment.deleted", "onboarding.bypassed"])
        self.assertEqual(r.json()["count"], 4)
        entry = next(e for e in r.json()["items"] if e["action"] == "payment.recorded")
        self.assertEqual(entry["actor"], self.owner.get_full_name() or self.owner.email)
        self.assertEqual(entry["changes"]["fee_status"], {"from": "unpaid", "to": "partially_paid"})
        # bob's payment is not in alice's history, but is in the global audit trail
        self.assertTrue(AuditEntry.objects.filter(action="payment.recorded", member=self.bob).exists())
        self.assertEqual(self.call("get", f"/{mid}/history", user=self.bob_user).status_code, 404)
