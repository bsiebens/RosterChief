"""Sign-up queue API (``/signups``) and the admin add-member flow (``POST /members`` with a membership, family and invite)."""

import json
from decimal import Decimal
from unittest import mock

from django.test import Client, TestCase
from django_tenants.utils import schema_context

from controlpanel.tests import login_session
from members.models import ClubMembership, Family, FamilyMembership, Member
from members.services import memberships, onboarding
from news.tests import NewsClubMixin
from teams.models import Position, TeamMembership

BASE = "/api/v1"


class SignupsApiTests(NewsClubMixin, TestCase):
    def setUp(self):
        super().setUp()
        door = mock.patch("members.services.access.can_access_management", return_value=True)
        door.start()
        self.addCleanup(door.stop)

    def call(self, method, path, user=None, data=None):
        headers = {"HTTP_HOST": self.host, "HTTP_X_SESSION_TOKEN": login_session(user or self.owner)}
        body = {"data": json.dumps(data), "content_type": "application/json"} if data is not None else {}
        return getattr(Client(), method)(BASE + path, **body, **headers)

    def person(self, first):
        """A member with no season membership yet (the shared fixture members all have one)."""
        return Member.objects.create(first_name=first, last_name="Test", email=f"{first.lower()}@signup.test")

    def signup(self, member, fee="40.00", paid=False, waived=False):
        m = memberships.create_membership(self.owner, member, self.season, fee_amount=Decimal(fee))
        if paid:
            memberships.mark_paid(self.owner, m)
        if waived:
            memberships.set_fee_waived(self.owner, m, True)
        return m

    def test_permission(self):
        for method, path in [("get", "/signups"), ("get", "/signups/summary"), ("get", "/signups/options"), ("post", "/signups/approve-clean")]:
            r = self.call(method, path, self.cara_user, {} if method == "post" else None)
            self.assertEqual(r.status_code, 403, path)

    def test_queue_lists_pending_with_clean_flag_and_open_requirements(self):
        paid, unpaid, waived = self.signup(self.person("Ann"), paid=True), self.signup(self.person("Bob"), paid=False), self.signup(self.person("Cara"), waived=True)
        onboarding.create_requirement(self.owner, name="Medical certificate")
        rows = {r["member"]["name"]: r for r in self.call("get", "/signups").json()["results"]}
        self.assertEqual(set(rows), {"Ann Test", "Bob Test", "Cara Test"})
        self.assertFalse(any(r["clean"] for r in rows.values()))  # the requirement is open for everyone
        self.assertEqual(rows["Ann Test"]["open_requirements"], ["Medical certificate"])
        requirement = onboarding.open_requirements().get()
        for m in (paid, waived):
            onboarding.bypass(self.owner, m, requirement, note="not needed")
        body = self.call("get", "/signups").json()
        self.assertEqual((body["count"], body["clean_count"]), (3, 2))
        self.assertEqual(sorted(r["member"]["name"] for r in self.call("get", "/signups?only=clean").json()["results"]), ["Ann Test", "Cara Test"])
        self.assertEqual([r["member"]["name"] for r in self.call("get", "/signups?only=open").json()["results"]], ["Bob Test"])
        self.assertEqual([r["member"]["name"] for r in self.call("get", "/signups?q=cara").json()["results"]], ["Cara Test"])
        self.assertEqual(self.call("get", "/signups/summary").json()["not_clean"], 1)
        self.assertEqual(unpaid.status, ClubMembership.StatusChoices.PENDING)

    def test_approve_rules(self):
        unpaid = self.signup(self.person("Ann"))
        r = self.call("post", f"/signups/{unpaid.pk}/approve")
        self.assertEqual(r.status_code, 422)  # the fee is not settled; paying alone never activates, approving is deliberate
        memberships.mark_paid(self.owner, unpaid)
        unpaid.refresh_from_db()
        self.assertEqual(unpaid.status, ClubMembership.StatusChoices.PENDING)
        onboarding.create_requirement(self.owner, name="Photo")
        r = self.call("post", f"/signups/{unpaid.pk}/approve")
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.json()["errors"]["requirements"], ["Photo"])
        onboarding.bypass(self.owner, unpaid, onboarding.open_requirements().get())
        r = self.call("post", f"/signups/{unpaid.pk}/approve")
        self.assertEqual(r.status_code, 200, r.content)
        unpaid.refresh_from_db()
        self.assertEqual(unpaid.status, ClubMembership.StatusChoices.ACTIVE)
        self.assertIsNotNone(unpaid.activated_at)
        self.assertEqual(self.call("post", f"/signups/{unpaid.pk}/approve").status_code, 422)  # no longer pending

    def test_approve_all_clean_only_activates_ready_ones(self):
        ready, waiting = self.signup(self.person("Ann"), paid=True), self.signup(self.person("Bob"))
        self.assertEqual(self.call("post", "/signups/approve-clean", data={}).json(), {"approved": 1})
        ready.refresh_from_db()
        waiting.refresh_from_db()
        self.assertEqual((ready.status, waiting.status), (ClubMembership.StatusChoices.ACTIVE, ClubMembership.StatusChoices.PENDING))
        self.assertEqual(self.call("get", "/signups/summary").json()["pending"], 1)

    def test_place_in_team(self):
        bob = self.person("Bob")
        m = self.signup(bob)
        position = Position.objects.create(name="Forward", short_name="F", staff_position=False)
        opts = self.call("get", "/signups/options").json()
        self.assertIn("Under 12", [t["name"] for t in opts["teams"]])
        self.assertEqual([(p["name"], p["staff"]) for p in opts["positions"]], [("Forward", False)])
        r = self.call("post", f"/signups/{m.pk}/place", data={"team_id": str(self.team.pk), "position_id": str(position.pk), "jersey_number": 9})
        self.assertEqual(r.status_code, 201, r.content)
        self.assertTrue(TeamMembership.objects.filter(team=self.team, member=bob, season=self.season, jersey_number=9).exists())
        self.assertEqual(self.call("post", f"/signups/{m.pk}/place", data={"team_id": str(self.team.pk)}).status_code, 422)  # twice
        self.assertEqual([t["name"] for t in self.call("get", "/signups").json()["results"][0]["teams"]], ["Under 12"])
        self.assertEqual(self.call("post", f"/signups/{m.pk}/place", data={"team_id": "00000000-0000-0000-0000-000000000000"}).status_code, 422)

    def test_a_volunteer_is_placed_on_the_staff_with_a_staff_position(self):
        from teams.models import StaffAssignment

        vera = self.person("Vera")
        m = self.signup(vera)
        coach = Position.objects.create(name="Team manager", short_name="TM", staff_position=True)
        opts = self.call("get", "/signups/options").json()
        self.assertIn(("Team manager", True), [(p["name"], p["staff"]) for p in opts["positions"]])
        r = self.call("post", f"/signups/{m.pk}/place", data={"team_id": str(self.team.pk), "position_id": str(coach.pk), "jersey_number": 9})
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual((r.json()["position"], r.json()["jersey_number"]), ("Team manager", None))
        self.assertTrue(StaffAssignment.objects.filter(team=self.team, member=vera, season=self.season, position=coach).exists())
        self.assertFalse(TeamMembership.objects.filter(member=vera).exists())
        self.assertEqual(self.call("post", f"/signups/{m.pk}/place", data={"team_id": str(self.team.pk), "position_id": str(coach.pk)}).status_code, 422)  # twice

    def test_cancel_removes_a_bare_signup_but_keeps_anyone_with_ties(self):
        with schema_context(self.club.schema_name):
            bare = Member.objects.create(first_name="Bare", last_name="Newcomer", email="bare@x.test")
        bare_m = self.signup(bare)
        self.assertEqual(self.call("post", f"/signups/{bare_m.pk}/cancel").json(), {"result": "deleted"})
        self.assertFalse(Member.objects.filter(pk=bare.pk).exists())
        bob = self.person("Bob")
        returning = self.signup(bob)
        TeamMembership.objects.create(team=self.team, member=bob, season=self.season)
        self.assertEqual(self.call("post", f"/signups/{returning.pk}/cancel").json(), {"result": "cancelled"})
        returning.refresh_from_db()
        self.assertEqual(returning.status, ClubMembership.StatusChoices.CANCELLED)
        self.assertTrue(Member.objects.filter(pk=bob.pk).exists())
        self.assertEqual(self.call("post", f"/signups/{returning.pk}/cancel").status_code, 422)  # not pending any more


