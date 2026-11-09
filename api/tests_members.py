"""Members REST API: access rules per viewer, CRUD with audit, filters and error shapes."""

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
from events.models import Season
from members.models import AuditEntry, FamilyMembership, Member
from members.services import families, memberships
from teams.models import Position, StaffAssignment, Team, TeamMembership

Role = FamilyMembership.FamilyRole
BASE = "/api/v1/members"


class MembersApiTests(TestCase):
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
        cls.owner = make_user("owner@api.test", first="Olivia", last="Owner")
        public = Club(schema_name="public", name="Platform", owner=cls.owner)
        public.auto_create_schema = False
        public.save()
        Domain.objects.create(domain="testserver", tenant=public, is_primary=True)
        cls.club = create_club("Api FC", cls.owner)
        cls.host = Domain.objects.get(tenant=cls.club, is_primary=True).domain
        today = timezone.localdate()
        with schema_context(cls.club.schema_name):
            cls.season = Season.objects.create(start_date=today - datetime.timedelta(days=100), end_date=today + datetime.timedelta(days=200))
            cls.team = Team.objects.create(name="U12", short_name="U12")
            cls.other_team = Team.objects.create(name="U14", short_name="U14")
            pos = Position.objects.create(name="Coach", short_name="C", staff_position=True)
            mk = lambda first, **kw: Member.objects.create(first_name=first, last_name="Test", **kw)  # noqa: E731
            cls.coach = mk("Coach", email="coach@api.test", phone="+32470123456")
            cls.player = mk("Player", phone="+32470111111", date_of_birth=datetime.date(2014, 1, 1))
            cls.elsewhere = mk("Elsewhere")
            cls.parent = mk("Pat", email="pat@api.test")
            cls.kid = mk("Kid", emergency_phone="+32470222222")
            cls.stranger = mk("Stranger", email="stranger@api.test")
            StaffAssignment.objects.create(team=cls.team, member=cls.coach, season=cls.season, position=pos)
            TeamMembership.objects.create(team=cls.team, member=cls.player, season=cls.season)
            TeamMembership.objects.create(team=cls.other_team, member=cls.elsewhere, season=cls.season)
            fam = families.create_family(cls.owner, members=[(cls.parent, Role.PARENT), (cls.kid, Role.CHILD)])
            cls.family_id = str(fam.pk)
            for m in (cls.player, cls.elsewhere, cls.parent, cls.kid):
                memberships.create_membership(cls.owner, m, cls.season)
        cls.coach_user, _ = grant_login(cls.club, cls.coach)
        cls.parent_user, _ = grant_login(cls.club, cls.parent)
        cls.stranger_user, _ = grant_login(cls.club, cls.stranger)
        cls.owner = type(cls.owner).objects.get(pk=cls.owner.pk)

    # -- request helpers --

    def call(self, method, path, user=None, data=None, **extra):
        headers = {"HTTP_HOST": self.host}
        if user is not None:
            headers["HTTP_X_SESSION_TOKEN"] = login_session(user)
        body = {"data": json.dumps(data), "content_type": "application/json"} if data is not None else {}
        return getattr(Client(), method)(BASE + path, **body, **headers, **extra)

    def get(self, path, user=None, **kw):
        return self.call("get", path, user, **kw)

    def ids(self, response):
        return {i["id"] for i in response.json()["items"]}

    def admin_post(self, path, data):
        return self.call("post", path, self.owner, data)

    # -- anonymous --

    def test_anonymous_is_refused(self):
        for method, path in [("get", ""), ("get", f"/{self.coach.pk}"), ("post", ""), ("get", "/history"), ("get", "/families"), ("get", "/groups")]:
            r = self.call(method, path)
            self.assertIn(r.status_code, (401, 403), (method, path, r.status_code))

    # -- admin --

    def test_admin_lists_everyone_paginated(self):
        r = self.get("", self.owner)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("count", body)
        row = next(i for i in body["items"] if i["id"] == str(self.player.pk))
        self.assertEqual(row["roles"], ["member"])
        self.assertEqual(row["date_of_birth"], "2014-01-01")
        self.assertEqual(row["family_ids"], [])
        self.assertEqual(self.get("?limit=1", self.owner).json()["items"].__len__(), 1)
        # coach is staff only -> hidden by default, shown with include_guardians
        self.assertNotIn(str(self.coach.pk), self.ids(r))
        self.assertIn(str(self.coach.pk), self.ids(self.get("?include_guardians=1", self.owner)))

    def test_detail_has_spaced_phone_for_display(self):
        data = self.get(f"/{self.coach.pk}", self.owner).json()
        self.assertEqual(data["phone"], "+32470123456")  # compact form stays for forms and tel: links
        self.assertEqual(data["phone_display"], "+32 470 12 34 56")
        self.assertIsNone(data["emergency_phone_display"])

    def test_role_none_lists_people_without_a_role_this_season(self):
        # The stranger holds no membership, staff assignment or guardian link; everyone else in the fixture does.
        ids = self.ids(self.get("?role=none", self.owner))
        self.assertIn(str(self.stranger.pk), ids)
        for person in (self.coach, self.player, self.elsewhere, self.parent, self.kid):
            self.assertNotIn(str(person.pk), ids)

    def test_role_none_without_any_season(self):
        with mock.patch.object(Season, "get_current", return_value=None):
            ids = self.ids(self.get("?role=none", self.owner))
        self.assertIn(str(self.stranger.pk), ids)
        self.assertNotIn(str(self.parent.pk), ids)  # still a guardian

    def test_admin_full_crud_and_history(self):
        r = self.admin_post("", {"first_name": "New", "last_name": "Person", "email": "new@api.test", "phone": "+32470999999"})
        self.assertEqual(r.status_code, 201, r.content)
        mid = r.json()["id"]
        self.assertEqual(r.json()["phone"], "+32470999999")

        r = self.call("patch", f"/{mid}", self.owner, {"last_name": "Changed", "language": "nl"})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual((r.json()["last_name"], r.json()["first_name"]), ("Changed", "New"))

        h = self.get(f"/{mid}/history", self.owner).json()
        self.assertEqual([e["action"] for e in h["items"]], ["member.updated", "member.created"])
        self.assertEqual(h["items"][0]["actor_email"], self.owner.email)
        self.assertEqual(h["items"][0]["changes"]["last_name"]["to"], "Changed")
        club_h = self.get("/history?action=member.&target_type=member", self.owner).json()
        self.assertGreaterEqual(club_h["count"], 2)
        self.assertEqual(self.get(f"/history?actor={self.owner.pk}", self.owner).status_code, 200)
        self.assertEqual(self.get("/history?since=2999-01-01T00:00:00Z", self.owner).json()["count"], 0)

        r = self.call("delete", f"/{mid}", self.owner)
        self.assertEqual(r.status_code, 204)
        self.assertEqual(self.get(f"/{mid}", self.owner).status_code, 404)
        self.assertTrue(AuditEntry.objects.filter(action="member.deleted", actor=self.owner).exists())

    def test_grant_login_and_delete_requires_detach(self):
        r = self.admin_post("", {"first_name": "Log", "last_name": "In", "email": "login@api.test"})
        mid = r.json()["id"]
        r = self.admin_post(f"/{mid}/grant-login", {})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(r.json()["user_created"])
        self.assertTrue(self.get(f"/{mid}", self.owner).json()["has_login"])
        r = self.call("delete", f"/{mid}", self.owner)
        self.assertEqual(r.status_code, 422)
        self.assertEqual(self.call("delete", f"/{mid}?detach_login=true", self.owner).status_code, 204)

    def test_invalid_is_422_with_body_shape(self):
        r = self.admin_post("", {"first_name": " ", "last_name": "X"})
        self.assertEqual(r.status_code, 422)
        body = r.json()
        self.assertEqual(body["code"], "invalid")
        self.assertIn("first_name", body["errors"])
        self.assertIn("detail", body)
        r = self.call("patch", f"/{self.player.pk}", self.owner, {"email": "not-an-email"})
        self.assertEqual(r.status_code, 422)
        self.assertIn("email", r.json()["errors"])
        self.assertEqual(self.get("?ordering=password", self.owner).status_code, 422)

    def test_detail_admin_sees_fees_and_groups(self):
        self.admin_post("/groups", {"name": "Coaches"})
        gid = self.get("/groups", self.owner).json()["items"][0]["id"]
        self.assertEqual(self.admin_post(f"/groups/{gid}/members/{self.player.pk}", None).status_code, 201)
        d = self.get(f"/{self.player.pk}", self.owner).json()
        self.assertIn("fee_status", d["club_memberships"][0])
        self.assertIsNotNone(d["club_memberships"][0]["fee_status"])
        self.assertEqual(d["groups"][0]["name"], "Coaches")
        self.assertEqual(d["team_memberships"][0]["team_name"], "U12")
        self.assertEqual(d["phone"], "+32470111111")

    def test_families_are_listed_alphabetically(self):
        in_family = lambda m: [{"member_id": str(m.pk), "role": "parent"}]  # noqa: E731  (a family is only listed with someone in it)
        self.admin_post("/families", {"name": "beta", "members": in_family(self.player)})
        self.admin_post("/families", {"name": "Alpha", "members": in_family(self.elsewhere)})
        self.admin_post("/families", {"name": "", "members": in_family(self.stranger)})  # no name: sorted by its surname ("Test")
        names = [f["display_name"] for f in self.get("/families", self.owner).json()["items"]]
        self.assertEqual(names, sorted(names, key=str.lower))
        self.assertEqual(names[:2], ["Alpha", "beta"])

    def test_families_crud(self):
        r = self.admin_post("/families", {"name": "Fam", "members": [{"member_id": str(self.stranger.pk), "role": "parent"}]})
        self.assertEqual(r.status_code, 201, r.content)
        fid = r.json()["id"]
        self.assertEqual(self.admin_post(f"/families/{fid}/members", {"member_id": str(self.elsewhere.pk), "role": "child"}).status_code, 201)
        self.assertEqual(self.admin_post(f"/families/{fid}/members", {"member_id": str(self.elsewhere.pk), "role": "child"}).status_code, 422)
        r = self.call("patch", f"/families/{fid}/members/{self.elsewhere.pk}", self.owner, {"role": "other"})
        self.assertEqual({m["role"] for m in r.json()["members"]}, {"parent", "other"})
        self.assertEqual(self.call("patch", f"/families/{fid}", self.owner, {"name": "Renamed"}).json()["name"], "Renamed")
        self.assertEqual(self.call("delete", f"/families/{fid}/members/{self.elsewhere.pk}", self.owner).status_code, 204)
        self.assertEqual(self.get("/families", self.owner).json()["count"], 2)
        self.assertEqual(self.call("delete", f"/families/{fid}", self.owner).status_code, 204)
        self.assertEqual(self.get(f"/families/{fid}", self.owner).status_code, 404)

    def test_grant_login_emails_the_member(self):
        from allauth.account.models import EmailAddress
        from django.core import mail

        mid = self.admin_post("", {"first_name": "Kid", "last_name": "Mail", "email": "kid@mail.test"}).json()["id"]
        mail.outbox.clear()
        r = self.admin_post(f"/{mid}/grant-login", {})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(r.json()["email_sent"])
        (msg,) = mail.outbox
        self.assertEqual(msg.to, ["kid@mail.test"])
        html = msg.alternatives[0][0]
        self.assertIn("password/reset/key/", msg.body)
        self.assertIn("password/reset/key/", html)
        self.assertIn("/login", html)
        self.assertTrue(EmailAddress.objects.get(email="kid@mail.test").verified)

    def test_grant_login_email_respects_the_pause(self):
        from django.core import mail
        from django_tenants.utils import schema_context

        from controlpanel.models import EmailSuppression

        mid = self.admin_post("", {"first_name": "Kid", "last_name": "Quiet", "email": "quiet@mail.test"}).json()["id"]
        mail.outbox.clear()
        from django.core.cache import cache

        with schema_context("public"):
            sw = EmailSuppression.objects.first() or EmailSuppression()
            sw.is_active = True
            sw.save()
        cache.delete(EmailSuppression._cache_key())  # the switch is cached for a few seconds
        self.addCleanup(cache.delete, EmailSuppression._cache_key())
        r = self.admin_post(f"/{mid}/grant-login", {})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertFalse(r.json()["email_sent"])
        self.assertEqual(mail.outbox, [])
        self.assertTrue(self.get(f"/{mid}", self.owner).json()["has_login"])  # the login is still granted

    def test_families_filters(self):
        r = self.admin_post("/families", {"name": "Zed", "members": [{"member_id": str(self.stranger.pk), "role": "guardian"}]})
        self.assertEqual(r.status_code, 201, r.content)
        by_role = self.get("/families?role=guardian", self.owner).json()
        self.assertEqual([f["name"] for f in by_role["items"]], ["Zed"])
        self.assertEqual(self.get("/families?q=zed", self.owner).json()["count"], 1)
        self.assertEqual(self.get(f"/families?q={self.stranger.first_name}", self.owner).json()["count"], 1)
        self.assertEqual(self.get("/families?q=nomatch", self.owner).json()["count"], 0)

    def test_member_list_carries_family_names(self):
        r = self.admin_post("/families", {"name": "Zed", "members": [{"member_id": str(self.stranger.pk), "role": "parent"}, {"member_id": str(self.elsewhere.pk), "role": "child"}]})
        self.assertEqual(r.status_code, 201, r.content)
        rows = {row["id"]: row for row in self.get("?include_guardians=true&limit=100", self.owner).json()["items"]}
        self.assertEqual([f["name"] for f in rows[str(self.stranger.pk)]["families"]], ["Zed"])
        self.assertEqual(rows[str(self.player.pk)]["families"], [])

    def test_groups_search(self):
        gid = self.admin_post("/groups", {"name": "Committee"}).json()["id"]
        self.admin_post("/groups", {"name": "Referees"})
        self.admin_post(f"/groups/{gid}/members/{self.player.pk}", None)
        self.assertEqual([g["name"] for g in self.get("/groups?q=comm", self.owner).json()["items"]], ["Committee"])
        self.assertEqual([g["name"] for g in self.get(f"/groups?q={self.player.first_name}", self.owner).json()["items"]], ["Committee"])
        self.assertEqual(self.get("/groups?q=zzz", self.owner).json()["count"], 0)
        self.assertEqual(self.get(f"/groups/{gid}", self.owner).json()["members"][0]["email"], self.player.email)

    def test_groups_admin_only_and_crud(self):
        gid = self.admin_post("/groups", {"name": "Committee"}).json()["id"]
        self.assertEqual(self.admin_post("/groups", {"name": "committee"}).status_code, 422)
        self.assertEqual(self.call("patch", f"/groups/{gid}", self.owner, {"name": "Board"}).json()["name"], "Board")
        self.admin_post(f"/groups/{gid}/members/{self.player.pk}", None)
        self.assertEqual(self.get(f"/groups/{gid}", self.owner).json()["member_count"], 1)
        self.assertEqual(self.call("delete", f"/groups/{gid}/members/{self.player.pk}", self.owner).status_code, 204)
        self.assertEqual(self.call("delete", f"/groups/{gid}", self.owner).status_code, 204)
        self.assertEqual(self.get("/groups", self.coach_user).status_code, 403)
        self.assertEqual(self.get("/groups", self.parent_user).status_code, 403)

    # -- team staff --

    def test_staff_sees_everyone_read_only(self):
        r = self.get("", self.coach_user)
        self.assertEqual(r.status_code, 200)
        ids = self.ids(r)
        for m in (self.player, self.elsewhere, self.parent, self.stranger):
            self.assertIn(str(m.pk), ids)
        d = self.get(f"/{self.player.pk}", self.coach_user).json()
        self.assertEqual(d["phone"], "+32470111111")
        self.assertIsNone(d["club_memberships"][0]["fee_status"])
        self.assertEqual(d["club_memberships"][0]["status"], "pending")
        self.assertEqual(d["groups"], [])
        self.assertEqual(self.get(f"/{self.elsewhere.pk}", self.coach_user).status_code, 200)
        # writes need members.manage_members
        for pk in (self.player.pk, self.elsewhere.pk):
            self.assertEqual(self.call("patch", f"/{pk}", self.coach_user, {"first_name": "X"}).status_code, 403)
            self.assertEqual(self.call("delete", f"/{pk}", self.coach_user).status_code, 403)
        self.assertEqual(self.call("post", "", self.coach_user, {"first_name": "A", "last_name": "B"}).status_code, 403)
        self.assertEqual(self.call("post", f"/{self.player.pk}/grant-login", self.coach_user, {}).status_code, 403)
        self.assertEqual(self.get("/history", self.coach_user).status_code, 404)
        self.assertEqual(self.get(f"/{self.player.pk}/history", self.coach_user).status_code, 404)

    # -- the door: only admins, owners and team staff use the management API --

    def test_parent_and_stranger_have_no_access(self):
        for user in (self.parent_user, self.stranger_user):
            for path in ("", f"/{self.player.pk}", f"/{self.kid.pk}", "/families", f"/families/{self.family_id}", "/teams"):
                r = self.get(path, user)
                self.assertEqual(r.status_code, 403, (path, r.content))
                self.assertEqual(r.json()["code"], "no_access")
            self.assertEqual(self.call("patch", f"/{self.kid.pk}", user, {"first_name": "X"}).status_code, 403)
        self.assertEqual(self.admin_post("/families", {}).status_code, 201)

    def test_staff_of_a_past_season_has_no_access(self):
        today = timezone.localdate()
        with schema_context(self.club.schema_name):
            old = Season.objects.create(start_date=today - datetime.timedelta(days=900), end_date=today - datetime.timedelta(days=500))
            StaffAssignment.objects.filter(member=self.coach).update(season=old)
        self.assertEqual(self.get("", self.coach_user).status_code, 403)

    # -- filters --

    def test_role_filter_member_and_guardian_under_both(self):
        both = str(self.parent.pk)
        self.assertIn(both, self.ids(self.get("?role=member", self.owner)))
        self.assertIn(both, self.ids(self.get("?role=guardian", self.owner)))
        self.assertNotIn(str(self.kid.pk), self.ids(self.get("?role=guardian", self.owner)))
        row = next(i for i in self.get("?role=guardian", self.owner).json()["items"] if i["id"] == both)
        self.assertEqual(row["roles"], ["member", "guardian"])
        self.assertEqual(self.ids(self.get("?role=staff", self.owner)), {str(self.coach.pk)})

    def test_team_filter_none(self):
        none = self.ids(self.get("?team=none", self.owner))
        self.assertIn(str(self.kid.pk), none)  # a member on no team this season
        self.assertNotIn(str(self.player.pk), none)  # on the roster
        self.assertNotIn(str(self.elsewhere.pk), none)  # on another team's roster
        self.assertEqual(self.get("?team=garbage", self.owner).status_code, 422)

    def test_search_season_status_team_ordering(self):
        self.assertEqual(self.ids(self.get("?q=pat", self.owner)), {str(self.parent.pk)})
        self.assertEqual(self.ids(self.get("?q=pat%20test", self.owner)), {str(self.parent.pk)})
        self.assertEqual(self.ids(self.get("?q=stranger@api.test&include_guardians=1", self.owner)), {str(self.stranger.pk)})
        self.assertEqual(len(self.ids(self.get(f"?season={self.season.pk}", self.owner))), 4)
        self.assertEqual(len(self.ids(self.get("?status=pending", self.owner))), 4)
        self.assertEqual(self.ids(self.get("?status=active", self.owner)), set())
        self.assertEqual(len(self.ids(self.get("?fee_status=unpaid", self.owner))), 4)
        self.assertEqual(self.ids(self.get(f"?team={self.team.pk}", self.owner)), {str(self.player.pk)})
        self.assertEqual(self.ids(self.get(f"?team={self.team.pk}&include_guardians=1", self.owner)), {str(self.player.pk), str(self.coach.pk)})
        names = [i["first_name"] for i in self.get("?ordering=-first_name", self.owner).json()["items"]]
        self.assertEqual(names, sorted(names, reverse=True))
