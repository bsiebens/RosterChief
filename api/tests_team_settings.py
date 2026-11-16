"""Team settings API: positions, number pools, reservations and the club-wide officiating people list."""

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
from features.entitlements import Entitlement, Entitlements
from members.models import AuditEntry, Member
from teams.models import OfficialLevel, OfficialProfile, RefereeLevel, RefereeProfile, StaffAssignment, Team, TeamMembership

BASE = "/api/v1"
WITH_FEATURE = Entitlements({"referee_officials": Entitlement(True)})


class TeamSettingsApiTests(TestCase):
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
        cls.owner = make_user("owner@tset.test", first="Olivia", last="Owner")
        public = Club(schema_name="public", name="Platform", owner=cls.owner)
        public.auto_create_schema = False
        public.save()
        Domain.objects.create(domain="testserver", tenant=public, is_primary=True)
        cls.club = create_club("Settings FC", cls.owner)
        cls.host = Domain.objects.get(tenant=cls.club, is_primary=True).domain
        today = datetime.date.today()
        with schema_context(cls.club.schema_name):
            cls.u12 = Team.objects.create(name="U12", short_name="U12")
            cls.u14 = Team.objects.create(name="U14", short_name="U14")
            cls.u16 = Team.objects.create(name="U16", short_name="U16")
            cls.season = Season.objects.filter(start_date__lte=today, end_date__gte=today).first() or Season.objects.create(start_date=today - datetime.timedelta(days=30), end_date=today + datetime.timedelta(days=300))
            cls.player = Member.objects.create(first_name="Pat", last_name="Player", email="pat@tset.test")
            cls.ann = Member.objects.create(first_name="Ann", last_name="Both", email="ann@tset.test")
            cls.bob = Member.objects.create(first_name="Bob", last_name="Zed", email="bob@tset.test")
            cls.coach = Member.objects.create(first_name="Coach", last_name="Test", email="coach@tset.test")
        cls.coach_user, _ = grant_login(cls.club, cls.coach)
        cls.owner = type(cls.owner).objects.get(pk=cls.owner.pk)

    def setUp(self):
        # About the settings rules themselves; who gets through the door and who manages what is in tests_permissions.
        door = mock.patch("members.services.access.can_access_management", return_value=True)
        door.start()
        self.addCleanup(door.stop)
        self.features = WITH_FEATURE
        patcher = mock.patch("features.middleware.entitlements_for_request", side_effect=lambda request: self.features)
        patcher.start()
        self.addCleanup(patcher.stop)

    def call(self, method, path, user=None, data=None):
        headers = {"HTTP_HOST": self.host}
        if user is not None:
            headers["HTTP_X_SESSION_TOKEN"] = login_session(user)
        body = {"data": json.dumps(data), "content_type": "application/json"} if data is not None else {}
        return getattr(Client(), method)(BASE + path, **body, **headers)

    def admin(self, method, path, data=None):
        return self.call(method, path, self.owner, data)

    def audit(self, action):
        with schema_context(self.club.schema_name):
            return list(AuditEntry.objects.filter(action=action))

    def mkpos(self, name="Forward", **kw):
        r = self.admin("post", "/team-positions", {"name": name, "short_name": name[:2], **kw})
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()

    def mkpool(self, name="Pool", lo=1, hi=99, **kw):
        r = self.admin("post", "/number-pools", {"name": name, "min_number": lo, "max_number": hi, **kw})
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()

    # -- permissions --

    def test_writes_need_the_settings_permission_and_reads_are_open(self):
        pid = uuid.uuid4()
        writes = [
            ("post", "/team-positions", {"name": "X"}),
            ("patch", f"/team-positions/{pid}", {"name": "X"}),
            ("delete", f"/team-positions/{pid}", None),
            ("post", "/team-positions/reorder", {"ids": []}),
            ("post", "/number-pools", {"name": "X", "min_number": 1, "max_number": 2}),
            ("patch", f"/number-pools/{pid}", {"name": "X"}),
            ("delete", f"/number-pools/{pid}", None),
            ("post", f"/number-pools/{pid}/reservations", {"number": 1}),  # needs a team to manage
        ]
        for method, path, data in writes:
            self.assertEqual(self.call(method, path, self.coach_user, data).status_code, 403, (method, path))
        for method, path in [("get", "/team-positions"), ("get", "/number-pools"), ("get", "/officiating/people")]:
            self.assertEqual(self.call(method, path, self.coach_user).status_code, 200, path)
        for method, path, data in [*writes, ("get", "/team-positions", None), ("get", "/officiating/people", None)]:
            self.assertIn(self.call(method, path, None, data).status_code, (401, 403))

    # -- positions --

    def test_position_crud_and_audit(self):
        p = self.mkpos("Goalie", ordering=2)
        self.assertEqual((p["short_name"], p["ordering"], p["in_use_count"]), ("Go", 2, 0))
        r = self.admin("patch", f"/team-positions/{p['id']}", {"name": "Keeper", "staff_position": True, "management_position": True})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual((r.json()["name"], r.json()["management_position"], r.json()["short_name"]), ("Keeper", True, "Go"))
        self.assertEqual([x["name"] for x in self.admin("get", "/team-positions").json()], ["Keeper"])
        self.assertEqual(self.audit("club.position_created")[0].changes["name"], {"from": None, "to": "Goalie"})
        self.assertEqual(self.audit("club.position_updated")[0].changes["name"], {"from": "Goalie", "to": "Keeper"})
        self.assertEqual(self.admin("delete", f"/team-positions/{p['id']}").status_code, 204)
        self.assertEqual(len(self.audit("club.position_deleted")), 1)
        self.assertEqual(self.admin("delete", f"/team-positions/{p['id']}").status_code, 404)

    def test_position_reorder(self):
        a, _b, c = (self.mkpos(n, ordering=i) for i, n in enumerate(["A", "B", "C"]))
        r = self.admin("post", "/team-positions/reorder", {"ids": [c["id"], a["id"]]})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual([(x["name"], x["ordering"]) for x in r.json()], [("C", 1), ("A", 2), ("B", 3)])
        self.assertEqual([x["name"] for x in self.admin("get", "/team-positions").json()], ["C", "A", "B"])
        self.assertEqual(len(self.audit("club.positions_reordered")), 1)
        self.assertEqual(self.admin("post", "/team-positions/reorder", {"ids": [a["id"], a["id"]]}).status_code, 422)
        self.assertEqual(self.admin("post", "/team-positions/reorder", {"ids": [str(uuid.uuid4())]}).status_code, 422)
        self.assertEqual(self.call("post", "/team-positions/reorder", self.coach_user, {"ids": []}).status_code, 403)

    def test_position_validation(self):
        r = self.admin("post", "/team-positions", {"name": "  "})
        self.assertEqual(r.status_code, 422)
        self.assertIn("name", r.json()["errors"])
        r = self.admin("post", "/team-positions", {"name": "Boss", "management_position": True})
        self.assertEqual(r.status_code, 422)
        self.assertIn("management_position", r.json()["errors"])
        p = self.mkpos("Plain")
        r = self.admin("patch", f"/team-positions/{p['id']}", {"management_position": True})
        self.assertEqual(r.status_code, 422)
        self.assertIn("management_position", r.json()["errors"])

    def test_position_in_use_protection(self):
        p = self.mkpos("Winger")
        s = self.mkpos("Coach", staff_position=True)
        with schema_context(self.club.schema_name):
            TeamMembership.objects.create(team=self.u12, member=self.player, season=self.season, position_id=p["id"])
            StaffAssignment.objects.create(team=self.u12, member=self.coach, season=self.season, position_id=s["id"])
        self.assertEqual({x["name"]: x["in_use_count"] for x in self.admin("get", "/team-positions").json()}, {"Winger": 1, "Coach": 1})
        for pos in (p, s):
            r = self.admin("delete", f"/team-positions/{pos['id']}")
            self.assertEqual(r.status_code, 422)
            self.assertIn("can't be deleted", r.json()["detail"])
        self.assertIn("1 roster spot", self.admin("delete", f"/team-positions/{p['id']}").json()["detail"])
        r = self.admin("patch", f"/team-positions/{p['id']}", {"staff_position": True})
        self.assertEqual(r.status_code, 422)

    # -- pools --

    def test_pool_crud_and_team_assignment(self):
        pool = self.mkpool("Juniors", 1, 50, team_ids=[str(self.u12.pk), str(self.u14.pk)])
        self.assertEqual(sorted(pool["team_ids"]), sorted([str(self.u12.pk), str(self.u14.pk)]))
        self.assertEqual({t["name"] for t in pool["teams"]}, {"U12", "U14"})
        self.assertEqual((pool["min_age_gap_years"], pool["reservation_count"]), (5, 0))
        r = self.admin("patch", f"/number-pools/{pool['id']}", {"team_ids": [str(self.u16.pk), str(self.u14.pk)], "max_number": 60})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(sorted(t["name"] for t in r.json()["teams"]), ["U14", "U16"])
        with schema_context(self.club.schema_name):
            self.assertIsNone(Team.objects.get(pk=self.u12.pk).pool_id)
            self.assertEqual(str(Team.objects.get(pk=self.u16.pk).pool_id), pool["id"])
        # no team_ids sent -> untouched
        r = self.admin("patch", f"/number-pools/{pool['id']}", {"name": "Youth"})
        self.assertEqual(len(r.json()["team_ids"]), 2)
        self.assertIn({"from": "Juniors", "to": "Youth"}, [e.changes.get("name") for e in self.audit("club.number_pool_updated")])
        self.assertEqual(len(self.audit("club.number_pool_created")), 1)
        self.assertEqual(self.admin("delete", f"/number-pools/{pool['id']}").status_code, 204)
        with schema_context(self.club.schema_name):
            self.assertIsNone(Team.objects.get(pk=self.u16.pk).pool_id)
        self.assertEqual(len(self.audit("club.number_pool_deleted")), 1)
        self.assertEqual(self.admin("get", "/number-pools").json(), [])

    def test_pool_validation(self):
        r = self.admin("post", "/number-pools", {"name": "", "min_number": 1, "max_number": 5})
        self.assertIn("name", r.json()["errors"])
        r = self.admin("post", "/number-pools", {"name": "X", "min_number": 10, "max_number": 5})
        self.assertEqual(r.status_code, 422)
        self.assertIn("max_number", r.json()["errors"])
        r = self.admin("post", "/number-pools", {"name": "X", "min_number": 1, "max_number": 5, "team_ids": [str(uuid.uuid4())]})
        self.assertIn("team_ids", r.json()["errors"])
        pool = self.mkpool("Ok", 1, 20)
        self.admin("post", f"/number-pools/{pool['id']}/reservations", {"number": 15})
        r = self.admin("patch", f"/number-pools/{pool['id']}", {"max_number": 10})
        self.assertEqual(r.status_code, 422)
        self.assertIn("#15", r.json()["errors"]["max_number"][0])

    # -- reservations --

    def test_reservations(self):
        pool = self.mkpool("P", 1, 30, team_ids=[str(self.u12.pk)])
        base = f"/number-pools/{pool['id']}/reservations"
        r = self.admin("post", base, {"number": 23, "note": "Retired"})
        self.assertEqual(r.status_code, 201, r.content)
        rid = r.json()["id"]
        self.assertEqual((r.json()["number"], r.json()["note"], r.json()["reserved_by_name"] is not None), (23, "Retired", True))
        self.assertEqual(self.admin("get", "/number-pools").json()[0]["reservation_count"], 1)
        for number in (0, 31, 100):
            self.assertEqual(self.admin("post", base, {"number": number}).status_code, 422)
        r = self.admin("post", base, {"number": 23})
        self.assertEqual(r.status_code, 422)
        self.assertIn("already reserved", r.json()["errors"]["number"][0])
        with schema_context(self.club.schema_name):
            TeamMembership.objects.create(team=self.u12, member=self.player, season=self.season, jersey_number=7)
            TeamMembership.objects.create(team=self.u14, member=self.ann, season=self.season, jersey_number=8)  # other pool
        r = self.admin("post", base, {"number": 7})
        self.assertEqual(r.status_code, 422)
        self.assertIn("Pat Player", r.json()["errors"]["number"][0])
        self.assertEqual(self.admin("post", base, {"number": 8}).status_code, 201)
        self.assertEqual([x["number"] for x in self.admin("get", base).json()], [8, 23])
        self.assertEqual(self.admin("delete", f"{base}/{rid}").status_code, 204)
        self.assertEqual(self.admin("delete", f"{base}/{rid}").status_code, 404)
        self.assertEqual(len(self.audit("club.number_reservation_created")), 2)
        self.assertEqual(len(self.audit("club.number_reservation_deleted")), 1)
        self.assertEqual(self.admin("get", f"/number-pools/{uuid.uuid4()}/reservations").status_code, 404)

    def test_reservation_release_rules(self):
        pool = self.admin("post", "/number-pools", {"name": "P", "min_number": 1, "max_number": 20}).json()
        res = self.admin("post", f"/number-pools/{pool['id']}/reservations", {"number": 9, "note": "Retired"}).json()
        self.assertEqual(res["can_release"], True)
        self.assertTrue(res["reserved_by_name"])
        listed = self.admin("get", f"/number-pools/{pool['id']}/reservations").json()
        self.assertEqual((listed[0]["reserved_by_name"], listed[0]["can_release"]), (res["reserved_by_name"], True))
        # Someone who is neither an admin nor the person who reserved it may not release it ...
        path = f"/number-pools/{pool['id']}/reservations/{res['id']}"
        self.assertEqual(self.call("delete", path, self.coach_user).status_code, 403)
        # ... but the person who made it may, even without admin rights.
        with schema_context(self.club.schema_name):
            from teams.models import NumberReservation

            NumberReservation.objects.filter(pk=res["id"]).update(reserved_by=self.coach_user)
        self.assertEqual(self.call("delete", path, self.coach_user).status_code, 204)
        self.assertEqual(self.call("delete", path, self.coach_user).status_code, 404)

    # -- officiating people --

    def _officials(self):
        today = datetime.date.today()
        with schema_context(self.club.schema_name):
            base = RefereeLevel.objects.create(name="Regional")
            base.teams.add(self.u12, self.u14)
            top = RefereeLevel.objects.create(name="National", inherits_from=base)
            top.teams.add(self.u16)
            ol = OfficialLevel.objects.create(name="Table")
            ol.teams.add(self.u12)
            r1 = RefereeProfile.objects.create(member=self.ann, level=top, valid_until=today + datetime.timedelta(days=30))
            r1.excluded_teams.add(self.u14)
            RefereeProfile.objects.create(member=self.bob, level=base, valid_until=today - datetime.timedelta(days=1))
            OfficialProfile.objects.create(member=self.ann, level=ol, valid_until=today + datetime.timedelta(days=5))
        return base, top, ol

    def get_people(self, query=""):
        r = self.admin("get", "/officiating/people" + query)
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def test_team_page_lists_eligible_referees(self):
        self._officials()  # Ann: National (inherits Regional: U12, U14; own: U16), excluded from U14; Bob: Regional but expired
        names = lambda team: [(r["first_name"], r["level_name"]) for r in self.admin("get", f"/teams/{team.pk}/referees").json()]  # noqa: E731
        self.assertEqual(names(self.u12), [("Ann", "National")])  # Bob's qualification has lapsed
        self.assertEqual(names(self.u14), [])  # Ann is excluded from it
        self.assertEqual(names(self.u16), [("Ann", "National")])
        officials = lambda team: [(r["first_name"], r["level_name"]) for r in self.admin("get", f"/teams/{team.pk}/officials").json()]  # noqa: E731
        self.assertEqual(officials(self.u12), [("Ann", "Table")])  # the official level covers U12 only
        self.assertEqual(officials(self.u14), [])
        self.features = Entitlements({})
        self.assertEqual(self.admin("get", f"/teams/{self.u12.pk}/referees").status_code, 404)
        self.assertEqual(self.admin("get", f"/teams/{self.u12.pk}/officials").status_code, 404)

    def test_people_both_kinds_and_eligibility(self):
        self._officials()
        rows = self.get_people()
        self.assertEqual([(x["last_name"], x["kind"]) for x in rows], [("Both", "official"), ("Both", "referee"), ("Zed", "referee")])
        ref = rows[1]
        self.assertEqual(ref["eligible_team_names"], ["U12", "U16"])
        self.assertEqual(ref["excluded_team_names"], ["U14"])
        self.assertEqual(ref["excluded_team_ids"], [str(self.u14.pk)])
        self.assertEqual(ref["inherited_team_names"], ["U12"])  # U16 is the National level's own team; U12 comes from Regional
        self.assertEqual(rows[0]["inherited_team_names"], [])
        self.assertEqual((ref["level_name"], ref["is_valid"]), ("National", True))
        self.assertEqual(rows[0]["eligible_team_names"], ["U12"])
        self.assertEqual(rows[0]["excluded_team_names"], [])
        zed = rows[2]
        self.assertEqual((zed["is_valid"], zed["eligible_team_names"]), (False, []))
        self.assertIsNone(zed["photo_url"])

    def test_people_filters(self):
        base, _top, _ol = self._officials()
        self.assertEqual(len(self.get_people("?kind=referee")), 2)
        self.assertEqual(len(self.get_people("?kind=official")), 1)
        self.assertEqual([x["last_name"] for x in self.get_people("?q=zed")], ["Zed"])
        self.assertEqual(len(self.get_people("?q=ann%20both")), 2)
        self.assertEqual([x["last_name"] for x in self.get_people(f"?level={base.pk}")], ["Zed"])
        self.assertEqual(len(self.get_people("?valid=valid")), 2)
        self.assertEqual([x["last_name"] for x in self.get_people("?valid=expired")], ["Zed"])
        self.assertEqual(self.get_people("?kind=official&valid=expired"), [])
        self.assertEqual(self.admin("get", "/officiating/people?kind=bogus").status_code, 422)

    def test_people_requires_feature(self):
        self.features = Entitlements({})
        r = self.admin("get", "/officiating/people")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json()["detail"], "Not found.")
        # positions and pools don't need the feature
        self.assertEqual(self.admin("get", "/team-positions").status_code, 200)
