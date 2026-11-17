"""The management app's permission model, end to end (no mocks except MFA enrolment).

* Door: only club admins, owners and team staff get in; everyone else gets 403 ``no_access``.
* View: everyone who got in sees all members, all teams, numbers and officials.
* Members: edits need ``members.manage_members``.
* Teams: editing a team, its roster and staff needs a management position on *that* team; creating, deleting and
  ordering teams, and all settings, need ``admin.manage_settings`` (or the Owner role).
"""

import datetime
import json
from unittest import mock

from django.db import connection
from django.test import Client, TestCase
from django.utils import timezone
from django_tenants.utils import schema_context

from authorization.services import assign_role, create_role
from club.models import Club, Domain
from club.services import create_club, grant_login
from controlpanel.tests import login_session, make_user
from evaluations.testing import evaluations_feature
from events.models import Event, Lineup, Season
from members.models import ClubMembership, Family, FamilyMembership, Member
from teams.models import Position, StaffAssignment, Team

BASE = "/api/v1"


class PermissionModelTests(TestCase):
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
        cls.owner = make_user("owner@perm.test", first="Olivia", last="Owner")
        public = Club(schema_name="public", name="Platform", owner=cls.owner)
        public.auto_create_schema = False
        public.save()
        Domain.objects.create(domain="testserver", tenant=public, is_primary=True)
        cls.club = create_club("Perm FC", cls.owner)
        cls.host = Domain.objects.get(tenant=cls.club, is_primary=True).domain
        today = datetime.date.today()
        with schema_context(cls.club.schema_name):
            cls.season = Season.objects.create(start_date=today - datetime.timedelta(days=100), end_date=today + datetime.timedelta(days=200))
            cls.u12 = Team.objects.create(name="U12", short_name="U12")
            cls.u14 = Team.objects.create(name="U14", short_name="U14")
            cls.head = Position.objects.create(name="Head coach", short_name="HC", staff_position=True, management_position=True)
            cls.assistant = Position.objects.create(name="Assistant", short_name="AC", staff_position=True)
            cls.player = Member.objects.create(first_name="Pat", last_name="Player", email="pat@perm.test")
            people = {}
            for key in ("members_admin", "settings_admin", "head_coach", "assistant", "parent"):
                people[key] = Member.objects.create(first_name=key, last_name="Test", email=f"{key}@perm.test")
            StaffAssignment.objects.create(team=cls.u12, member=people["head_coach"], season=cls.season, position=cls.head)
            StaffAssignment.objects.create(team=cls.u14, member=people["assistant"], season=cls.season, position=cls.assistant)
            cls.role_members = create_role("Members admin", ["members.manage_members"])
            cls.role_settings = create_role("Settings admin", ["admin.manage_settings"])
        cls.users = {key: grant_login(cls.club, m)[0] for key, m in people.items()}
        with schema_context(cls.club.schema_name):
            assign_role(cls.users["members_admin"], cls.role_members)
            assign_role(cls.users["settings_admin"], cls.role_settings)
        cls.owner = type(cls.owner).objects.get(pk=cls.owner.pk)

    def call(self, method, path, user=None, data=None):
        headers = {"HTTP_HOST": self.host}
        if user is not None:
            headers["HTTP_X_SESSION_TOKEN"] = login_session(user)
        body = {"data": json.dumps(data), "content_type": "application/json"} if data is not None else {}
        return getattr(Client(), method)(BASE + path, **body, **headers)

    # -- the door --

    def test_door(self):
        for key in ("members_admin", "settings_admin", "head_coach", "assistant"):
            self.assertEqual(self.call("get", "/me", self.users[key]).status_code, 200, key)
        self.assertEqual(self.call("get", "/me", self.owner).status_code, 200)
        r = self.call("get", "/me", self.users["parent"])
        self.assertEqual((r.status_code, r.json()["code"]), (403, "no_access"))
        for path in ("/members", f"/members/{self.player.pk}", "/teams", "/team-positions", "/club/settings"):
            self.assertEqual(self.call("get", path, self.users["parent"]).status_code, 403, path)

    def test_me_reports_what_the_user_may_do(self):
        me = lambda key: self.call("get", "/me", self.users[key]).json()  # noqa: E731
        self.assertEqual((me("members_admin")["can_manage_members"], me("members_admin")["can_manage_settings"]), (True, False))
        self.assertEqual((me("settings_admin")["can_manage_members"], me("settings_admin")["can_manage_settings"]), (False, True))
        self.assertEqual(me("head_coach")["managed_team_ids"], [str(self.u12.pk)])
        self.assertEqual(me("assistant")["managed_team_ids"], [])
        owner = self.call("get", "/me", self.owner).json()
        self.assertEqual((owner["can_manage_members"], owner["can_manage_settings"]), (True, True))

    def test_switcher_requires_membership_or_guardian_role_in_addition_to_management(self):
        coach = self.users["head_coach"]
        assistant = self.users["assistant"]
        self.assertIs(self.call("get", "/me", coach).json()["has_member_portal_role"], False)
        self.assertIs(self.call("get", "/me", assistant).json()["has_member_portal_role"], False)
        with schema_context(self.club.schema_name):
            ClubMembership.objects.create(member=Member.objects.get(user=coach), season=self.season)
            family = Family.objects.create(name="Coach family")
            FamilyMembership.objects.create(family=family, member=Member.objects.get(user=assistant), role=FamilyMembership.FamilyRole.GUARDIAN)
            FamilyMembership.objects.create(family=family, member=self.player, role=FamilyMembership.FamilyRole.CHILD)
        self.assertIs(self.call("get", "/me", coach).json()["has_member_portal_role"], True)
        self.assertIs(self.call("get", "/me", assistant).json()["has_member_portal_role"], True)

    def test_coach_home_reports_unpublished_and_published_lineups_for_own_team(self):
        start = timezone.now() + datetime.timedelta(days=2)
        with schema_context(self.club.schema_name):
            draft = Event.objects.create(title="Draft lineup", kind=Event.EventKind.GAME, start=start, season=self.season)
            published = Event.objects.create(title="Published lineup", kind=Event.EventKind.GAME, start=start, season=self.season)
            other = Event.objects.create(title="Other team", kind=Event.EventKind.GAME, start=start, season=self.season)
            draft.teams.add(self.u12)
            published.teams.add(self.u12)
            other.teams.add(self.u14)
            Lineup.objects.create(event=published, team=self.u12, published_at=timezone.now())

        response = self.call("get", "/portal/coach", self.users["head_coach"])
        self.assertEqual(response.status_code, 200, response.content)
        rows = {row["id"]: row for row in response.json()["events"]}
        self.assertEqual(set(rows), {str(draft.pk), str(published.pk)})
        self.assertIs(rows[str(draft.pk)]["lineup_published"], False)
        self.assertIs(rows[str(published.pk)]["lineup_published"], True)
        self.assertEqual((rows[str(draft.pk)]["lineup_status"], rows[str(published.pk)]["lineup_status"]), ("none", "published"))  # no picks yet: nothing to call a draft

    def test_coach_home_shows_when_a_scheduled_lineup_goes_out(self):
        start = timezone.now() + datetime.timedelta(days=3)
        goes_out = timezone.now() + datetime.timedelta(days=1)
        with schema_context(self.club.schema_name):
            game = Event.objects.create(title="Scheduled", kind=Event.EventKind.GAME, start=start, season=self.season)
            game.teams.add(self.u12)
            lineup = Lineup.objects.create(event=game, team=self.u12, scheduled_publish_at=goes_out)
            lineup.selections.create(member=self.player)
        row = next(r for r in self.call("get", "/portal/coach", self.users["head_coach"]).json()["events"] if r["id"] == str(game.pk))
        self.assertEqual(row["lineup_status"], "scheduled")
        self.assertTrue(row["lineup_scheduled_at"].startswith(goes_out.date().isoformat()))

    def test_sponsors_require_the_dedicated_permission(self):
        user = self.users["settings_admin"]
        path = "/sponsor-management"
        self.assertEqual(self.call("get", path, user).status_code, 403)
        self.assertEqual(self.call("get", path, self.owner).status_code, 200)
        with schema_context(self.club.schema_name):
            assign_role(user, create_role("Sponsor manager", ["sponsoring.manage_sponsors"]))
        self.assertIs(self.call("get", "/me", user).json()["can_manage_sponsors"], True)
        headers = {"HTTP_HOST": self.host, "HTTP_X_SESSION_TOKEN": login_session(user)}
        fields = {"name": "Rink partner", "url": "https://sponsor.example", "start_date": "2026-10-01"}
        invalid = Client().post(BASE + path, {**fields, "end_date": "2026-09-01"}, **headers)
        self.assertEqual(invalid.status_code, 422, invalid.content)
        created = Client().post(BASE + path, fields, **headers)
        self.assertEqual(created.status_code, 201, created.content)
        detail = f"{path}/{created.json()['id']}"
        updated = Client().post(BASE + detail, {**fields, "name": "New rink partner"}, **headers)
        self.assertEqual(updated.json()["name"], "New rink partner", updated.content)
        self.assertEqual(self.call("delete", detail, user).status_code, 204)
        self.assertEqual(self.call("get", path, user).json(), [])

    def test_evaluations_need_their_own_permission(self):
        self.enterContext(evaluations_feature())
        with schema_context(self.club.schema_name):
            role = create_role("Evaluator", ["evaluations.execute_evaluations"])
            assign_role(self.users["assistant"], role)
        me = lambda key: self.call("get", "/me", self.users[key]).json()  # noqa: E731
        self.assertTrue(me("assistant")["can_execute_evaluations"])
        self.assertFalse(me("head_coach")["can_execute_evaluations"])  # not implied by managing a team
        self.assertFalse(me("members_admin")["can_execute_evaluations"])
        self.assertFalse(me("settings_admin")["can_execute_evaluations"])
        self.assertTrue(self.call("get", "/me", self.owner).json()["can_execute_evaluations"])  # the Owner role holds the whole catalog

    # -- viewing: everyone who got in --

    def test_everyone_inside_views_members_and_teams(self):
        for key in ("members_admin", "settings_admin", "head_coach", "assistant"):
            u = self.users[key]
            r = self.call("get", "/members", u)
            self.assertEqual(r.status_code, 200, key)
            self.assertIn(str(self.player.pk), {i["id"] for i in r.json()["items"]})
            self.assertEqual(self.call("get", f"/members/{self.player.pk}", u).status_code, 200, key)
            for path in ("/teams", f"/teams/{self.u12.pk}", f"/teams/{self.u14.pk}", "/teams/numbers/overview", "/team-positions", "/number-pools"):
                self.assertEqual(self.call("get", path, u).status_code, 200, (key, path))

    # -- members: manage_members --

    def test_member_edits_need_manage_members(self):
        body = {"first_name": "Changed"}
        for key in ("settings_admin", "head_coach", "assistant"):
            self.assertEqual(self.call("patch", f"/members/{self.player.pk}", self.users[key], body).status_code, 403, key)
            self.assertEqual(self.call("post", "/members", self.users[key], {"first_name": "A", "last_name": "B"}).status_code, 403, key)
        self.assertEqual(self.call("patch", f"/members/{self.player.pk}", self.users["members_admin"], body).status_code, 200)
        self.assertEqual(self.call("patch", f"/members/{self.player.pk}", self.owner, {"first_name": "Pat"}).status_code, 200)

    # -- teams: a management position on that team --

    def test_team_edit_needs_management_of_that_team(self):
        rename = {"name": "U12 renamed"}
        self.assertEqual(self.call("patch", f"/teams/{self.u12.pk}", self.users["head_coach"], rename).status_code, 200)
        self.assertEqual(self.call("patch", f"/teams/{self.u14.pk}", self.users["head_coach"], {"name": "Nope"}).status_code, 403)
        for key in ("assistant", "members_admin"):  # a non-management staff position, or member rights, don't help
            self.assertEqual(self.call("patch", f"/teams/{self.u12.pk}", self.users[key], {"name": "Nope"}).status_code, 403, key)
        for key in ("settings_admin",):
            self.assertEqual(self.call("patch", f"/teams/{self.u14.pk}", self.users[key], {"name": "U14 by settings"}).status_code, 200)
        self.assertEqual(self.call("patch", f"/teams/{self.u14.pk}", self.owner, {"name": "U14"}).status_code, 200)

    def test_pool_ages_and_feeds_into_need_settings(self):
        head, u12 = self.users["head_coach"], f"/teams/{self.u12.pk}"
        for body in ({"age_min": 8}, {"age_max": 12}, {"feeds_into_id": str(self.u14.pk)}):
            self.assertEqual(self.call("patch", u12, head, body).status_code, 403, body)
        pool = self.call("post", "/number-pools", self.owner, {"name": "P", "min_number": 1, "max_number": 9}).json()
        self.assertEqual(self.call("patch", u12, head, {"pool_id": pool["id"]}).status_code, 403)
        # resending the current values (what the form does) is fine, and so are the team's other fields
        r = self.call("patch", u12, head, {"name": "U12 b", "pool_id": None, "age_min": None, "age_max": None, "feeds_into_id": None})
        self.assertEqual(r.status_code, 200, r.content)
        s = self.users["settings_admin"]
        r = self.call("patch", u12, s, {"age_min": 8, "age_max": 12, "pool_id": pool["id"], "feeds_into_id": str(self.u14.pk)})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(self.call("patch", u12, head, {"age_min": 8}).status_code, 200)  # unchanged value

    def test_roster_and_staff_follow_team_management(self):
        with schema_context(self.club.schema_name):
            ClubMembership.objects.create(member=self.player, season=self.season, status=ClubMembership.StatusChoices.ACTIVE)
        add = {"member_id": str(self.player.pk), "season_id": str(self.season.pk), "position_id": None, "jersey_number": None, "is_captain": False, "is_alternate_captain": False}
        self.assertEqual(self.call("post", f"/teams/{self.u14.pk}/roster", self.users["head_coach"], add).status_code, 403)
        r = self.call("post", f"/teams/{self.u12.pk}/roster", self.users["head_coach"], add)
        self.assertEqual(r.status_code, 201, r.content)
        entry = r.json()["id"]
        self.assertEqual(self.call("patch", f"/teams/{self.u12.pk}/roster/{entry}", self.users["assistant"], {"jersey_number": 4}).status_code, 403)
        self.assertEqual(self.call("patch", f"/teams/{self.u12.pk}/roster/{entry}", self.users["head_coach"], {"jersey_number": 4}).status_code, 200)
        staff = {"member_id": str(self.users["assistant"].pk), "season_id": str(self.season.pk), "position_id": str(self.assistant.pk)}
        self.assertEqual(self.call("post", f"/teams/{self.u14.pk}/staff", self.users["head_coach"], staff).status_code, 403)
        self.assertEqual(self.call("delete", f"/teams/{self.u12.pk}/roster/{entry}", self.users["assistant"]).status_code, 403)
        self.assertEqual(self.call("delete", f"/teams/{self.u12.pk}/roster/{entry}", self.users["head_coach"]).status_code, 204)

    def test_management_ends_with_the_season(self):
        today = datetime.date.today()
        with schema_context(self.club.schema_name):
            old = Season.objects.create(start_date=today - datetime.timedelta(days=900), end_date=today - datetime.timedelta(days=500))
            StaffAssignment.objects.filter(team=self.u12).update(season=old)
        self.assertEqual(self.call("get", "/me", self.users["head_coach"]).status_code, 403)  # no current or future staff role: out

    # -- club-wide team actions and settings: manage_settings --

    def test_create_delete_reorder_teams_need_settings(self):
        for key in ("members_admin", "head_coach", "assistant"):
            u = self.users[key]
            self.assertEqual(self.call("post", "/teams", u, {"name": "New"}).status_code, 403, key)
            self.assertEqual(self.call("post", "/teams/reorder", u, {"ids": []}).status_code, 403, key)
            self.assertEqual(self.call("delete", f"/teams/{self.u14.pk}", u).status_code, 403, key)
        r = self.call("post", "/teams", self.users["settings_admin"], {"name": "New"})
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(self.call("post", "/teams/reorder", self.users["settings_admin"], {"ids": [str(self.u14.pk), str(self.u12.pk)]}).status_code, 204)
        self.assertEqual(self.call("delete", f"/teams/{r.json()['id']}", self.owner).status_code, 204)

    def test_settings_need_manage_settings(self):
        writes = [
            ("patch", "/club/settings", {"name": "Renamed"}),
            ("post", "/team-positions", {"name": "Wing", "short_name": "W"}),
            ("post", "/number-pools", {"name": "P", "min_number": 1, "max_number": 9}),
        ]
        for key in ("members_admin", "head_coach", "assistant"):
            for method, path, data in writes:
                self.assertEqual(self.call(method, path, self.users[key], data).status_code, 403, (key, path))
            self.assertEqual(self.call("get", "/club/settings", self.users[key]).status_code, 403, key)
        for method, path, data in writes:
            self.assertEqual(self.call(method, path, self.users["settings_admin"], data).status_code, 201 if method == "post" else 200, path)

    def test_reservations_need_a_team_to_manage(self):
        pool = self.call("post", "/number-pools", self.owner, {"name": "P", "min_number": 1, "max_number": 30}).json()
        path = f"/number-pools/{pool['id']}/reservations"
        self.assertEqual(self.call("post", path, self.users["assistant"], {"number": 7}).status_code, 403)
        self.assertEqual(self.call("post", path, self.users["members_admin"], {"number": 7}).status_code, 403)
        r = self.call("post", path, self.users["head_coach"], {"number": 7, "note": "Retired"})
        self.assertEqual(r.status_code, 201, r.content)
        rid = r.json()["id"]
        # the reserver and settings holders release; another team manager or staff cannot
        self.assertEqual(self.call("delete", f"{path}/{rid}", self.users["assistant"]).status_code, 403)
        self.assertEqual(self.call("delete", f"{path}/{rid}", self.users["head_coach"]).status_code, 204)
        r = self.call("post", path, self.users["head_coach"], {"number": 8})
        self.assertEqual(self.call("delete", f"{path}/{r.json()['id']}", self.users["settings_admin"]).status_code, 204)
