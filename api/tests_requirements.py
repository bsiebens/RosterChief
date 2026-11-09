"""/api/v1/requirements: requirement CRUD, a member's checklist with expiry, complete / bypass / reopen / download, event gating
and the daily reminders (real HTTP for the API, the services for the rest)."""

import datetime
import json
import shutil
import tempfile
from unittest import mock

from dateutil.relativedelta import relativedelta
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from django_tenants.utils import schema_context

from club.models import Club, Domain
from club.services import create_club, grant_login
from controlpanel.tests import login_session, make_user
from events.models import Event, Season
from events.services import attendance
from members.models import ClubMembership, Member, MemberRequirementStatus
from members.services import requirement_reminders
from notifications.models import InboxNotification

BASE = "/api/v1/requirements"


class RequirementsApiTests(TestCase):
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
        cls.owner = make_user("owner@req.test", first="Olivia", last="Owner")
        public = Club(schema_name="public", name="Platform", owner=cls.owner)
        public.auto_create_schema = False
        public.save()
        Domain.objects.create(domain="testserver", tenant=public, is_primary=True)
        cls.club = create_club("Requirements FC", cls.owner)
        cls.host = Domain.objects.get(tenant=cls.club, is_primary=True).domain
        today = timezone.localdate()
        with schema_context(cls.club.schema_name):
            cls.season = Season.objects.create(start_date=today - datetime.timedelta(days=100), end_date=today + datetime.timedelta(days=200))
            cls.alice = Member.objects.create(first_name="Alice", last_name="Anders", email="alice@req.test")
            cls.bob = Member.objects.create(first_name="Bob", last_name="Baker", email="bob@req.test")
            cls.alice_ms = ClubMembership.objects.create(member=cls.alice, season=cls.season, status=ClubMembership.StatusChoices.ACTIVE)
        cls.alice_user, _ = grant_login(cls.club, cls.alice)
        cls.bob_user, _ = grant_login(cls.club, cls.bob)

    def setUp(self):
        door = mock.patch("members.services.access.can_access_management", return_value=True)
        door.start()
        self.addCleanup(door.stop)
        self._ctx = schema_context(self.club.schema_name)
        self._ctx.__enter__()
        self.addCleanup(self._ctx.__exit__, None, None, None)

    # -- helpers --
    def call(self, method, path, data=None, *, user=None, multipart=False):
        kwargs = {"HTTP_HOST": self.host}
        if user is not False:
            kwargs["HTTP_X_SESSION_TOKEN"] = login_session(user or self.owner)
        if data is not None:
            kwargs.update(data=data) if multipart else kwargs.update(data=json.dumps(data), content_type="application/json")
        elif method in ("post", "patch"):
            kwargs.update(data="{}", content_type="application/json")
        return getattr(Client(), method)(f"{BASE}{path}", **kwargs)

    def requirement(self, name="Medical certificate", **extra):
        r = self.call("post", "", {"name": name, **extra})
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()

    def complete(self, req, member_ms=None, **fields):
        ms = member_ms or self.alice_ms
        return self.call("post", f"/memberships/{ms.pk}/{req['id']}/complete", fields, multipart=True)

    def checklist(self, member=None, user=None):
        r = self.call("get", f"/members/{(member or self.alice).pk}/checklist", user=user)
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    # -- access --
    def test_anonymous_rejected(self):
        self.assertEqual(self.call("get", "", user=False).status_code, 401)
        self.assertEqual(self.call("get", f"/members/{self.alice.pk}/checklist", user=False).status_code, 401)

    def test_non_managers_read_but_cannot_change(self):
        req = self.requirement(requires_document=False)
        for method, path, body in [("get", "", None), ("post", "", {"name": "X"}), ("patch", f"/{req['id']}", {"name": "Y"}), ("delete", f"/{req['id']}", None)]:
            self.assertEqual(self.call(method, path, body, user=self.bob_user).status_code, 403, (method, path))
        self.assertEqual(self.checklist(user=self.bob_user)["total"], 1)  # may see the member, may read the checklist
        for action in ("complete", "bypass", "reopen"):
            r = self.call("post", f"/memberships/{self.alice_ms.pk}/{req['id']}/{action}", {"note": "x"}, user=self.bob_user)
            self.assertEqual(r.status_code, 403, action)
        self.assertEqual(self.call("get", f"/memberships/{self.alice_ms.pk}/{req['id']}/document", user=self.bob_user).status_code, 404)

    # -- requirements --
    def test_requirement_crud_and_validation(self):
        req = self.requirement(valid_for_months=12, reminder_days_before=14, blocked_event_kinds=["game"])
        self.assertEqual((req["valid_for_months"], req["reminder_days_before"], req["blocked_event_kinds"]), (12, 14, ["game"]))
        self.assertEqual(self.call("post", "", {"name": "medical certificate"}).status_code, 422)  # name taken, any case
        for bad in ({"valid_for_months": 0}, {"valid_for_months": 500}, {"reminder_days_before": -1}, {"reminder_days_before": 999}, {"blocked_event_kinds": ["nope"]}):
            r = self.call("patch", f"/{req['id']}", bad)
            self.assertEqual(r.status_code, 422, bad)
        r = self.call("patch", f"/{req['id']}", {"valid_for_months": None, "is_active": False})
        self.assertEqual((r.json()["valid_for_months"], r.json()["is_active"]), (None, False))
        listed = self.call("get", "").json()
        self.assertEqual([(x["name"], x["usage_count"]) for x in listed], [("Medical certificate", 0)])
        self.assertEqual(self.call("delete", f"/{req['id']}").status_code, 204)

    # -- checklist and expiry --
    def test_checklist_states_and_expiry(self):
        req = self.requirement(valid_for_months=12, reminder_days_before=30)
        self.assertEqual(self.checklist()["items"][0]["display"], "missing")
        self.assertEqual(self.checklist(self.bob)["membership"], None)  # bob has no membership: nothing to show
        r = self.complete(req, note="seen")
        self.assertEqual(r.status_code, 200, r.content)
        item = r.json()
        self.assertEqual((item["state"], item["display"], item["note"]), ("complete", "complete", "seen"))
        self.assertEqual(item["valid_until"], str(timezone.localdate() + relativedelta(months=12)))
        # Close to expiry: expiring soon, still resolved.
        MemberRequirementStatus.objects.update(valid_until=timezone.localdate() + datetime.timedelta(days=10))
        item = self.checklist()["items"][0]
        self.assertEqual((item["state"], item["display"], item["days_left"]), ("complete", "expiring_soon", 10))
        # Run out: open again, like a missing one.
        MemberRequirementStatus.objects.update(valid_until=timezone.localdate() - datetime.timedelta(days=1))
        data = self.checklist()
        self.assertEqual((data["items"][0]["state"], data["items"][0]["display"], data["expired"], data["open"], data["done"]), ("open", "expired", 1, 1, 0))
        self.assertEqual(self.alice_ms.open_requirement_count, 1)
        # Completing again renews it.
        self.assertEqual(self.complete(req).json()["display"], "complete")

    def test_complete_with_date_document_and_rules(self):
        req = self.requirement(requires_document=True)
        r = self.complete(req)
        self.assertEqual(r.status_code, 422)  # needs a document
        pdf = SimpleUploadedFile("cert.pdf", b"%PDF-1.4 test", content_type="application/pdf")
        future = str(timezone.localdate() + datetime.timedelta(days=90))
        r = self.complete(req, document=pdf, valid_until=future)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual((r.json()["has_document"], r.json()["valid_until"]), (True, future))
        self.assertEqual(self.complete(req, valid_until=str(timezone.localdate() - datetime.timedelta(days=1))).status_code, 422)  # in the past
        bad = SimpleUploadedFile("run.exe", b"MZ", content_type="application/octet-stream")
        self.assertEqual(self.complete(req, document=bad).status_code, 422)
        empty = SimpleUploadedFile("empty.pdf", b"", content_type="application/pdf")
        self.assertEqual(self.complete(req, document=empty).status_code, 422)
        d = self.call("get", f"/memberships/{self.alice_ms.pk}/{req['id']}/document")
        self.assertEqual(d.status_code, 200)
        self.assertEqual(b"".join(d.streaming_content), b"%PDF-1.4 test")
        # Reopen removes the file and the date.
        r = self.call("post", f"/memberships/{self.alice_ms.pk}/{req['id']}/reopen")
        self.assertEqual((r.json()["display"], r.json()["has_document"], r.json()["valid_until"]), ("missing", False, None))
        self.assertEqual(self.call("get", f"/memberships/{self.alice_ms.pk}/{req['id']}/document").status_code, 404)

    def test_bypass_needs_a_note_and_never_expires(self):
        req = self.requirement(valid_for_months=6)
        self.assertEqual(self.call("post", f"/memberships/{self.alice_ms.pk}/{req['id']}/bypass", {"note": "  "}).status_code, 422)
        r = self.call("post", f"/memberships/{self.alice_ms.pk}/{req['id']}/bypass", {"note": "has one on file"})
        self.assertEqual((r.json()["state"], r.json()["display"], r.json()["valid_until"]), ("bypassed", "bypassed", None))

    # -- gating --
    def test_open_requirement_keeps_a_member_out_of_that_kind_of_event(self):
        req = self.requirement(blocked_event_kinds=["game"])
        now = timezone.now() + datetime.timedelta(days=3)
        game = Event.objects.create(title="Game", kind="game", start=now, season=self.season)
        training = Event.objects.create(title="Training", kind="training", start=now, season=self.season)
        for event in (game, training):
            event.invited_members.add(self.alice, self.bob)
        self.assertEqual(set(attendance.effective_members(game).values_list("pk", flat=True)), {self.bob.pk})  # alice: open requirement; bob: no membership
        self.assertEqual(set(attendance.effective_members(training).values_list("pk", flat=True)), {self.alice.pk, self.bob.pk})  # only games are blocked
        self.complete(req)
        self.assertEqual(set(attendance.effective_members(game).values_list("pk", flat=True)), {self.alice.pk, self.bob.pk})
        MemberRequirementStatus.objects.update(valid_until=timezone.localdate() - datetime.timedelta(days=1))  # expired: blocked again
        self.assertEqual(set(attendance.effective_members(game).values_list("pk", flat=True)), {self.bob.pk})

    # -- reminders --
    def reminders(self, now=None):
        return requirement_reminders.send_due(now)

    def test_reminders_missing_expiring_expired(self):
        req = self.requirement(valid_for_months=12, reminder_days_before=30)
        now = timezone.now()
        mine = lambda: InboxNotification.objects.filter(user=self.alice_user).order_by("created")  # noqa: E731
        # Missing: one reminder, not repeated straight away, repeated after two weeks.
        self.assertEqual(self.reminders(now)["reminders"], 1)
        self.assertEqual(self.reminders(now + datetime.timedelta(days=1))["reminders"], 0)
        self.assertEqual(self.reminders(now + datetime.timedelta(days=15))["reminders"], 1)
        # Completed and far from expiry: nothing. Within the reminder window: once.
        self.complete(req)
        self.assertEqual(self.reminders(now)["reminders"], 0)
        MemberRequirementStatus.objects.update(valid_until=timezone.localdate() + datetime.timedelta(days=10))
        self.assertEqual(self.reminders(now)["reminders"], 1)
        self.assertEqual(self.reminders(now + datetime.timedelta(days=5))["reminders"], 0)  # expiring is told once per completion
        # Expired: told again, then every two weeks.
        MemberRequirementStatus.objects.update(valid_until=timezone.localdate() - datetime.timedelta(days=1))
        self.assertEqual(self.reminders(now + datetime.timedelta(days=6))["reminders"], 1)
        self.assertEqual(self.reminders(now + datetime.timedelta(days=7))["reminders"], 0)
        self.assertEqual(self.reminders(now + datetime.timedelta(days=25))["reminders"], 1)
        self.assertGreaterEqual(mine().count(), 5)

    def test_reminders_only_for_active_memberships_and_active_requirements(self):
        req = self.requirement()
        ClubMembership.objects.filter(pk=self.alice_ms.pk).update(status=ClubMembership.StatusChoices.PENDING)
        self.assertEqual(self.reminders()["reminders"], 0)  # not a member yet: the checklist is for staff
        ClubMembership.objects.filter(pk=self.alice_ms.pk).update(status=ClubMembership.StatusChoices.ACTIVE)
        self.call("patch", f"/{req['id']}", {"is_active": False})
        self.assertEqual(self.reminders()["reminders"], 0)

    def test_reminder_goes_to_the_childs_guardian(self):
        from members.models import Family, FamilyMembership

        self.requirement()
        child = Member.objects.create(first_name="Kid", last_name="Anders")
        ClubMembership.objects.create(member=child, season=self.season, status=ClubMembership.StatusChoices.ACTIVE)
        family = Family.objects.create(name="Anders")
        FamilyMembership.objects.create(family=family, member=self.alice, role=FamilyMembership.FamilyRole.PARENT)
        FamilyMembership.objects.create(family=family, member=child, role=FamilyMembership.FamilyRole.CHILD)
        self.reminders()
        titles = list(InboxNotification.objects.filter(user=self.alice_user).values_list("body", flat=True))
        self.assertTrue(any("Kid" in body for body in titles), titles)  # "Kid: ..." on the child's behalf
