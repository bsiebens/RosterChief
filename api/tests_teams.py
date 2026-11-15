"""Teams API (``/teams``): admin only, validation, roster / staff CRUD, jersey numbers, delete protection, seasons."""

import datetime
import json
import uuid
from unittest import mock

from django.db import connection
from django.test import Client, TestCase
from django_tenants.utils import schema_context

from club.models import Club, Domain
from club.services import create_club, grant_login
from controlpanel.tests import login_session, make_user
from events.models import Season
from members.models import AuditEntry, ClubMembership, Member
from members.services.errors import Invalid
from teams.models import NumberPool, NumberReservation, Position, StaffAssignment, Team, TeamMembership

BASE = "/api/v1/teams"


class TeamsApiTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls._mfa = mock.patch("authorization.middleware.is_local_development", return_value=True)
        cls._mfa.start()
        super().setUpClass()

    def setUp(self):
        # About the team rules; the door and the team-manager matrix are in tests_permissions.
        door = mock.patch("members.services.access.can_access_management", return_value=True)
        door.start()
        self.addCleanup(door.stop)

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        connection.set_schema_to_public()
        cls._mfa.stop()

    @classmethod
    def setUpTestData(cls):
        cls.owner = make_user("owner@tm.test", first="Olivia", last="Owner")
        public = Club(schema_name="public", name="Platform", owner=cls.owner)
        public.auto_create_schema = False
        public.save()
        Domain.objects.create(domain="testserver", tenant=public, is_primary=True)
        cls.club = create_club("Team FC", cls.owner)
        cls.host = Domain.objects.get(tenant=cls.club, is_primary=True).domain
        today = datetime.date.today()
        with schema_context(cls.club.schema_name):
            cls.season = Season.objects.create(start_date=today - datetime.timedelta(days=100), end_date=today + datetime.timedelta(days=200))
            cls.old_season = Season.objects.create(start_date=today - datetime.timedelta(days=500), end_date=today - datetime.timedelta(days=200))
            cls.pool = NumberPool.objects.create(name="Juniors", min_number=1, max_number=20, min_age_gap_years=5)
            cls.u12 = Team.objects.create(name="U12", short_name="U12", pool=cls.pool, ordering=2)
            cls.u16 = Team.objects.create(name="U16", short_name="U16", pool=cls.pool, ordering=1)
            cls.loose = Team.objects.create(name="Loose", short_name="L")
            cls.forward = Position.objects.create(name="Forward", short_name="F", ordering=1)
            cls.goalie = Position.objects.create(name="Goalie", short_name="G", ordering=0)
            cls.coach_pos = Position.objects.create(name="Coach", short_name="C", staff_position=True)
            cls.kid = Member.objects.create(first_name="Kim", last_name="Kid", email="kid@tm.test", date_of_birth=datetime.date(2014, 3, 1))
            cls.teen = Member.objects.create(first_name="Tom", last_name="Teen", email="teen@tm.test", date_of_birth=datetime.date(2008, 3, 1))
            cls.peer = Member.objects.create(first_name="Pat", last_name="Peer", email="peer@tm.test", date_of_birth=datetime.date(2014, 6, 1))
            cls.nodob = Member.objects.create(first_name="Nod", last_name="Ob", email="nodob@tm.test")
            cls.coach = Member.objects.create(first_name="Coach", last_name="Test", email="coach@tm.test")
            # Only people with an active membership in the season can be put on a roster or a staff.
            for person in (cls.kid, cls.teen, cls.peer, cls.nodob, cls.coach):
                for season in (cls.season, cls.old_season):
                    ClubMembership.objects.create(member=person, season=season, status=ClubMembership.StatusChoices.ACTIVE)
        cls.coach_user, _ = grant_login(cls.club, cls.coach)
        cls.owner = type(cls.owner).objects.get(pk=cls.owner.pk)

    def call(self, method, path, user=None, data=None):
        headers = {"HTTP_HOST": self.host}
        if user is not None:
            headers["HTTP_X_SESSION_TOKEN"] = login_session(user)
        body = {"data": json.dumps(data), "content_type": "application/json"} if data is not None else {}
        return getattr(Client(), method)(BASE + path, **body, **headers)

    def admin(self, method, path, data=None):
        return self.call(method, path, self.owner, data)

    def roster_add(self, team, member, **kw):
        return self.admin("post", f"/{team.pk}/roster", {"member_id": str(member.pk), **kw})

    def audit(self, action):
        with schema_context(self.club.schema_name):
            return list(AuditEntry.objects.filter(action=action))

    # -- gate --

    def test_writes_need_a_permission_and_reads_are_open(self):
        tid = uuid.uuid4()
        writes = [
            ("post", "", {"name": "X"}),
            ("post", "/reorder", {"ids": []}),
            ("delete", f"/{self.u12.pk}", None),
            ("patch", f"/{self.u12.pk}", {"name": "X"}),
            ("post", f"/{self.u12.pk}/roster", {"member_id": str(tid)}),
            ("post", f"/{self.u12.pk}/staff", {"member_id": str(tid), "position_id": str(tid)}),
        ]
        for method, path, data in writes:
            self.assertEqual(self.call(method, path, self.coach_user, data).status_code, 403, (method, path))
            self.assertIn(self.call(method, path, None, data).status_code, (401, 403))
        for path in ("", "/numbers/overview", f"/{self.u12.pk}", f"/{self.u12.pk}/numbers"):
            self.assertEqual(self.call("get", path, self.coach_user).status_code, 200, path)

    def test_referee_management_is_a_settings_field(self):
        body = self.admin("patch", f"/{self.u12.pk}", {"referee_management": "federation"}).json()
        self.assertEqual((body["referee_management"], body["official_management"]), ("federation", "club"))
        self.assertEqual(self.admin("patch", f"/{self.u12.pk}", {"official_management": "federation"}).json()["official_management"], "federation")
        self.assertEqual(self.admin("patch", f"/{self.u12.pk}", {"referee_management": "nobody"}).status_code, 422)
        # A team's own coach may resend the current values but not change them.
        self.assertEqual(self.call("patch", f"/{self.u12.pk}", self.coach_user, {"referee_management": "club"}).status_code, 403)

    def test_coach_filter(self):
        head = Position.objects.create(name="Head coach", short_name="HC", staff_position=True, management_position=True)
        StaffAssignment.objects.create(team=self.u12, member=self.coach, season=self.season, position=head)
        StaffAssignment.objects.create(team=self.u16, member=self.kid, season=self.season, position=self.coach_pos)  # staff, not a management position
        names = lambda r: {t["name"] for t in r.json()}  # noqa: E731
        self.assertEqual(names(self.admin("get", "?coach=none")), {"U16", "Loose"})
        self.assertEqual(names(self.admin("get", "?coach=any")), {"U12", "U16", "Loose"})
        self.assertEqual(names(self.admin("get", f"?coach=none&season={self.old_season.pk}")), {"U12", "U16", "Loose"})  # nobody coached them back then
        self.assertEqual(self.admin("get", "?coach=bogus").status_code, 422)

    def _png(self):
        import io

        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (8, 6), "red").save(buf, "PNG")
        return buf.getvalue()

    def test_lapsed_members_cannot_be_added_to_a_team_or_a_group(self):
        from members.models import Group
        from members.services import groups

        with schema_context(self.club.schema_name):
            ClubMembership.objects.filter(member__in=(self.kid, self.coach), season=self.season).update(status=ClubMembership.StatusChoices.LAPSED)
            group = Group.objects.create(name="Parents")
        r = self.roster_add(self.u12, self.kid, season_id=str(self.season.pk))
        self.assertEqual(r.status_code, 422, r.content)
        self.assertIn("member_id", r.json()["errors"])
        r = self.admin("post", f"/{self.u12.pk}/staff", {"member_id": str(self.coach.pk), "position_id": str(self.coach_pos.pk), "season_id": str(self.season.pk)})
        self.assertEqual(r.status_code, 422, r.content)
        with schema_context(self.club.schema_name):
            with self.assertRaises(Invalid):
                groups.add_member(self.owner, group, self.kid)
            groups.add_member(self.owner, group, self.teen)  # active: fine
        self.assertEqual(self.roster_add(self.u12, self.teen, season_id=str(self.season.pk)).status_code, 201)

    def test_add_people(self):
        with schema_context(self.club.schema_name):
            ClubMembership.objects.filter(member=self.nodob, season=self.season).update(status=ClubMembership.StatusChoices.PENDING)  # pending: not allowed
        row = lambda m, pos, **kw: {"member_id": str(m.pk), "position_id": str(pos.pk) if pos else None, **kw}  # noqa: E731
        body = {
            "season_id": str(self.season.pk),
            "rows": [
                row(self.kid, self.forward, jersey_number=7, is_captain=True),
                row(self.teen, self.forward),
                row(self.coach, self.coach_pos),  # a staff position: staff
                row(self.kid, self.coach_pos),  # the same person as staff too
                row(self.peer, None),  # no position
                row(self.nodob, self.forward),  # not active
                row(self.kid, self.forward),  # already on the roster
            ],
        }
        r = self.admin("post", f"/{self.u12.pk}/people", body)
        self.assertEqual(r.status_code, 200, r.content)
        out = r.json()
        self.assertEqual(out["added"], 4)
        self.assertEqual([(x["ok"], x["role"]) for x in out["rows"]], [(True, "player"), (True, "player"), (True, "staff"), (True, "staff"), (False, None), (False, None), (False, None)])
        self.assertIn("position", out["rows"][4]["error"].lower())
        self.assertIn("active", out["rows"][5]["error"].lower())
        detail = self.admin("get", f"/{self.u12.pk}").json()
        self.assertEqual({(p["first_name"], p["jersey_number"], p["is_captain"]) for p in detail["roster"]}, {("Kim", 7, True), ("Tom", None, False)})
        self.assertEqual({s["first_name"] for s in detail["staff"]}, {"Coach", "Kim"})
        self.assertEqual(self.call("post", f"/{self.u12.pk}/people", self.coach_user, body).status_code, 403)
        too_many = {"season_id": str(self.season.pk), "rows": [row(self.kid, self.forward)] * 101}
        self.assertEqual(self.admin("post", f"/{self.u12.pk}/people", too_many).status_code, 422)

    def test_same_person_keeps_their_number_in_another_team(self):
        add = lambda team, m, n: self.admin("post", f"/{team.pk}/roster", {"member_id": str(m.pk), "season_id": str(self.season.pk), "jersey_number": n})  # noqa: E731
        self.assertEqual(add(self.u12, self.teen, 7).status_code, 201)
        self.assertEqual(add(self.u16, self.teen, 7).status_code, 201)  # the same person on another team of the pool: fine
        r = add(self.u16, self.peer, 7)  # someone else with the same number is still blocked
        self.assertEqual(r.status_code, 422, r.content)
        self.assertIn("already held by Tom Teen", str(r.json()))
        ov = next(p for p in self.admin("get", "/numbers/overview").json() if p["pool"])
        seven = next(n for n in ov["numbers"] if n["number"] == 7)
        self.assertNotEqual(seven["status"], "conflict")

    def test_team_photo(self):
        import tempfile

        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.test import override_settings

        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media):
            up = lambda data, name="t.png", user=None: Client().post(  # noqa: E731
                f"{BASE}/{self.u12.pk}/photo",
                {"file": SimpleUploadedFile(name, data), "season_id": str(self.season.pk)},
                HTTP_HOST=self.host,
                HTTP_X_SESSION_TOKEN=login_session(user or self.owner),
            )
            self.assertEqual(up(self._png(), user=self.coach_user).status_code, 403)  # not a team manager
            self.assertEqual(up(b"not an image").status_code, 422)
            r = up(self._png())
            self.assertEqual(r.status_code, 200, r.content)
            first = r.json()["photo_url"]
            self.assertTrue(first and first.startswith("/media/"), first)
            self.assertEqual(self.admin("get", f"/{self.u12.pk}").json()["photo_url"], first)
            self.assertIsNone(self.admin("get", f"/{self.u12.pk}?season={self.old_season.pk}").json()["photo_url"])  # per season
            self.assertNotEqual(up(self._png()).json()["photo_url"], first)  # replaced
            self.assertEqual(self.admin("delete", f"/{self.u12.pk}/photo").status_code, 204)
            self.assertIsNone(self.admin("get", f"/{self.u12.pk}").json()["photo_url"])
            self.assertTrue(AuditEntry.objects.filter(action="team.photo_set").exists())

    def test_eligible_referees_need_the_feature(self):
        self.assertEqual(self.admin("get", f"/{self.u12.pk}/referees").status_code, 404)  # club without referee_officials

    def test_unknown_ids_404(self):
        tid = uuid.uuid4()
        self.assertEqual(self.admin("get", f"/{tid}").status_code, 404)
        self.assertEqual(self.admin("delete", f"/{tid}").status_code, 404)
        r = self.roster_add(self.u12, self.kid)
        mid = r.json()["id"]
        self.assertEqual(self.admin("patch", f"/{self.u16.pk}/roster/{mid}", {}).status_code, 404)  # other team
        self.assertEqual(self.admin("delete", f"/{self.u12.pk}/staff/{uuid.uuid4()}").status_code, 404)

    # -- teams --

    def test_list_defaults_and_counts(self):
        self.roster_add(self.u12, self.kid, position_id=str(self.forward.pk))
        self.admin("post", f"/{self.u12.pk}/staff", {"member_id": str(self.coach.pk), "position_id": str(self.coach_pos.pk)})
        rows = self.admin("get", "").json()
        self.assertEqual([r["name"] for r in rows], ["U16", "U12", "Loose"])  # ordering first, then name
        by = {r["name"]: r for r in rows}
        self.assertEqual((by["U12"]["player_count"], by["U12"]["staff_count"], by["U12"]["pool_name"]), (1, 1, "Juniors"))
        old = self.admin("get", f"?season={self.old_season.pk}").json()
        self.assertEqual({r["name"]: r["player_count"] for r in old}["U12"], 0)
        self.assertEqual([r["name"] for r in self.admin("get", "?q=lo").json()], ["Loose"])
        self.assertEqual(self.admin("get", f"?season={uuid.uuid4()}").status_code, 422)

    def test_create_update_delete_with_audit(self):
        r = self.admin("post", "", {"name": "U8", "short_name": "U8", "pool_id": str(self.pool.pk), "feeds_into_id": str(self.u12.pk), "age_max": 8})
        self.assertEqual(r.status_code, 201, r.content)
        body = r.json()
        self.assertEqual((body["pool_name"], body["feeds_into_name"], body["age_max"]), ("Juniors", "U12", 8))
        r = self.admin("patch", f"/{body['id']}", {"name": "U9", "pool_id": None, "tournament_only": True})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual((r.json()["name"], r.json()["pool_id"], r.json()["tournament_only"], r.json()["feeds_into_name"]), ("U9", None, True, "U12"))
        (entry,) = self.audit("team.updated")
        self.assertEqual(entry.changes["name"], {"from": "U8", "to": "U9"})
        self.assertEqual(len(self.audit("team.created")), 1)
        self.assertEqual(self.admin("delete", f"/{body['id']}").status_code, 204)
        self.assertEqual(len(self.audit("team.deleted")), 1)

    def test_reorder(self):
        loose = Team.objects.get(name="Loose")
        r = self.admin("post", "/reorder", {"ids": [str(loose.pk), str(self.u12.pk)]})
        self.assertEqual(r.status_code, 204, r.content)
        self.assertEqual([x["name"] for x in self.admin("get", "").json()][:2], ["Loose", "U12"])
        self.assertEqual(len(self.audit("team.reordered")), 1)
        self.assertEqual(self.admin("post", "/reorder", {"ids": [str(loose.pk), str(loose.pk)]}).status_code, 422)
        self.assertEqual(self.admin("post", "/reorder", {"ids": [str(uuid.uuid4())]}).status_code, 422)

    def test_validation(self):
        r = self.admin("post", "", {"name": "u12"})
        self.assertEqual(r.status_code, 422)
        self.assertIn("name", r.json()["errors"])
        r = self.admin("post", "", {"name": "X", "age_min": 12, "age_max": 8, "pool_id": str(uuid.uuid4()), "feeds_into_id": str(uuid.uuid4())})
        self.assertEqual(set(r.json()["errors"]), {"age_max", "pool_id", "feeds_into_id"})
        self.assertEqual(self.admin("post", "", {"name": " "}).status_code, 422)
        r = self.admin("patch", f"/{self.u12.pk}", {"feeds_into_id": str(self.u12.pk)})
        self.assertIn("feeds_into_id", r.json()["errors"])
        self.assertEqual(self.admin("patch", f"/{self.u12.pk}", {"feeds_into_id": str(self.u16.pk)}).status_code, 200)
        r = self.admin("patch", f"/{self.u16.pk}", {"feeds_into_id": str(self.u12.pk)})  # loop
        self.assertEqual(r.status_code, 422)
        self.assertIn("feeds_into_id", r.json()["errors"])
        self.assertEqual(self.admin("patch", f"/{self.u12.pk}", {"name": "U16"}).status_code, 422)  # unique
        self.assertEqual(self.admin("patch", f"/{self.u12.pk}", {"name": "U12"}).status_code, 200)  # own name ok

    def test_delete_protection(self):
        self.roster_add(self.u12, self.kid)
        r = self.admin("delete", f"/{self.u12.pk}")
        self.assertEqual(r.status_code, 422)
        self.assertIn("history", r.json()["detail"])
        with schema_context(self.club.schema_name):
            self.assertTrue(Team.objects.filter(pk=self.u12.pk).exists())
        self.assertEqual(self.admin("delete", f"/{self.u16.pk}").status_code, 204)

    # -- roster --

    def test_roster_crud_and_detail(self):
        r = self.roster_add(self.u12, self.kid, position_id=str(self.forward.pk), jersey_number=7)
        self.assertEqual(r.status_code, 201, r.content)
        mid = r.json()["id"]
        self.assertEqual((r.json()["position_name"], r.json()["jersey_number"], r.json()["last_name"]), ("Forward", 7, "Kid"))
        self.roster_add(self.u12, self.peer, position_id=str(self.goalie.pk), jersey_number=1)
        self.roster_add(self.u12, self.nodob)  # no position: last
        detail = self.admin("get", f"/{self.u12.pk}").json()
        self.assertEqual(detail["season_id"], str(self.season.pk))
        self.assertEqual([p["last_name"] for p in detail["roster"]], ["Peer", "Kid", "Ob"])
        r = self.admin("patch", f"/{self.u12.pk}/roster/{mid}", {"jersey_number": 9, "position_id": str(self.goalie.pk)})
        self.assertEqual((r.status_code, r.json()["jersey_number"], r.json()["position_name"]), (200, 9, "Goalie"))
        (entry,) = self.audit("team.roster_updated")
        self.assertEqual(entry.changes["jersey_number"], {"from": 7, "to": 9})
        self.assertEqual(len(self.audit("team.roster_added")), 3)
        self.assertEqual(self.admin("patch", f"/{self.u12.pk}/roster/{mid}", {"jersey_number": None}).json()["jersey_number"], None)
        self.assertEqual(self.admin("delete", f"/{self.u12.pk}/roster/{mid}").status_code, 204)
        self.assertEqual(len(self.audit("team.roster_removed")), 1)
        self.assertEqual(len(self.admin("get", f"/{self.u12.pk}").json()["roster"]), 2)
        self.assertEqual(self.admin("get", f"/{self.u12.pk}?season={self.old_season.pk}").json()["roster"], [])

    def test_roster_validation(self):
        self.assertEqual(self.roster_add(self.u12, self.kid).status_code, 201)
        r = self.roster_add(self.u12, self.kid)
        self.assertIn("member_id", r.json()["errors"])  # already on team
        self.assertIn("member_id", self.admin("post", f"/{self.u12.pk}/roster", {"member_id": str(uuid.uuid4())}).json()["errors"])
        r = self.roster_add(self.u12, self.teen, position_id=str(self.coach_pos.pk))
        self.assertIn("position_id", r.json()["errors"])  # staff position
        self.assertIn("position_id", self.roster_add(self.u12, self.teen, position_id=str(uuid.uuid4())).json()["errors"])
        self.assertIn("season_id", self.roster_add(self.u12, self.teen, season_id=str(uuid.uuid4())).json()["errors"])
        # the same member can join on another season
        self.assertEqual(self.roster_add(self.u12, self.kid, season_id=str(self.old_season.pk)).status_code, 201)

    def test_number_range_and_clash(self):
        r = self.roster_add(self.u12, self.kid, jersey_number=21)
        self.assertIn("jersey_number", r.json()["errors"])
        self.assertIn("between 1 and 20", r.json()["errors"]["jersey_number"][0])
        self.assertEqual(self.roster_add(self.u12, self.kid, jersey_number=10).status_code, 201)
        # same team never shares
        r = self.roster_add(self.u12, self.teen, jersey_number=10)
        self.assertEqual(r.status_code, 422)
        self.assertIn("Kim Kid", r.json()["errors"]["jersey_number"][0])
        # other team in the pool, born < 5 years apart: clash, naming the holder
        r = self.roster_add(self.u16, self.peer, jersey_number=10)
        self.assertEqual(r.status_code, 422)
        self.assertIn("Kim Kid", r.json()["errors"]["jersey_number"][0])
        # >= 5 years apart: may share
        self.assertEqual(self.roster_add(self.u16, self.teen, jersey_number=10).status_code, 201)
        # missing date of birth: cannot share
        self.assertEqual(self.roster_add(self.u16, self.nodob, jersey_number=10).status_code, 422)
        # a team without a pool only checks itself
        self.assertEqual(self.roster_add(self.loose, self.peer, jersey_number=10).status_code, 201)
        self.assertEqual(self.roster_add(self.loose, self.nodob, jersey_number=500).status_code, 201)
        # a different season is independent
        self.assertEqual(self.roster_add(self.u16, self.peer, jersey_number=10, season_id=str(self.old_season.pk)).status_code, 201)

    def test_number_patch_and_reservation(self):
        with schema_context(self.club.schema_name):
            NumberReservation.objects.create(pool=self.pool, number=23 - 10, note="Retired - Jane")
        r = self.roster_add(self.u12, self.kid, jersey_number=13)
        self.assertIn("Retired - Jane", r.json()["errors"]["jersey_number"][0])
        mid = self.roster_add(self.u12, self.kid, jersey_number=4).json()["id"]
        self.assertEqual(self.admin("patch", f"/{self.u12.pk}/roster/{mid}", {"jersey_number": 4}).status_code, 200)  # keeping own number
        self.assertEqual(self.admin("patch", f"/{self.u12.pk}/roster/{mid}", {"jersey_number": 99}).status_code, 422)

    def test_captains(self):
        a = self.roster_add(self.u12, self.kid, is_captain=True).json()
        b = self.roster_add(self.u12, self.peer).json()
        r = self.admin("patch", f"/{self.u12.pk}/roster/{b['id']}", {"is_captain": True})
        self.assertTrue(r.json()["is_captain"])
        roster = {p["id"]: p for p in self.admin("get", f"/{self.u12.pk}").json()["roster"]}
        self.assertFalse(roster[a["id"]]["is_captain"])
        r = self.admin("patch", f"/{self.u12.pk}/roster/{b['id']}", {"is_alternate_captain": True})
        self.assertEqual((r.json()["is_captain"], r.json()["is_alternate_captain"]), (False, True))
        r = self.admin("patch", f"/{self.u12.pk}/roster/{b['id']}", {"is_captain": True, "is_alternate_captain": True})
        self.assertEqual(r.status_code, 422)

    # -- staff --

    def test_staff_crud(self):
        r = self.admin("post", f"/{self.u12.pk}/staff", {"member_id": str(self.coach.pk), "position_id": str(self.forward.pk)})
        self.assertIn("position_id", r.json()["errors"])  # not a staff position
        r = self.admin("post", f"/{self.u12.pk}/staff", {"member_id": str(self.coach.pk), "position_id": str(self.coach_pos.pk)})
        self.assertEqual(r.status_code, 201, r.content)
        sid = r.json()["id"]
        self.assertEqual(r.json()["position_name"], "Coach")
        self.assertIn("member_id", self.admin("post", f"/{self.u12.pk}/staff", {"member_id": str(self.coach.pk), "position_id": str(self.coach_pos.pk)}).json()["errors"])
        with schema_context(self.club.schema_name):
            mgr = Position.objects.create(name="Manager", short_name="M", staff_position=True)
        r = self.admin("patch", f"/{self.u12.pk}/staff/{sid}", {"position_id": str(mgr.pk)})
        self.assertEqual(r.json()["position_name"], "Manager")
        self.assertEqual(self.admin("patch", f"/{self.u12.pk}/staff/{sid}", {"position_id": str(self.forward.pk)}).status_code, 422)
        detail = self.admin("get", f"/{self.u12.pk}").json()
        self.assertEqual([s["position_name"] for s in detail["staff"]], ["Manager"])
        self.assertEqual(self.admin("delete", f"/{self.u12.pk}/staff/{sid}").status_code, 204)
        for a in ("added", "updated", "removed"):
            self.assertEqual(len(self.audit(f"team.staff_{a}")), 1)

    # -- numbers --

    def test_overview_statuses(self):
        self.roster_add(self.u12, self.kid, jersey_number=1)  # taken
        self.roster_add(self.u12, self.peer, jersey_number=2)
        self.roster_add(self.u16, self.teen, jersey_number=2)  # shared (6 years apart)
        self.roster_add(self.loose, self.nodob, jersey_number=9)
        with schema_context(self.club.schema_name):
            NumberReservation.objects.create(pool=self.pool, number=3, note="Retired")
            # a conflict created behind the API's back
            TeamMembership.objects.create(team=self.u16, member=self.nodob, season=self.season, jersey_number=1)
            NumberReservation.objects.create(pool=self.pool, number=4, note="Held anyway")
            TeamMembership.objects.create(team=self.u16, member=self.coach, season=self.season, jersey_number=4)
        data = self.admin("get", f"/numbers/overview?season={self.season.pk}").json()
        self.assertEqual(len(data), 2)
        pool_entry, loose_entry = data
        self.assertEqual(pool_entry["pool"]["name"], "Juniors")
        self.assertEqual({t["name"] for t in pool_entry["teams"]}, {"U12", "U16"})
        nums = {n["number"]: n for n in pool_entry["numbers"]}
        self.assertEqual(len(nums), 20)
        self.assertEqual(nums[1]["status"], "conflict")
        self.assertEqual(len(nums[1]["holders"]), 2)
        self.assertEqual(nums[2]["status"], "shared")
        self.assertEqual(nums[3]["status"], "reserved")
        self.assertEqual(nums[3]["reservation"]["note"], "Retired")
        self.assertEqual(nums[4]["status"], "conflict")
        self.assertEqual(nums[5]["status"], "free")
        self.assertEqual(nums[2]["holders"][0]["team_name"], "U12")
        self.assertIsNone(loose_entry["pool"])
        self.assertEqual([(n["number"], n["status"]) for n in loose_entry["numbers"]], [(9, "taken")])
        only = self.admin("get", f"/numbers/overview?pool={self.pool.pk}").json()
        self.assertEqual(len(only), 1)
        self.assertEqual(self.admin("get", f"/numbers/overview?season={self.old_season.pk}&pool={self.pool.pk}").json()[0]["numbers"][1]["status"], "free")

    def test_team_numbers(self):
        self.roster_add(self.u12, self.kid, jersey_number=1)
        self.roster_add(self.u16, self.teen, jersey_number=2)
        with schema_context(self.club.schema_name):
            NumberReservation.objects.create(pool=self.pool, number=3, note="Retired")
        d = self.admin("get", f"/{self.u12.pk}/numbers").json()
        self.assertEqual([p["jersey_number"] for p in d["players"]], [1])
        self.assertNotIn(1, d["free_numbers"])
        self.assertNotIn(2, d["free_numbers"])
        self.assertNotIn(3, d["free_numbers"])
        self.assertIn(4, d["free_numbers"])
        self.assertEqual(d["taken_by_others"]["2"][0]["last_name"], "Teen")
        self.assertEqual(d["reserved"], {"3": "Retired"})
        self.assertEqual(d["pool"]["max_number"], 20)
        d = self.admin("get", f"/{self.loose.pk}/numbers").json()
        self.assertEqual((d["pool"], d["free_numbers"]), (None, []))
