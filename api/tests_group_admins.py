"""Group admins: appointing them, what they may do (schedule for and manage the people of their group), and the
"responds to invites" flag."""

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
from events.models import Attendance, Event, Season
from members.models import AuditEntry, Group, GroupMembership, Member

BASE = "/api/v1"


class GroupAdminApiTests(TestCase):
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
        cls.owner = make_user("owner@grp.test", first="Olivia", last="Owner")
        public = Club(schema_name="public", name="Platform", owner=cls.owner)
        public.auto_create_schema = False
        public.save()
        Domain.objects.create(domain="testserver", tenant=public, is_primary=True)
        cls.club = create_club("Group FC", cls.owner)
        cls.host = Domain.objects.get(tenant=cls.club, is_primary=True).domain
        today = datetime.date.today()
        with schema_context(cls.club.schema_name):
            Season.objects.create(start_date=today - datetime.timedelta(days=30), end_date=today + datetime.timedelta(days=300))
            cls.training = Group.objects.create(name="Off-ice training")
            cls.other = Group.objects.create(name="Committee")
            cls.lead = Member.objects.create(first_name="Lea", last_name="Lead", email="lea@grp.test")
            cls.plain = Member.objects.create(first_name="Pat", last_name="Plain", email="pat@grp.test")
            cls.newbie = Member.objects.create(first_name="Nina", last_name="New", email="nina@grp.test")
            GroupMembership.objects.create(group=cls.training, member=cls.lead, is_admin=True, must_respond=False)
            GroupMembership.objects.create(group=cls.training, member=cls.plain)
        cls.lead_user, _ = grant_login(cls.club, cls.lead)
        cls.plain_user, _ = grant_login(cls.club, cls.plain)
        cls.owner = type(cls.owner).objects.get(pk=cls.owner.pk)

    def call(self, method, path, user=None, data=None):
        headers = {"HTTP_HOST": self.host}
        if user is not None:
            headers["HTTP_X_SESSION_TOKEN"] = login_session(user)
        body = {"data": json.dumps(data), "content_type": "application/json"} if data is not None else {}
        return getattr(Client(), method)(BASE + path, **body, **headers)

    def test_group_admin_sees_only_their_groups_and_may_manage_members(self):
        self.assertEqual(self.call("get", "/members/groups", self.plain_user).status_code, 403)
        rows = self.call("get", "/members/groups", self.lead_user).json()["items"]
        self.assertEqual([g["name"] for g in rows], ["Off-ice training"])
        self.assertEqual(self.call("get", f"/members/groups/{self.other.pk}", self.lead_user).status_code, 403)
        detail = self.call("get", f"/members/groups/{self.training.pk}", self.lead_user).json()
        self.assertEqual((detail["can_manage"], detail["can_manage_members"]), (False, True))
        self.assertEqual({m["first_name"]: (m["is_admin"], m["must_respond"]) for m in detail["members"]}, {"Lea": (True, False), "Pat": (False, True)})
        # add / remove people, and say whether they must respond
        self.assertEqual(self.call("post", f"/members/groups/{self.training.pk}/members/{self.newbie.pk}?must_respond=false", self.lead_user).status_code, 201)
        self.assertEqual(self.call("patch", f"/members/groups/{self.training.pk}/members/{self.newbie.pk}", self.lead_user, {"must_respond": True}).status_code, 200)
        self.assertEqual(self.call("delete", f"/members/groups/{self.training.pk}/members/{self.newbie.pk}", self.lead_user).status_code, 204)
        # but they can't touch other groups, rename or delete, or appoint admins
        r = self.call("post", f"/members/groups/{self.other.pk}/members/{self.newbie.pk}", self.lead_user)
        self.assertEqual(r.status_code, 403, r.content)
        self.assertEqual(self.call("patch", f"/members/groups/{self.training.pk}", self.lead_user, {"name": "X"}).status_code, 403)
        self.assertEqual(self.call("delete", f"/members/groups/{self.training.pk}", self.lead_user).status_code, 403)
        self.assertEqual(self.call("patch", f"/members/groups/{self.training.pk}/members/{self.plain.pk}", self.lead_user, {"is_admin": True}).status_code, 403)
        self.assertEqual(self.call("post", f"/members/groups/{self.training.pk}/members/{self.newbie.pk}?is_admin=true", self.lead_user).status_code, 403)

    def test_members_manager_appoints_admins(self):
        r = self.call("patch", f"/members/groups/{self.training.pk}/members/{self.plain.pk}", self.owner, {"is_admin": True})
        self.assertEqual(r.status_code, 200, r.content)
        with schema_context(self.club.schema_name):
            self.assertTrue(GroupMembership.objects.get(group=self.training, member=self.plain).is_admin)
            self.assertTrue(AuditEntry.objects.filter(action="group.member_updated").exists())
        self.assertEqual(self.call("patch", f"/members/groups/{self.training.pk}/members/{self.newbie.pk}", self.owner, {"is_admin": True}).status_code, 422)  # not in the group

    def test_group_admin_schedules_for_their_group_only(self):
        start = (timezone.now() + datetime.timedelta(days=2)).replace(microsecond=0).isoformat()
        options = self.call("get", "/events/options", self.lead_user).json()
        self.assertEqual([g["name"] for g in options["groups"]], ["Off-ice training"])
        self.assertTrue(options["can_create"])
        r = self.call("post", "/events", self.lead_user, {"kind": "training", "start": start, "group_ids": [str(self.training.pk)]})
        self.assertEqual(r.status_code, 201, r.content)
        event = r.json()
        self.assertEqual((event["title"], event["can_edit"], event["can_manage_attendance"]), ("Training Off-ice training", True, True))
        # the admin doesn't have to respond; the plain member does
        with schema_context(self.club.schema_name):
            self.assertEqual(set(Attendance.objects.filter(event_id=event["id"]).values_list("member_id", flat=True)), {self.plain.pk})
        self.assertEqual(self.call("post", "/events", self.lead_user, {"kind": "training", "start": start, "group_ids": [str(self.other.pk)]}).status_code, 403)
        self.assertEqual(self.call("post", "/events", self.plain_user, {"kind": "training", "start": start, "group_ids": [str(self.training.pk)]}).status_code, 403)
        with schema_context(self.club.schema_name):
            self.assertEqual(Event.objects.count(), 1)

    def test_me_lists_the_groups_you_administer(self):
        r = self.call("get", "/me", self.lead_user)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["admin_group_ids"], [str(self.training.pk)])
        self.assertEqual(self.call("get", "/me", self.plain_user).status_code, 403)  # a plain member still has no management app
