"""Member list rows carry the season's membership status (and fee status for admins); status=open filter."""

import datetime
from unittest import mock

from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from django_tenants.utils import schema_context

from club.models import Club, Domain
from club.services import create_club, grant_login
from controlpanel.tests import login_session, make_user
from events.models import Season
from members.models import ClubMembership, Member
from teams.models import Position, StaffAssignment, Team, TeamMembership

Status = ClubMembership.StatusChoices
Fee = ClubMembership.FeeStatus
URL = "/api/v1/members"


class MemberListStatusTests(TestCase):
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
        cls.owner = make_user("owner@status.test", first="Olivia", last="Owner")
        public = Club(schema_name="public", name="Platform", owner=cls.owner)
        public.auto_create_schema = False
        public.save()
        Domain.objects.create(domain="testserver", tenant=public, is_primary=True)
        cls.club = create_club("Status FC", cls.owner)
        cls.host = Domain.objects.get(tenant=cls.club, is_primary=True).domain
        today = timezone.localdate()
        with schema_context(cls.club.schema_name):
            cls.season = Season.objects.create(start_date=today - datetime.timedelta(days=100), end_date=today + datetime.timedelta(days=200))
            cls.old_season = Season.objects.create(start_date=today - datetime.timedelta(days=500), end_date=today - datetime.timedelta(days=300))
            cls.team = Team.objects.create(name="U12", short_name="U12")
            pos = Position.objects.create(name="Coach", short_name="C", staff_position=True)
            cls.coach = Member.objects.create(first_name="Coach", last_name="Zed", email="coach@status.test")
            StaffAssignment.objects.create(team=cls.team, member=cls.coach, season=cls.season, position=pos)
            cls.people = {}
            for name, st, fee in [("Active", Status.ACTIVE, Fee.PAID), ("Pending", Status.PENDING, Fee.UNPAID), ("Lapsed", Status.LAPSED, Fee.UNPAID), ("Cancelled", Status.CANCELLED, Fee.UNPAID)]:
                m = Member.objects.create(first_name=name, last_name="Person")
                ClubMembership.objects.create(member=m, season=cls.season, status=st, fee_status=fee)
                TeamMembership.objects.create(team=cls.team, member=m, season=cls.season)
                cls.people[name] = m
            cls.active_old = cls.people["Active"]
            ClubMembership.objects.create(member=cls.active_old, season=cls.old_season, status=Status.LAPSED, fee_status=Fee.WAIVED)
            cls.nomem = Member.objects.create(first_name="Nomem", last_name="Person")
            TeamMembership.objects.create(team=cls.team, member=cls.nomem, season=cls.season)
        cls.coach_user, _ = grant_login(cls.club, cls.coach)
        cls.owner = type(cls.owner).objects.get(pk=cls.owner.pk)

    def get(self, user, query=""):
        return Client().get(URL + query, HTTP_HOST=self.host, HTTP_X_SESSION_TOKEN=login_session(user))

    def rows(self, user, query=""):
        r = self.get(user, query)
        self.assertEqual(r.status_code, 200, r.content)
        return {i["first_name"]: i for i in r.json()["items"]}

    def test_admin_sees_status_and_fee_status(self):
        rows = self.rows(self.owner)
        expected = {"Active": ("active", "paid"), "Pending": ("pending", "unpaid"), "Lapsed": ("lapsed", "unpaid"), "Cancelled": ("cancelled", "unpaid")}
        for name, (st, fee) in expected.items():
            self.assertEqual((rows[name]["status"], rows[name]["fee_status"]), (st, fee), name)

    def test_member_without_membership_has_none(self):
        row = self.rows(self.owner)["Nomem"]
        self.assertIsNone(row["status"])
        self.assertIsNone(row["fee_status"])

    def test_staff_viewer_sees_status_but_not_fee_status(self):
        rows = self.rows(self.coach_user)
        self.assertIn("Active", rows)
        self.assertEqual(rows["Active"]["status"], "active")
        self.assertEqual(rows["Pending"]["status"], "pending")
        for row in rows.values():
            self.assertIsNone(row["fee_status"])

    def test_status_open_hides_lapsed_and_cancelled(self):
        rows = self.rows(self.owner, "?status=open")
        self.assertEqual(set(rows), {"Active", "Pending"})
        self.assertEqual(set(self.rows(self.owner, "?status=lapsed")), {"Lapsed"})

    def test_other_season_shows_that_seasons_status(self):
        rows = self.rows(self.owner, f"?season={self.old_season.pk}")
        self.assertEqual(set(rows), {"Active"})
        self.assertEqual((rows["Active"]["status"], rows["Active"]["fee_status"]), ("lapsed", "waived"))

    def test_query_count_is_constant(self):
        def count():
            with CaptureQueriesContext(connection) as ctx:
                r = self.get(self.owner)
            self.assertEqual(r.status_code, 200)
            return len(ctx), len(r.json()["items"])

        count()  # warm caches
        small_q, small_n = count()
        with schema_context(self.club.schema_name):
            for i in range(8):
                m = Member.objects.create(first_name=f"Extra{i}", last_name="Person")
                ClubMembership.objects.create(member=m, season=self.season, status=Status.ACTIVE)
        big_q, big_n = count()
        self.assertGreater(big_n, small_n)
        self.assertEqual(big_q, small_q)
