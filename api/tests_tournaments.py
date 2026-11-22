"""Tournament API (``/tournaments``, ``/dressing-rooms``, ``/public/tournaments``, ``/portal/tournaments``): permission matrix, feature gate,
/me flags, public pages and registration, and the happy paths (approve, schedule, results, standings, shifts)."""

import datetime
import json
import uuid
from unittest import mock

from django.core.cache import cache
from django.db import connection
from django.test import Client, TestCase
from django.utils import timezone
from django_tenants.utils import schema_context

from authorization.services import assign_role, create_role
from club.models import Club, Domain
from club.services import create_club, grant_login
from controlpanel.tests import login_session, make_user
from events.models import Attendance, Event, Season
from features.entitlements import Entitlement, Entitlements
from members.models import Member
from teams.models import Position, StaffAssignment, Team, TeamMembership
from tournaments.models import Match, MatchAttribute, MatchAttributeValue, MatchEvent, Tournament, TournamentField, TournamentGroup, TournamentRosterEntry, TournamentStage, TournamentTask, TournamentTeam

BASE = "/api/v1"
WITH = Entitlements({"tournaments": Entitlement(True)})
LUNCH_SETTINGS = {"duration_minutes": 30, "gap_before_minutes": 15, "gap_after_minutes": 15, "capacity_teams": 2, "interval_minutes": 10}
PERMS = {"manage": "tournaments.manage_tournaments", "operate": "tournaments.operate_tournaments"}