class AddMemberFlowTests(NewsClubMixin, TestCase):
    def setUp(self):
        super().setUp()
        door = mock.patch("members.services.access.can_access_management", return_value=True)
        door.start()
        self.addCleanup(door.stop)

    def call(self, method, path, user=None, data=None):
        headers = {"HTTP_HOST": self.host, "HTTP_X_SESSION_TOKEN": login_session(user or self.owner)}
        body = {"data": json.dumps(data), "content_type": "application/json"} if data is not None else {}
        return getattr(Client(), method)(BASE + path, **body, **headers)

    def create(self, **extra):
        return self.call("post", "/members", data={"first_name": "Nina", "last_name": "New", **extra})

    def test_plain_create_is_unchanged(self):
        r = self.create()
        self.assertEqual(r.status_code, 201, r.content)
        member = Member.objects.get(pk=r.json()["id"])
        self.assertFalse(member.club_memberships.exists())  # nothing extra unless asked for

    def test_active_membership_in_the_current_season_skips_the_queue(self):
        r = self.create(membership={"status": "active"})
        self.assertEqual(r.status_code, 201, r.content)
        m = ClubMembership.objects.get(member_id=r.json()["id"])
        self.assertEqual((m.season_id, m.status), (self.season.pk, ClubMembership.StatusChoices.ACTIVE))
        self.assertIsNotNone(m.activated_at)
        self.assertEqual(r.json()["membership_id"], str(m.pk))

    def test_pending_membership_with_fee_or_waived(self):
        r = self.create(membership={"status": "pending", "fee_amount": "55.00"})
        m = ClubMembership.objects.get(member_id=r.json()["id"])
        self.assertEqual((m.status, m.fee_amount, m.fee_status), (ClubMembership.StatusChoices.PENDING, Decimal("55.00"), "unpaid"))
        r = self.create(first_name="Wanda", membership={"status": "pending", "fee_amount": "55.00", "waived": True})
        self.assertEqual(ClubMembership.objects.get(member_id=r.json()["id"]).fee_status, "waived")
        self.assertEqual(self.call("get", "/signups/summary").json()["pending"], 2)

    def test_membership_errors_do_not_leave_a_half_created_member(self):
        r = self.create(membership={"status": "active", "season_id": "00000000-0000-0000-0000-000000000000"})
        self.assertEqual(r.status_code, 422)
        self.assertFalse(Member.objects.filter(first_name="Nina").exists())

    def test_join_an_existing_family(self):
        fam = Family.objects.get(memberships__member=self.kid)
        r = self.create(family={"family_id": str(fam.pk), "role": "child"})
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(FamilyMembership.objects.get(family=fam, member_id=r.json()["id"]).role, "child")

    def test_guardians_existing_and_new_make_a_family(self):
        r = self.create(date_of_birth="2015-05-05", guardians=[{"member_id": str(self.parent.pk)}, {"first_name": "Gus", "last_name": "Guardian", "email": "gus@x.test"}])
        self.assertEqual(r.status_code, 201, r.content)
        child = Member.objects.get(pk=r.json()["id"])
        family = child.family_memberships.get().family
        roles = {fm.member.first_name: fm.role for fm in family.memberships.select_related("member")}
        self.assertEqual(roles, {"Nina": "child", "Parent": "parent", "Gus": "parent"})
        self.assertEqual(len(r.json()["guardian_ids"]), 2)

    def test_invite_grants_a_login_and_sends_the_email(self):
        with mock.patch("api.routers.members.send_login_granted_email", return_value=True) as send:
            r = self.create(email="nina@x.test", invite={"send": True})
        self.assertEqual(r.status_code, 201, r.content)
        self.assertTrue(Member.objects.get(pk=r.json()["id"]).user_id)
        self.assertEqual(r.json()["invite"], {"sent": True, "user_created": True, "email": "nina@x.test"})
        self.assertEqual(send.call_count, 1)
        # no email address: the member is still created, the invite is reported as not possible
        r = self.create(first_name="Nomail", invite={"send": True})
        self.assertEqual(r.status_code, 422)
        self.assertFalse(Member.objects.filter(first_name="Nomail").exists())
