"""Referee management API (``/referee-management``): permission and feature gate, the game list with needs, eligibility,
assigning members and external people, capacity, fees, types, audit and the payment form (HTML and PDF), plus the PDF preview
in the club settings."""

import datetime
import json
import uuid
from decimal import Decimal
from unittest import mock

from django.db import connection
from django.test import Client, TestCase
from django.utils import timezone
from django_tenants.utils import schema_context

from authorization.services import assign_role, create_role
from club.models import Club, Domain, RefereeType, Sport
from club.services import create_club, grant_login
from controlpanel.tests import login_session, make_user
from events.models import Attendance, Event, EventOfficial, EventReferee, Location, RefereeSignup, Season
from features.entitlements import Entitlement, Entitlements
from members.models import AuditEntry, Member
from teams.models import OfficialLevel, OfficialProfile, RefereeLevel, RefereeProfile, Team

BASE = "/api/v1/referee-management"
WITH_FEATURE = Entitlements({"referee_officials": Entitlement(True)})


class RefereeManagementTests(TestCase):
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
        cls.owner = make_user("owner@ref.test", first="Olivia", last="Owner")
        public = Club(schema_name="public", name="Platform", owner=cls.owner)
        public.auto_create_schema = False
        public.save()
        Domain.objects.create(domain="testserver", tenant=public, is_primary=True)
        cls.sport = Sport.objects.create(name="Hockey")
        cls.other_sport = Sport.objects.create(name="Football")
        cls.t_ref = RefereeType.objects.create(sport=cls.sport, kind="referee", name="Referee", ordering=1)
        cls.t_line = RefereeType.objects.create(sport=cls.sport, kind="referee", name="Linesman", ordering=2)
        cls.t_table = RefereeType.objects.create(sport=cls.sport, kind="official", name="Table official")
        cls.t_foreign = RefereeType.objects.create(sport=cls.other_sport, kind="referee", name="Football ref")
        cls.club = create_club("Ref FC", cls.owner, sport=cls.sport)
        cls.host = Domain.objects.get(tenant=cls.club, is_primary=True).domain
        today = datetime.date.today()
        with schema_context(cls.club.schema_name):
            cls.season = Season.objects.create(start_date=today - datetime.timedelta(days=100), end_date=today + datetime.timedelta(days=200))
            cls.u12 = Team.objects.create(name="U12", short_name="U12")
            cls.u14 = Team.objects.create(name="U14", short_name="U14")
            cls.fed = Team.objects.create(name="Seniors", short_name="SEN", referee_management="federation", official_management="federation")
            cls.home = Location.objects.create(name="Home ground", address="Sportlaan 1", city="Gent", zip_code="9000", country="BE", is_home=True)
            cls.away = Location.objects.create(name="Away ground", address="Elders 2", city="Aalst", zip_code="9300", country="BE")
            cls.alice = Member.objects.create(first_name="Alice", last_name="Ref", email="alice@ref.test")
            cls.bob = Member.objects.create(first_name="Bob", last_name="Whistle", email="bob@ref.test")
            cls.old = Member.objects.create(first_name="Olga", last_name="Expired", email="olga@ref.test")
            cls.nobody = Member.objects.create(first_name="Nina", last_name="NoProfile", email="nina@ref.test")
            cls.tabler = Member.objects.create(first_name="Tim", last_name="Table", email="tim@ref.test")
            cls.manager = Member.objects.create(first_name="Mara", last_name="Manager", email="mara@ref.test")
            cls.plain = Member.objects.create(first_name="Pim", last_name="Plain", email="pim@ref.test")
            level = RefereeLevel.objects.create(name="Regional")
            level.teams.set([cls.u12, cls.u14])
            cls.national = RefereeLevel.objects.create(name="Junior")
            cls.national.teams.set([cls.u12])
            RefereeProfile.objects.create(member=cls.alice, level=level, valid_until=today + datetime.timedelta(days=100))
            RefereeProfile.objects.create(member=cls.bob, level=cls.national, valid_until=today + datetime.timedelta(days=100))
            RefereeProfile.objects.create(member=cls.old, level=level, valid_until=today - datetime.timedelta(days=5))
            olevel = OfficialLevel.objects.create(name="Table")
            olevel.teams.set([cls.u12])
            OfficialProfile.objects.create(member=cls.tabler, level=olevel, valid_until=today + datetime.timedelta(days=100))
            cls.role = create_role("Referee manager", ["events.manage_referees"])
        cls.manager_user, _ = grant_login(cls.club, cls.manager)
        cls.plain_user, _ = grant_login(cls.club, cls.plain)
        with schema_context(cls.club.schema_name):
            assign_role(cls.manager_user, cls.role)
        cls.owner = type(cls.owner).objects.get(pk=cls.owner.pk)

    def setUp(self):
        self.features = WITH_FEATURE
        patcher = mock.patch("features.middleware.entitlements_for_request", side_effect=lambda request: self.features)
        patcher.start()
        self.addCleanup(patcher.stop)
        door = mock.patch("members.services.access.can_access_management", return_value=True)
        door.start()
        self.addCleanup(door.stop)
        ctx = schema_context(self.club.schema_name)
        ctx.__enter__()
        self.addCleanup(ctx.__exit__, None, None, None)

    # -- helpers --

    def call(self, method, path, user=None, data=None):
        headers = {"HTTP_HOST": self.host}
        if user is not None:
            headers["HTTP_X_SESSION_TOKEN"] = login_session(user)
        body = {"data": json.dumps(data), "content_type": "application/json"} if data is not None else {}
        response = getattr(Client(), method)(BASE + path, **body, **headers)
        connection.set_tenant(self.club)
        return response

    def admin(self, method, path, data=None):
        return self.call(method, path, self.owner, data)

    def game(self, days=3, teams=None, location=None, **kw):
        event = Event.objects.create(kind=Event.EventKind.GAME, title="Game", start=timezone.now() + datetime.timedelta(days=days), location=self.home if location is None else location, season=self.season, **kw)
        event.teams.set(teams if teams is not None else [self.u12])
        return event

    def assign(self, event, kind="referee", **data):
        return self.admin("post", f"/games/{event.pk}/assignments", {"kind": kind, **data})

    # -- gate --

    def test_permission_then_feature(self):
        event = self.game()
        calls = [
            ("get", "/summary", None),
            ("get", "/games", None),
            ("get", f"/games/{event.pk}", None),
            ("post", f"/games/{event.pk}/signups", {"kind": "referee", "member_id": str(uuid.uuid4())}),
            ("get", f"/games/{event.pk}/candidates", None),
            ("post", f"/games/{event.pk}/assignments", {"kind": "referee", "external_name": "X"}),
            ("patch", f"/games/{event.pk}/capacity", {"max_referees": 3}),
            ("patch", f"/assignments/referee/{uuid.uuid4()}", {"fee": "5"}),
            ("delete", f"/assignments/referee/{uuid.uuid4()}", None),
            ("get", f"/games/{event.pk}/payment-form", None),
            ("get", f"/games/{event.pk}/payment-form.pdf", None),
        ]
        for method, path, data in calls:
            self.assertEqual(self.call(method, path, self.plain_user, data).status_code, 403, (method, path))
            self.assertIn(self.call(method, path, None, data).status_code, (401, 403), (method, path))
        self.features = Entitlements({})
        for method, path, data in calls:
            self.assertEqual(self.call(method, path, self.plain_user, data).status_code, 403, (method, path))  # 403 first
            self.assertEqual(self.admin(method, path, data).status_code, 404, (method, path))
        self.features = WITH_FEATURE
        for method, path, data in calls[:2]:
            self.assertEqual(self.call(method, path, self.manager_user, data).status_code, 200, (method, path))

    def test_me_reports_the_permission(self):
        self.assertTrue(Client().get("/api/v1/me", HTTP_HOST=self.host, HTTP_X_SESSION_TOKEN=login_session(self.manager_user)).json()["can_manage_referees"])
        self.assertFalse(Client().get("/api/v1/me", HTTP_HOST=self.host, HTTP_X_SESSION_TOKEN=login_session(self.plain_user)).json()["can_manage_referees"])
        self.assertTrue(Client().get("/api/v1/me", HTTP_HOST=self.host, HTTP_X_SESSION_TOKEN=login_session(self.owner)).json()["can_manage_referees"])

    # -- the list --

    def test_games_list_needs_and_scope(self):
        mine = self.game(days=2)
        self.game(days=3, location=self.away)  # not a home game
        self.game(days=4, teams=[self.fed])  # federation arranges
        self.game(days=40)  # outside the two-week window
        r = self.admin("get", "/games")
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual([g["id"] for g in body["games"]], [str(mine.pk)])
        game = body["games"][0]
        self.assertEqual((game["needs_referees"], game["needs_officials"], game["referee_short"], game["max_referees"], game["max_officials"]), (True, True, True, 2, 1))
        self.assertEqual(body["counts"]["open"], 1)
        self.assertEqual(len(self.admin("get", "/games?range=50").json()["games"]), 2)
        self.assertEqual(self.admin("get", "/games?need=fees").json()["games"], [])
        self.assertEqual(self.admin("get", "/games?range=bogus&need=bogus").json()["range"], "two_weeks")

    def test_summary_kpis_and_types(self):
        game = self.game(days=2)
        EventReferee.objects.create(event=game, external_name="Ext One")
        summary = self.admin("get", "/summary").json()
        self.assertEqual(summary["kpis"]["referee"], {"games": 1, "none": 0, "partial": 1, "full": 0})
        self.assertEqual(summary["kpis"]["official"], {"games": 1, "none": 1, "partial": 0, "full": 0})
        self.assertEqual(summary["fees_pending"], 1)
        self.assertEqual([t["name"] for t in summary["types"]["referee"]], ["Referee", "Linesman"])  # only this sport's, in order
        self.assertEqual([t["name"] for t in summary["types"]["official"]], ["Table official"])

    def test_summary_counts_every_game_once_and_a_game_needing_nobody_is_fully_staffed(self):
        self.game(days=2)  # needs 2 referees, has none
        zero = self.game(days=3)
        zero.max_referees = zero.max_officials = 0
        zero.save()
        summary = self.admin("get", "/summary").json()
        self.assertEqual(summary["kpis"]["referee"], {"games": 2, "none": 1, "partial": 0, "full": 1})
        self.assertEqual(summary["kpis"]["official"], {"games": 2, "none": 1, "partial": 0, "full": 1})

    # -- eligibility --

    def test_candidates_follow_level_teams_validity_and_exclusions(self):
        game = self.game(teams=[self.u14])
        names = {c["name"]: c for c in self.admin("get", f"/games/{game.pk}/candidates").json()}
        self.assertEqual(set(names), {"Alice Ref", "Olga Expired"})  # Bob's level only covers U12
        self.assertTrue(names["Olga Expired"]["expired"])
        self.assertFalse(names["Alice Ref"]["expired"])
        RefereeProfile.objects.get(member=self.alice).excluded_teams.add(self.u14)
        self.assertEqual({c["name"] for c in self.admin("get", f"/games/{game.pk}/candidates").json()}, {"Olga Expired"})
        officials = self.admin("get", f"/games/{self.game().pk}/candidates?kind=official").json()
        self.assertEqual([c["name"] for c in officials], ["Tim Table"])
        self.assertEqual(self.admin("get", f"/games/{game.pk}/candidates?kind=bogus").status_code, 422)

    def test_any_member_can_be_found_and_assigned_even_when_not_eligible(self):
        game = self.game(teams=[self.u14])
        EventReferee.objects.create(event=game, member=self.alice)
        EventReferee.objects.create(event=game, member=self.old)  # nobody eligible is left for this game
        self.assertEqual(self.admin("get", f"/games/{game.pk}/candidates").json(), [])
        found = self.admin("get", f"/games/{game.pk}/candidates?q=tim").json()
        self.assertEqual([(c["name"], c["eligible"], c["level"]) for c in found], [("Tim Table", False, "")])
        EventReferee.objects.filter(event=game, member=self.old).delete()
        eligible = {c["name"]: c["eligible"] for c in self.admin("get", f"/games/{game.pk}/candidates?q=o").json()}
        self.assertTrue(eligible["Olga Expired"])  # eligible people still come first, and are flagged so
        game.max_referees = 3
        game.save()
        r = self.assign(game, member_id=str(self.timmy_id()))
        self.assertEqual(r.status_code, 201, r.content)

    def timmy_id(self):
        return Member.objects.get(first_name="Tim").pk

    def test_candidates_flag_conflicts_sort_last_and_skip_assigned(self):
        game = self.game(days=3, teams=[self.u12])
        clash = Event.objects.create(kind=Event.EventKind.TRAINING, title="U16 training", start=game.start + datetime.timedelta(minutes=30), season=self.season)
        Attendance.objects.create(event=clash, member=self.alice, status="present")
        Attendance.objects.create(event=clash, member=self.bob, status="absent")  # said no: no clash
        # Bob also referees another game at the same time: a clash as well, but only for Olga's later check
        other = self.game(days=3, teams=[self.u12])
        EventReferee.objects.create(event=other, member=self.old, external_name="")
        rows = self.admin("get", f"/games/{game.pk}/candidates").json()
        by_name = {r["name"]: r for r in rows}
        self.assertEqual(by_name["Bob Whistle"]["conflicts"], [])
        (c,) = by_name["Alice Ref"]["conflicts"]
        self.assertEqual((c["title"], c["role"], c["event_id"], c["kind"]), ("U16 training", "attendance", str(clash.pk), "training"))
        self.assertIn("start", c)
        self.assertIn("end", c)
        (c,) = by_name["Olga Expired"]["conflicts"]
        self.assertEqual((c["role"], c["event_id"]), ("referee", str(other.pk)))
        self.assertEqual(rows[0]["name"], "Bob Whistle")  # free people first, conflicts below
        self.assertEqual({r["name"] for r in rows[1:]}, {"Alice Ref", "Olga Expired"})

    def test_assigning_over_a_conflict_needs_confirmation(self):
        game = self.game(days=3)
        clash = Event.objects.create(kind=Event.EventKind.TRAINING, title="U16 training", start=game.start, season=self.season)
        Attendance.objects.create(event=clash, member=self.alice, status="no_response")
        r = self.assign(game, member_id=str(self.alice.pk))
        self.assertEqual((r.status_code, r.json()["code"]), (422, "conflict"))
        self.assertIn("U16 training", r.json()["detail"])
        self.assertFalse(EventReferee.objects.exists())
        r = self.assign(game, member_id=str(self.alice.pk), force=True)
        self.assertEqual(r.status_code, 201, r.content)
        self.assertNotIn("Alice Ref", [c["name"] for c in self.admin("get", f"/games/{game.pk}/candidates").json()])
        # the assigned person shows the clash in the list and the detail
        listed = self.admin("get", "/games?range=50").json()["games"]
        row = next(g for g in listed if g["id"] == str(game.pk))
        self.assertEqual(row["conflict_count"], 1)
        self.assertEqual(row["referees"][0]["conflicts"][0]["title"], "U16 training")
        self.assertEqual(self.admin("get", f"/games/{game.pk}").json()["referees"][0]["conflicts"][0]["role"], "attendance")

    def test_overlap_is_by_time_range(self):
        game = self.game(days=3)
        Attendance.objects.create(
            event=Event.objects.create(kind=Event.EventKind.TRAINING, title="Before", start=game.start - datetime.timedelta(hours=5), end=game.start - datetime.timedelta(hours=3), season=self.season), member=self.alice, status="present"
        )
        Attendance.objects.create(event=Event.objects.create(kind=Event.EventKind.TRAINING, title="After", start=game.start + datetime.timedelta(hours=5), season=self.season), member=self.alice, status="present")
        alice = next(c for c in self.admin("get", f"/games/{game.pk}/candidates").json() if c["name"] == "Alice Ref")
        self.assertEqual(alice["conflicts"], [])
        self.assertEqual(self.assign(game, member_id=str(self.alice.pk)).status_code, 201)

    # -- sign-ups --

    def test_signups_flow(self):
        game = self.game()
        r = self.admin("post", f"/games/{game.pk}/signups", {"kind": "referee", "member_id": str(self.alice.pk)})
        self.assertEqual((r.status_code, r.json()["name"]), (201, "Alice Ref"))
        self.assertEqual(self.admin("post", f"/games/{game.pk}/signups", {"kind": "referee", "member_id": str(self.alice.pk)}).status_code, 201)  # idempotent
        self.assertEqual(RefereeSignup.objects.count(), 1)
        self.assertEqual(self.admin("post", f"/games/{game.pk}/signups", {"kind": "referee", "member_id": str(self.nobody.pk)}).status_code, 422)  # not eligible
        self.assertEqual(self.admin("post", f"/games/{game.pk}/signups", {"kind": "official", "member_id": str(self.alice.pk)}).status_code, 422)  # no official profile
        listed = next(g for g in self.admin("get", "/games?range=50").json()["games"] if g["id"] == str(game.pk))
        self.assertEqual([(s["kind"], s["name"]) for s in listed["signups"]], [("referee", "Alice Ref")])
        detail = self.admin("get", f"/games/{game.pk}").json()
        self.assertTrue(detail["signups"][0]["can_assign"])
        cands = self.admin("get", f"/games/{game.pk}/candidates").json()
        self.assertTrue(next(c for c in cands if c["name"] == "Alice Ref")["signed_up"])
        self.assertEqual(cands[0]["name"], "Alice Ref")  # signed up people come first among the free ones
        # assigning settles the sign-up
        self.assertEqual(self.assign(game, member_id=str(self.alice.pk)).status_code, 201)
        self.assertFalse(RefereeSignup.objects.exists())

    def test_signup_removal_audit_and_gate(self):
        game = self.game()
        sid = self.admin("post", f"/games/{game.pk}/signups", {"kind": "referee", "member_id": str(self.alice.pk)}).json()["id"]
        self.assertEqual(self.call("delete", f"/signups/referee/{sid}", self.plain_user).status_code, 403)
        self.assertEqual(self.admin("delete", f"/signups/official/{sid}").status_code, 404)
        self.assertEqual(self.admin("delete", f"/signups/referee/{sid}").status_code, 204)
        self.assertEqual(self.admin("delete", f"/signups/referee/{sid}").status_code, 404)
        self.assertEqual(self.call("post", f"/games/{game.pk}/signups", self.plain_user, {"kind": "referee", "member_id": str(self.alice.pk)}).status_code, 403)
        self.assertTrue(AuditEntry.objects.filter(action="event.referee_signup_added").exists())
        self.assertTrue(AuditEntry.objects.filter(action="event.referee_signup_removed").exists())

    def test_detail_says_who_signed_up_cannot_be_assigned(self):
        game = self.game()
        self.admin("post", f"/games/{game.pk}/signups", {"kind": "referee", "member_id": str(self.alice.pk)})
        self.admin("patch", f"/games/{game.pk}/capacity", {"max_referees": 1})
        self.assertEqual(self.assign(game, external_name="Fed").status_code, 201)  # the only place is taken
        self.assertFalse(self.admin("get", f"/games/{game.pk}").json()["signups"][0]["can_assign"])
        self.assertEqual(self.admin("get", f"/games/{uuid.uuid4()}").status_code, 404)

    # -- assigning --

    def test_assign_member_and_external_with_audit(self):
        game = self.game()
        r = self.assign(game, member_id=str(self.alice.pk), referee_type_id=str(self.t_ref.pk))
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual((r.json()["name"], r.json()["type_name"], r.json()["fee_pending"], r.json()["is_external"]), ("Alice Ref", "Referee", True, False))
        r = self.assign(game, external_name="  Fed Ref  ")
        self.assertEqual((r.status_code, r.json()["name"], r.json()["is_external"]), (201, "Fed Ref", True))
        self.assertEqual(self.admin("get", "/games").json()["games"][0]["referee_short"], False)
        actions = list(AuditEntry.objects.filter(action="event.referee_assigned").values_list("target_label", flat=True))
        self.assertEqual(sorted(actions), ["Alice Ref - Game", "Fed Ref - Game"])
        self.assertEqual(len(actions), 2)
        # officials are a separate list with their own capacity
        r = self.assign(game, kind="official", member_id=str(self.tabler.pk))
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(EventOfficial.objects.count(), 1)

    def test_assign_rules(self):
        game = self.game(teams=[self.u14])
        # anyone can be assigned by hand, eligible or not (the picker flags them "not eligible")
        spare = self.game(teams=[self.u14])
        self.assertEqual(self.assign(spare, member_id=str(self.bob.pk)).status_code, 201)  # not eligible for U14
        self.assertEqual(self.assign(spare, member_id=str(self.nobody.pk)).status_code, 201)  # no profile
        self.assertEqual(self.assign(spare, member_id=str(uuid.uuid4())).status_code, 422)  # not a member
        self.assertEqual(self.assign(game).status_code, 422)  # neither
        self.assertEqual(self.assign(game, member_id=str(self.alice.pk), external_name="X").status_code, 422)  # both
        self.assertEqual(self.assign(game, external_name="X", referee_type_id=str(self.t_table.pk)).status_code, 422)  # wrong kind
        self.assertEqual(self.assign(game, external_name="X", referee_type_id=str(self.t_foreign.pk)).status_code, 422)  # other sport
        self.assertEqual(self.assign(game, kind="bogus", external_name="X").status_code, 422)
        self.assertEqual(self.assign(game, external_name="A").status_code, 201)
        self.assertEqual(self.assign(game, member_id=str(self.alice.pk)).status_code, 201)
        full = self.assign(game, external_name="C")
        self.assertEqual(full.status_code, 422)  # max_referees is 2
        self.assertIn("maximum", full.json()["detail"])
        self.assertEqual(EventReferee.objects.filter(event=game).count(), 2)
        # not a home game / federation-managed team: nothing to arrange
        self.assertEqual(self.assign(self.game(location=self.away), external_name="X").status_code, 422)
        self.assertEqual(self.assign(self.game(teams=[self.fed]), external_name="X").status_code, 422)
        self.assertEqual(self.admin("post", f"/games/{uuid.uuid4()}/assignments", {"kind": "referee", "external_name": "X"}).status_code, 404)

    def test_manager_with_only_the_permission_can_assign(self):
        game = self.game()
        r = self.call("post", f"/games/{game.pk}/assignments", self.manager_user, {"kind": "referee", "external_name": "Ext"})
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(EventReferee.objects.get().assigned_by, self.manager)

    # -- capacity --

    def test_capacity(self):
        game = self.game()
        self.assign(game, external_name="A")
        self.assign(game, external_name="B")
        self.assertEqual(self.admin("patch", f"/games/{game.pk}/capacity", {"max_referees": 1}).status_code, 422)  # two already there
        self.assertEqual(self.admin("patch", f"/games/{game.pk}/capacity", {"max_referees": 99}).status_code, 422)
        self.assertEqual(self.admin("patch", f"/games/{game.pk}/capacity", {"max_referees": 3, "max_officials": 2}).status_code, 200)
        game.refresh_from_db()
        self.assertEqual((game.max_referees, game.max_officials), (3, 2))
        self.assertEqual(self.assign(game, external_name="C").status_code, 201)
        self.assertEqual(AuditEntry.objects.filter(action="event.referee_capacity_updated").count(), 1)

    # -- fees --

    def test_fee_line_type_rename_and_total(self):
        game = self.game()
        row_id = self.assign(game, external_name="Fed Ref").json()["id"]
        r = self.admin("patch", f"/assignments/referee/{row_id}", {"fee": "35.00", "km": "12.0", "km_rate": "0.3500", "referee_type_id": str(self.t_line.pk), "name": "Federico Ref"})
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual((Decimal(body["total"]), body["type_name"], body["name"], body["fee_pending"]), (Decimal("39.20"), "Linesman", "Federico Ref", False))
        listed = self.admin("get", "/games").json()["games"][0]
        self.assertEqual((Decimal(listed["total"]), listed["fees_pending"]), (Decimal("39.20"), False))
        # only the sent fields change
        self.assertEqual(Decimal(self.admin("patch", f"/assignments/referee/{row_id}", {"fee": "40"}).json()["km"]), Decimal("12.0"))
        # "no fee" clears the line and settles the pending flag
        r = self.admin("patch", f"/assignments/referee/{row_id}", {"no_fee": True}).json()
        self.assertEqual((r["no_fee"], Decimal(r["total"]), r["km"], r["fee_pending"]), (True, Decimal("0"), None, False))
        self.assertTrue(AuditEntry.objects.filter(action="event.referee_updated").exists())

    def test_fee_validation(self):
        game = self.game()
        row_id = self.assign(game, external_name="Fed Ref").json()["id"]
        for payload in ({"fee": "-1"}, {"km": "-2"}, {"km_rate": "1000"}, {"name": " "}, {"referee_type_id": str(self.t_table.pk)}):
            r = self.admin("patch", f"/assignments/referee/{row_id}", payload)
            self.assertEqual(r.status_code, 422, payload)
        member_row = self.assign(game, member_id=str(self.alice.pk)).json()["id"]
        self.assertEqual(self.admin("patch", f"/assignments/referee/{member_row}", {"name": "Hacked"}).status_code, 422)  # members keep their name
        self.assertEqual(self.admin("patch", f"/assignments/referee/{uuid.uuid4()}", {"fee": "1"}).status_code, 404)
        self.assertEqual(self.admin("patch", f"/assignments/official/{row_id}", {"fee": "1"}).status_code, 404)  # kinds are separate
        self.assertEqual(self.admin("patch", f"/assignments/bogus/{row_id}", {"fee": "1"}).status_code, 422)

    def test_remove(self):
        game = self.game()
        row_id = self.assign(game, external_name="Fed Ref").json()["id"]
        self.assertEqual(self.admin("delete", f"/assignments/referee/{row_id}").status_code, 204)
        self.assertFalse(EventReferee.objects.exists())
        self.assertEqual(self.admin("delete", f"/assignments/referee/{row_id}").status_code, 404)
        self.assertTrue(AuditEntry.objects.filter(action="event.referee_removed").exists())

    def test_assigned_and_removed_people_with_a_login_are_notified(self):
        user, _ = grant_login(self.club, self.alice)
        game = self.game()
        with mock.patch("notifications.inbox.notify") as notify, self.captureOnCommitCallbacks(execute=True):
            row_id = self.assign(game, member_id=str(self.alice.pk), referee_type_id=str(self.t_ref.pk)).json()["id"]
            self.assign(game, external_name="Fed Ref")  # an external has nobody to tell
        self.assertEqual(notify.call_count, 1)
        args, kwargs = notify.call_args
        self.assertEqual(args[0].pk, user.pk)
        self.assertIn("assigned as referee", kwargs["title"])
        self.assertEqual((kwargs["category"], kwargs["push"], kwargs["href"]), ("crew", True, f"/calendar/events/{game.pk}"))
        with mock.patch("notifications.inbox.notify") as notify, self.captureOnCommitCallbacks(execute=True):
            self.admin("delete", f"/assignments/referee/{row_id}")
        self.assertEqual(notify.call_count, 1)
        self.assertIn("taken off", notify.call_args.kwargs["title"])

    def test_event_details_list_referees_and_officials_without_fees(self):
        game = self.game()
        self.assign(game, member_id=str(self.alice.pk), referee_type_id=str(self.t_ref.pk))
        self.assign(game, external_name="Fed Ref")
        self.assign(game, kind="official", member_id=str(self.tabler.pk))
        body = self.client.get(f"/api/v1/events/{game.pk}", HTTP_HOST=self.host, HTTP_X_SESSION_TOKEN=login_session(self.plain_user)).json()
        self.assertEqual(sorted(p["name"] for p in body["referees"]), ["Alice Ref", "Fed Ref"])
        self.assertEqual([p["name"] for p in body["officials"]], ["Tim Table"])
        self.assertTrue(all("fee" not in p for p in body["referees"]))

    # -- payments and workload --

    def test_season_payments_and_workload_include_upcoming_games_and_externals(self):
        past = self.game(days=-5)
        EventReferee.objects.create(event=past, member=self.alice, fee=Decimal("30.00"))
        EventReferee.objects.create(event=past, external_name="Ext", fee=Decimal("20.00"), km=Decimal("10"), km_rate=Decimal("0.5"))
        EventReferee.objects.create(event=self.game(days=-2), member=self.alice)  # fee still to fill in
        upcoming = self.game(days=2)
        EventReferee.objects.create(event=upcoming, external_name="Ext", fee=Decimal("35.00"), km=Decimal("12"), km_rate=Decimal("0.35"))
        self.game(days=-9)  # nobody on it: not listed
        body = self.admin("get", "/games?scope=season").json()
        self.assertEqual(len(body["games"]), 3)
        self.assertEqual(body["games"][0]["id"], str(past.pk))  # date order, past first
        self.assertEqual(body["games"][-1]["id"], str(upcoming.pk))
        self.assertEqual(Decimal(body["games"][0]["total"]), Decimal("55.00"))
        self.assertEqual(Decimal(body["games"][-1]["total"]), Decimal("39.20"))
        self.assertEqual(body["counts"]["fees"], 1)
        summary = self.admin("get", "/summary").json()
        self.assertEqual([(w["name"], w["games"], Decimal(w["total"])) for w in summary["workload"]], [("Alice Ref", 2, Decimal("30.00")), ("Ext", 2, Decimal("64.20"))])
        self.assertEqual(len({w["key"] for w in summary["workload"]}), 2)
        self.assertEqual(summary["fees_pending"], 1)

    # -- payment forms --

    def test_payment_form_html_and_pdf(self):
        game = self.game(external_game_id="BE-1", opponent=None)
        a = self.assign(game, member_id=str(self.alice.pk), referee_type_id=str(self.t_ref.pk)).json()["id"]
        self.admin("patch", f"/assignments/referee/{a}", {"fee": "35", "km": "12", "km_rate": "0.35"})
        r = self.admin("get", f"/games/{game.pk}/payment-form")
        self.assertEqual((r.status_code, r["content-type"]), (200, "text/html; charset=utf-8"))
        html = r.content.decode()
        for needle in ("Referee payment form", "Alice Ref", "Referee", "39.20", "BE-1", "Ref FC", "Sportlaan 1", "9000 Gent"):
            self.assertIn(needle, html)
        self.assertIn("Betaalformulier", self.admin("get", f"/games/{game.pk}/payment-form?lang=nl").content.decode())
        self.assertIn("Official payment form", self.admin("get", f"/games/{game.pk}/payment-form?kind=official").content.decode())
        self.assertIn("No officials assigned yet.", self.admin("get", f"/games/{game.pk}/payment-form?kind=official").content.decode())
        self.assertEqual(self.admin("get", f"/games/{game.pk}/payment-form?kind=bogus").status_code, 422)
        with mock.patch("club.previews.render_pdf", return_value=b"%PDF-1.4 fake") as render:
            r = self.admin("get", f"/games/{game.pk}/payment-form.pdf")
        self.assertEqual((r.status_code, r["content-type"], r.content), (200, "application/pdf", b"%PDF-1.4 fake"))
        self.assertIn("attachment", r["content-disposition"])
        self.assertIn("Alice Ref", render.call_args.args[0])
        with mock.patch("club.previews.render_pdf", side_effect=RuntimeError("no pango")):
            self.assertEqual(self.admin("get", f"/games/{game.pk}/payment-form.pdf").status_code, 503)

    # -- the PDF tab in the club settings --

    def test_pdf_previews_in_club_settings(self):
        def get(path):
            return Client().get(f"/api/v1/club/settings{path}", HTTP_HOST=self.host, HTTP_X_SESSION_TOKEN=login_session(self.owner))

        listing = {p["key"]: p for p in get("/previews").json()}
        self.assertEqual(listing["referee_payment_form"]["kind"], "pdf")
        self.assertEqual(listing["official_payment_form"]["kind"], "pdf")
        html = get("/previews/referee_payment_form/html?lang=fr").content.decode()
        for needle in ("Jamie Doe", "Ref FC", "Formulaire de paiement"):
            self.assertIn(needle, html)
        with mock.patch("club.previews.render_pdf", return_value=b"%PDF-1.4 fake"):
            r = get("/previews/referee_payment_form/file")
        self.assertEqual((r.status_code, r["content-type"]), (200, "application/pdf"))
        self.features = Entitlements({})  # no feature: the documents are not offered
        self.assertNotIn("referee_payment_form", {p["key"] for p in get("/previews").json()})
        self.assertEqual(get("/previews/referee_payment_form/html").status_code, 404)