class TournamentApiTests(TestCase):
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
        cls.owner = make_user("owner@trn.test", first="Olivia", last="Owner")
        public = Club(schema_name="public", name="Platform", owner=cls.owner)
        public.auto_create_schema = False
        public.save()
        Domain.objects.create(domain="testserver", tenant=public, is_primary=True)
        cls.club = create_club("Trn FC", cls.owner)
        cls.host = Domain.objects.get(tenant=cls.club, is_primary=True).domain
        cls.users = {}
        with schema_context(cls.club.schema_name):
            cls.season = Season.objects.create(start_date=datetime.date(2026, 7, 1), end_date=datetime.date(2027, 6, 30))
            cls.club_team = Team.objects.create(name="First Team", short_name="1st")
            members = {key: Member.objects.create(first_name=key.title(), last_name="Staff", email=f"{key}@trn.test") for key in [*PERMS, "plain"]}
            roles = {key: create_role(f"Trn {key}", [code]) for key, code in PERMS.items()}
        for key, member in members.items():
            user, _ = grant_login(cls.club, member)
            cls.users[key] = user
            cls.users[key + "_member"] = member
            if key in roles:
                with schema_context(cls.club.schema_name):
                    assign_role(user, roles[key])
        cls.owner = type(cls.owner).objects.get(pk=cls.owner.pk)

    def setUp(self):
        self.features = WITH
        for target in ("features.middleware.entitlements_for_request", "features.context.get_current_entitlements", "features.backends.get_current_entitlements"):
            patcher = mock.patch(target, side_effect=lambda *a, **k: self.features)
            patcher.start()
            self.addCleanup(patcher.stop)
        door = mock.patch("members.services.access.can_access_management", return_value=True)
        door.start()
        self.addCleanup(door.stop)
        targets = (
            "api.routers.tournaments.notifications.dispatch_notify_tournament_shifts_open",
            "api.routers.tournaments.notifications.send_registration_decision_email",
            "api.routers.public_tournaments.notifications.send_registration_confirmation_email",
        )
        for target in targets:
            patcher = mock.patch(target)
            patcher.start()
            self.addCleanup(patcher.stop)
        cache.clear()
        ctx = schema_context(self.club.schema_name)
        ctx.__enter__()
        self.addCleanup(ctx.__exit__, None, None, None)

    # -- helpers --

    def call(self, method, path, user=None, data=None, prefix="/tournaments"):
        headers = {"HTTP_HOST": self.host}
        if user is not None:
            headers["HTTP_X_SESSION_TOKEN"] = login_session(user)
        body = {"data": json.dumps(data), "content_type": "application/json"} if data is not None else {}
        response = getattr(Client(), method)(BASE + prefix + path, **body, **headers)
        connection.set_tenant(self.club)
        return response

    def as_(self, key, method, path, data=None, prefix="/tournaments"):
        return self.call(method, path, self.owner if key == "owner" else self.users[key], data, prefix)

    def make(self, **kw):
        fields = {"name": "Summer Cup", "season": self.season, "start_date": datetime.date(2026, 9, 19), "end_date": datetime.date(2026, 9, 20)}
        return Tournament.objects.create(**{**fields, **kw})

    def foreign(self, tournament, name):
        return TournamentTeam.objects.create(tournament=tournament, foreign_name=name, foreign_contact_name="C", foreign_contact_email="c@x.test")

    # -- permission matrix --

    def test_permission_matrix(self):
        t = self.make()
        i = uuid.uuid4()
        endpoints = [  # (method, path, body, keys that may pass)
            ("get", "", None, {"manage", "operate"}),
            ("get", f"/{t.pk}", None, {"manage", "operate"}),
            ("get", f"/{t.pk}/standings", None, {"manage", "operate"}),
            ("get", f"/{t.pk}/setup", None, {"manage", "operate"}),
            ("get", f"/{t.pk}/tasks", None, {"manage", "operate"}),
            ("post", "", {"name": "X", "days": [{"date": "2026-09-19", "starts_at": "09:00", "ends_at": "17:00"}]}, {"manage"}),
            ("patch", f"/{t.pk}", {"name": "Y"}, {"manage"}),
            ("post", f"/registrations/{i}/approve", None, {"manage"}),
            ("post", f"/teams/{i}/withdraw", {}, {"manage"}),
            ("patch", f"/teams/{i}/payment", {"payment_status": "unpaid"}, {"manage"}),
            ("post", f"/{t.pk}/fields", {"name": "F1"}, {"manage", "operate"}),
            ("post", f"/{t.pk}/schedule/clear", None, {"manage", "operate"}),
            ("get", f"/{t.pk}/lunch", None, {"manage", "operate"}),
            ("put", f"/{t.pk}/lunch", {"settings": LUNCH_SETTINGS, "days": []}, {"manage", "operate"}),
            ("post", f"/{t.pk}/lunch/generate", {}, {"manage", "operate"}),
            ("post", f"/{t.pk}/lunch/clear", None, {"manage", "operate"}),
            ("patch", f"/matches/{i}/teams", {"home_team_id": None}, {"manage", "operate"}),
            ("delete", f"/matches/{i}", None, {"manage", "operate"}),
            ("post", f"/{t.pk}/stages/{i}/matches", {}, {"manage", "operate"}),
            ("post", f"/{t.pk}/tasks", {"title": "Gate", "starts_at": "2026-09-19T09:00:00Z", "ends_at": "2026-09-19T10:00:00Z"}, {"manage", "operate"}),
        ]
        for method, path, body, allowed in endpoints:
            for key in ("plain", "manage", "operate"):
                response = self.as_(key, method, path, body)
                if key in allowed:
                    self.assertNotEqual(response.status_code, 403, (key, method, path))
                else:
                    self.assertEqual(response.status_code, 403, (key, method, path))
            self.assertNotEqual(self.as_("owner", method, path, body).status_code, 403, ("owner", method, path))
            self.assertEqual(self.call(method, path, None, body).status_code, 401, (method, path))
        self.assertEqual(self.as_("plain", "get", "", prefix="/dressing-rooms").status_code, 403)
        self.assertEqual(self.as_("operate", "post", "", {"name": "Room"}, prefix="/dressing-rooms").status_code, 403)
        self.assertEqual(self.as_("manage", "post", "", {"name": "Room"}, prefix="/dressing-rooms").status_code, 201)

    def test_no_feature_is_404_for_owner_and_403_for_holders(self):
        t = self.make()
        self.features = Entitlements({})
        self.assertEqual(self.as_("owner", "get", "").status_code, 404)
        self.assertEqual(self.as_("owner", "get", f"/{t.pk}/schedule").status_code, 404)
        self.assertEqual(self.as_("owner", "get", "", prefix="/dressing-rooms").status_code, 404)
        self.assertEqual(self.as_("manage", "get", "").status_code, 403)
        self.assertEqual(self.as_("plain", "get", "").status_code, 403)

    def test_me_flags(self):
        def me(key):
            user = self.owner if key == "owner" else self.users[key]
            body = Client().get(BASE + "/me", HTTP_HOST=self.host, HTTP_X_SESSION_TOKEN=login_session(user)).json()
            return body["can_manage_tournaments"], body["can_operate_tournaments"]

        self.assertEqual(me("owner"), (True, True))
        self.assertEqual(me("manage"), (True, True))
        self.assertEqual(me("operate"), (False, True))
        self.assertEqual(me("plain"), (False, False))
        self.features = Entitlements({})
        self.assertEqual(me("owner"), (False, False))

    # -- tournament and registration queue --

    def test_create_update_and_season_rules(self):
        body = {"name": "Autumn Cup", "days": [{"date": "2026-10-03", "starts_at": "09:00", "ends_at": "17:00"}, {"date": "2026-10-04", "starts_at": "09:00", "ends_at": "17:00"}], "status": "published", "home_team_id": str(self.club_team.pk)}
        r = self.as_("manage", "post", "", body)
        self.assertEqual(r.status_code, 201, r.content)
        tid = r.json()["id"]
        self.assertEqual(r.json()["slug"], "autumn-cup")
        self.assertTrue(TournamentTeam.objects.filter(tournament_id=tid, team=self.club_team).exists())  # home team entered
        self.assertEqual(self.as_("manage", "post", "", {**body, "days": [{"date": "2030-01-01", "starts_at": "09:00", "ends_at": "17:00"}]}).status_code, 422)  # no season
        self.assertEqual(self.as_("manage", "post", "", {**body, "days": [{"date": "2026-10-03", "starts_at": "17:00", "ends_at": "09:00"}]}).status_code, 422)
        r = self.as_("manage", "patch", f"/{tid}", {"max_teams": 8, "entry_fee_amount": "25"})
        self.assertEqual((r.json()["max_teams"], r.json()["entry_fee_amount"]), (8, "25.00"))
        self.assertEqual(self.as_("operate", "get", "").json()["results"][0]["name"], "Autumn Cup")

    def test_home_team_tournament_shows_on_the_calendar(self):
        body = {"name": "Autumn Cup", "days": [{"date": "2026-10-03", "starts_at": "09:00", "ends_at": "17:00"}, {"date": "2026-10-04", "starts_at": "09:00", "ends_at": "17:00"}], "status": "published", "home_team_id": str(self.club_team.pk)}
        tid = self.as_("manage", "post", "", body).json()["id"]
        event = Tournament.objects.get(pk=tid).event
        self.assertIsNotNone(event)
        listing = self.call("get", "?from=2026-10-01&to=2026-10-31", self.owner, prefix="/events")
        self.assertEqual(listing.status_code, 200, listing.content)
        detail = self.call("get", f"/{event.pk}", self.owner, prefix="/events")
        self.assertEqual(detail.status_code, 200, detail.content)
        form = self.call("get", f"/{event.pk}/form", self.owner, prefix="/events")
        self.assertEqual(form.status_code, 200, form.content)

    def test_days_drive_the_dates_and_the_calendar_event(self):
        body = {"name": "Days Cup", "status": "draft", "home_team_id": str(self.club_team.pk), "days": [
            {"date": "2026-10-10", "starts_at": "10:30", "ends_at": "18:00"},
            {"date": "2026-10-03", "starts_at": "09:00", "ends_at": "17:00"},  # out of order and not consecutive on purpose
        ]}
        r = self.as_("manage", "post", "", body)
        self.assertEqual(r.status_code, 201, r.content)
        out = r.json()
        self.assertEqual((out["start_date"], out["end_date"]), ("2026-10-03", "2026-10-10"))
        self.assertEqual(out["dates"], ["2026-10-03", "2026-10-10"])  # only the days played, not everything in between
        self.assertEqual([(d["date"], d["starts_at"], d["ends_at"]) for d in out["days"]], [("2026-10-03", "09:00", "17:00"), ("2026-10-10", "10:30", "18:00")])
        event = Event.objects.get(pk=out["event_id"])
        local = timezone.localtime
        self.assertEqual((local(event.start).strftime("%Y-%m-%d %H:%M"), local(event.end).strftime("%Y-%m-%d %H:%M")), ("2026-10-03 09:00", "2026-10-10 18:00"))
        # changing the days moves the range and the calendar event with them
        r = self.as_("manage", "patch", f"/{out['id']}", {"days": [{"date": "2026-10-04", "starts_at": "08:00", "ends_at": "12:00"}]})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual((r.json()["start_date"], r.json()["end_date"], r.json()["dates"]), ("2026-10-04", "2026-10-04", ["2026-10-04"]))
        event.refresh_from_db()
        self.assertEqual((local(event.start).strftime("%Y-%m-%d %H:%M"), local(event.end).strftime("%H:%M")), ("2026-10-04 08:00", "12:00"))
        # a patch without days leaves them alone
        self.as_("manage", "patch", f"/{out['id']}", {"max_teams": 4})
        self.assertEqual(len(self.as_("manage", "get", f"/{out['id']}").json()["days"]), 1)

    def test_day_validation(self):
        base = {"name": "Bad Days", "days": []}
        self.assertEqual(self.as_("manage", "post", "", base).status_code, 422)  # at least one day
        twice = {**base, "days": [{"date": "2026-10-03", "starts_at": "09:00", "ends_at": "17:00"}] * 2}
        self.assertEqual(self.as_("manage", "post", "", twice).status_code, 422)  # a date only once
        backwards = {**base, "days": [{"date": "2026-10-03", "starts_at": "17:00", "ends_at": "09:00"}]}
        self.assertEqual(self.as_("manage", "post", "", backwards).status_code, 422)  # ends after it starts

    def test_the_home_roster_follows_the_events_lineup_and_staff_is_copied_once(self):
        player = self.users["plain_member"]
        coach = self.users["operate_member"]
        with schema_context(self.club.schema_name):
            TeamMembership.objects.create(team=self.club_team, member=player, season=self.season)
            position = Position.objects.create(name="Head coach", short_name="HC", staff_position=True)
            StaffAssignment.objects.create(team=self.club_team, member=coach, season=self.season, position=position)
        body = {"name": "Lineup Cup", "status": "draft", "home_team_id": str(self.club_team.pk), "days": [{"date": "2026-12-12", "starts_at": "09:00", "ends_at": "17:00"}]}
        tid = self.as_("manage", "post", "", body).json()["id"]
        team = TournamentTeam.objects.get(tournament_id=tid, team=self.club_team)
        self.assertEqual(list(team.roster_entries.values_list("member_id", "role", "position_label")), [(coach.pk, "staff", "Head coach")])  # staff copied on creation
        event = Tournament.objects.get(pk=tid).event
        # the coach picks the player on the event's line-up screen
        Attendance.objects.filter(event=event, member=player).update(status=Attendance.AttendanceStatus.PRESENT)  # only players who said they can come are pickable
        r = self.call("put", f"/{event.pk}/lineup", self.owner, {"member_ids": [str(player.pk)]}, prefix="/events")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(set(team.roster_entries.values_list("member_id", "role")), {(coach.pk, "staff"), (player.pk, "player")})
        # un-picking removes the player, never the staff
        self.call("put", f"/{event.pk}/lineup", self.owner, {"member_ids": []}, prefix="/events")
        self.assertEqual(list(team.roster_entries.values_list("member_id", flat=True)), [coach.pk])
        # staff kept by hand sticks across saves of the tournament
        team.roster_entries.all().delete()
        self.as_("manage", "patch", f"/{tid}", {"max_teams": 6})
        self.assertEqual(team.roster_entries.count(), 0)

    def test_roster_entries_of_members_carry_their_name_birth_date_and_licence(self):
        t = self.make(earliest_birth_year=2012, latest_birth_year=2016)
        team = TournamentTeam.objects.create(tournament=t, team=self.club_team)
        with schema_context(self.club.schema_name):
            kid = Member.objects.create(first_name="Kim", last_name="Kid", date_of_birth=datetime.date(2010, 5, 1), license_number="L-42")
            coach = Member.objects.create(first_name="Cora", last_name="Coach", date_of_birth=datetime.date(1980, 1, 1), license_number="L-7")
        TournamentRosterEntry.objects.create(tournament_team=team, member=kid, jersey_number=9, position_label="Forward")
        TournamentRosterEntry.objects.create(tournament_team=team, member=coach, role="staff", position_label="Head coach")
        r = self.as_("operate", "get", f"/{t.pk}/teams/{team.pk}/roster")
        self.assertEqual(r.status_code, 200, r.content)
        entries = {e["last_name"]: e for e in r.json()["entries"]}
        self.assertEqual((entries["Kid"]["first_name"], entries["Kid"]["date_of_birth"], entries["Kid"]["license_number"]), ("Kim", "2010-05-01", "L-42"))
        self.assertTrue(entries["Kid"]["age_flagged"])  # born 2010, the tournament takes 2012-2016
        self.assertEqual((entries["Coach"]["first_name"], entries["Coach"]["position_label"]), ("Cora", "Head coach"))
        self.assertIsNone(entries["Coach"]["date_of_birth"])  # staff has none
        self.assertFalse(entries["Coach"]["age_flagged"])
        self.assertTrue(self.as_("operate", "get", f"/{t.pk}/teams").json()["results"][0]["has_age_flag"])

    def test_break_rules_are_listed_in_priority_order_and_can_be_reordered(self):
        t = self.make()
        body = {"trigger_type": "game_count", "trigger_every_n_games": 4, "gap_minutes": 10}
        ids = [self.as_("operate", "post", f"/{t.pk}/break-rules", {**body, "priority": n}).json()["id"] for n in (0, 1, 2)]
        order = lambda: [r["id"] for r in self.as_("operate", "get", f"/{t.pk}/setup").json()["break_rules"]]  # noqa: E731
        self.assertEqual(order(), ids)
        # moving the first rule down is two partial updates that renumber the priorities
        self.assertEqual(self.as_("operate", "patch", f"/{t.pk}/break-rules/{ids[0]}", {"priority": 1}).status_code, 200)
        self.assertEqual(self.as_("operate", "patch", f"/{t.pk}/break-rules/{ids[1]}", {"priority": 0}).status_code, 200)
        self.assertEqual(order(), [ids[1], ids[0], ids[2]])
        # the rest of the rule is left alone by a priority-only update
        self.assertEqual(self.as_("operate", "get", f"/{t.pk}/setup").json()["break_rules"][0]["gap_minutes"], 10)

    def test_a_full_tournament_refuses_another_team_until_one_leaves(self):
        t = self.make(max_teams=1)
        first = TournamentTeam.objects.create(tournament=t, team=self.club_team)
        self.assertTrue(self.as_("operate", "get", f"/{t.pk}").json()["is_full"])
        body = {"kind": "foreign", "foreign_name": "Walk-up FC", "foreign_contact_name": "Wes", "foreign_contact_email": "w@x.test"}
        r = self.as_("operate", "post", f"/{t.pk}/teams", body)
        self.assertEqual((r.status_code, r.json()["code"]), (409, "full"))
        # a withdrawal frees a place
        self.assertEqual(self.as_("manage", "post", f"/teams/{first.pk}/withdraw", {"reason": "Injuries"}).status_code, 200)
        self.assertFalse(self.as_("operate", "get", f"/{t.pk}").json()["is_full"])
        self.assertEqual(self.as_("operate", "post", f"/{t.pk}/teams", body).status_code, 201)

    def test_the_gap_between_matches_is_a_tournament_setting(self):
        body = {"name": "Gap Cup", "status": "draft", "days": [{"date": "2026-10-03", "starts_at": "09:00", "ends_at": "17:00"}]}
        out = self.as_("manage", "post", "", body).json()
        self.assertEqual(out["default_gap_minutes"], 5)
        r = self.as_("manage", "patch", f"/{out['id']}", {"default_gap_minutes": 10})
        self.assertEqual((r.status_code, r.json()["default_gap_minutes"]), (200, 10))
        self.assertEqual(self.as_("manage", "patch", f"/{out['id']}", {"default_gap_minutes": -1}).status_code, 422)

    def test_stages_are_added_last_and_reordered_with_the_sequence(self):
        t = self.make()
        ids = [self.as_("operate", "post", f"/{t.pk}/stages", {"name": name, "format": "round_robin"}).json()["id"] for name in ("Groups", "Final", "Placement")]
        order = lambda: [s["id"] for s in self.as_("operate", "get", f"/{t.pk}/setup").json()["stages"]]  # noqa: E731
        self.assertEqual(order(), ids)  # each new stage goes after the others
        # moving the first stage down renumbers the sequence of the two that change
        self.as_("operate", "patch", f"/{t.pk}/stages/{ids[0]}", {"sequence": 1})
        self.as_("operate", "patch", f"/{t.pk}/stages/{ids[1]}", {"sequence": 0})
        self.assertEqual(order(), [ids[1], ids[0], ids[2]])

    def test_regenerating_a_stage_replaces_its_matches_and_groups(self):
        t = self.make()
        for i in range(1, 5):
            TournamentTeam.objects.create(tournament=t, foreign_name=f"T{i}", foreign_contact_name="C", foreign_contact_email="c@x.test", seed=i)
        stage = self.as_("operate", "post", f"/{t.pk}/stages", {"name": "Groups", "format": "groups", "num_groups": 2}).json()["id"]
        gen = lambda body: self.as_("operate", "post", f"/{t.pk}/stages/{stage}/generate-matches", body)  # noqa: E731
        self.assertEqual(gen({}).status_code, 200)
        self.assertEqual(gen({}).status_code, 422)  # a plain generate still refuses when matches exist
        self.assertEqual(gen({"regenerate": True}).status_code, 200)
        self.assertEqual(Match.objects.filter(stage_id=stage).count(), 2)  # two groups of two teams: one match each
        self.assertEqual(TournamentGroup.objects.filter(stage_id=stage).count(), 2)  # the old groups are gone, not doubled

    def test_a_stage_with_played_matches_or_dependents_cannot_be_regenerated(self):
        t = self.make()
        for i in range(1, 5):
            TournamentTeam.objects.create(tournament=t, foreign_name=f"T{i}", foreign_contact_name="C", foreign_contact_email="c@x.test", seed=i)
        stage = self.as_("operate", "post", f"/{t.pk}/stages", {"name": "RR", "format": "round_robin"}).json()["id"]
        gen = lambda: self.as_("operate", "post", f"/{t.pk}/stages/{stage}/generate-matches", {"regenerate": True})  # noqa: E731
        self.assertEqual(gen().status_code, 200)
        match = Match.objects.filter(stage_id=stage).first()
        later = TournamentStage.objects.create(tournament=t, name="KO", format="single_elim", sequence=5)
        dependent = Match.objects.create(stage=later, round_number=1, sequence=0, home_source_match=match)
        r = gen()
        self.assertEqual((r.status_code, r.json()["code"]), (409, "has_dependents"))
        dependent.delete()
        Match.objects.filter(pk=match.pk).update(status=Match.Status.COMPLETE)
        r = gen()
        self.assertEqual((r.status_code, r.json()["code"]), (409, "has_results"))

    def test_standings_list_every_team_even_before_a_match_is_played_and_skip_empty_groups(self):
        t = self.make()
        for i in range(1, 5):
            TournamentTeam.objects.create(tournament=t, foreign_name=f"T{i}", foreign_contact_name="C", foreign_contact_email="c@x.test", seed=i)
        stage = self.as_("operate", "post", f"/{t.pk}/stages", {"name": "Groups", "format": "groups", "num_groups": 2}).json()["id"]
        self.as_("operate", "post", f"/{t.pk}/stages/{stage}/generate-matches", {})
        TournamentGroup.objects.create(stage_id=stage, name="Group A", sequence=9)  # an empty leftover must not become a table
        doc = self.as_("operate", "get", f"/{t.pk}/standings").json()["standings"][0]
        self.assertEqual([tbl["group"]["name"] for tbl in doc["tables"]], ["Group A", "Group B"])
        for table in doc["tables"]:
            self.assertEqual(len(table["rows"]), 2)
            self.assertEqual({r["played"] for r in table["rows"]}, {0})
            self.assertEqual([r["rank"] for r in table["rows"]], [1, 2])

    def test_a_knockout_stage_can_play_for_every_place(self):
        t = self.make()
        for i in range(1, 5):
            TournamentTeam.objects.create(tournament=t, foreign_name=f"T{i}", foreign_contact_name="C", foreign_contact_email="c@x.test", seed=i)
        plain = self.as_("operate", "post", f"/{t.pk}/stages", {"name": "KO", "format": "single_elim"}).json()
        full = self.as_("operate", "post", f"/{t.pk}/stages", {"name": "KO ranked", "format": "single_elim", "placement": True}).json()
        self.assertEqual((plain["placement"], full["placement"]), (False, True))
        gen = lambda sid: self.as_("operate", "post", f"/{t.pk}/stages/{sid}/generate-matches", {})  # noqa: E731
        self.assertEqual(gen(plain["id"]).json()["generated_count"], 3)  # two semi-finals and a final: the losers are simply out
        self.assertEqual(gen(full["id"]).json()["generated_count"], 4)  # plus the match for third place: every team gets a rank
        # the setting survives an edit of another field, and only exists for the knockout formats
        self.assertTrue(self.as_("operate", "patch", f"/{t.pk}/stages/{full['id']}", {"name": "Ranked"}).json()["placement"])
        other = self.as_("operate", "post", f"/{t.pk}/stages", {"name": "RR", "format": "round_robin", "placement": True}).json()
        self.assertFalse(other["placement"])

    def test_a_double_elimination_stage_can_play_for_every_place(self):
        t = self.make()
        for i in range(1, 9):
            TournamentTeam.objects.create(tournament=t, foreign_name=f"T{i}", foreign_contact_name="C", foreign_contact_email="c@x.test", seed=i)
        plain = self.as_("operate", "post", f"/{t.pk}/stages", {"name": "DE", "format": "double_elim"}).json()
        full = self.as_("operate", "post", f"/{t.pk}/stages", {"name": "DE ranked", "format": "double_elim", "placement": True}).json()
        self.assertEqual((plain["placement"], full["placement"]), (False, True))
        gen = lambda sid: self.as_("operate", "post", f"/{t.pk}/stages/{sid}/generate-matches", {})  # noqa: E731
        self.assertEqual(gen(plain["id"]).json()["generated_count"], 13)
        self.assertEqual(gen(full["id"]).json()["generated_count"], 16)  # plus 3rd, 5th and 7th place
        self.assertTrue(self.as_("operate", "patch", f"/{t.pk}/stages/{full['id']}", {"name": "Ranked"}).json()["placement"])

    def test_a_double_elimination_placement_bracket_needs_a_power_of_two_number_of_teams(self):
        t = self.make()
        for i in range(1, 7):
            TournamentTeam.objects.create(tournament=t, foreign_name=f"T{i}", foreign_contact_name="C", foreign_contact_email="c@x.test", seed=i)
        sid = self.as_("operate", "post", f"/{t.pk}/stages", {"name": "DE ranked", "format": "double_elim", "placement": True}).json()["id"]
        self.assertEqual(self.as_("operate", "post", f"/{t.pk}/stages/{sid}/generate-matches", {}).status_code, 422)

    def test_a_placement_bracket_needs_a_power_of_two_number_of_teams(self):
        t = self.make()
        for i in range(1, 6):
            TournamentTeam.objects.create(tournament=t, foreign_name=f"T{i}", foreign_contact_name="C", foreign_contact_email="c@x.test", seed=i)
        sid = self.as_("operate", "post", f"/{t.pk}/stages", {"name": "KO ranked", "format": "single_elim", "placement": True}).json()["id"]
        self.assertEqual(self.as_("operate", "post", f"/{t.pk}/stages/{sid}/generate-matches", {}).status_code, 422)

    def test_stage_draw_rules_and_penalty_shots(self):
        t = self.make()
        for i in range(1, 5):
            TournamentTeam.objects.create(tournament=t, foreign_name=f"T{i}", foreign_contact_name="C", foreign_contact_email="c@x.test", seed=i)
        mk = lambda body: self.as_("operate", "post", f"/{t.pk}/stages", body).json()  # noqa: E731
        table = mk({"name": "RR", "format": "round_robin"})
        strict = mk({"name": "RR no draws", "format": "round_robin", "allow_draws": False})
        shots = mk({"name": "RR shots", "format": "round_robin", "allow_draws": False, "penalty_shots": True})
        ko = mk({"name": "KO", "format": "single_elim"})
        self.assertEqual([(s["allow_draws"], s["penalty_shots"]) for s in (table, strict, shots, ko)], [(True, False), (False, False), (False, True), (False, True)])
        # the setting survives an edit of another field, and a knockout cannot allow draws
        self.assertFalse(self.as_("operate", "patch", f"/{t.pk}/stages/{strict['id']}", {"name": "Strict"}).json()["allow_draws"])
        self.assertFalse(mk({"name": "KO2", "format": "double_elim", "allow_draws": True})["allow_draws"])

        def play(stage, **result):
            self.as_("operate", "post", f"/{t.pk}/stages/{stage['id']}/generate-matches", {})
            match = Match.objects.filter(stage_id=stage["id"]).order_by("round_number", "sequence").first()
            return self.as_("operate", "post", f"/matches/{match.pk}/result", {"score_home": 1, "score_away": 1, **result}), match

        r, _m = play(table)
        self.assertEqual(r.status_code, 200)  # a draw is fine here
        r, _m = play(strict)
        self.assertEqual((r.status_code, r.json()["code"]), (422, "draw_not_allowed"))
        r, m = play(shots)
        self.assertEqual((r.status_code, r.json()["code"]), (422, "penalty_shots_required"))
        self.assertEqual(self.as_("operate", "post", f"/matches/{m.pk}/result", {"score_home": 1, "score_away": 1, "penalty_home": 3, "penalty_away": 3}).status_code, 422)
        self.assertEqual(self.as_("operate", "post", f"/matches/{m.pk}/result", {"score_home": 1, "score_away": 1, "penalty_home": 4, "penalty_away": 3}).status_code, 200)
        m.refresh_from_db()
        self.assertEqual(m.winner, m.home_team)  # the shootout decided it
        # a result that is not level drops stale shots
        r = self.as_("operate", "post", f"/matches/{m.pk}/result", {"score_home": 2, "score_away": 1, "penalty_home": 4, "penalty_away": 3})
        self.assertEqual(r.status_code, 200)
        m.refresh_from_db()
        self.assertEqual((m.penalty_home, m.penalty_away), (None, None))

    def test_a_shootout_win_counts_as_a_win_in_the_standings(self):
        t = self.make()
        a = TournamentTeam.objects.create(tournament=t, foreign_name="A", foreign_contact_name="C", foreign_contact_email="c@x.test", seed=1)
        b = TournamentTeam.objects.create(tournament=t, foreign_name="B", foreign_contact_name="C", foreign_contact_email="c@x.test", seed=2)
        stage = TournamentStage.objects.create(tournament=t, name="RR", format="round_robin", config={"allow_draws": False, "penalty_shots": True})
        Match.objects.create(stage=stage, round_number=1, sequence=0, home_team=a, away_team=b, status=Match.Status.COMPLETE, score_home=2, score_away=2, penalty_home=1, penalty_away=3)
        rows = {r["team"]["name"]: r for r in self.as_("operate", "get", f"/{t.pk}/standings").json()["standings"][0]["tables"][0]["rows"]}
        self.assertEqual((rows["B"]["won"], rows["B"]["points"], rows["A"]["lost"], rows["A"]["drawn"]), (1, 3, 1, 0))

    def test_reorder_hands_the_times_to_the_new_order(self):
        t = self.make(status="in_progress")
        [self.foreign(t, n) for n in ("A", "B", "C", "D")]
        self.as_("operate", "post", f"/{t.pk}/fields", {"name": "Field 1", "availability": [{"date": "2026-09-19", "start": "09:00", "end": "18:00"}]})
        stage = self.as_("operate", "post", f"/{t.pk}/stages", {"name": "RR", "format": "round_robin"}).json()
        self.as_("operate", "post", f"/{t.pk}/stages/{stage['id']}/generate-matches", {})
        self.as_("operate", "post", f"/{t.pk}/schedule/generate", {})
        matches = list(Match.objects.filter(stage_id=stage["id"], scheduled_start__isnull=False).order_by("scheduled_start"))
        first = matches[0]
        other = next(m for m in matches[1:] if not {m.home_team_id, m.away_team_id} & {first.home_team_id, first.away_team_id})  # two games between four different teams swap places safely
        before = [(first.scheduled_start, first.field_id), (other.scheduled_start, other.field_id)]
        new_order = [other.pk, first.pk]
        r = self.as_("operate", "post", f"/{t.pk}/schedule/reorder", {"match_ids": [str(i) for i in new_order]})
        self.assertEqual(r.status_code, 200, r.content)
        for position, pk in enumerate(new_order):
            m = Match.objects.get(pk=pk)
            self.assertEqual((m.scheduled_start, m.field_id), before[position])
        self.assertEqual(self.as_("operate", "post", f"/{t.pk}/schedule/reorder", {"match_ids": [str(new_order[0])]}).status_code, 422)  # needs two or more

    def test_clear_a_stages_matches(self):
        t = self.make(status="in_progress")
        [self.foreign(t, n) for n in ("A", "B", "C", "D")]
        stage = self.as_("operate", "post", f"/{t.pk}/stages", {"name": "RR", "format": "round_robin"}).json()
        self.as_("operate", "post", f"/{t.pk}/stages/{stage['id']}/generate-matches", {})
        self.assertEqual(Match.objects.filter(stage_id=stage["id"]).count(), 6)
        r = self.as_("operate", "post", f"/{t.pk}/stages/{stage['id']}/clear-matches", {})
        self.assertEqual((r.status_code, r.json()["cleared_count"]), (200, 6), r.content)
        self.assertEqual(Match.objects.filter(stage_id=stage["id"]).count(), 0)
        self.assertEqual(TournamentStage.objects.get(pk=stage["id"]).status, "pending")
        self.as_("operate", "post", f"/{t.pk}/stages/{stage['id']}/generate-matches", {})
        Match.objects.filter(stage_id=stage["id"]).update(status=Match.Status.COMPLETE)
        self.assertEqual(self.as_("operate", "post", f"/{t.pk}/stages/{stage['id']}/clear-matches", {}).status_code, 409)  # played matches stay

    def test_two_legged_knockout_and_stage_game_time(self):
        t = self.make(status="in_progress")
        [self.foreign(t, n) for n in ("A", "B", "C", "D")]
        self.as_("operate", "post", f"/{t.pk}/fields", {"name": "Field 1", "availability": [{"date": "2026-09-19", "start": "09:00", "end": "20:00"}]})
        stage = self.as_("operate", "post", f"/{t.pk}/stages", {"name": "KO", "format": "single_elim", "two_legged": True, "match_duration_minutes": 20}).json()
        self.assertEqual((stage["two_legged"], stage["match_duration_minutes"]), (True, 20))
        self.as_("operate", "post", f"/{t.pk}/stages/{stage['id']}/generate-matches", {})
        ties = list(Match.objects.filter(stage_id=stage["id"], leg_of__isnull=True).order_by("round_number", "sequence"))
        self.assertEqual((len(ties), Match.objects.filter(stage_id=stage["id"], leg_of__isnull=False).count()), (3, 3))  # semis, final: each has a first leg
        semi = ties[0]
        first = Match.objects.get(leg_of=semi)
        # the second leg cannot be entered before the first
        r = self.as_("operate", "post", f"/matches/{semi.pk}/result", {"score_home": 1, "score_away": 0})
        self.assertEqual((r.status_code, r.json()["code"]), (422, "first_leg_missing"))
        # a first leg may end level and never needs shots
        self.assertEqual(self.as_("operate", "post", f"/matches/{first.pk}/result", {"score_home": 1, "score_away": 1}).status_code, 200)
        # aggregate level (1-1 then 2-2) needs the shootout
        r = self.as_("operate", "post", f"/matches/{semi.pk}/result", {"score_home": 2, "score_away": 2})
        self.assertEqual((r.status_code, r.json()["code"]), (422, "penalty_shots_required"))
        r = self.as_("operate", "post", f"/matches/{semi.pk}/result", {"score_home": 2, "score_away": 1})  # aggregate 3-2 to the home side
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()["match"]
        self.assertEqual((body["leg"], body["aggregate"]), (2, [3, 2]))
        semi.refresh_from_db()
        self.assertEqual(semi.winner, semi.home_team)
        final = ties[2]
        self.assertEqual(Match.objects.get(pk=final.pk).home_team_id, semi.home_team_id)  # the winner advanced
        self.assertEqual(Match.objects.get(leg_of=final).home_team_id, semi.home_team_id)  # and into the final's first leg too
        # the game time of the stage drives scheduling
        self.as_("operate", "post", f"/{t.pk}/schedule/generate", {"match_duration_minutes": 45})
        starts = sorted(Match.objects.filter(stage_id=stage["id"], scheduled_start__isnull=False).values_list("scheduled_start", flat=True))
        self.assertEqual((starts[1] - starts[0]).total_seconds() / 60, 25)  # 20 minutes of play + the default 5 minute gap
        self.assertTrue(Match.objects.get(leg_of=semi).scheduled_start < Match.objects.get(pk=semi.pk).scheduled_start)  # first leg before second
        self.assertEqual(self.as_("operate", "delete", f"/matches/{ties[1].pk}").status_code, 409)  # feeds the final
        self.assertEqual(self.as_("operate", "delete", f"/matches/{Match.objects.get(leg_of=final).pk}").status_code, 204)  # the final goes with both legs
        self.assertFalse(Match.objects.filter(pk=final.pk).exists() or Match.objects.filter(leg_of_id=final.pk).exists())

    def test_block_and_lunch_locations(self):
        t = self.make()
        r = self.as_("operate", "post", f"/{t.pk}/schedule-blocks", {"title": "Ceremony", "starts_at": "2026-09-19T09:00:00Z", "ends_at": "2026-09-19T09:30:00Z", "location": "  Main hall "})
        self.assertEqual((r.status_code, r.json()["location"]), (201, "Main hall"), r.content)
        bid = r.json()["id"]
        self.assertEqual(self.as_("operate", "patch", f"/{t.pk}/schedule-blocks/{bid}", {"location": "Cafeteria"}).json()["location"], "Cafeteria")
        body = self.as_("operate", "get", f"/{t.pk}/lunch").json()
        r = self.as_("operate", "put", f"/{t.pk}/lunch", {"settings": {**body["settings"], "location": " Club bar "}, "days": []})
        self.assertEqual(r.json()["settings"]["location"], "Club bar", r.content)

    def test_delete_only_a_draft_tournament(self):
        body = {"name": "Spring Cup", "days": [{"date": "2026-10-03", "starts_at": "09:00", "ends_at": "17:00"}, {"date": "2026-10-04", "starts_at": "09:00", "ends_at": "17:00"}], "status": "draft", "home_team_id": str(self.club_team.pk)}
        tid = self.as_("manage", "post", "", body).json()["id"]
        event_id = Tournament.objects.get(pk=tid).event_id
        self.assertEqual(self.as_("operate", "delete", f"/{tid}").status_code, 403)
        self.assertEqual(self.call("delete", f"/{tid}").status_code, 401)
        self.as_("manage", "patch", f"/{tid}", {"status": "published"})
        r = self.as_("manage", "delete", f"/{tid}")
        self.assertEqual(r.status_code, 409, r.content)
        self.assertTrue(Tournament.objects.filter(pk=tid).exists())
        self.as_("manage", "patch", f"/{tid}", {"status": "draft"})
        r = self.as_("manage", "delete", f"/{tid}")
        self.assertEqual(r.status_code, 204, r.content)
        self.assertFalse(Tournament.objects.filter(pk=tid).exists())
        self.assertFalse(Event.objects.filter(pk=event_id).exists())  # the calendar event it created goes too
        self.assertEqual(self.as_("manage", "delete", f"/{tid}").status_code, 404)

    def test_public_registration_approve_and_status(self):
        t = self.make(status="published", lunch_mode="optional", entry_fee_amount="50")
        slug = t.slug
        players = [
            {"first_name": "Ann", "last_name": "A", "jersey_number": 7, "date_of_birth": "2010-05-01", "lunch_days": ["2026-09-19", "2030-01-01"]},
            {"first_name": "Coach", "last_name": "C", "role": "staff", "date_of_birth": "1980-01-01"},
            {"first_name": "", "last_name": ""},
        ]
        payload = {"foreign_name": "Visitors", "foreign_contact_name": "Vic", "foreign_contact_email": "vic@x.test", "players": players}
        r = self.call("post", f"/{slug}/registrations", None, payload, prefix="/public/tournaments")
        self.assertEqual(r.status_code, 201, r.content)
        token = r.json()["status_token"]
        status = self.call("get", f"/{slug}/registrations/{token}", prefix="/public/tournaments").json()
        self.assertEqual(status["registration"]["status"], "pending")
        self.assertEqual([p["name"] for p in status["players"]], ["Ann A", "Coach C"])
        self.assertEqual(status["players"][0]["lunch_days"], ["2026-09-19"])
        self.assertIsNone(status["players"][1]["date_of_birth"])  # staff has none
        # validation: duplicate jersey numbers, bad email, future date of birth
        bad = {**payload, "players": [{"first_name": "A", "last_name": "B", "jersey_number": 1}, {"first_name": "C", "last_name": "D", "jersey_number": 1}]}
        self.assertEqual(self.call("post", f"/{slug}/registrations", None, bad, prefix="/public/tournaments").status_code, 422)
        self.assertEqual(self.call("post", f"/{slug}/registrations", None, {**payload, "foreign_contact_email": "nope"}, prefix="/public/tournaments").status_code, 422)
        future = {**payload, "players": [{"first_name": "A", "last_name": "B", "date_of_birth": "2999-01-01"}]}
        self.assertEqual(self.call("post", f"/{slug}/registrations", None, future, prefix="/public/tournaments").status_code, 422)
        # staff: queue, approve, then approving again conflicts
        queue = self.as_("operate", "get", f"/{t.pk}/registrations").json()["results"]
        self.assertEqual(len(queue), 1)
        rid = queue[0]["id"]
        self.assertEqual(self.as_("operate", "post", f"/registrations/{rid}/approve").status_code, 403)
        r = self.as_("manage", "post", f"/registrations/{rid}/approve")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(self.as_("manage", "post", f"/registrations/{rid}/approve").status_code, 409)
        team = TournamentTeam.objects.get(tournament=t)
        self.assertEqual(team.roster_entries.count(), 2)
        status = self.call("get", f"/{slug}/registrations/{token}", prefix="/public/tournaments").json()
        self.assertEqual(status["registration"]["status"], "approved")
        self.assertEqual(status["amount_owed"], "50.00")
        # roster stays editable by the team until the tournament starts
        with mock.patch("api.routers.public_tournaments.timezone.localdate", return_value=datetime.date(2026, 9, 1)):
            r = self.call("put", f"/{slug}/registrations/{token}/players", None, {"players": [{"first_name": "New", "last_name": "P"}]}, prefix="/public/tournaments")
        self.assertEqual((r.status_code, team.roster_entries.count()), (200, 1), r.content)
        r = self.call("put", f"/{slug}/registrations/{token}/players", None, {"players": []}, prefix="/public/tournaments")  # tournament has started
        self.assertEqual((r.status_code, r.json()["code"]), (422, "locked"))
        # payment and withdrawal
        self.assertEqual(self.as_("manage", "patch", f"/teams/{team.pk}/payment", {"payment_status": "fully_paid"}).json()["payment_status"], "fully_paid")
        self.assertEqual(self.as_("manage", "post", f"/teams/{team.pk}/withdraw", {"reason": "ill"}).status_code, 200)

    def test_reject_registration(self):
        t = self.make(status="published")
        self.call("post", f"/{t.slug}/registrations", None, {"foreign_name": "V", "foreign_contact_name": "Vic", "foreign_contact_email": "v@x.test"}, prefix="/public/tournaments")
        rid = self.as_("manage", "get", f"/{t.pk}/registrations").json()["results"][0]["id"]
        self.assertEqual(self.as_("manage", "post", f"/registrations/{rid}/reject", {"reason": "full"}).json()["status"], "rejected")

    def test_registration_closed_and_hidden_states(self):
        draft = self.make(name="D", status="draft")
        self.assertEqual(self.call("get", f"/{draft.slug}", prefix="/public/tournaments").status_code, 200)
        self.assertEqual(self.call("get", "", prefix="/public/tournaments").json()["upcoming"], [])
        closed = self.make(name="C", status="published", registration_open=False)
        body = {"foreign_name": "V", "foreign_contact_name": "Vic", "foreign_contact_email": "v@x.test"}
        r = self.call("post", f"/{closed.slug}/registrations", None, body, prefix="/public/tournaments")
        self.assertEqual((r.status_code, r.json()["code"]), (422, "closed"))
        started = self.make(name="S", status="in_progress")
        self.assertEqual(self.call("post", f"/{started.slug}/registrations", None, body, prefix="/public/tournaments").status_code, 404)

    def test_public_endpoints_are_404_without_feature(self):
        t = self.make(status="published")
        self.features = Entitlements({})
        for path in ("", f"/{t.slug}", f"/{t.slug}/schedule", f"/{t.slug}/standings", f"/{t.slug}/teams"):
            self.assertEqual(self.call("get", path, prefix="/public/tournaments").status_code, 404, path)
        body = {"foreign_name": "V", "foreign_contact_name": "Vic", "foreign_contact_email": "v@x.test"}
        self.assertEqual(self.call("post", f"/{t.slug}/registrations", None, body, prefix="/public/tournaments").status_code, 404)

    # -- competition --

    def test_schedule_results_and_standings(self):
        t = self.make(status="in_progress")
        teams = [self.foreign(t, n) for n in ("Alpha", "Bravo", "Charlie")]
        r = self.as_("operate", "post", f"/{t.pk}/fields", {"name": "Field 1", "availability": [{"date": "2026-09-19", "start": "09:00", "end": "18:00"}]})
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(self.as_("operate", "post", f"/{t.pk}/fields", {"name": "F", "availability": [{"date": "2026-09-19", "start": "10:00", "end": "09:00"}]}).status_code, 422)
        r = self.as_("operate", "post", f"/{t.pk}/stages", {"name": "League", "format": "round_robin"})
        self.assertEqual(r.status_code, 201, r.content)
        sid = r.json()["id"]
        self.assertEqual(self.as_("operate", "post", f"/{t.pk}/stages", {"name": "Swiss", "format": "swiss"}).status_code, 422)  # rounds required
        r = self.as_("operate", "post", f"/{t.pk}/stages/{sid}/generate-matches", {})
        self.assertEqual((r.status_code, r.json().get("generated_count")), (200, 3), r.content)
        r = self.as_("operate", "post", f"/{t.pk}/schedule/generate", {"match_duration_minutes": 30})
        self.assertEqual((r.status_code, r.json()["scheduled_count"]), (200, 3), r.content)
        schedule = self.as_("manage", "get", f"/{t.pk}/schedule").json()
        matches = [m for rnd in schedule["stages"][0]["rounds"] for m in rnd["matches"]]
        self.assertTrue(all(m["field"] and m["scheduled_start"] for m in matches))
        self.assertEqual(self.as_("manage", "get", f"/{t.pk}/schedule/board").status_code, 200)
        for m, (h, a) in zip(matches, [(3, 0), (1, 1), (0, 2)], strict=True):
            r = self.as_("operate", "post", f"/matches/{m['id']}/result", {"score_home": h, "score_away": a})
            self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(Match.objects.filter(status="complete").count(), 3)
        rows = self.as_("manage", "get", f"/{t.pk}/standings").json()["standings"][0]["tables"][0]["rows"]
        self.assertEqual([r["rank"] for r in rows], [1, 2, 3])
        self.assertEqual(sorted(r["points"] for r in rows), sorted([r["points"] for r in rows]))
        self.assertEqual(self.as_("operate", "post", f"/{t.pk}/schedule/clear").json()["cleared_count"], 0)  # all played
        public = self.call("get", f"/{t.slug}/standings", prefix="/public/tournaments").json()
        self.assertEqual(len(public["standings"][0]["tables"][0]["rows"]), 3)
        self.assertEqual(len(teams), 3)

    def test_result_needs_both_teams_and_reschedule_unknown_field(self):
        t = self.make()
        stage = t.stages.create(name="Cup", format="single_elim")
        m = Match.objects.create(stage=stage)
        self.assertEqual(self.as_("operate", "post", f"/matches/{m.pk}/result", {"score_home": 1, "score_away": 0}).status_code, 422)
        r = self.as_("operate", "post", f"/matches/{m.pk}/reschedule", {"field_id": str(uuid.uuid4()), "scheduled_start": "2026-09-19T10:00:00Z"})
        self.assertEqual(r.status_code, 422)

    def _edit_setup(self):
        t = self.make(status="in_progress")
        stage = t.stages.create(name="Cup", format="single_elim")
        a, b, c = (self.foreign(t, n) for n in ("Alpha", "Bravo", "Charlie"))
        field = TournamentField.objects.create(tournament=t, name="F1")
        return t, stage, a, b, c, field

    def test_match_teams_set_swap_clear_and_validate(self):
        _t, stage, a, b, c, _field = self._edit_setup()
        m = Match.objects.create(stage=stage)
        url = f"/matches/{m.pk}/teams"
        r = self.as_("operate", "patch", url, {"home_team_id": str(a.pk), "away_team_id": str(b.pk)})
        self.assertEqual(r.status_code, 200, r.content)
        doc = r.json()
        self.assertEqual((doc["home_team_id"], doc["away_team_id"], doc["home_is_tbd"]), (str(a.pk), str(b.pk), False))
        r = self.as_("operate", "patch", url, {"home_team_id": str(b.pk), "away_team_id": str(a.pk)})  # swap
        self.assertEqual((r.json()["home_team"]["id"], r.json()["away_team"]["id"]), (str(b.pk), str(a.pk)))
        r = self.as_("operate", "patch", url, {"away_team_id": str(c.pk)})  # only the sent key changes
        self.assertEqual((r.json()["home_team_id"], r.json()["away_team_id"]), (str(b.pk), str(c.pk)))
        r = self.as_("operate", "patch", url, {"home_team_id": None})
        self.assertEqual((r.json()["home_team_id"], r.json()["home_is_tbd"], r.json()["away_team_id"]), (None, True, str(c.pk)))
        r = self.as_("operate", "patch", url, {"home_team_id": str(c.pk)})  # same on both sides
        self.assertEqual(r.status_code, 422, r.content)
        other = self.foreign(self.make(name="Other"), "Elsewhere")
        r = self.as_("operate", "patch", url, {"home_team_id": str(other.pk)})
        self.assertEqual((r.status_code, "home_team_id" in r.json()["errors"]), (422, True), r.content)
        self.assertEqual(self.as_("operate", "patch", url, {"home_team_id": str(uuid.uuid4())}).status_code, 422)
        a.withdrawn_at = timezone.now()
        a.save()
        self.assertEqual(self.as_("operate", "patch", url, {"home_team_id": str(a.pk)}).status_code, 422)
        self.assertEqual(self.as_("operate", "patch", f"/matches/{uuid.uuid4()}/teams", {}).status_code, 404)

    def test_match_teams_replace_the_source_that_fed_the_slot(self):
        _t, stage, a, b, c, _field = self._edit_setup()
        semi = Match.objects.create(stage=stage, home_team=a, away_team=b)
        group = stage.groups.create(name="A")
        final = Match.objects.create(stage=stage, round_number=2, home_source_match=semi, home_source_is_winner=False, away_source_group=group, away_source_rank=2)
        doc = self.as_("operate", "patch", f"/matches/{final.pk}/teams", {"home_team_id": str(c.pk)}).json()
        self.assertEqual((doc["home_source"], doc["away_source"]["type"]), (None, "group"))
        final.refresh_from_db()
        self.assertEqual((final.home_team_id, final.home_source_match_id, final.home_source_is_winner), (c.pk, None, True))
        self.assertEqual(final.away_source_group_id, group.pk)
        self.as_("operate", "post", f"/matches/{semi.pk}/result", {"score_home": 2, "score_away": 0})
        final.refresh_from_db()
        self.assertEqual(final.home_team_id, c.pk)  # resolve_match_slots no longer touches it
        doc = self.as_("operate", "patch", f"/matches/{final.pk}/teams", {"away_team_id": None}).json()
        self.assertEqual(doc["away_source"], None)

    def test_a_played_match_cannot_be_edited_or_deleted(self):
        _t, stage, a, b, c, _field = self._edit_setup()
        m = Match.objects.create(stage=stage, home_team=a, away_team=b)
        self.assertEqual(self.as_("operate", "post", f"/matches/{m.pk}/result", {"score_home": 1, "score_away": 0}).status_code, 200)
        r = self.as_("operate", "patch", f"/matches/{m.pk}/teams", {"home_team_id": str(c.pk)})
        self.assertEqual((r.status_code, r.json()["code"]), (409, "match_complete"), r.content)
        r = self.as_("operate", "delete", f"/matches/{m.pk}")
        self.assertEqual((r.status_code, r.json()["code"]), (409, "match_complete"), r.content)
        self.assertTrue(Match.objects.filter(pk=m.pk).exists())

    def test_delete_match_refuses_dependents_and_flags_regeneration(self):
        t, stage, a, b, c, field = self._edit_setup()
        semi = Match.objects.create(stage=stage, home_team=a, away_team=b)
        Match.objects.create(stage=stage, round_number=2, round_label="Final", home_source_match=semi)
        r = self.as_("operate", "delete", f"/matches/{semi.pk}")
        self.assertEqual((r.status_code, r.json()["code"]), (409, "has_dependents"), r.content)
        self.assertIn("Final", r.json()["detail"])
        # scheduled match deleted while another stays scheduled -> flag; an unscheduled one -> no flag
        start = timezone.make_aware(datetime.datetime(2026, 9, 19, 10, 0))
        scheduled = [Match.objects.create(stage=stage, home_team=a, away_team=c, field=field, scheduled_start=start + datetime.timedelta(hours=h)) for h in (0, 3)]
        loose = Match.objects.create(stage=stage)
        self.assertEqual(self.as_("operate", "delete", f"/matches/{loose.pk}").status_code, 204)
        t.refresh_from_db()
        self.assertFalse(t.schedule_needs_regeneration)
        self.assertEqual(self.as_("operate", "delete", f"/matches/{scheduled[0].pk}").status_code, 204)
        t.refresh_from_db()
        self.assertTrue(t.schedule_needs_regeneration)
        self.assertFalse(Match.objects.filter(pk=scheduled[0].pk).exists())

    def test_delete_last_scheduled_match_does_not_flag(self):
        t, stage, a, b, _c, field = self._edit_setup()
        only = Match.objects.create(stage=stage, home_team=a, away_team=b, field=field, scheduled_start=timezone.make_aware(datetime.datetime(2026, 9, 19, 10, 0)))
        self.assertEqual(self.as_("operate", "delete", f"/matches/{only.pk}").status_code, 204)
        t.refresh_from_db()
        self.assertFalse(t.schedule_needs_regeneration)

    def test_create_match_in_a_stage(self):
        t, stage, a, b, _c, _field = self._edit_setup()
        url = f"/{t.pk}/stages/{stage.pk}/matches"
        r = self.as_("operate", "post", url, {})
        self.assertEqual(r.status_code, 201, r.content)
        doc = r.json()
        self.assertEqual((doc["round_number"], doc["sequence"], doc["status"], doc["home_is_tbd"], doc["away_is_tbd"], doc["field"]), (1, 0, "scheduled", True, True, None))
        r = self.as_("operate", "post", url, {"home_team_id": str(a.pk), "away_team_id": str(b.pk), "round_label": "Friendly"})
        self.assertEqual((r.json()["round_number"], r.json()["sequence"], r.json()["home_team_id"], r.json()["round_label"]), (1, 1, str(a.pk), "Friendly"))
        r = self.as_("operate", "post", url, {"home_team_id": str(a.pk), "away_team_id": str(b.pk), "round_number": 3})  # teams may repeat
        self.assertEqual((r.status_code, r.json()["round_number"], r.json()["sequence"]), (201, 3, 0))
        r = self.as_("operate", "post", url, {})  # default: the stage's last round
        self.assertEqual((r.json()["round_number"], r.json()["sequence"]), (3, 1))
        self.assertFalse(Tournament.objects.get(pk=t.pk).schedule_needs_regeneration)
        group = stage.groups.create(name="A")
        r = self.as_("operate", "post", url, {"group_id": str(group.pk), "round_number": 1})
        self.assertEqual((r.status_code, r.json()["group"]["id"], r.json()["sequence"]), (201, str(group.pk), 0), r.content)

    def test_create_match_validation_and_placement(self):
        t, stage, a, b, c, field = self._edit_setup()
        url = f"/{t.pk}/stages/{stage.pk}/matches"
        for body in (
            {"home_team_id": str(a.pk), "away_team_id": str(a.pk)}, {"home_team_id": str(uuid.uuid4())}, {"round_number": 0}, {"group_id": str(uuid.uuid4())},
            {"field_id": str(field.pk)}, {"scheduled_start": "2026-09-19T10:00:00Z"}, {"field_id": str(uuid.uuid4()), "scheduled_start": "2026-09-19T10:00:00Z"},
        ):  # fmt: skip
            self.assertEqual(self.as_("operate", "post", url, body).status_code, 422, body)
        a.withdrawn_at = timezone.now()
        a.save()
        self.assertEqual(self.as_("operate", "post", url, {"away_team_id": str(a.pk)}).status_code, 422)
        self.assertEqual(self.as_("operate", "post", f"/{t.pk}/stages/{uuid.uuid4()}/matches", {}).status_code, 404)
        self.assertEqual(Match.objects.count(), 0)
        body = {"home_team_id": str(b.pk), "away_team_id": str(c.pk), "field_id": str(field.pk), "scheduled_start": "2026-09-19T10:00:00Z"}
        r = self.as_("operate", "post", url, body)
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual((r.json()["field"]["id"], r.json()["auto_scheduled"]), (str(field.pk), False))
        self.assertTrue(Tournament.objects.get(pk=t.pk).schedule_needs_regeneration)
        r = self.as_("operate", "post", url, body)  # same field, same time, same teams: placement refused and nothing is left behind
        self.assertEqual(r.status_code, 422, r.content)
        self.assertEqual(Match.objects.count(), 1)

    def test_schedule_doc_exposes_what_the_editor_needs(self):
        t, stage, a, _b, _c, _field = self._edit_setup()
        semi = Match.objects.create(stage=stage, home_team=a)
        group = stage.groups.create(name="A")
        Match.objects.create(stage=stage, round_number=2, group=group, home_source_match=semi, away_source_group=group, away_source_rank=1)
        matches = [m for rnd in self.as_("manage", "get", f"/{t.pk}/schedule").json()["stages"][0]["rounds"] for m in rnd["matches"]]
        first, second = matches
        self.assertEqual((first["home_team_id"], first["away_team_id"], first["home_is_tbd"], first["away_is_tbd"], first["home_source"]), (str(a.pk), None, False, True, None))
        self.assertEqual(second["home_source"], {"type": "match", "match_id": str(semi.pk), "is_winner": True})
        self.assertEqual(second["away_source"], {"type": "group", "group_id": str(group.pk), "rank": 1})
        self.assertEqual((second["group_id"], second["group"]["id"], second["round_number"], second["status"]), (str(group.pk), str(group.pk), 2, "scheduled"))

    def test_schedule_blocks_and_break_rules(self):
        t = self.make()
        field = TournamentField.objects.create(tournament=t, name="F1")
        r = self.as_("operate", "post", f"/{t.pk}/schedule-blocks", {"title": "Lunch", "starts_at": "2026-09-19T12:00:00Z", "ends_at": "2026-09-19T13:00:00Z", "field_id": str(field.pk)})
        self.assertEqual(r.status_code, 201, r.content)
        bad = {"title": "x", "starts_at": "2026-09-19T13:00:00Z", "ends_at": "2026-09-19T12:00:00Z"}
        self.assertEqual(self.as_("operate", "post", f"/{t.pk}/schedule-blocks", bad).status_code, 422)
        r = self.as_("operate", "post", f"/{t.pk}/break-rules", {"trigger_type": "game_count", "trigger_every_n_games": 3, "gap_minutes": 15})
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(self.as_("operate", "post", f"/{t.pk}/break-rules", {"trigger_type": "game_count"}).status_code, 422)
        setup = self.as_("operate", "get", f"/{t.pk}/setup").json()
        self.assertEqual(len(setup["break_rules"]), 1)

    # -- lunch scheduling --

    def _lunch_setup(self, teams=4):
        t = self.make(lunch_mode="optional")
        for date in ("2026-09-19", "2026-09-20"):
            t.days.create(date=date, starts_at="09:00", ends_at="18:00")
        eaters = []
        for i in range(teams):
            team = self.foreign(t, f"Team {i}")
            TournamentRosterEntry.objects.create(tournament_team=team, foreign_first_name="P", foreign_last_name=str(i), lunch_days=["2026-09-19"])
            eaters.append(team)
        return t, eaters

    def test_lunch_settings_round_trip_and_validation(self):
        t = self.make()
        t.days.create(date="2026-09-19", starts_at="09:00", ends_at="18:00")
        doc = self.as_("operate", "get", f"/{t.pk}/lunch").json()
        self.assertEqual(doc["enabled"], False)
        self.assertEqual(doc["settings"], LUNCH_SETTINGS | {"duration_minutes": 30, "location": ""})
        self.assertEqual(doc["days"], [{"date": "2026-09-19", "lunch_start": None, "lunch_end": None, "day_start": "09:00", "day_end": "18:00", "teams_eating": 0, "sittings": []}])
        self.assertFalse(doc["generated"])
        body = {"settings": {"duration_minutes": 40, "gap_before_minutes": 0, "gap_after_minutes": 5, "capacity_teams": 3, "interval_minutes": 0}, "days": [{"date": "2026-09-19", "lunch_start": "11:30", "lunch_end": "13:00"}]}
        r = self.as_("operate", "put", f"/{t.pk}/lunch", body)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["settings"], {**body["settings"], "location": ""})
        self.assertEqual((r.json()["days"][0]["lunch_start"], r.json()["days"][0]["lunch_end"]), ("11:30", "13:00"))
        self.assertEqual(self.as_("manage", "get", f"/{t.pk}/lunch").json(), r.json())
        r = self.as_("operate", "put", f"/{t.pk}/lunch", {**body, "days": [{"date": "2026-09-19", "lunch_start": None, "lunch_end": None}]})
        self.assertEqual(r.json()["days"][0]["lunch_start"], None)
        for bad in (
            {**body, "settings": {**body["settings"], "duration_minutes": 0}}, {**body, "settings": {**body["settings"], "capacity_teams": 0}},
            {**body, "settings": {**body["settings"], "gap_before_minutes": -1}}, {**body, "settings": {k: v for k, v in body["settings"].items() if k != "interval_minutes"}},
            {**body, "days": [{"date": "2026-09-25", "lunch_start": "11:00", "lunch_end": "12:00"}]},
            {**body, "days": [{"date": "2026-09-19", "lunch_start": "13:00", "lunch_end": "12:00"}]},
            {**body, "days": [{"date": "2026-09-19", "lunch_start": "12:00", "lunch_end": "12:00"}]},
            {**body, "days": [{"date": "2026-09-19", "lunch_start": "12:00", "lunch_end": None}]},
        ):  # fmt: skip
            self.assertEqual(self.as_("operate", "put", f"/{t.pk}/lunch", bad).status_code, 422, bad)
        self.assertEqual(Tournament.objects.get(pk=t.pk).lunch_duration_minutes, 40)  # a refused PUT changes nothing
        self.assertEqual(self.as_("operate", "get", f"/{uuid.uuid4()}/lunch").status_code, 404)

    def test_lunch_days_survive_editing_the_tournament_days(self):
        t, _teams = self._lunch_setup(0)
        self.as_("operate", "put", f"/{t.pk}/lunch", {"settings": LUNCH_SETTINGS, "days": [{"date": "2026-09-19", "lunch_start": "12:00", "lunch_end": "13:00"}]})
        days = [{"date": "2026-09-19", "starts_at": "08:00", "ends_at": "19:00"}]
        self.assertEqual(self.as_("manage", "patch", f"/{t.pk}", {"days": days}).status_code, 200)
        self.assertEqual(self.as_("operate", "get", f"/{t.pk}/lunch").json()["days"][0]["lunch_start"], "12:00")

    def test_lunch_generate_clear_and_problems(self):
        t, _teams = self._lunch_setup(5)
        window = {"settings": LUNCH_SETTINGS, "days": [{"date": "2026-09-19", "lunch_start": "12:00", "lunch_end": "12:30"}]}  # one sitting of two
        self.as_("operate", "put", f"/{t.pk}/lunch", window)
        doc = self.as_("operate", "get", f"/{t.pk}/lunch").json()
        self.assertEqual([d["teams_eating"] for d in doc["days"]], [5, 0])
        r = self.as_("operate", "post", f"/{t.pk}/lunch/generate", {})
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual((body["sittings_created"], body["games_replanned"]), (1, 0))
        self.assertIsNotNone(body["note"])
        self.assertEqual({p["type"] for p in body["problems"]}, {"no_room"})
        self.assertEqual(len(body["problems"]), 3)
        self.assertEqual(set(body["problems"][0]), {"type", "date", "team", "message"})
        self.assertEqual(body["problems"][0]["date"], "2026-09-19")
        doc = self.as_("operate", "get", f"/{t.pk}/lunch").json()
        self.assertTrue(doc["generated"])
        self.assertEqual(doc["days"][0]["sittings"][0]["start"], "12:00")
        self.assertEqual(doc["days"][0]["sittings"][0]["end"], "12:30")
        self.assertEqual(len(doc["days"][0]["sittings"][0]["teams"]), 2)
        self.assertEqual(set(doc["days"][0]["sittings"][0]["teams"][0]), {"id", "name"})
        blocks = self.as_("operate", "get", f"/{t.pk}/schedule").json()["blocks"]
        self.assertEqual([(b["kind"], b["title"], b["field"]) for b in blocks], [("lunch", "Lunch", None)])
        self.assertEqual(len(blocks[0]["teams"]), 2)
        self.assertNotEqual(blocks[0]["starts_at"], blocks[0]["lunch_start"])  # the block carries the padding, lunch_* the real sitting
        manual = self.as_("operate", "post", f"/{t.pk}/schedule-blocks", {"title": "Opening", "starts_at": "2026-09-19T09:00:00Z", "ends_at": "2026-09-19T09:30:00Z"}).json()
        self.assertEqual((manual["kind"], manual["lunch_start"]), ("block", None))
        self.assertEqual(self.as_("operate", "post", f"/{t.pk}/lunch/clear").status_code, 204)
        self.assertEqual([b["kind"] for b in self.as_("operate", "get", f"/{t.pk}/schedule").json()["blocks"]], ["block"])
        self.assertFalse(self.as_("operate", "get", f"/{t.pk}/lunch").json()["generated"])
        self.as_("operate", "put", f"/{t.pk}/lunch", {"settings": LUNCH_SETTINGS, "days": [{"date": "2026-09-19", "lunch_start": None, "lunch_end": None}]})
        body = self.as_("operate", "post", f"/{t.pk}/lunch/generate", {}).json()
        self.assertEqual([(p["type"], p["team"]) for p in body["problems"]], [("no_window", None)])
        self.assertEqual(self.as_("operate", "post", f"/{t.pk}/lunch/generate", {"match_duration_minutes": 0}).status_code, 422)

    def test_lunch_generate_replans_games_and_keeps_manual_blocks_on_schedule_generate(self):
        t, teams = self._lunch_setup(4)
        TournamentField.objects.create(tournament=t, name="F1", availability=[{"date": "2026-09-19", "start": "09:00", "end": "20:00"}])
        stage = t.stages.create(name="RR", format="round_robin")
        from tournaments.services.scheduling import generate_stage_matches

        generate_stage_matches(stage, entries=teams)
        self.as_("operate", "put", f"/{t.pk}/lunch", {"settings": LUNCH_SETTINGS, "days": [{"date": "2026-09-19", "lunch_start": "09:30", "lunch_end": "10:40"}]})
        body = self.as_("operate", "post", f"/{t.pk}/lunch/generate", {"match_duration_minutes": 30}).json()
        self.assertEqual((body["sittings_created"], body["games_replanned"], body["problems"], body["note"]), (2, 6, [], None))
        before = sorted(b["id"] for b in self.as_("operate", "get", f"/{t.pk}/schedule").json()["blocks"])
        self.assertEqual(self.as_("operate", "post", f"/{t.pk}/schedule/generate", {"match_duration_minutes": 30}).status_code, 200)
        self.assertEqual(self.as_("operate", "post", f"/{t.pk}/schedule/clear").status_code, 200)
        self.assertEqual(sorted(b["id"] for b in self.as_("operate", "get", f"/{t.pk}/schedule").json()["blocks"]), before)

    # -- roster, lunch, PDFs --

    def test_roster_crud_lunch_and_pdf(self):
        t = self.make(lunch_mode="optional", lunch_price_per_day="8")
        team = self.foreign(t, "Alpha")
        path = f"/{t.pk}/teams/{team.pk}/roster"
        r = self.as_("operate", "post", path, {"first_name": "Ann", "last_name": "A", "jersey_number": 9, "date_of_birth": "2010-01-01", "lunch_days": ["2026-09-20"]})
        self.assertEqual(r.status_code, 201, r.content)
        eid = r.json()["id"]
        self.assertEqual(self.as_("operate", "post", path, {"first_name": "B", "last_name": "B", "jersey_number": 9}).status_code, 422)  # jersey taken
        self.assertEqual(self.as_("operate", "put", f"{path}/{eid}/lunch", {"lunch_days": ["2026-09-19", "2026-09-20"]}).json()["lunch_days"], ["2026-09-19", "2026-09-20"])
        r = self.as_("operate", "patch", f"{path}/{eid}", {"role": "staff"})
        self.assertIsNone(r.json()["date_of_birth"])
        doc = self.as_("manage", "get", path).json()
        self.assertEqual(doc["lunch"][0]["staff_count"], 1)
        with mock.patch("tournaments.services.pdf._write_pdf", return_value=b"%PDF-1") as write:
            r = self.as_("manage", "get", f"{path}/pdf")
            self.assertEqual((r.status_code, r["Content-Type"]), (200, "application/pdf"))
            self.assertEqual(self.as_("manage", "get", f"/{t.pk}/lunch-summary/pdf").status_code, 200)
            self.assertEqual(write.call_count, 2)
        self.assertEqual(self.as_("operate", "delete", f"{path}/{eid}").status_code, 204)

    def test_manual_teams(self):
        t = self.make()
        self.assertEqual(self.as_("operate", "post", f"/{t.pk}/teams", {"kind": "foreign", "foreign_name": "X"}).status_code, 422)
        r = self.as_("operate", "post", f"/{t.pk}/teams", {"kind": "foreign", "foreign_name": "X", "foreign_contact_name": "C", "foreign_contact_email": "c@x.test"})
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(self.as_("operate", "post", f"/{t.pk}/teams", {"kind": "club", "team_id": str(self.club_team.pk)}).status_code, 201)
        self.assertEqual(self.as_("operate", "post", f"/{t.pk}/teams", {"kind": "club", "team_id": str(self.club_team.pk)}).status_code, 422)
        self.assertEqual(self.as_("operate", "delete", f"/teams/{r.json()['id']}").status_code, 403)
        self.assertEqual(self.as_("manage", "delete", f"/teams/{r.json()['id']}").status_code, 204)

    # -- tasks, dressing rooms, portal --

    def test_tasks_and_portal_claim(self):
        t = self.make(status="published")
        body = {"title": "Gate", "starts_at": "2026-09-19T09:00:00Z", "ends_at": "2026-09-19T10:30:00Z", "shift_minutes": 30, "needed_quantity": 1}
        r = self.as_("operate", "post", f"/{t.pk}/tasks/generate", body)
        self.assertEqual((r.status_code, r.json()["created_count"]), (201, 3), r.content)
        task = TournamentTask.objects.order_by("starts_at").first()
        plain = self.users["plain"]
        r = self.call("post", f"/tasks/{task.pk}/claim", plain, prefix="/portal/tournaments")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(r.json()["claimed_by_me"])
        self.assertEqual(self.call("post", f"/tasks/{task.pk}/claim", plain, prefix="/portal/tournaments").status_code, 409)
        listed = self.call("get", f"/{t.slug}/tasks", plain, prefix="/portal/tournaments").json()["results"]
        self.assertEqual([x["claimed"] for x in listed], [1, 0, 0])
        self.assertEqual(self.as_("operate", "get", f"/{t.pk}/tasks").json()["results"][0]["claims"][0]["member"]["id"], str(self.users["plain_member"].pk))
        r = self.as_("operate", "post", f"/{t.pk}/tasks/{task.pk}/assign", {"member_id": str(self.users["manage_member"].pk)})
        self.assertEqual(r.status_code, 409)  # full
        self.assertEqual(self.call("delete", f"/tasks/{task.pk}/claim", plain, prefix="/portal/tournaments").json()["claimed"], 0)
        self.assertEqual(self.as_("operate", "get", f"/{t.pk}/tasks/print").status_code, 200)
        self.features = Entitlements({})
        self.assertEqual(self.call("get", "", plain, prefix="/portal/tournaments").status_code, 404)

    def test_dressing_rooms_and_bookings(self):
        t = self.make()
        team = self.foreign(t, "Alpha")
        room = self.as_("manage", "post", "", {"name": "Room 1", "capacity": 20}, prefix="/dressing-rooms").json()
        r = self.as_("operate", "post", f"/{t.pk}/dressing-rooms/assignments", {"dressing_room_id": room["id"], "tournament_team_id": str(team.pk)})
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(self.as_("operate", "post", f"/{t.pk}/dressing-rooms/assignments", {"dressing_room_id": room["id"], "tournament_team_id": str(team.pk)}).status_code, 409)
        doc = self.as_("operate", "get", f"/{t.pk}/dressing-rooms").json()
        self.assertEqual(len(doc["teams"][0]["assignments"]), 1)
        self.assertEqual(self.as_("operate", "delete", f"/{t.pk}/dressing-rooms/assignments/{r.json()['id']}").status_code, 204)

    # -- match attributes, tie-breaker chains, timeline --

    def _attr(self, t, **body):
        return self.as_("operate", "post", f"/{t.pk}/attributes", {"name": "PIM", **body})

    def test_attribute_crud_and_validation(self):
        t = self.make()
        r = self._attr(t)
        self.assertEqual(r.status_code, 201, r.content)
        pim = r.json()
        self.assertEqual((pim["key"], pim["source"], pim["event_type"], pim["aggregate"], pim["position"]), ("pim", "manual", None, None, 0))
        self.assertEqual(self._attr(t, name="Pim").status_code, 422)  # same key
        self.assertEqual(self._attr(t, name="!!!").status_code, 422)
        self.assertEqual(self._attr(t, name="X", source="timeline").status_code, 422)  # needs event_type + aggregate
        self.assertEqual(self._attr(t, name="X", source="timeline", event_type="goal").status_code, 422)
        self.assertEqual(self._attr(t, name="X", event_type="goal").status_code, 422)  # manual takes neither
        self.assertEqual(self._attr(t, name="X", source="timeline", event_type="bogus", aggregate="sum").status_code, 422)
        mins = self._attr(t, name="Penalty minutes", source="timeline", event_type="penalty", aggregate="sum").json()
        self.assertEqual((mins["key"], mins["position"], mins["event_type"], mins["aggregate"]), ("penalty_minutes", 1, "penalty", "sum"))
        doc = self.as_("operate", "get", f"/{t.pk}/attributes").json()
        self.assertEqual(doc["event_types"], ["goal", "penalty", "goalie_change", "timeout", "other"])
        self.assertEqual([a["key"] for a in doc["results"]], ["pim", "penalty_minutes"])
        # rename and reorder; key and source stay
        r = self.as_("operate", "patch", f"/{t.pk}/attributes/{mins['id']}", {"name": "PIM total", "position": 0, "aggregate": "count"})
        self.assertEqual((r.json()["name"], r.json()["key"], r.json()["aggregate"], r.json()["source"]), ("PIM total", "penalty_minutes", "count", "timeline"))
        self.assertEqual(self.as_("operate", "patch", f"/{t.pk}/attributes/{pim['id']}", {"aggregate": "sum"}).status_code, 422)
        self.assertEqual(self.as_("operate", "patch", f"/{t.pk}/attributes/{uuid.uuid4()}", {"name": "Z"}).status_code, 404)
        self.assertEqual(self.as_("operate", "delete", f"/{t.pk}/attributes/{pim['id']}").status_code, 204)
        self.assertEqual(MatchAttribute.objects.filter(tournament=t).count(), 1)

    def test_attribute_delete_is_refused_while_a_stage_tiebreaker_uses_it(self):
        t = self.make()
        pim = self._attr(t).json()
        r = self.as_("operate", "post", f"/{t.pk}/stages", {"name": "League", "format": "round_robin", "tiebreakers": [{"criterion": "attr:pim", "direction": "least"}]})
        self.assertEqual(r.status_code, 201, r.content)
        stage = r.json()
        r = self.as_("operate", "delete", f"/{t.pk}/attributes/{pim['id']}")
        self.assertEqual((r.status_code, r.json()["code"]), (409, "in_use"), r.content)
        self.assertIn("League", r.json()["detail"])
        self.as_("operate", "patch", f"/{t.pk}/stages/{stage['id']}", {"tiebreakers": []})
        self.assertEqual(self.as_("operate", "delete", f"/{t.pk}/attributes/{pim['id']}").status_code, 204)

    def test_stage_tiebreakers_new_shape_old_fields_and_validation(self):
        t = self.make()
        self._attr(t)
        url = f"/{t.pk}/stages"
        default = self.as_("operate", "post", url, {"name": "A", "format": "round_robin"}).json()
        self.assertEqual([x["criterion"] for x in default["tiebreakers"]], ["goal_diff", "goals_for"])
        self.assertEqual((default["tiebreaker_1"], default["tiebreaker_2"]), ("goal_diff", "goals_for"))
        chain = [{"criterion": "wins"}, {"criterion": "goals_against", "direction": "most"}, {"criterion": "attr:pim", "direction": "least"}, {"criterion": "head_to_head"}]
        r = self.as_("operate", "post", url, {"name": "B", "format": "round_robin", "tiebreakers": chain})
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(
            r.json()["tiebreakers"],
            [{"criterion": "wins", "direction": "most"}, {"criterion": "goals_against", "direction": "most"}, {"criterion": "attr:pim", "direction": "least"}, {"criterion": "head_to_head", "direction": "most"}],
        )
        self.assertEqual((r.json()["tiebreaker_1"], r.json()["tiebreaker_2"]), ("won", "goals_against"))
        stored = TournamentStage.objects.get(pk=r.json()["id"]).standings_config["tiebreakers"]
        self.assertEqual(stored[2], {"criterion": "attr:pim", "direction": "least"})
        # old fields only apply when "tiebreakers" is absent; patching one keeps the other
        r = self.as_("operate", "post", url, {"name": "C", "format": "round_robin", "tiebreaker_1": "goals_for", "tiebreaker_2": None})
        self.assertEqual([x["criterion"] for x in r.json()["tiebreakers"]], ["goals_for"])
        r = self.as_("operate", "patch", f"{url}/{r.json()['id']}", {"tiebreaker_2": "won"})
        self.assertEqual([x["criterion"] for x in r.json()["tiebreakers"]], ["goals_for", "wins"])
        r = self.as_("operate", "patch", f"{url}/{r.json()['id']}", {"name": "C2"})
        self.assertEqual([x["criterion"] for x in r.json()["tiebreakers"]], ["goals_for", "wins"])  # untouched
        for bad in ([{"criterion": "bogus"}], [{"criterion": "attr:nope"}], [{"criterion": "wins"}, {"criterion": "wins"}], [{"criterion": "wins", "direction": "up"}]):
            self.assertEqual(self.as_("operate", "post", url, {"name": "D", "format": "round_robin", "tiebreakers": bad}).status_code, 422, bad)
        self.assertEqual(self.as_("operate", "post", url, {"name": "E", "format": "round_robin", "tiebreakers": []}).json()["tiebreakers"], [])
        # a stage saved in the old string format reads and ranks as before
        old = TournamentStage.objects.create(tournament=t, name="Old", format="round_robin", standings_config={"points": {"win": 3, "draw": 1, "loss": 0}, "tiebreakers": ["goal_diff", "won"]})
        doc = next(s for s in self.as_("operate", "get", f"/{t.pk}/setup").json()["stages"] if s["id"] == str(old.pk))
        self.assertEqual(doc["tiebreakers"], [{"criterion": "goal_diff", "direction": "most"}, {"criterion": "wins", "direction": "most"}])

    def _league(self):
        t = self.make(status="in_progress")
        self._attr(t)
        stage = self.as_("operate", "post", f"/{t.pk}/stages", {"name": "League", "format": "round_robin", "tiebreakers": [{"criterion": "attr:pim", "direction": "least"}]}).json()
        a, b, c = (self.foreign(t, n) for n in ("Alpha", "Bravo", "Charlie"))
        stage_obj = TournamentStage.objects.get(pk=stage["id"])
        return t, stage_obj, (a, b, c), [Match.objects.create(stage=stage_obj, home_team=x, away_team=c) for x in (a, b)]

    def test_manual_attribute_values_come_with_the_result_and_feed_standings(self):
        t, _stage, (a, b, c), (m1, m2) = self._league()
        timeline_attr = self._attr(t, name="Goals", source="timeline", event_type="goal", aggregate="count").json()
        r = self.as_("operate", "post", f"/matches/{m1.pk}/result", {"score_home": 1, "score_away": 0, "attributes": {"pim": {"home": 10, "away": 2}}})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["match"]["attributes"], {"pim": {"home": 10, "away": 2}, "goals": {"home": 0, "away": 0}})
        self.assertEqual(r.json()["match"]["event_count"], 0)
        self.as_("operate", "post", f"/matches/{m2.pk}/result", {"score_home": 1, "score_away": 0, "attributes": {"pim": {"home": 4, "away": 6}}})
        self.as_("operate", "post", f"/matches/{m1.pk}/result", {"score_home": 1, "score_away": 0, "attributes": {"pim": {"home": 12, "away": 2}}})  # an edit replaces the value
        self.assertEqual(MatchAttributeValue.objects.filter(match=m1, attribute__key="pim").get().home_value, 12)
        for bad in ({"nope": {"home": 1, "away": 1}}, {timeline_attr["key"]: {"home": 1, "away": 1}}, {"pim": {"home": -1, "away": 1}}):
            self.assertEqual(self.as_("operate", "post", f"/matches/{m2.pk}/result", {"score_home": 1, "score_away": 0, "attributes": bad}).status_code, 422, bad)
        doc = self.as_("operate", "get", f"/{t.pk}/standings").json()
        entry = doc["standings"][0]
        self.assertEqual(entry["attribute_columns"], [{"key": "pim", "name": "PIM"}])
        self.assertEqual(entry["stage"]["tiebreakers"], [{"criterion": "attr:pim", "direction": "least"}])
        rows = {r["team"]["id"]: r for r in entry["tables"][0]["rows"]}
        self.assertEqual((rows[str(a.pk)]["attributes"]["pim"], rows[str(b.pk)]["attributes"]["pim"], rows[str(c.pk)]["attributes"]["pim"]), (12, 4, 8))
        self.assertEqual([r["team"]["name"] for r in entry["tables"][0]["rows"]][:2], ["Bravo", "Alpha"])  # level on everything else: fewer penalty minutes first
        schedule = self.as_("operate", "get", f"/{t.pk}/schedule").json()
        self.assertIn("pim", schedule["stages"][0]["rounds"][0]["matches"][0]["attributes"])
        # public standings use the same builder
        t.status = "published"
        t.save()
        public = self.call("get", f"/{t.slug}/standings", None, prefix="/public/tournaments")
        self.assertEqual(public.status_code, 200, public.content)
        self.assertEqual(public.json()["standings"][0]["attribute_columns"][0]["key"], "pim")

    def test_timeline_crud_goals_fill_the_score_and_attributes_follow(self):
        t, _stage, _teams, (m1, _m2) = self._league()
        mins = self._attr(t, name="Penalty minutes", source="timeline", event_type="penalty", aggregate="sum").json()
        url = f"/matches/{m1.pk}/events"
        empty = self.as_("operate", "get", url).json()
        self.assertEqual((empty["events"], empty["ice_hockey"], empty["penalty_codes"]), ([], False, []))
        self.assertEqual(set(empty["rosters"]), {"home", "away"})
        r = self.as_("operate", "post", url, {"type": "goal", "side": "home", "minute": 12, "player": "Ann"})
        self.assertEqual(r.status_code, 201, r.content)
        goal = r.json()
        self.assertEqual((goal["type"], goal["side"], goal["minute"], goal["player"], goal["note"], goal["value"]), ("goal", "home", 12, "Ann", "", None))
        self.assertEqual((goal["match"]["score_home"], goal["match"]["score_away"], goal["match"]["event_count"], goal["match"]["status"]), (1, 0, 1, "scheduled"))
        self.as_("operate", "post", url, {"type": "goal", "side": "away", "minute": 5})
        r = self.as_("operate", "post", url, {"type": "penalty", "side": "away", "minute": 20, "value": 2})
        self.assertEqual(r.json()["match"]["attributes"][mins["key"]], {"home": 0, "away": 2})
        self.as_("operate", "post", url, {"type": "penalty", "side": "away", "minute": 30, "value": 5})
        r = self.as_("operate", "post", url, {"type": "other", "note": "End of period 1", "minute": 25})
        self.assertEqual((r.status_code, r.json()["side"]), (201, None))
        listed = self.as_("operate", "get", url).json()
        self.assertEqual([(e["type"], e["minute"]) for e in listed["events"]], [("goal", 5), ("goal", 12), ("penalty", 20), ("other", 25), ("penalty", 30)])
        self.assertEqual((listed["match"]["score_home"], listed["match"]["score_away"], listed["match"]["attributes"][mins["key"]]), (1, 1, {"home": 0, "away": 7}))
        # edit: flip the first goal to the other side, change penalty minutes
        first = listed["events"][0]
        r = self.as_("operate", "patch", f"/matches/events/{first['id']}", {"side": "home"})
        self.assertEqual((r.status_code, r.json()["match"]["score_home"], r.json()["match"]["score_away"]), (200, 2, 0))
        pen = listed["events"][4]
        r = self.as_("operate", "patch", f"/matches/events/{pen['id']}", {"value": 10, "note": "Slashing"})
        self.assertEqual((r.json()["value"], r.json()["note"], r.json()["match"]["attributes"][mins["key"]]), (10, "Slashing", {"home": 0, "away": 12}))
        # delete: 200 with the match; removing every goal leaves the score
        for event in self.as_("operate", "get", url).json()["events"]:
            if event["type"] == "goal":
                r = self.as_("operate", "delete", f"/matches/events/{event['id']}")
                self.assertEqual(r.status_code, 200)
        self.assertEqual((r.json()["match"]["score_home"], r.json()["match"]["score_away"], r.json()["match"]["event_count"]), (1, 0, 3))  # the last synced score stays
        self.assertEqual(self.as_("operate", "delete", f"/matches/events/{uuid.uuid4()}").status_code, 404)
        # a timeline attribute added later is backfilled from the existing events
        late = self._attr(t, name="Penalties", source="timeline", event_type="penalty", aggregate="count").json()
        self.assertEqual(self.as_("operate", "get", url).json()["match"]["attributes"][late["key"]], {"home": 0, "away": 2})

    def test_hockey_timeline_players_assists_penalty_codes_and_goalies(self):
        from club.models import Sport

        self.club.sport = Sport.objects.create(name="Ice hockey")
        self.club.save()
        _t, _stage, _teams, (m1, _m2) = self._league()
        home, away = m1.home_team, m1.away_team
        mk = lambda team, n, first: TournamentRosterEntry.objects.create(tournament_team=team, foreign_first_name=first, foreign_last_name="X", jersey_number=n)  # noqa: E731
        h1, h2, h3 = mk(home, 9, "Ann"), mk(home, 10, "Bo"), mk(home, 30, "Gil")
        a1 = mk(away, 7, "Cy")
        url = f"/matches/{m1.pk}/events"
        doc = self.as_("operate", "get", url).json()
        self.assertTrue(doc["ice_hockey"])
        self.assertIn("BOARD", [p["code"] for p in doc["penalty_codes"]])
        self.assertEqual(doc["penalty_lengths"], ["2", "2+2", "5", "10", "20"])
        self.assertEqual([p["number"] for p in doc["rosters"]["home"]], [9, 10, 30])
        # a goal with scorer and two assists, at minute and second
        r = self.as_("operate", "post", url, {"type": "goal", "side": "home", "minute": 3, "second": 41, "player_id": str(h1.pk), "assist_ids": [str(h2.pk), str(h3.pk)]})
        self.assertEqual(r.status_code, 201, r.content)
        goal = r.json()
        self.assertEqual((goal["second"], goal["player"], goal["player_number"], [x["number"] for x in goal["assists"]]), (41, "Ann X", 9, [10, 30]))
        self.assertEqual(r.json()["match"]["score_home"], 1)
        # players of the other team, the scorer assisting themselves, and a third assist are refused
        self.assertEqual(self.as_("operate", "post", url, {"type": "goal", "side": "home", "minute": 4, "player_id": str(a1.pk)}).status_code, 422)
        self.assertEqual(self.as_("operate", "post", url, {"type": "goal", "side": "home", "minute": 4, "player_id": str(h1.pk), "assist_ids": [str(h1.pk)]}).status_code, 422)
        self.assertEqual(self.as_("operate", "post", url, {"type": "goal", "side": "home", "minute": 4, "assist_ids": [str(h1.pk), str(h2.pk), str(h3.pk)]}).status_code, 422)
        # a penalty needs a code and a length; the player is optional (a team penalty); the length gives the minutes
        self.assertEqual(self.as_("operate", "post", url, {"type": "penalty", "side": "away", "minute": 5, "penalty_code": "NOPE", "penalty_length": "2"}).status_code, 422)
        self.assertEqual(self.as_("operate", "post", url, {"type": "penalty", "side": "away", "minute": 5, "penalty_code": "HOOK"}).status_code, 422)
        r = self.as_("operate", "post", url, {"type": "penalty", "side": "away", "minute": 5, "second": 0, "penalty_code": "HOOK", "penalty_length": "2+2"})
        self.assertEqual((r.status_code, r.json()["penalty_name"], r.json()["value"], r.json()["player"]), (201, "Hooking", 4, ""))
        r = self.as_("operate", "post", url, {"type": "penalty", "side": "away", "minute": 6, "player_id": str(a1.pk), "penalty_code": "BOARD", "penalty_length": "5"})
        self.assertEqual((r.json()["player_number"], r.json()["value"]), (7, 5))
        # goalie change and timeout
        r = self.as_("operate", "post", url, {"type": "goalie_change", "side": "home", "minute": 9, "goalie_out_id": str(h3.pk), "goalie_in_id": str(h2.pk)})
        self.assertEqual((r.status_code, r.json()["goalie_out"]["number"], r.json()["goalie_in"]["number"]), (201, 30, 10))
        self.assertEqual(self.as_("operate", "post", url, {"type": "goalie_change", "side": "home", "minute": 9, "goalie_out_id": str(h3.pk), "goalie_in_id": str(h3.pk)}).status_code, 422)
        self.assertEqual(self.as_("operate", "post", url, {"type": "timeout", "side": "away", "minute": 12, "second": 30}).status_code, 201)
        listed = self.as_("operate", "get", url).json()["events"]
        self.assertEqual([(e["type"], e["minute"], e["second"]) for e in listed][:2], [("goal", 3, 41), ("penalty", 5, 0)])

    def test_event_validation(self):
        t, _stage, _teams, (m1, _m2) = self._league()
        url = f"/matches/{m1.pk}/events"
        for body in ({"type": "goal"}, {"type": "penalty"}, {"type": "timeout"}, {"type": "goalie_change"}, {"type": "bogus", "side": "home"}, {"type": "goal", "side": "left"},
                     {"type": "goal", "side": "home", "minute": 1000}, {"type": "goal", "side": "home", "player": "x" * 101}, {"type": "goal", "side": "home", "note": "x" * 256}):
            self.assertEqual(self.as_("operate", "post", url, body).status_code, 422, body)
        self.assertEqual(self.as_("operate", "post", url, {"type": "other"}).status_code, 201)  # neutral is fine for "other"
        self.assertEqual(self.as_("operate", "post", f"/matches/{uuid.uuid4()}/events", {"type": "other"}).status_code, 404)
        goal = self.as_("operate", "post", url, {"type": "goal", "side": "home"}).json()
        self.assertEqual(self.as_("operate", "patch", f"/matches/events/{goal['id']}", {"side": None}).status_code, 422)  # a goal needs a side
        self.assertEqual(Match.objects.get(pk=m1.pk).score_home, 1)
        self.assertEqual(t.match_attributes.count(), 1)

    def test_goal_changes_on_a_completed_match_follow_the_bracket_or_are_refused(self):
        t = self.make(status="in_progress")
        stage = t.stages.create(name="Cup", format="single_elim")
        a, b, c = (self.foreign(t, n) for n in ("Alpha", "Bravo", "Charlie"))
        semi = Match.objects.create(stage=stage, home_team=a, away_team=b)
        final = Match.objects.create(stage=stage, round_number=2, home_source_match=semi, away_team=c)
        self.as_("operate", "post", f"/matches/{semi.pk}/result", {"score_home": 1, "score_away": 0})
        self.assertEqual(Match.objects.get(pk=final.pk).home_team_id, a.pk)
        url = f"/matches/{semi.pk}/events"
        # Bravo scores twice: the completed result flips to 0-2 and Bravo moves into the final
        self.as_("operate", "post", url, {"type": "goal", "side": "away"})
        r = self.as_("operate", "post", url, {"type": "goal", "side": "away"})
        self.assertEqual((r.status_code, r.json()["match"]["status"], r.json()["match"]["score_away"]), (201, "complete", 2))
        self.assertEqual(Match.objects.get(pk=final.pk).home_team_id, b.pk)
        # once the final is played, a change that would alter its feeder's winner is refused and nothing is saved
        self.as_("operate", "post", f"/matches/{final.pk}/result", {"score_home": 3, "score_away": 0})
        before = MatchEvent.objects.filter(match=semi).count()
        r = self.as_("operate", "post", url, {"type": "goal", "side": "home"})
        self.assertEqual(r.status_code, 201)  # 1-2: Bravo still wins
        r = self.as_("operate", "post", url, {"type": "goal", "side": "home"})  # 2-2: no winner left to feed the played final
        self.assertEqual((r.status_code, r.json()["code"]), (409, "match_complete"), r.content)
        semi.refresh_from_db()
        self.assertEqual((semi.score_home, semi.score_away, MatchEvent.objects.filter(match=semi).count()), (1, 2, before + 1))
        self.assertEqual(Match.objects.get(pk=final.pk).home_team_id, b.pk)
        self.assertEqual(self.as_("operate", "post", url, {"type": "other"}).status_code, 201)  # events that don't touch the score are always fine

    def test_timeline_and_attribute_permissions(self):
        t, _stage, _teams, (m1, _m2) = self._league()
        event = self.as_("operate", "post", f"/matches/{m1.pk}/events", {"type": "other"}).json()
        pim = t.match_attributes.get()
        i = uuid.uuid4()
        endpoints = [
            ("get", f"/{t.pk}/attributes", None, {"manage", "operate"}),
            ("post", f"/{t.pk}/attributes", {"name": "Shots"}, {"manage", "operate"}),
            ("patch", f"/{t.pk}/attributes/{pim.pk}", {"name": "PIM2"}, {"manage", "operate"}),
            ("delete", f"/{t.pk}/attributes/{i}", None, {"manage", "operate"}),
            ("get", f"/matches/{m1.pk}/events", None, {"manage", "operate"}),
            ("post", f"/matches/{m1.pk}/events", {"type": "other"}, {"manage", "operate"}),
            ("patch", f"/matches/events/{event['id']}", {"note": "x"}, {"manage", "operate"}),
            ("delete", f"/matches/events/{i}", None, {"manage", "operate"}),
        ]
        for method, path, body, allowed in endpoints:
            for key in ("plain", "manage", "operate"):
                response = self.as_(key, method, path, body)
                if key in allowed:
                    self.assertNotEqual(response.status_code, 403, (key, method, path))
                else:
                    self.assertEqual(response.status_code, 403, (key, method, path))
            self.assertEqual(self.call(method, path, None, body).status_code, 401, (method, path))
        self.features = Entitlements({})
        self.assertEqual(self.as_("owner", "get", f"/{t.pk}/attributes").status_code, 404)
