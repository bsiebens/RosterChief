from api.panel.tests.base_slice import SliceTestCase
from club.models import ClubRole
from teams.models import NumberPool, Position, Team, TeamMembership


class PositionsTests(SliceTestCase):
    def payload(self, **extra):
        return {"name": "Goalie", "short_name": "G", **extra}

    def test_no_token_is_401(self):
        for method in ("get", "post"):
            self.assertError(self.api(method, "/manage/positions", host="ajax-united.rosterchief.app"), 401, "unauthenticated")

    def test_plain_member_is_403(self):
        token = self.login()
        self.assertError(self.call("get", "/positions", token), 403, "forbidden")

    def test_staff_read_but_only_admin_writes(self):
        Position.objects.create(club=self.club, name="Forward", short_name="F", ordering=2)
        Position.objects.create(club=self.club, name="Back", short_name="B", ordering=1)
        token = self.as_role(ClubRole.Roles.MEMBER_ADMIN)
        for role in (ClubRole.Roles.MEMBER_ADMIN, ClubRole.Roles.EDITOR):
            ClubRole.objects.filter(club=self.club, member=self.member).update(role=role)
            listed = self.call("get", "/positions", token)
            self.assertEqual(listed.status_code, 200)
            self.assertEqual([p["name"] for p in listed.json()["items"]], ["Back", "Forward"])
            self.assertError(self.call("post", "/positions", token, data=self.payload()), 403, "forbidden")
        position = Position.objects.first()
        self.assertError(self.call("patch", f"/positions/{position.pk}", token, data={"name": "Z"}), 403, "forbidden")
        self.assertError(self.call("delete", f"/positions/{position.pk}", token), 403, "forbidden")

    def test_other_clubs_position_is_404(self):
        other, _season, _stranger = self.other_club()
        position = Position.objects.create(club=other, name="Away", short_name="A")
        token = self.admin()
        for method, data in (("get", None), ("patch", {"name": "X"}), ("delete", None)):
            self.assertError(self.call(method, f"/positions/{position.pk}", token, data=data), 404, "not_found")
        self.assertTrue(Position.objects.filter(pk=position.pk).exists())

    def test_crud(self):
        token = self.admin()
        created = self.call("post", "/positions", token, data=self.payload(staff_position=True, management_position=True, ordering=3))
        self.assertEqual(created.status_code, 201, created.content)
        body = created.json()
        self.assertEqual((body["staff_position"], body["management_position"], body["ordering"]), (True, True, 3))
        patched = self.call("patch", f"/positions/{body['id']}", token, data={"short_name": "GK"})
        self.assertEqual(patched.status_code, 200, patched.content)
        self.assertEqual((patched.json()["short_name"], patched.json()["staff_position"]), ("GK", True))
        self.assertEqual(self.call("get", f"/positions/{body['id']}", token).json()["name"], "Goalie")
        self.assertEqual(self.call("delete", f"/positions/{body['id']}", token).status_code, 204)
        self.assertFalse(Position.objects.filter(pk=body["id"]).exists())

    def test_validation_422(self):
        Position.objects.create(club=self.club, name="Goalie", short_name="G")
        token = self.admin()
        duplicate = self.call("post", "/positions", token, data=self.payload())
        self.assertError(duplicate, 422, "validation_error")
        self.assertIn("name", duplicate.json()["error"]["fields"])
        management = self.call("post", "/positions", token, data=self.payload(name="Coach", management_position=True))
        self.assertError(management, 422, "validation_error")
        self.assertIn("management_position", management.json()["error"]["fields"])
        self.assertError(self.call("post", "/positions", token, data={"name": ""}), 422, "validation_error")
        other = Position.objects.create(club=self.club, name="Other", short_name="O")
        self.assertError(self.call("patch", f"/positions/{other.pk}", token, data={"name": "Goalie"}), 422, "validation_error")
        # An existing staff position can drop staff_position only together with management_position.
        staff = Position.objects.create(club=self.club, name="Head", short_name="H", staff_position=True, management_position=True)
        self.assertError(self.call("patch", f"/positions/{staff.pk}", token, data={"staff_position": False}), 422, "validation_error")

    def test_delete_in_use_is_409(self):
        position = Position.objects.create(club=self.club, name="Goalie", short_name="G")
        team = Team.objects.create(club=self.club, name="U12")
        TeamMembership.objects.create(team=team, member=self.child, season=self.season, position=position)
        token = self.admin()
        self.assertError(self.call("delete", f"/positions/{position.pk}", token), 409, "position_in_use")
        self.assertTrue(Position.objects.filter(pk=position.pk).exists())

    def test_create_is_idempotent(self):
        token = self.admin()
        first = self.call("post", "/positions", token, data=self.payload(), HTTP_IDEMPOTENCY_KEY="p-1")
        second = self.call("post", "/positions", token, data=self.payload(), HTTP_IDEMPOTENCY_KEY="p-1")
        self.assertEqual((first.status_code, second.status_code), (201, 201))
        self.assertEqual(first.json()["id"], second.json()["id"])
        self.assertEqual(Position.objects.filter(club=self.club).count(), 1)


