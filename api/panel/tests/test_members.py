import datetime

from api.protected.tests import CLUB_HOST, OTHER_HOST, AppApiTestCase
from club.models import Club, ClubMembership, ClubRole
from members.models import Family, FamilyMembership, Member
from teams.models import Position, StaffAssignment, Team

BASE = "/manage/members"


class MembersPanelTestCase(AppApiTestCase):
    def as_role(self, role):
        ClubRole.objects.update_or_create(club=self.club, member=self.member, defaults={"role": role})
        return self.login_with_totp()

    def admin(self):
        return self.as_role(ClubRole.Roles.ADMIN)

    def call(self, method, path, token, **kwargs):
        return self.api(method, BASE + path, token=token, host=CLUB_HOST, **kwargs)

    def other_club_member(self):
        other = Club.objects.create(name="Rival FC", slug="rival-fc")
        stranger = Member.objects.create(first_name="Sam", last_name="Stranger")
        from club.models import Season

        season = Season.objects.create(club=other, start_date=datetime.date.today() - datetime.timedelta(days=10), end_date=datetime.date.today() + datetime.timedelta(days=200))
        ClubMembership.objects.create(club=other, member=stranger, season=season, status=ClubMembership.StatusChoices.ACTIVE)
        family = Family.objects.create(club=other)
        FamilyMembership.objects.create(family=family, member=stranger, role=FamilyMembership.FamilyRole.PARENT)
        return stranger, family


class AccessTests(MembersPanelTestCase):
    def test_no_token_is_401(self):
        for method, path in (("get", ""), ("post", ""), ("get", "/families"), ("get", "/memberships")):
            self.assertError(self.api(method, BASE + path, host=CLUB_HOST), 401, "unauthenticated")

    def test_plain_member_is_403(self):
        token = self.login()
        for method, path in (("get", ""), ("get", f"/{self.child.pk}"), ("get", "/families"), ("get", "/memberships")):
            self.assertError(self.call(method, path, token), 403, "forbidden")
        self.assertError(self.call("post", "", token, data={"first_name": "A", "last_name": "B"}), 403, "forbidden")

    def test_coach_can_read_but_not_write(self):
        team = Team.objects.create(club=self.club, name="U12")
        position = Position.objects.create(club=self.club, name="Assistant", short_name="AC", staff_position=True)
        StaffAssignment.objects.create(team=team, member=self.member, season=self.season, position=position)
        token = self.login()
        self.assertEqual(self.call("get", "", token).status_code, 200)
        self.assertError(self.call("post", "", token, data={"first_name": "A", "last_name": "B"}), 403, "forbidden")
        self.assertError(self.call("delete", f"/{self.child.pk}", token), 403, "forbidden")
        self.assertError(self.call("get", "/memberships", token), 403, "forbidden")

    def test_member_admin_cannot_edit_fees_or_see_membership_list(self):
        token = self.as_role(ClubRole.Roles.MEMBER_ADMIN)
        self.assertEqual(self.call("post", "", token, data={"first_name": "A", "last_name": "B"}).status_code, 201)
        self.assertError(self.call("patch", f"/{self.child.pk}", token, data={"membership": {"fee_status": "paid"}}), 403, "forbidden")
        response = self.call("patch", f"/{self.child.pk}", token, data={"membership": {"license": "L-1"}})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["membership"]["license"], "L-1")
        self.assertError(self.call("get", "/memberships", token), 403, "forbidden")

    def test_other_clubs_objects_are_404(self):
        stranger, family = self.other_club_member()
        token = self.admin()
        for method, path, data in (
            ("get", f"/{stranger.pk}", None),
            ("patch", f"/{stranger.pk}", {"first_name": "X"}),
            ("delete", f"/{stranger.pk}", None),
            ("get", f"/{stranger.pk}/memberships", None),
            ("post", f"/{stranger.pk}/login", {"email": "x@example.com"}),
            ("post", f"/{stranger.pk}/families", {"role": "parent"}),
            ("get", f"/families/{family.pk}", None),
            ("post", f"/families/{family.pk}/children", {"first_name": "A", "last_name": "B"}),
            ("post", f"/families/{family.pk}/parents", {"email": "p@example.com", "first_name": "A", "last_name": "B"}),
            ("patch", f"/families/{family.pk}/members/{stranger.pk}", {"role": "guardian"}),
        ):
            self.assertError(self.call(method, path, token, data=data), 404, "not_found")
        self.assertTrue(Member.objects.filter(pk=stranger.pk).exists())

    def test_wrong_host_club_sees_nothing(self):
        token = self.admin()
        response = self.api("get", BASE, token=token, host=OTHER_HOST)
        self.assertIn(response.status_code, (403, 404))


