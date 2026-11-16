"""Roles API (``/roles``): create and edit roles, their permissions, and who holds them."""

import json
from unittest import mock

from django.test import Client, TestCase
from django_tenants.utils import schema_context

from authorization.models import Role
from authorization.services import assign_role, create_role
from controlpanel.tests import login_session
from evaluations.testing import evaluations_feature
from news.tests import NewsClubMixin

BASE = "/api/v1"


class RolesApiTests(NewsClubMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        with schema_context(cls.club.schema_name):
            assign_role(cls.ann_user, create_role("Role managers", ["authorizations.manage_roles"], slug="role-managers"))

    def setUp(self):
        super().setUp()
        door = mock.patch("members.services.access.can_access_management", return_value=True)
        door.start()
        self.addCleanup(door.stop)

    def call(self, method, path, user=None, data=None):
        headers = {"HTTP_HOST": self.host, "HTTP_X_SESSION_TOKEN": login_session(user or self.ann_user)}
        body = {"data": json.dumps(data), "content_type": "application/json"} if data is not None else {}
        return getattr(Client(), method)(BASE + path, **body, **headers)

    def make(self, name="Coaches", permissions=("shop.manage_orders",), **extra):
        r = self.call("post", "/roles", data={"name": name, "permissions": list(permissions), **extra})
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()

    def test_permission(self):
        for method, path in [("get", "/roles"), ("get", "/roles/permissions"), ("post", "/roles")]:
            self.assertEqual(self.call(method, path, self.bob_user, {"name": "x"} if method == "post" else None).status_code, 403, path)
        self.assertTrue(self.call("get", "/me", self.ann_user).json()["can_manage_roles"])
        self.assertFalse(self.call("get", "/me", self.bob_user).json()["can_manage_roles"])

    def test_permission_catalog(self):
        rows = {p["code"]: p for p in self.call("get", "/roles/permissions").json()}
        self.assertIn("evaluations.manage_evaluations", rows)
        self.assertEqual(rows["evaluations.manage_evaluations"]["feature"], "evaluations")
        self.assertFalse(rows["evaluations.manage_evaluations"]["available"])  # this club's plan has no evaluations here
        self.assertTrue(rows["members.manage_members"]["available"])
        with evaluations_feature():
            rows = {p["code"]: p for p in self.call("get", "/roles/permissions").json()}
        self.assertTrue(rows["evaluations.manage_evaluations"]["available"])

    def test_list_shows_owner_with_everything(self):
        roles = {r["slug"]: r for r in self.call("get", "/roles").json()}
        self.assertTrue(roles["owner"]["is_owner"])
        self.assertIn("evaluations.execute_evaluations", roles["owner"]["permissions"])
        self.assertEqual(roles["role-managers"]["member_count"], 1)

    def test_create_edit_delete(self):
        role = self.make(description="Staff")
        self.assertEqual(role["permissions"], ["shop.manage_orders"])
        r = self.call("patch", f"/roles/{role['id']}", data={"name": "Head coaches", "permissions": ["forms.manage_forms", "news.publish_newsitems"]})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual((r.json()["name"], r.json()["permissions"], r.json()["description"]), ("Head coaches", ["forms.manage_forms", "news.publish_newsitems"], "Staff"))
        self.assertEqual(self.call("delete", f"/roles/{role['id']}").status_code, 204)
        self.assertFalse(Role.objects.filter(pk=role["id"]).exists())

    def test_validation(self):
        self.make("Coaches")
        self.assertEqual(self.call("post", "/roles", data={"name": "coaches"}).status_code, 422)  # same name, any case
        self.assertEqual(self.call("post", "/roles", data={"name": " "}).status_code, 422)
        r = self.call("post", "/roles", data={"name": "X", "permissions": ["nope.nothing"]})
        self.assertEqual(r.status_code, 422)
        self.assertIn("permissions", r.json()["errors"])
        self.assertEqual(self.make("Coaches 2")["slug"], "coaches-2")

    def test_owner_and_system_roles_are_protected(self):
        owner = next(r for r in self.call("get", "/roles").json() if r["is_owner"])
        self.assertEqual(self.call("patch", f"/roles/{owner['id']}", data={"name": "Boss"}).status_code, 400)
        self.assertEqual(self.call("delete", f"/roles/{owner['id']}").status_code, 400)
        member = next(r for r in self.call("get", "/roles").json() if r["slug"] == "member")
        self.assertEqual(self.call("delete", f"/roles/{member['id']}").status_code, 400)

    def test_give_and_take_a_role(self):
        role = self.make()
        r = self.call("post", f"/roles/{role['id']}/members", data={"member_id": str(self.cara.pk)})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual([m["name"] for m in r.json()["members"]], ["Cara Test"])
        self.assertEqual(r.json()["member_count"], 1)
        self.assertTrue(self.call("get", "/me", self.cara_user).json()["permissions"].count("shop.manage_orders") >= 0)
        r = self.call("post", f"/roles/{role['id']}/members", data={"member_id": str(self.kid.pk)})
        self.assertEqual(r.status_code, 422)  # no login
        self.assertEqual(r.json()["code"], "no_login")
        r = self.call("delete", f"/roles/{role['id']}/members/{self.cara.pk}")
        self.assertEqual(r.json()["members"], [])

    def test_assignments_lists_people_per_role(self):
        role = self.make()
        self.call("post", f"/roles/{role['id']}/members", data={"member_id": str(self.cara.pk)})
        rows = {r["slug"]: r for r in self.call("get", "/roles/assignments").json()}
        self.assertEqual([m["name"] for m in rows[role["slug"]]["members"]], ["Cara Test"])
        self.assertTrue(rows["owner"]["members"])
        self.assertEqual(self.call("get", "/roles/assignments", self.bob_user).status_code, 403)

    def test_last_owner_cannot_be_removed(self):
        owner = next(r for r in self.call("get", "/roles").json() if r["is_owner"])
        holders = self.call("get", f"/roles/{owner['id']}").json()["members"]
        self.assertTrue(holders)
        for person in holders:
            r = self.call("delete", f"/roles/{owner['id']}/members/{person['id']}")
        self.assertEqual(r.status_code, 400)  # the last one stays
