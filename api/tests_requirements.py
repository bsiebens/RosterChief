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
        Member.objects.filter(pk=self.alice.pk).update(language="en")

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

    def test_blocked_people_are_shown_on_the_event_and_the_team_page(self):
        from teams.models import Team, TeamMembership

        def get(path):
            return Client().get(f"/api/v1{path}", HTTP_HOST=self.host, HTTP_X_SESSION_TOKEN=login_session(self.owner))

        req = self.requirement(name="Medical certificate", blocked_event_kinds=["game"])
        team = Team.objects.create(name="Under 12", short_name="U12")
        TeamMembership.objects.create(member=self.alice, team=team, season=self.season)
        game = Event.objects.create(title="Game", kind="game", start=timezone.now() + datetime.timedelta(days=3), season=self.season)
        game.teams.add(team)
        training = Event.objects.create(title="Training", kind="training", start=timezone.now() + datetime.timedelta(days=3), season=self.season)
        training.teams.add(team)
        blocked = get(f"/events/{game.pk}/attendance").json()["blocked"]
        self.assertEqual([(b["first_name"], b["requirements"]) for b in blocked], [("Alice", ["Medical certificate"])])
        self.assertEqual(get(f"/events/{training.pk}/attendance").json()["blocked"], [])
        roster = get(f"/teams/{team.pk}?season={self.season.pk}").json()["roster"]
        self.assertEqual(roster[0]["open_requirements"], ["Medical certificate"])
        self.complete(req)
        self.assertEqual(get(f"/events/{game.pk}/attendance").json()["blocked"], [])
        self.assertEqual(get(f"/teams/{team.pk}?season={self.season.pk}").json()["roster"][0]["open_requirements"], [])

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

    # -- review: decline / request / history / sent file / early renewal --
    def submit(self, req, *, ms=None, name="cert.pdf", **kwargs):
        from members.models import OnboardingRequirement
        from members.services import onboarding

        requirement = OnboardingRequirement.objects.get(pk=req["id"])
        return onboarding.submit_as(self.alice_user, ms or self.alice_ms, requirement, document=SimpleUploadedFile(name, b"%PDF-1.4 x", "application/pdf"), **kwargs)

    def action(self, req, name, data=None, **kw):
        return self.call("post", f"/memberships/{self.alice_ms.pk}/{req['id']}/{name}", data if data is not None else {}, **kw)

    def item(self, req):
        return next(i for i in self.checklist()["items"] if i["requirement"]["id"] == req["id"])

    def test_decline_needs_a_reason_tells_the_member_and_allows_a_new_version(self):
        req = self.requirement(requires_document=True)
        self.assertEqual(self.action(req, "decline", {"note": "x"}).status_code, 422)  # nothing waiting
        self.submit(req)
        self.assertEqual(self.action(req, "decline", {"note": "  "}).status_code, 422)
        self.assertEqual(self.action(req, "decline", {"note": "Blurry"}, user=self.bob_user).status_code, 403)
        done = self.action(req, "decline", {"note": "Photo is blurry"})
        self.assertEqual(done.status_code, 200, done.content)
        item = done.json()
        self.assertEqual((item["display"], item["state"], item["review_note"]), ("declined", "open", "Photo is blurry"))
        self.assertEqual([e["kind"] for e in item["history"]], ["submitted", "declined"])
        self.assertEqual(item["history"][1]["note"], "Photo is blurry")
        note = InboxNotification.objects.get(user=self.alice_user, title__contains="Declined")
        self.assertEqual((note.href, "Photo is blurry" in note.body), ("/parent?tab=payments", True))
        self.submit(req, name="again.pdf")  # a new version goes back to review
        self.assertEqual(self.item(req)["display"], "pending")
        self.assertEqual(self.item(req)["review_note"], "")
        self.assertEqual(self.action(req, "approve").status_code, 200)
        item = self.item(req)
        self.assertEqual((item["display"], [e["kind"] for e in item["history"]]), ("complete", ["submitted", "declined", "submitted", "approved"]))
        self.assertTrue(InboxNotification.objects.filter(user=self.alice_user, title__contains="Approved").exists())
        self.assertTrue(MemberRequirementStatus.objects.filter(is_pending_review=False, review_state="approved").exists())

    def test_request_a_message_at_any_time(self):
        req = self.requirement()
        self.assertEqual(self.action(req, "request", {"note": ""}).status_code, 422)
        item = self.action(req, "request", {"note": "Please send the new form"}).json()
        self.assertEqual((item["display"], item["review_note"]), ("requested", "Please send the new form"))
        self.assertTrue(InboxNotification.objects.filter(user=self.alice_user, body__contains="Please send the new form").exists())
        self.assertEqual(self.item(req)["history"][-1]["kind"], "requested")
        self.complete(req)  # completing resolves it
        self.assertEqual(self.item(req)["display"], "complete")
        item = self.action(req, "request", {"note": "Update please"}).json()  # still valid, but the club asks for an update
        self.assertEqual((item["display"], item["state"]), ("requested", "complete"))
        self.assertEqual(self.action(req, "reopen").status_code, 200)
        self.assertEqual(self.item(req)["display"], "missing")
        self.assertEqual(self.item(req)["review_note"], "")

    def test_early_renewal_keeps_the_old_completion_until_approved(self):
        req = self.requirement(valid_for_months=12, reminder_days_before=30)
        soon = timezone.localdate() + datetime.timedelta(days=10)
        self.complete(req, valid_until=soon.isoformat())
        self.assertEqual(self.item(req)["display"], "expiring_soon")
        new_expiry = timezone.localdate() + datetime.timedelta(days=400)
        self.submit(req, valid_until=new_expiry)
        item = self.item(req)
        self.assertEqual((item["display"], item["state"], item["valid_until"]), ("pending", "complete", soon.isoformat()))  # old one still valid
        self.assertEqual(self.action(req, "approve").status_code, 200)
        item = self.item(req)
        self.assertEqual((item["display"], item["valid_until"]), ("complete", new_expiry.isoformat()))
        # a plain valid completion is still locked
        from members.models import OnboardingRequirement
        from members.services import onboarding
        from members.services.errors import Invalid

        with self.assertRaises(Invalid):
            onboarding.submit_as(self.alice_user, self.alice_ms, OnboardingRequirement.objects.get(pk=req["id"]), document=SimpleUploadedFile("x.pdf", b"%PDF x"))

    def test_declined_renewal_leaves_the_old_completion_in_force(self):
        req = self.requirement(valid_for_months=12)
        self.complete(req, valid_until=(timezone.localdate() + datetime.timedelta(days=5)).isoformat())
        self.submit(req)
        item = self.action(req, "decline", {"note": "Wrong year"}).json()
        self.assertEqual((item["display"], item["state"]), ("declined", "complete"))

    def test_send_a_document_to_one_member(self):
        req = self.requirement()
        files = {"document": SimpleUploadedFile("prefilled.pdf", b"%PDF-1.4 form", "application/pdf"), "note": "Fill in page 2"}
        self.assertEqual(self.call("post", f"/memberships/{self.alice_ms.pk}/{req['id']}/sent-file", files, multipart=True, user=self.bob_user).status_code, 403)
        done = self.call("post", f"/memberships/{self.alice_ms.pk}/{req['id']}/sent-file", {"document": SimpleUploadedFile("prefilled.pdf", b"%PDF-1.4 form", "application/pdf"), "note": "Fill in page 2"}, multipart=True)
        self.assertEqual(done.status_code, 200, done.content)
        item = done.json()
        self.assertEqual((item["has_sent_file"], item["sent_note"], item["history"][-1]["kind"]), (True, "Fill in page 2", "sent_file"))
        self.assertTrue(InboxNotification.objects.filter(user=self.alice_user, title__contains="sent you a document").exists())
        got = self.call("get", f"/memberships/{self.alice_ms.pk}/{req['id']}/sent-file")
        self.assertEqual((got.status_code, b"".join(got.streaming_content)), (200, b"%PDF-1.4 form"))
        self.assertEqual(self.call("get", f"/memberships/{self.alice_ms.pk}/{req['id']}/sent-file", user=self.bob_user).status_code, 404)
        bad = self.call("post", f"/memberships/{self.alice_ms.pk}/{req['id']}/sent-file", {"document": SimpleUploadedFile("x.exe", b"MZ")}, multipart=True)
        self.assertEqual(bad.status_code, 422)
        self.submit(req)  # the member hands the filled-in version back
        self.assertEqual(self.item(req)["display"], "pending")
        self.assertEqual(self.action(req, "approve").status_code, 200)
        locked = self.call("delete", f"/memberships/{self.alice_ms.pk}/{req['id']}/sent-file")
        self.assertEqual(locked.status_code, 422)  # approved: no changes
        self.assertEqual(self.call("post", f"/memberships/{self.alice_ms.pk}/{req['id']}/sent-file", {"document": SimpleUploadedFile("b.pdf", b"%PDF x")}, multipart=True).status_code, 422)

    def test_member_cannot_hand_in_until_the_club_sent_its_file(self):
        from members.services.errors import Invalid

        req = self.requirement(needs_staff_file=True, member_instructions="Take the form to your doctor.")
        self.assertEqual((req["needs_staff_file"], req["member_instructions"]), (True, "Take the form to your doctor."))
        self.assertTrue(self.item(req)["awaiting_file"])
        with self.assertRaises(Invalid) as caught:
            self.submit(req)
        self.assertEqual(caught.exception.code, "awaiting_file")
        sent = self.call("post", f"/memberships/{self.alice_ms.pk}/{req['id']}/sent-file", {"document": SimpleUploadedFile("form.pdf", b"%PDF-1.4 form", "application/pdf")}, multipart=True)
        self.assertEqual(sent.status_code, 200, sent.content)
        self.assertFalse(self.item(req)["awaiting_file"])
        self.submit(req)
        self.assertEqual(self.item(req)["display"], "pending")

    def test_a_question_never_waits_for_a_staff_file(self):
        req = self.requirement(name="Allergies", kind="question", question_type="text", needs_staff_file=True)
        self.assertFalse(req["needs_staff_file"])

    def test_remove_the_sent_document_before_approval(self):
        req = self.requirement()
        self.call("post", f"/memberships/{self.alice_ms.pk}/{req['id']}/sent-file", {"document": SimpleUploadedFile("a.pdf", b"%PDF x")}, multipart=True)
        gone = self.call("delete", f"/memberships/{self.alice_ms.pk}/{req['id']}/sent-file")
        self.assertEqual((gone.status_code, gone.json()["has_sent_file"]), (200, False))
        self.assertEqual(self.call("get", f"/memberships/{self.alice_ms.pk}/{req['id']}/sent-file").status_code, 404)