class PoolsTests(SliceTestCase):
    def payload(self, **extra):
        return {"name": "Youth", "min_number": 1, "max_number": 99, **extra}

    def test_no_token_is_401(self):
        self.assertError(self.api("get", "/manage/number-pools", host="ajax-united.rosterchief.app"), 401, "unauthenticated")

    def test_plain_member_is_403(self):
        self.assertError(self.call("get", "/number-pools", self.login()), 403, "forbidden")

    def test_staff_read_but_only_admin_writes(self):
        NumberPool.objects.create(club=self.club, name="Seniors", min_number=1, max_number=50)
        token = self.as_role(ClubRole.Roles.MEMBER_ADMIN)
        self.assertEqual(self.call("get", "/number-pools", token).status_code, 200)
        self.assertError(self.call("post", "/number-pools", token, data=self.payload()), 403, "forbidden")
        pool = NumberPool.objects.get()
        self.assertError(self.call("patch", f"/number-pools/{pool.pk}", token, data={"name": "Z"}), 403, "forbidden")
        self.assertError(self.call("delete", f"/number-pools/{pool.pk}", token), 403, "forbidden")

    def test_other_clubs_pool_is_404(self):
        other, _season, _stranger = self.other_club()
        pool = NumberPool.objects.create(club=other, name="Away", min_number=1, max_number=9)
        token = self.admin()
        for method, data in (("get", None), ("patch", {"name": "X"}), ("delete", None)):
            self.assertError(self.call(method, f"/number-pools/{pool.pk}", token, data=data), 404, "not_found")

    def test_crud_and_team_count(self):
        token = self.admin()
        created = self.call("post", "/number-pools", token, data=self.payload(min_age_gap_years=3))
        self.assertEqual(created.status_code, 201, created.content)
        body = created.json()
        self.assertEqual((body["min_number"], body["max_number"], body["min_age_gap_years"], body["team_count"]), (1, 99, 3, 0))
        Team.objects.create(club=self.club, name="U12", pool_id=body["id"])
        patched = self.call("patch", f"/number-pools/{body['id']}", token, data={"max_number": 50})
        self.assertEqual(patched.status_code, 200, patched.content)
        self.assertEqual((patched.json()["max_number"], patched.json()["min_number"], patched.json()["team_count"]), (50, 1, 1))
        self.assertEqual([p["name"] for p in self.call("get", "/number-pools", token).json()["items"]], ["Youth"])
        self.assertEqual(self.call("delete", f"/number-pools/{body['id']}", token).status_code, 204)
        self.assertIsNone(Team.objects.get(name="U12").pool_id)

    def test_validation_422(self):
        NumberPool.objects.create(club=self.club, name="Youth", min_number=1, max_number=9)
        token = self.admin()
        duplicate = self.call("post", "/number-pools", token, data=self.payload())
        self.assertIn("name", duplicate.json()["error"]["fields"])
        self.assertEqual(duplicate.status_code, 422)
        backwards = self.call("post", "/number-pools", token, data=self.payload(name="B", min_number=50, max_number=10))
        self.assertError(backwards, 422, "validation_error")
        self.assertIn("max_number", backwards.json()["error"]["fields"])
        self.assertError(self.call("post", "/number-pools", token, data={"name": "x"}), 422, "validation_error")
        pool = NumberPool.objects.get()
        self.assertError(self.call("patch", f"/number-pools/{pool.pk}", token, data={"min_number": 20}), 422, "validation_error")

    def test_create_is_idempotent(self):
        token = self.admin()
        first = self.call("post", "/number-pools", token, data=self.payload(), HTTP_IDEMPOTENCY_KEY="np-1")
        second = self.call("post", "/number-pools", token, data=self.payload(), HTTP_IDEMPOTENCY_KEY="np-1")
        self.assertEqual(first.json()["id"], second.json()["id"])
        self.assertEqual(NumberPool.objects.filter(club=self.club).count(), 1)