class MemberTests(MembersPanelTestCase):
    def test_list_default_hides_guardians_and_searches(self):
        token = self.admin()
        response = self.call("get", "", token)
        self.assertEqual(response.status_code, 200, response.content)
        names = {row["first_name"] for row in response.json()["items"]}
        self.assertIn("Kim", names)
        row = next(r for r in response.json()["items"] if r["first_name"] == "Kim")
        self.assertEqual(row["membership"]["status"], "active")
        self.assertEqual(row["families"][0]["role"], "child")
        self.assertEqual([r["first_name"] for r in self.call("get", "?q=kim", token).json()["items"]], ["Kim"])
        self.assertEqual(self.call("get", "?q=zzz", token).json()["items"], [])

    def test_list_kind_and_status_filters(self):
        token = self.admin()
        guardians = self.call("get", "?kind=guardian", token).json()["items"]
        self.assertEqual([r["first_name"] for r in guardians], ["Pat"])
        ClubMembership.objects.filter(member=self.child).update(status="lapsed")
        self.assertNotIn("Kim", {r["first_name"] for r in self.call("get", "", token).json()["items"]})
        self.assertIn("Kim", {r["first_name"] for r in self.call("get", "?status=all", token).json()["items"]})
        self.assertEqual([r["first_name"] for r in self.call("get", "?status=lapsed", token).json()["items"]], ["Kim"])

    def test_list_paginates(self):
        for i in range(3):
            member = Member.objects.create(first_name=f"P{i}", last_name="Zed")
            ClubMembership.objects.create(club=self.club, member=member, season=self.season, status="active")
        token = self.admin()
        first = self.call("get", "?limit=2", token).json()
        self.assertEqual(len(first["items"]), 2)
        self.assertTrue(first["next_cursor"])
        second = self.call("get", f"?limit=2&cursor={first['next_cursor']}", token).json()
        self.assertTrue(second["items"])
        self.assertFalse({r["id"] for r in first["items"]} & {r["id"] for r in second["items"]})
        self.assertError(self.call("get", "?cursor=garbage", token), 400, "invalid_cursor")

    def test_list_does_not_leak_other_club(self):
        stranger, _family = self.other_club_member()
        token = self.admin()
        ids = {r["id"] for r in self.call("get", "?status=all&kind=both", token).json()["items"]}
        self.assertNotIn(str(stranger.pk), ids)

    def test_detail(self):
        token = self.admin()
        body = self.call("get", f"/{self.child.pk}", token).json()
        self.assertEqual(body["first_name"], "Kim")
        self.assertEqual(body["membership"]["kind"], "member")
        self.assertEqual(body["families"][0]["guardians"][0]["first_name"], "Pat")
        self.assertEqual(body["guardians"][0]["first_name"], "Pat")
        self.assertFalse(body["person_fields_locked"])

    def test_create_signs_up_for_current_season(self):
        token = self.admin()
        response = self.call("post", "", token, data={"first_name": "Nia", "last_name": "New", "date_of_birth": "2012-03-04", "email": "nia@example.com"})
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual(body["membership"]["status"], "active")
        self.assertEqual(body["date_of_birth"], "2012-03-04")
        self.assertTrue(ClubMembership.objects.filter(club=self.club, member_id=body["id"], season=self.season).exists())

    def test_create_validation_errors(self):
        token = self.admin()
        response = self.call("post", "", token, data={"first_name": "", "last_name": "X", "email": "nope"})
        self.assertError(response, 422, "validation_error")
        self.assertIn("first_name", response.json()["error"]["fields"])
        self.assertIn("email", response.json()["error"]["fields"])
        self.assertError(self.call("post", "", token, data={"last_name": "X"}), 422, "validation_error")

    def test_create_is_idempotent(self):
        token = self.admin()
        data = {"first_name": "Ida", "last_name": "Once"}
        one = self.call("post", "", token, data=data, HTTP_IDEMPOTENCY_KEY="k1")
        two = self.call("post", "", token, data=data, HTTP_IDEMPOTENCY_KEY="k1")
        self.assertEqual(one.json(), two.json())
        self.assertEqual(Member.objects.filter(last_name="Once").count(), 1)

    def test_update_person_and_membership(self):
        token = self.admin()
        response = self.call("patch", f"/{self.child.pk}", token, data={"first_name": "Kimberly", "membership": {"fee_status": "paid", "license": "L-9"}})
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual((body["first_name"], body["last_name"]), ("Kimberly", "Parent"))
        self.assertEqual((body["membership"]["fee_status"], body["membership"]["license"]), ("paid", "L-9"))

    def test_update_validation_error(self):
        token = self.admin()
        self.assertError(self.call("patch", f"/{self.child.pk}", token, data={"first_name": ""}), 422, "validation_error")
        self.assertError(self.call("patch", f"/{self.child.pk}", token, data={"membership": {"status": "bogus"}}), 422, "validation_error")

    def test_update_locked_person_is_refused_but_membership_edits(self):
        _stranger, _family = self.other_club_member()
        ClubMembership.objects.create(club=Club.objects.get(slug="rival-fc"), member=self.child, season=Club.objects.get(slug="rival-fc").seasons.first(), status="active")
        token = self.admin()
        self.assertTrue(self.call("get", f"/{self.child.pk}", token).json()["person_fields_locked"])
        self.assertError(self.call("patch", f"/{self.child.pk}", token, data={"first_name": "Nope"}), 409, "person_locked")
        self.assertEqual(self.call("patch", f"/{self.child.pk}", token, data={"membership": {"license": "ok"}}).status_code, 200)

    def test_delete_removes_unshared_member(self):
        loner = Member.objects.create(first_name="Lo", last_name="Ner")
        ClubMembership.objects.create(club=self.club, member=loner, season=self.season, status="active")
        token = self.admin()
        response = self.call("delete", f"/{loner.pk}", token)
        self.assertEqual(response.json(), {"result": "deleted"})
        self.assertFalse(Member.objects.filter(pk=loner.pk).exists())

    def test_delete_shared_member_only_leaves_club(self):
        rival = Club.objects.create(name="Rival FC", slug="rival-fc")
        from club.models import Season

        other_season = Season.objects.create(club=rival, start_date=datetime.date.today(), end_date=datetime.date.today() + datetime.timedelta(days=100))
        ClubMembership.objects.create(club=rival, member=self.child, season=other_season, status="active")
        token = self.admin()
        self.assertEqual(self.call("delete", f"/{self.child.pk}", token).json(), {"result": "removed_from_club"})
        self.assertTrue(Member.objects.filter(pk=self.child.pk).exists())
        self.assertFalse(ClubMembership.objects.filter(club=self.club, member=self.child).exists())
        self.assertTrue(ClubMembership.objects.filter(club=rival, member=self.child).exists())

    def test_membership_history(self):
        token = self.admin()
        body = self.call("get", f"/{self.child.pk}/memberships", token).json()
        self.assertEqual(len(body["items"]), 1)
        self.assertEqual(body["items"][0]["season_id"], str(self.season.pk))

    def test_membership_list_for_admin(self):
        token = self.admin()
        ClubMembership.objects.filter(member=self.child).update(fee_amount="50.00")
        body = self.call("get", "/memberships", token).json()
        self.assertEqual(body["season_id"], str(self.season.pk))
        self.assertEqual([r["first_name"] for r in body["items"]], ["Kim"])
        ClubMembership.objects.filter(member=self.child).update(fee_status="paid")
        self.assertEqual(self.call("get", "/memberships", token).json()["items"], [])
        self.assertEqual(len(self.call("get", "/memberships?fee_status=paid&q=kim", token).json()["items"]), 1)

    def test_grant_login(self):
        token = self.admin()
        response = self.call("post", f"/{self.child.pk}/login", token, data={"email": "kim@example.com"})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()["has_login"])
        self.assertError(self.call("post", f"/{self.child.pk}/login", token, data={"email": "again@example.com"}), 409, "has_login")

    def test_grant_login_rejects_used_email(self):
        loner = Member.objects.create(first_name="Lo", last_name="Ner")
        ClubMembership.objects.create(club=self.club, member=loner, season=self.season, status="active")
        token = self.admin()
        response = self.call("post", f"/{loner.pk}/login", token, data={"email": self.user.email})
        self.assertError(response, 422, "validation_error")
        self.assertIn("email", response.json()["error"]["fields"])


