from api.panel.tests.base_slice import SliceTestCase
from club.models import ClubRole
from members.models import Group, GroupMembership, Member


class GroupsTests(SliceTestCase):
    def make_group(self, name="Coaches", club=None):
        return Group.objects.create(club=club or self.club, name=name)

    def test_no_token_is_401(self):
        group = self.make_group()
        for method, path in (("get", "/groups"), ("post", "/groups"), ("get", f"/groups/{group.pk}"), ("delete", f"/groups/{group.pk}")):
            self.assertError(self.api(method, "/manage" + path, host="ajax-united.rosterchief.app"), 401, "unauthenticated")

    def test_plain_member_is_403(self):
        group = self.make_group()
        token = self.login()
        self.assertError(self.call("get", "/groups", token), 403, "forbidden")
        self.assertError(self.call("post", "/groups", token, data={"name": "X"}), 403, "forbidden")
        self.assertError(self.call("delete", f"/groups/{group.pk}", token), 403, "forbidden")

    def test_board_member_without_member_admin_is_403(self):
        token = self.as_role(ClubRole.Roles.EDITOR)
        self.assertError(self.call("get", "/groups", token), 403, "forbidden")

    def test_member_admin_can_manage(self):
        token = self.as_role(ClubRole.Roles.MEMBER_ADMIN)
        self.assertEqual(self.call("post", "/groups", token, data={"name": "Refs"}).status_code, 201)

    def test_other_clubs_group_is_404(self):
        other, _season, stranger = self.other_club()
        group = self.make_group("Away", club=other)
        membership = GroupMembership.objects.create(group=group, member=stranger)
        token = self.admin()
        for method, path, data in (
            ("get", f"/groups/{group.pk}", None),
            ("patch", f"/groups/{group.pk}", {"name": "X"}),
            ("delete", f"/groups/{group.pk}", None),
            ("post", f"/groups/{group.pk}/members", {"member_ids": [str(self.child.pk)]}),
            ("delete", f"/groups/{group.pk}/members/{membership.pk}", None),
        ):
            self.assertError(self.call(method, path, token, data=data), 404, "not_found")
        self.assertTrue(Group.objects.filter(pk=group.pk).exists())

    def test_list_counts_filters_and_pages(self):
        coaches = self.make_group("Coaches")
        self.make_group("Referees")
        GroupMembership.objects.create(group=coaches, member=self.child)
        token = self.admin()
        body = self.call("get", "/groups", token).json()
        self.assertEqual([(g["name"], g["member_count"]) for g in body["items"]], [("Coaches", 1), ("Referees", 0)])
        self.assertEqual([g["name"] for g in self.call("get", "/groups?q=ref", token).json()["items"]], ["Referees"])
        first = self.call("get", "/groups?limit=1", token).json()
        self.assertTrue(first["next_cursor"])
        second = self.call("get", f"/groups?limit=1&cursor={first['next_cursor']}", token).json()
        self.assertEqual(second["items"][0]["name"], "Referees")

    def test_create_rename_delete(self):
        token = self.admin()
        created = self.call("post", "/groups", token, data={"name": "Board"})
        self.assertEqual(created.status_code, 201, created.content)
        group_id = created.json()["id"]
        renamed = self.call("patch", f"/groups/{group_id}", token, data={"name": "Bestuur"})
        self.assertEqual(renamed.json()["name"], "Bestuur")
        self.assertEqual(self.call("delete", f"/groups/{group_id}", token).status_code, 204)
        self.assertFalse(Group.objects.filter(pk=group_id).exists())

    def test_validation_422(self):
        self.make_group("Coaches")
        token = self.admin()
        for data in ({"name": ""}, {"name": "Coaches"}):
            response = self.call("post", "/groups", token, data=data)
            self.assertError(response, 422, "validation_error")
            self.assertIn("name", response.json()["error"]["fields"])
        self.assertError(self.call("post", "/groups", token, data={}), 422, "validation_error")
        other = self.make_group("Other")
        self.assertError(self.call("patch", f"/groups/{other.pk}", token, data={"name": "Coaches"}), 422, "validation_error")
        # Renaming to its own name is fine.
        self.assertEqual(self.call("patch", f"/groups/{other.pk}", token, data={"name": "Other"}).status_code, 200)

    def test_create_is_idempotent(self):
        token = self.admin()
        first = self.call("post", "/groups", token, data={"name": "Once"}, HTTP_IDEMPOTENCY_KEY="g-1")
        second = self.call("post", "/groups", token, data={"name": "Once"}, HTTP_IDEMPOTENCY_KEY="g-1")
        self.assertEqual((first.status_code, second.status_code), (201, 201))
        self.assertEqual(first.json()["id"], second.json()["id"])
        self.assertEqual(Group.objects.filter(club=self.club, name="Once").count(), 1)

    def test_add_and_remove_members(self):
        group = self.make_group()
        token = self.admin()
        response = self.call("post", f"/groups/{group.pk}/members", token, data={"member_ids": [str(self.child.pk), str(self.member.pk)]})
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual(body["member_count"], 2)
        self.assertEqual(sorted(m["last_name"] for m in body["members"]), ["Parent", "Parent"])
        membership = next(m for m in body["members"] if m["member_id"] == str(self.child.pk))
        detail = self.call("get", f"/groups/{group.pk}", token).json()
        self.assertEqual(len(detail["members"]), 2)
        self.assertEqual(self.call("delete", f"/groups/{group.pk}/members/{membership['membership_id']}", token).status_code, 204)
        self.assertEqual(GroupMembership.objects.filter(group=group).count(), 1)
        self.assertError(self.call("delete", f"/groups/{group.pk}/members/{membership['membership_id']}", token), 404, "not_found")

    def test_add_members_rejects_duplicates_strangers_and_repeats(self):
        group = self.make_group()
        GroupMembership.objects.create(group=group, member=self.child)
        _other, _season, stranger = self.other_club()
        token = self.admin()
        path = f"/groups/{group.pk}/members"
        for ids in ([self.child.pk], [stranger.pk], [self.member.pk, self.member.pk]):
            response = self.call("post", path, token, data={"member_ids": [str(pk) for pk in ids]})
            self.assertError(response, 422, "validation_error")
            self.assertIn("member_ids", response.json()["error"]["fields"])
        self.assertError(self.call("post", path, token, data={"member_ids": []}), 422, "validation_error")
        self.assertEqual(GroupMembership.objects.filter(group=group).count(), 1)
        self.assertEqual(Member.objects.filter(pk=stranger.pk).count(), 1)

    def test_delete_group_keeps_members(self):
        group = self.make_group()
        GroupMembership.objects.create(group=group, member=self.child)
        token = self.admin()
        self.assertEqual(self.call("delete", f"/groups/{group.pk}", token).status_code, 204)
        self.assertTrue(Member.objects.filter(pk=self.child.pk).exists())
