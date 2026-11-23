"""A club with a team, people in every role and an API client, shared by the chat test modules."""

import datetime
import json
import uuid
from unittest import mock

from django.core.cache import cache
from django.db import connection
from django.test import Client, TestCase
from django.utils import timezone
from django_tenants.utils import schema_context

from authorization.models import Role
from authorization.services import assign_role, create_role
from club.models import Club, Domain
from club.services import create_club, grant_login
from controlpanel.tests import login_session, make_user
from events.models import Season
from features.entitlements import Entitlement, Entitlements
from members.models import Family, FamilyMembership, Member
from teams.models import Position, StaffAssignment, Team, TeamMembership

BASE = "/api/v1/chat"
WITH = Entitlements({"chat": Entitlement(True)})
WITHOUT = Entitlements({})


class ChatTestBase(TestCase):
    CLUB = "Chat FC"

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
        owner = make_user(f"owner@{cls.CLUB.split()[0].lower()}.test", first="Olivia", last="Owner")
        public = Club(schema_name="public", name="Platform", owner=owner)
        public.auto_create_schema = False
        public.save()
        Domain.objects.create(domain="testserver", tenant=public, is_primary=True)
        cls.club = create_club(cls.CLUB, owner)
        cls.host = Domain.objects.get(tenant=cls.club, is_primary=True).domain
        cls.users, cls.m = {}, {}
        today = timezone.localdate()
        with schema_context(cls.club.schema_name):
            cls.season = Season.objects.create(start_date=today - datetime.timedelta(days=100), end_date=today + datetime.timedelta(days=200))
            cls.team = Team.objects.create(name="U14", short_name="U14")
            cls.other_team = Team.objects.create(name="U16", short_name="U16")
            cls.head_coach = Position.objects.create(name="Head coach", short_name="HC", staff_position=True, management_position=True)
            cls.forward = Position.objects.create(name="Forward", short_name="F")
            make = lambda key, first, last, dob=None: Member.objects.create(first_name=first, last_name=last, email=f"{key}@chat.test", date_of_birth=dob)  # noqa: E731
            cls.m = {
                "coach": make("coach", "Carl", "Coach", datetime.date(1980, 1, 1)),
                "p1": make("p1", "Pia", "Player", today.replace(year=today.year - 12)),
                "p3": make("p3", "Pip", "Player", today.replace(year=today.year - 11)),
                "p2": make("p2", "Paul", "Player", datetime.date(1999, 5, 5)),
                "parent": make("parent", "Pat", "Parent", datetime.date(1982, 2, 2)),
                "stranger": make("stranger", "Sam", "Stranger", datetime.date(1990, 3, 3)),
                "mod": make("mod", "Mo", "Derator", datetime.date(1985, 4, 4)),
                "announcer": make("announcer", "Ann", "Ouncer", datetime.date(1985, 4, 5)),
                "plain": make("plain", "Pam", "Plain", datetime.date(1991, 6, 6)),
            }
            StaffAssignment.objects.create(team=cls.team, member=cls.m["coach"], season=cls.season, position=cls.head_coach)
            TeamMembership.objects.create(team=cls.team, member=cls.m["p1"], season=cls.season, position=cls.forward)
            TeamMembership.objects.create(team=cls.team, member=cls.m["p2"], season=cls.season, position=cls.forward)
            family = Family.objects.create(name="Player")
            FamilyMembership.objects.create(family=family, member=cls.m["parent"], role=FamilyMembership.FamilyRole.PARENT)
            FamilyMembership.objects.create(family=family, member=cls.m["p1"], role=FamilyMembership.FamilyRole.CHILD)
            FamilyMembership.objects.create(family=family, member=cls.m["p3"], role=FamilyMembership.FamilyRole.CHILD)
            roles = {"mod": create_role("Chat moderator", ["chat.moderate"]), "announcer": create_role("Chat announcer", ["chat.announce"])}
        for key, member in cls.m.items():
            user, _ = grant_login(cls.club, member)
            cls.users[key] = user
            if key in roles:
                with schema_context(cls.club.schema_name):
                    assign_role(user, roles[key])
        cls.nomember = make_user(f"nomember@{cls.CLUB.split()[0].lower()}.test", first="No", last="Member")  # a login without a member record
        cls.club.add_user(cls.nomember)
        with schema_context(cls.club.schema_name):
            assign_role(cls.nomember, Role.objects.get(slug="member"))

    def setUp(self):
        self.features = WITH
        for target in ("features.middleware.entitlements_for_request", "features.context.get_current_entitlements", "features.backends.get_current_entitlements"):
            patcher = mock.patch(target, side_effect=lambda *a, **k: self.features)
            patcher.start()
            self.addCleanup(patcher.stop)
        door = mock.patch("members.services.access.can_access_management", return_value=True)
        door.start()
        self.addCleanup(door.stop)
        cache.clear()
        ctx = schema_context(self.club.schema_name)
        ctx.__enter__()
        self.addCleanup(ctx.__exit__, None, None, None)

    # -- helpers --

    def call(self, key, method, path, data=None, **extra):
        headers = {"HTTP_HOST": self.host}
        user = self.nomember if key == "nomember" else self.users[key]
        headers["HTTP_X_SESSION_TOKEN"] = login_session(user)
        if data is not None:
            extra = {"data": data, **extra} if "content_type" in extra else {"data": json.dumps(data), "content_type": "application/json", **extra}
        response = getattr(Client(), method)(BASE + path, **extra, **headers)
        connection.set_tenant(self.club)
        return response

    def get(self, key, path):
        return self.call(key, "get", path)

    def post(self, key, path, data=None):
        return self.call(key, "post", path, data if data is not None else {})

    def start(self, key, kind="group", **body):
        """Create a conversation as ``key`` and return its id."""
        if kind == "group":
            body.setdefault("title", "Group")
            body["member_ids"] = [str(self.m[k].pk) for k in body.pop("with_", [])]
        response = self.post(key, "/conversations", {"kind": kind, **body})
        self.assertIn(response.status_code, (200, 201), response.content)
        return response.json()["id"]

    def say(self, key, conversation_id, body="hello", **extra):
        return self.post(key, f"/conversations/{conversation_id}/messages", {"body": body, "client_key": uuid.uuid4().hex, **extra})
