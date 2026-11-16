"""Overview numbers and who sees the money."""

import datetime
from unittest import mock

from django.db import connection
from django.test import Client, TestCase
from django_tenants.utils import schema_context

from club.models import Club, Domain
from club.services import create_club, grant_login
from controlpanel.tests import login_session, make_user
from events.models import Season
from members.models import ClubMembership, Member
from members.services import memberships
from teams.models import Position, StaffAssignment, Team, TeamMembership


class OverviewTests(TestCase):
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
        cls.owner = make_user("owner@ov.test", first="Olivia", last="Owner")
        public = Club(schema_name="public", name="Platform", owner=cls.owner)
        public.auto_create_schema = False
        public.save()
        Domain.objects.create(domain="testserver", tenant=public, is_primary=True)
        cls.club = create_club("Overview FC", cls.owner)
        cls.host = Domain.objects.get(tenant=cls.club, is_primary=True).domain
        today = datetime.date.today()
        with schema_context(cls.club.schema_name):
            cls.old = Season.objects.create(start_date=today - datetime.timedelta(days=500), end_date=today - datetime.timedelta(days=200))
            cls.season = Season.objects.create(start_date=today - datetime.timedelta(days=100), end_date=today + datetime.timedelta(days=200))
            cls.u12 = Team.objects.create(name="U12", short_name="U12")
            cls.u14 = Team.objects.create(name="U14", short_name="U14")
            head = Position.objects.create(name="Head coach", short_name="HC", staff_position=True, management_position=True)
            mk = lambda n, **kw: Member.objects.create(first_name=n, last_name="T", email=f"{n.lower()}@ov.test", **kw)  # noqa: E731
            cls.ann, cls.bob, cls.cy, cls.dee, cls.coach = mk("Ann"), mk("Bob"), mk("Cy"), mk("Dee"), mk("Coach")
            StaffAssignment.objects.create(team=cls.u12, member=cls.coach, season=cls.season, position=head)
            for m in (cls.ann, cls.bob, cls.cy):  # last season
                memberships.create_membership(cls.owner, m, cls.old)
            memberships.create_membership(cls.owner, cls.ann, cls.season)  # renewed
            memberships.create_membership(cls.owner, cls.bob, cls.season)
            memberships.create_membership(cls.owner, cls.dee, cls.season)  # new
            TeamMembership.objects.create(team=cls.u12, member=cls.ann, season=cls.season)
            ann_ms = ClubMembership.objects.get(member=cls.ann, season=cls.season)
            memberships.set_fee_waived(cls.owner, ann_ms)
            memberships.activate(cls.owner, ann_ms)
        cls.coach_user, _ = grant_login(cls.club, cls.coach)
        cls.owner = type(cls.owner).objects.get(pk=cls.owner.pk)

    def get(self, user):
        headers = {"HTTP_HOST": self.host, "HTTP_X_SESSION_TOKEN": login_session(user)}
        return Client().get("/api/v1/overview", **headers)

    def test_numbers(self):
        r = self.get(self.owner)
        self.assertEqual(r.status_code, 200, r.content)
        b = r.json()
        self.assertEqual(b["kpis"], {"total_members": 3, "previous_total_members": 3, "awaiting_approval": 2, "documents_to_review": 0, "teams_without_coach": 1, "unrostered_members": 2, "new_members": 1, "previous_new_members": 3})
        self.assertEqual((b["fees"]["unpaid"], b["fees"]["waived"], b["fees"]["paid"]), (2, 1, 0))
        self.assertEqual(b["renewal"], {"previous_season": self.old.name, "renewed": 2, "of": 3, "rate": 67, "previous_rate": None})
        self.assertEqual(len(b["signups"]), 12)
        self.assertEqual(sum(m["new"] + m["returning"] for m in b["signups"]), 6)  # all six memberships were signed up today
        self.assertEqual(b["signups"][-1], {"month": datetime.date.today().strftime("%Y-%m"), "new": 4, "returning": 2})  # first-ever: Ann, Bob and Cy (last season) and Dee
        self.assertEqual(b["cards"]["members"], {"total": 3, "active": 1, "pending": 2, "lapsed": 0})
        self.assertEqual(b["cards"]["teams"], {"teams": 2, "players": 1, "staff": 1})
        self.assertEqual(b["cards"]["people"]["admins"], 1)  # the owner

    def test_money_is_for_member_managers_only(self):
        with mock.patch("members.services.access.can_access_management", return_value=True):
            b = self.get(self.coach_user).json()
        self.assertIsNone(b["fees"])
        self.assertIsNone(b["renewal"])
        self.assertEqual(b["kpis"]["total_members"], 3)

    def test_platform_host_is_404(self):
        headers = {"HTTP_HOST": "testserver", "HTTP_X_SESSION_TOKEN": login_session(self.owner)}
        self.assertEqual(Client().get("/api/v1/overview", **headers).status_code, 404)