class FamilyTests(MembersPanelTestCase):
    def test_list_and_detail(self):
        token = self.admin()
        body = self.call("get", "/families", token).json()
        self.assertEqual(len(body["items"]), 1)
        family_id = body["items"][0]["id"]
        self.assertEqual(body["items"][0]["children"][0]["first_name"], "Kim")
        self.assertEqual(len(self.call("get", "/families?q=zzz", token).json()["items"]), 0)
        detail = self.call("get", f"/families/{family_id}", token).json()
        self.assertEqual(detail["guardians"][0]["first_name"], "Pat")

    def test_create_family(self):
        token = self.admin()
        data = {"parent_first_name": "Pia", "parent_last_name": "Neu", "parent_email": "pia@example.com", "child_first_name": "Noor", "child_last_name": "Neu"}
        response = self.call("post", "/families", token, data=data)
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual(body["children"][0]["first_name"], "Noor")
        self.assertEqual(body["guardians"][0]["has_login"], True)
        self.assertEqual(Family.objects.filter(club=self.club).count(), 2)

    def test_create_family_validation(self):
        token = self.admin()
        response = self.call("post", "/families", token, data={"parent_first_name": "", "parent_last_name": "N", "parent_email": "bad", "child_first_name": "C", "child_last_name": "N"})
        self.assertError(response, 422, "validation_error")
        self.assertEqual(Family.objects.count(), 1)

    def test_add_child(self):
        token = self.admin()
        family = self.member.family_memberships.get().family
        response = self.call("post", f"/families/{family.pk}/children", token, data={"first_name": "Sib", "last_name": "Parent"})
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual({c["first_name"] for c in response.json()["children"]}, {"Kim", "Sib"})
        self.assertError(self.call("post", f"/families/{family.pk}/children", token, data={"first_name": "", "last_name": "x"}), 422, "validation_error")

    def test_add_parent_new_existing_and_repeat(self):
        token = self.admin()
        family = self.member.family_memberships.get().family
        path = f"/families/{family.pk}/parents"
        new = self.call("post", path, token, data={"email": "second@example.com", "first_name": "Sec", "last_name": "Parent"})
        self.assertEqual(new.status_code, 201, new.content)
        self.assertEqual(new.json()["result"], "added")
        again = self.call("post", path, token, data={"email": "second@example.com", "first_name": "Sec", "last_name": "Parent"})
        self.assertEqual(again.json()["result"], "already_in_family")
        existing = self.call("post", path, token, data={"email": "parent@example.com", "first_name": "Pat", "last_name": "Parent"})
        self.assertEqual(existing.json()["result"], "already_in_family")

    def test_role_update(self):
        token = self.admin()
        family = self.member.family_memberships.get().family
        response = self.call("patch", f"/families/{family.pk}/members/{self.member.pk}", token, data={"role": "guardian"})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["guardians"][0]["role"], "guardian")
        self.assertError(self.call("patch", f"/families/{family.pk}/members/{self.member.pk}", token, data={"role": "bogus"}), 422, "validation_error")

    def test_attach_and_detach(self):
        loner = Member.objects.create(first_name="Lo", last_name="Ner")
        ClubMembership.objects.create(club=self.club, member=loner, season=self.season, status="active")
        token = self.admin()
        family = self.member.family_memberships.get().family

        response = self.call("post", f"/{loner.pk}/families", token, data={"role": "parent", "family_id": str(family.pk)})
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["families"][0]["id"], str(family.pk))
        self.assertError(self.call("post", f"/{loner.pk}/families", token, data={"role": "parent", "family_id": str(family.pk)}), 422, "validation_error")

        response = self.call("delete", f"/{loner.pk}/families/{family.pk}", token)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["families"], [])
        self.assertError(self.call("delete", f"/{loner.pk}/families/{family.pk}", token), 404, "not_found")

        created = self.call("post", f"/{loner.pk}/families", token, data={"role": "parent"})
        self.assertEqual(len(created.json()["families"]), 1)
        self.assertEqual(Family.objects.filter(club=self.club).count(), 2)

    def test_detach_last_member_deletes_family(self):
        loner = Member.objects.create(first_name="Lo", last_name="Ner")
        ClubMembership.objects.create(club=self.club, member=loner, season=self.season, status="active")
        token = self.admin()
        family_id = self.call("post", f"/{loner.pk}/families", token, data={"role": "parent"}).json()["families"][0]["id"]
        self.call("delete", f"/{loner.pk}/families/{family_id}", token)
        self.assertFalse(Family.objects.filter(pk=family_id).exists())
