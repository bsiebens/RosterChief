"""Calendar API (``/events``): range + filters + rights flags, form options, one-off and series writes with scopes,
attendance (coach notes hidden), line-up, tasks, attachments, statistics, overview attendance and notifications."""

import datetime
import json
import os
import shutil
import tempfile
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from django_tenants.utils import schema_context

from club.models import Club, Domain
from club.services import create_club, grant_login
from controlpanel.tests import login_session, make_user
from events.models import Attendance, Event, EventAttachment, EventSeries, EventTask, Lineup, Location, Season
from events.services import event_notifications as notices
from members.models import AuditEntry, Family, FamilyMembership, Group, GroupMembership, Member
from members.services import access
from notifications.models import InboxNotification
from teams.models import Position, StaffAssignment, Team, TeamMembership

BASE = "/api/v1"


def upload(name="schedule.pdf", content=b"%PDF-1.4 hi"):
    return SimpleUploadedFile(name, content)


class EventsApiTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls._mfa = mock.patch("authorization.middleware.is_local_development", return_value=True)
        cls._mfa.start()
        cls._tmp = tempfile.mkdtemp(prefix="rc-events-api-")
        cls._media = override_settings(PRIVATE_MEDIA_ROOT=cls._tmp)
        cls._media.enable()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        connection.set_schema_to_public()
        cls._media.disable()
        shutil.rmtree(cls._tmp, ignore_errors=True)
        cls._mfa.stop()

    @classmethod
    def setUpTestData(cls):
        cls.owner = make_user("owner@evapi.test", first="Olivia", last="Owner")
        public = Club(schema_name="public", name="Platform", owner=cls.owner)
        public.auto_create_schema = False
        public.save()
        Domain.objects.create(domain="testserver", tenant=public, is_primary=True)
        cls.club = create_club("Events FC", cls.owner)
        cls.host = Domain.objects.get(tenant=cls.club, is_primary=True).domain
        today = datetime.date.today()
        with schema_context(cls.club.schema_name):
            cls.season = Season.objects.create(start_date=today - datetime.timedelta(days=100), end_date=today + datetime.timedelta(days=200))
            cls.team_a = Team.objects.create(name="U12", short_name="U12", ordering=1)
            cls.team_b = Team.objects.create(name="U14", short_name="U14", ordering=2)
            cls.group = Group.objects.create(name="Committee")
            cls.location = Location.objects.create(name="Sporthal", address="Straat 1", city="Gent", zip_code="9000", country="BE")
            head = Position.objects.create(name="Head coach", short_name="HC", staff_position=True, management_position=True)
            assistant = Position.objects.create(name="Assistant", short_name="AC", staff_position=True)
            cls.forward = Position.objects.create(name="Forward", short_name="F", ordering=1)

            def member(first, last="Test", email=""):
                return Member.objects.create(first_name=first, last_name=last, email=email)

            cls.head = member("Hank", "Head", "head@evapi.test")
            cls.assistant = member("Assia", "Assist", "assist@evapi.test")
            cls.bcoach = member("Bert", "Bcoach", "bcoach@evapi.test")
            cls.grouper = member("Greta", "Group", "group@evapi.test")
            cls.stranger = member("Sam", "Stranger", "stranger@evapi.test")
            cls.p1 = member("Ann", "Alpha", "p1@evapi.test")
            cls.p2 = member("Bob", "Beta")
            cls.p3 = member("Cas", "Gamma")
            StaffAssignment.objects.create(team=cls.team_a, member=cls.head, season=cls.season, position=head)
            StaffAssignment.objects.create(team=cls.team_a, member=cls.assistant, season=cls.season, position=assistant)
            StaffAssignment.objects.create(team=cls.team_b, member=cls.bcoach, season=cls.season, position=head)
            GroupMembership.objects.create(group=cls.group, member=cls.grouper, is_admin=True)
            for n, p in enumerate((cls.p1, cls.p2, cls.p3), start=1):
                TeamMembership.objects.create(team=cls.team_a, member=p, season=cls.season, position=cls.forward, jersey_number=n)
        for m in (cls.head, cls.assistant, cls.bcoach, cls.grouper, cls.stranger, cls.p1):
            grant_login(cls.club, m)
        with schema_context(cls.club.schema_name):
            for m in (cls.head, cls.assistant, cls.bcoach, cls.grouper, cls.stranger, cls.p1):
                m.refresh_from_db()
        cls.owner = type(cls.owner).objects.get(pk=cls.owner.pk)

    def setUp(self):
        # The door (who gets into the management app at all) is tested in tests_permissions; here everyone is inside.
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

    def upload(self, path, user, files):
        response = Client().post(BASE + path, {"files": files}, HTTP_HOST=self.host, HTTP_X_SESSION_TOKEN=login_session(user))
        connection.set_tenant(self.club)
        return response

    def event(self, days=3, teams=(), groups=(), kind=Event.EventKind.TRAINING, title="Practice", **kw):
        kw.setdefault("season", self.season)
        e = Event.objects.create(kind=kind, title=title, start=timezone.now() + datetime.timedelta(days=days), **kw)
        if teams:
            e.teams.set(teams)
        if groups:
            e.groups.set(groups)
        return e

    def range(self, days=10):
        today = timezone.localdate()
        return f"from={today}&to={today + datetime.timedelta(days=days)}"

    def inbox(self, member):
        return list(InboxNotification.objects.filter(user=member.user, club=self.club).values_list("title", flat=True))

    def iso(self, dt):
        return dt.isoformat()

    # -- calendar --

    def test_range_filters_and_rights_flags(self):
        ea = self.event(teams=[self.team_a], title="A")
        eb = self.event(teams=[self.team_b], title="B", kind=Event.EventKind.GAME)
        eg = self.event(groups=[self.group], title="G")
        ec = self.event(club_wide=True, title="Club")
        eab = self.event(teams=[self.team_a, self.team_b], title="AB")
        self.event(days=30, teams=[self.team_a], title="Later")
        users = {"owner": self.owner, "head": self.head.user, "assistant": self.assistant.user, "grouper": self.grouper.user, "stranger": self.stranger.user, "bcoach": self.bcoach.user}
        expected_edit = {
            "owner": {"A", "B", "G", "Club", "AB"},
            "head": {"A"},
            "assistant": set(),
            "grouper": {"G"},
            "stranger": set(),
            "bcoach": {"B"},
        }
        expected_attend = {"owner": {"A", "B", "G", "Club", "AB"}, "head": {"A", "AB"}, "assistant": set(), "grouper": {"G"}, "stranger": set(), "bcoach": {"B", "AB"}}
        for key, user in users.items():
            with self.subTest(user=key):
                r = self.call("get", f"/events?{self.range()}", user)
                self.assertEqual(r.status_code, 200)
                rows = r.json()
                self.assertEqual({e["title"] for e in rows}, {"A", "B", "G", "Club", "AB"})
                self.assertEqual({e["title"] for e in rows if e["can_edit"]}, expected_edit[key])
                self.assertEqual({e["title"] for e in rows if e["can_manage_attendance"]}, expected_attend[key])
                for event in (ea, eb, eg, ec, eab):  # the page-level rights agree with the access helpers
                    row = next(e for e in rows if e["id"] == str(event.pk))
                    self.assertEqual(row["can_edit"], access.can_manage_event(user, event))
                    self.assertEqual(row["can_manage_attendance"], access.can_manage_attendance(user, event))
        rows = {e["title"]: e for e in self.call("get", f"/events?{self.range()}", self.head.user).json()}
        self.assertEqual(rows["A"]["attendance"]["total"], 3)  # the roster (staff are not invited)
        self.assertEqual(rows["A"]["attendance"]["no_response"], 3)
        self.assertEqual(rows["A"]["teams"], [{"id": str(self.team_a.pk), "name": "U12", "short_name": "U12"}])
        self.assertEqual(rows["G"]["groups"], [{"id": str(self.group.pk), "name": "Committee", "short_name": ""}])

        titles = lambda q, user=self.owner: {e["title"] for e in self.call("get", f"/events?{self.range()}&{q}", user).json()}  # noqa: E731
        self.assertEqual(titles(f"team={self.team_a.pk}"), {"A", "AB"})
        self.assertEqual(titles(f"group={self.group.pk}"), {"G"})
        self.assertEqual(titles("kind=game"), {"B"})
        self.assertEqual(titles("mine=true", self.grouper.user), {"G", "Club"})
        self.assertEqual(titles("mine=true", self.head.user), {"A", "AB", "Club"})
        self.assertEqual(titles("mine=true", self.stranger.user), {"Club"})
        self.assertIn("Later", {e["title"] for e in self.call("get", f"/events?{self.range(40)}", self.owner).json()})

        today = timezone.localdate()
        for query in (f"from={today}&to={today - datetime.timedelta(days=1)}", f"from={today}&to={today + datetime.timedelta(days=401)}", f"to={today}", f"{self.range()}&kind=bogus"):
            self.assertEqual(self.call("get", f"/events?{query}", self.owner).status_code, 422, query)

    def test_list_has_no_n_plus_one(self):
        def count():
            with CaptureQueriesContext(connection) as ctx:
                self.assertEqual(self.call("get", f"/events?{self.range()}", self.head.user).status_code, 200)
            return len(ctx.captured_queries)

        for _ in range(2):
            self.event(teams=[self.team_a, self.team_b], groups=[self.group])
        count()  # warm up (first-request bookkeeping)
        few = count()
        for _ in range(6):
            self.event(teams=[self.team_a], groups=[self.group], location=self.location)
        self.assertEqual(count(), few)

    def test_detail(self):
        e = self.event(teams=[self.team_a], location=self.location)
        e.invited_members.add(self.stranger)
        e.excluded_members.add(self.p3)
        r = self.call("get", f"/events/{e.pk}", self.assistant.user)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["invited_member_ids"], [str(self.stranger.pk)])
        self.assertEqual(body["excluded_member_ids"], [str(self.p3.pk)])
        self.assertEqual(body["location"], {"id": str(self.location.pk), "name": "Sporthal", "city": "Gent", "address": "Straat 1", "is_home": False})
        self.assertEqual(body["lineup"]["available"], False)
        self.assertIsNone(body["series"])
        self.assertEqual(body["season"]["id"], str(self.season.pk))
        self.assertFalse(body["can_edit"])
        self.assertEqual(self.call("get", "/events/00000000-0000-0000-0000-000000000000", self.owner).status_code, 404)

    def test_options_per_user(self):
        def options(user):
            r = self.call("get", "/events/options", user)
            self.assertEqual(r.status_code, 200)
            return r.json()

        owner = options(self.owner)
        self.assertEqual([t["name"] for t in owner["teams"]], ["U12", "U14"])
        self.assertEqual([g["name"] for g in owner["groups"]], ["Committee"])
        self.assertTrue(owner["can_club_wide"] and owner["can_create"])
        self.assertEqual([loc["name"] for loc in owner["locations"]], ["Sporthal"])
        self.assertIn({"value": "game", "label": "Game"}, owner["kinds"])
        self.assertEqual([w["code"] for w in owner["weekdays"]][:2], ["MO", "TU"])
        head = options(self.head.user)
        self.assertEqual(([t["name"] for t in head["teams"]], head["groups"], head["can_club_wide"], head["can_create"]), (["U12"], [], False, True))
        grouper = options(self.grouper.user)
        self.assertEqual((grouper["teams"], [g["name"] for g in grouper["groups"]], grouper["can_create"]), ([], ["Committee"], True))
        for user in (self.assistant.user, self.stranger.user):
            got = options(user)
            self.assertEqual((got["teams"], got["groups"], got["can_club_wide"], got["can_create"]), ([], [], False, False))
            self.assertEqual(len(got["locations"]), 1)  # the read-only lists are complete

    # -- writes --

    def body(self, **kw):
        start = timezone.localtime(timezone.now() + datetime.timedelta(days=5)).replace(hour=18, minute=0, second=0, microsecond=0)
        return {"title": "Practice", "kind": "training", "start": self.iso(start), "end": self.iso(start + datetime.timedelta(hours=2)), "team_ids": [str(self.team_a.pk)], **kw}

    def test_create_one_off_permissions_and_validation(self):
        r = self.call("post", "/events", self.head.user, self.body(location_id=str(self.location.pk)))
        self.assertEqual(r.status_code, 201, r.content)
        self.assertTrue(r.json()["can_edit"])
        self.assertEqual(r.json()["location"]["name"], "Sporthal")
        self.assertEqual(AuditEntry.objects.filter(action="event.created").count(), 1)
        refused = [
            self.body(team_ids=[str(self.team_b.pk)]),  # a team you do not manage
            self.body(team_ids=[str(self.team_a.pk), str(self.team_b.pk)]),
            self.body(team_ids=[], club_wide=True),  # club-wide needs the settings permission
            self.body(team_ids=[], group_ids=[str(self.group.pk)]),  # a group you are not in
        ]
        for data in refused:
            self.assertEqual(self.call("post", "/events", self.head.user, data).status_code, 403, data)
        for user in (self.assistant.user, self.stranger.user):
            self.assertEqual(self.call("post", "/events", user, self.body()).status_code, 403)
        self.assertEqual(self.call("post", "/events", self.grouper.user, self.body(team_ids=[], group_ids=[str(self.group.pk)])).status_code, 201)
        self.assertEqual(self.call("post", "/events", self.owner, self.body(team_ids=[], club_wide=True, title="AGM", kind="meeting")).status_code, 201)

        start = timezone.now() + datetime.timedelta(days=5)
        cases = [
            (self.body(end=self.iso(start - datetime.timedelta(days=1))), "end"),
            (self.body(kind="game", team_ids=[]), "team_ids"),
            (self.body(team_ids=["00000000-0000-0000-0000-000000000000"]), "team_ids"),
            (self.body(kind="bogus"), "kind"),
            (self.body(title="", kind="other"), "title"),
            (self.body(club_wide=True), "club_wide"),
        ]
        for data, field in cases:
            r = self.call("post", "/events", self.owner, data)
            self.assertEqual(r.status_code, 422, (field, r.content))
            self.assertIn(field, r.json()["errors"])

    def test_edit_cancel_delete_one_off(self):
        e = self.event(teams=[self.team_a])
        r = self.call("patch", f"/events/{e.pk}", self.head.user, {"title": "Renamed", "location_id": str(self.location.pk)})
        self.assertEqual((r.status_code, r.json()["title"], r.json()["location"]["name"]), (200, "Renamed", "Sporthal"))
        self.assertEqual(self.call("patch", f"/events/{e.pk}", self.head.user, {"location_id": None}).json()["location"], None)
        self.assertEqual(self.call("patch", f"/events/{e.pk}", self.head.user, {"team_ids": [str(self.team_b.pk)]}).status_code, 403)
        for user in (self.assistant.user, self.bcoach.user, self.grouper.user):
            self.assertEqual(self.call("patch", f"/events/{e.pk}", user, {"title": "X"}).status_code, 403)
            self.assertEqual(self.call("post", f"/events/{e.pk}/cancel", user).status_code, 403)
            self.assertEqual(self.call("delete", f"/events/{e.pk}", user).status_code, 403)
        self.assertEqual(self.call("patch", f"/events/{e.pk}", self.head.user, {"end": self.iso(e.start - datetime.timedelta(hours=1))}).status_code, 422)
        self.assertEqual(self.call("patch", f"/events/{e.pk}?scope=bogus", self.head.user, {"title": "X"}).status_code, 422)
        self.assertTrue(self.call("post", f"/events/{e.pk}/cancel", self.head.user).json()["cancelled"])
        self.assertFalse(self.call("post", f"/events/{e.pk}/uncancel", self.head.user).json()["cancelled"])
        form = self.call("get", f"/events/{e.pk}/form", self.head.user).json()
        self.assertEqual((form["event"]["title"], form["series"], form["can_edit"]), ("Renamed", None, True))
        self.assertEqual(self.call("delete", f"/events/{e.pk}", self.head.user).status_code, 204)
        self.assertFalse(Event.objects.filter(pk=e.pk).exists())

    def test_series_with_scopes(self):
        start = timezone.localtime(timezone.now() + datetime.timedelta(days=2)).replace(hour=18, minute=0, second=0, microsecond=0)
        from events.services.recurrence import WEEKDAY_CODES

        weekday = WEEKDAY_CODES[start.weekday()]
        data = self.body(start=self.iso(start), end=self.iso(start + datetime.timedelta(hours=1)), recurrence={"frequency": "weekly", "weekdays": [weekday], "count": 6})
        self.assertEqual(self.call("post", "/events", self.head.user, {**data, "team_ids": [str(self.team_b.pk)]}).status_code, 403)
        self.assertEqual(self.call("post", "/events", self.head.user, {**data, "season_id": str(self.season.pk)}).status_code, 422)
        self.assertIn("weekdays", self.call("post", "/events", self.head.user, {**data, "recurrence": {"frequency": "weekly", "weekdays": []}}).json()["errors"])
        r = self.call("post", "/events", self.head.user, data)
        self.assertEqual(r.status_code, 201, r.content)
        series = EventSeries.objects.get(pk=r.json()["series_id"])
        self.assertEqual(r.json()["series"]["recurrence"]["count"], 6)
        self.assertTrue(r.json()["series"]["description"])
        occ = list(series.occurrences.order_by("start"))
        self.assertEqual(len(occ), 6)
        self.assertEqual(r.json()["id"], str(occ[0].pk))

        form = self.call("get", f"/events/{occ[1].pk}/form", self.head.user).json()
        self.assertEqual((form["series"]["recurrence"]["frequency"], form["series"]["recurrence"]["weekdays"]), ("weekly", [weekday]))

        r = self.call("patch", f"/events/{occ[2].pk}?scope=this", self.head.user, {"title": "Special"})
        self.assertEqual((r.status_code, r.json()["detached"]), (200, True))
        r = self.call("patch", f"/events/{occ[3].pk}?scope=following", self.head.user, {"title": "Later"})
        self.assertEqual(r.status_code, 200, r.content)
        new_series_id = r.json()["series_id"]
        self.assertNotEqual(new_series_id, str(series.pk))
        self.assertEqual(set(Event.objects.filter(series_id=new_series_id).values_list("title", flat=True)), {"Later"})
        r = self.call("patch", f"/events/{occ[0].pk}?scope=all", self.head.user, {"title": "All"})
        self.assertEqual(r.status_code, 200)
        titles = {e.pk: e.title for e in Event.objects.filter(pk__in=[o.pk for o in occ])}
        self.assertEqual([titles[o.pk] for o in occ], ["All", "All", "Special", "Later", "Later", "Later"])
        # moving a series to other days goes through its recurrence (scope all only)
        self.assertIn("start", self.call("patch", f"/events/{occ[1].pk}?scope=all", self.head.user, {"start": self.iso(occ[1].start + datetime.timedelta(days=1))}).json()["errors"])
        self.assertEqual(self.call("patch", f"/events/{occ[1].pk}?scope=this", self.head.user, {"recurrence": {"frequency": "daily"}}).status_code, 422)
        self.assertEqual(self.call("patch", f"/events/{occ[1].pk}?scope=all", self.bcoach.user, {"title": "X"}).status_code, 403)
        r = self.call("patch", f"/events/{occ[1].pk}?scope=all", self.head.user, {"recurrence": {"frequency": "weekly", "weekdays": [weekday], "count": 1}})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["series"]["recurrence"]["count"], 1)

        self.assertEqual(self.call("delete", f"/events/{occ[4].pk}?scope=this", self.head.user).status_code, 204)
        self.assertFalse(Event.objects.filter(pk=occ[4].pk).exists())
        self.assertEqual(self.call("delete", f"/events/{occ[3].pk}?scope=all", self.head.user).status_code, 204)
        self.assertFalse(EventSeries.objects.filter(pk=new_series_id).exists())
        self.assertEqual(self.call("delete", f"/events/{occ[2].pk}?scope=nope", self.head.user).status_code, 422)

    # -- notifications --

    def test_notifications_on_create_cancel_and_reschedule(self):
        with self.captureOnCommitCallbacks(execute=True):
            r = self.call("post", "/events", self.head.user, self.body(title="Drills", location_id=str(self.location.pk)))
        event_id = r.json()["id"]
        self.assertEqual(self.inbox(self.p1), ["New event: Drills"])
        self.assertEqual(self.inbox(self.head), [])  # never the actor
        self.assertEqual(self.inbox(self.assistant), [])  # staff are not part of the audience
        self.assertEqual(self.inbox(self.stranger), [])
        item = InboxNotification.objects.get(user=self.p1.user, title="New event: Drills")
        self.assertEqual((item.href, item.club_id), (f"/calendar/events/{event_id}", self.club.pk))
        self.assertIn("Sporthal", item.body)
        steps = [
            ("post", f"/events/{event_id}/cancel", None),
            ("post", f"/events/{event_id}/cancel", None),  # already cancelled: nothing new
            ("post", f"/events/{event_id}/uncancel", None),
            ("patch", f"/events/{event_id}", {"start": self.iso(timezone.now() + datetime.timedelta(days=6))}),
            ("patch", f"/events/{event_id}", {"location_id": None}),  # not a change people are told about
            ("post", f"/events/{event_id}/cancel?notify=false", None),
        ]
        for method, path, data in steps:
            with self.captureOnCommitCallbacks(execute=True):
                self.assertEqual(self.call(method, path, self.head.user, data).status_code, 200, path)
        self.assertEqual(sorted(self.inbox(self.p1)), sorted(["New event: Drills", "Cancelled: Drills", "Back on: Drills", "Rescheduled: Drills"]))

        InboxNotification.objects.all().delete()
        start = timezone.localtime(timezone.now() + datetime.timedelta(days=2))
        from events.services.recurrence import WEEKDAY_CODES

        with self.captureOnCommitCallbacks(execute=True):
            r = self.call("post", "/events", self.head.user, self.body(title="Weekly", start=self.iso(start), end=None, recurrence={"frequency": "weekly", "weekdays": [WEEKDAY_CODES[start.weekday()]], "count": 5}))
            self.assertEqual(r.status_code, 201, r.content)
            self.call("post", "/events", self.head.user, self.body(title="Quiet", notify=False))
            self.call("post", "/events", self.head.user, self.body(title="Past", start=self.iso(timezone.now() - datetime.timedelta(days=2)), end=None))
        self.assertEqual(self.inbox(self.p1), ["New recurring event: Weekly"])  # one summary, not five

    def test_notification_service(self):
        past = self.event(days=-2, teams=[self.team_a])
        self.assertEqual(notices.notify_event_created(past), 0)
        cancelled = self.event(teams=[self.team_a], cancelled=True)
        self.assertEqual(notices.notify_event_created(cancelled), 0)
        e = self.event(teams=[self.team_a], title="Run")
        self.assertEqual(notices.notify_event_created(e, actor=self.p1.user), 0)  # never the actor
        self.assertEqual(notices.notify_event_created(e), 1)  # Ann; Bob and Cas have no login
        self.assertEqual(notices.notify_event_changed(e, "rescheduled", series_wide=True), 1)
        self.assertIn("later occurrences", InboxNotification.objects.filter(user=self.p1.user, title="Rescheduled: Run").get().body)
        with self.assertRaises(ValueError):
            notices.notify_event_changed(e, "exploded")

    def test_event_invitation_reaches_other_guardian_for_child_without_login(self):
        family = Family.objects.create(name="Family")
        FamilyMembership.objects.create(family=family, member=self.head, role=FamilyMembership.FamilyRole.PARENT)
        FamilyMembership.objects.create(family=family, member=self.bcoach, role=FamilyMembership.FamilyRole.GUARDIAN)
        FamilyMembership.objects.create(family=family, member=self.p2, role=FamilyMembership.FamilyRole.CHILD)

        with self.captureOnCommitCallbacks(execute=True):
            response = self.call("post", "/events", self.head.user, self.body(title="Family practice"))
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(self.inbox(self.head), [])
        self.assertEqual(self.inbox(self.bcoach), ["For Bob: New event: Family practice"])

    # -- attendance --

    def test_coach_note_alone_keeps_the_answer(self):
        e = self.event(teams=[self.team_a])
        self.call("put", f"/events/{e.pk}/attendance/{self.p1.pk}", self.head.user, {"status": "maybe"})
        r = self.call("put", f"/events/{e.pk}/attendance/{self.p1.pk}", self.head.user, {"coach_note": "Knee"})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual((r.json()["status"], r.json()["coach_note"]), ("maybe", "Knee"))
        self.assertEqual(self.call("put", f"/events/{e.pk}/attendance/{self.p1.pk}", self.head.user, {}).status_code, 422)  # neither

    def test_remind_nudges_those_without_an_answer(self):
        e = self.event(teams=[self.team_a])
        self.call("put", f"/events/{e.pk}/attendance/{self.p1.pk}", self.head.user, {"status": "present"})
        r = self.call("post", f"/events/{e.pk}/attendance/remind", self.head.user)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json(), {"reminded": Attendance.objects.filter(event=e, status="no_response").count()})
        self.assertGreater(r.json()["reminded"], 0)
        self.assertEqual(self.call("post", f"/events/{e.pk}/attendance/remind", self.bcoach.user).status_code, 403)

    def test_coach_home_counts_and_rights(self):
        e = self.event(teams=[self.team_a])
        self.call("put", f"/events/{e.pk}/attendance/{self.p1.pk}", self.head.user, {"status": "maybe"})
        r = Client().get("/api/v1/portal/coach", HTTP_HOST=self.host, HTTP_X_SESSION_TOKEN=login_session(self.head.user))
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        row = next(x for x in body["events"] if x["id"] == str(e.pk))
        self.assertEqual((row["maybe"], row["has_game_sheet"]), (1, False))
        self.assertEqual(row["invited"], Attendance.objects.filter(event=e).count())
        self.assertTrue(body["can_create_events"])

    def test_attendance_history_and_answer_name(self):
        e = self.event(teams=[self.team_a])
        url = f"/events/{e.pk}/attendance/{self.p1.pk}"
        self.call("put", url, self.head.user, {"status": "absent"})
        self.call("put", url, self.head.user, {"status": "absent", "coach_note": "x"})  # unchanged: not logged
        self.call("put", url, self.head.user, {"status": "present"})
        r = self.call("get", url + "/history", self.stranger.user)  # everyone can look
        self.assertEqual(r.status_code, 200, r.content)
        rows = r.json()
        self.assertEqual([(c["from_status"], c["to_status"], c["via"], c["changed_by_name"]) for c in rows], [("absent", "present", "coach", "Hank Head"), ("no_response", "absent", "coach", "Hank Head")])
        listing = {row["member"]["id"]: row for row in self.call("get", f"/events/{e.pk}/attendance", self.assistant.user).json()["rows"]}[str(self.p1.pk)]
        self.assertEqual((listing["answered_by_name"], listing["answered_via"], listing["answered_by_coach"]), ("Hank Head", "coach", True))
        self.call("put", url, self.head.user, {"status": "no_response"})
        listing = {row["member"]["id"]: row for row in self.call("get", f"/events/{e.pk}/attendance", self.assistant.user).json()["rows"]}[str(self.p1.pk)]
        self.assertEqual((listing["answered_by_name"], listing["answered_via"]), (None, None))
        self.assertEqual(self.call("get", f"/events/{e.pk}/attendance/{self.p2.pk}/history", self.head.user).json(), [])

    def test_options_carry_filters_and_addresses_and_me_the_member_id(self):
        r = self.call("get", "/events/options", self.head.user).json()
        self.assertTrue(len(r["filter_teams"]) >= len(r["teams"]) >= 1)
        self.assertTrue(all("address" in loc and "is_home" in loc for loc in r["locations"]))
        self.assertTrue(all("short_name" in t for t in r["teams"] + r["filter_teams"]))
        me = self.call("get", "/me", self.head.user).json()
        self.assertEqual(me["member_id"], str(self.head.pk))

    def test_attendance_read_and_coach_writes(self):
        e = self.event(teams=[self.team_a])
        r = self.call("put", f"/events/{e.pk}/attendance/{self.p1.pk}", self.head.user, {"status": "absent", "coach_note": "Secret injury"})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual((r.json()["status"], r.json()["coach_note"], r.json()["answered_by_coach"]), ("absent", "Secret injury", True))
        for user, manager in ((self.head.user, True), (self.owner, True), (self.assistant.user, False), (self.bcoach.user, False), (self.stranger.user, False)):
            with self.subTest(user=user.email):
                r = self.call("get", f"/events/{e.pk}/attendance", user)
                self.assertEqual(r.status_code, 200)
                self.assertEqual(r.json()["can_manage"], manager)
                self.assertEqual("Secret injury" in r.content.decode(), manager)
                rows = {row["member"]["id"]: row for row in r.json()["rows"]}
                self.assertEqual(rows[str(self.p1.pk)]["coach_note"], "Secret injury" if manager else None)
        body = self.call("get", f"/events/{e.pk}/attendance", self.head.user).json()
        rows = {row["member"]["id"]: row for row in body["rows"]}
        self.assertEqual((rows[str(self.p2.pk)]["position"], rows[str(self.p2.pk)]["jersey_number"], rows[str(self.p2.pk)]["team_id"]), ("Forward", 2, str(self.team_a.pk)))
        self.assertEqual((body["counts"]["absent"], body["counts"]["no_response"], body["counts"]["total"]), (1, 2, 3))

        for user in (self.assistant.user, self.bcoach.user, self.grouper.user):
            self.assertEqual(self.call("put", f"/events/{e.pk}/attendance/{self.p2.pk}", user, {"status": "present"}).status_code, 403)
            self.assertEqual(self.call("put", f"/events/{e.pk}/attendance/{self.p2.pk}/check-in", user, {"showed_up": True}).status_code, 403)
            self.assertEqual(self.call("post", f"/events/{e.pk}/attendance/bulk", user, {"member_ids": [str(self.p2.pk)], "status": "present"}).status_code, 403)
        self.assertEqual(self.call("put", f"/events/{e.pk}/attendance/{self.p2.pk}", self.head.user, {"status": "bogus"}).status_code, 422)
        self.assertEqual(self.call("put", f"/events/{e.pk}/attendance/{self.stranger.pk}", self.head.user, {"status": "present"}).status_code, 404)

        r = self.call("put", f"/events/{e.pk}/attendance/{self.p2.pk}/check-in", self.head.user, {"showed_up": True})
        self.assertEqual((r.status_code, r.json()["showed_up"]), (200, True))
        self.assertIsNone(self.call("put", f"/events/{e.pk}/attendance/{self.p2.pk}/check-in", self.head.user, {"showed_up": None}).json()["showed_up"])

        r = self.call("post", f"/events/{e.pk}/attendance/bulk", self.head.user, {"member_ids": [str(self.p1.pk), str(self.p2.pk), str(self.p3.pk), str(self.stranger.pk)], "status": "present"})
        self.assertEqual(r.status_code, 200)
        results = {x["member_id"]: x for x in r.json()["results"]}
        self.assertEqual([results[str(m.pk)]["ok"] for m in (self.p1, self.p2, self.p3, self.stranger)], [True, True, True, False])
        self.assertEqual(set(Attendance.objects.filter(event=e, member__in=[self.p1, self.p2, self.p3]).values_list("status", flat=True)), {"present"})
        self.assertEqual(self.call("post", f"/events/{e.pk}/attendance/bulk", self.head.user, {"member_ids": [], "status": "nope"}).status_code, 422)

    def test_attendance_of_a_past_event_without_rows(self):
        e = self.event(days=-3, teams=[self.team_a])  # created after the fact: no rows were synced
        self.assertFalse(Attendance.objects.filter(event=e).exists())
        rows = self.call("get", f"/events/{e.pk}/attendance", self.head.user).json()["rows"]
        self.assertEqual(len(rows), 3)
        r = self.call("put", f"/events/{e.pk}/attendance/{self.p3.pk}", self.head.user, {"status": "present"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Attendance.objects.get(event=e, member=self.p3).status, "present")

    # -- line-up --

    def test_lineup_members_carry_note_and_turnout_for_managers(self):
        game = self.event(teams=[self.team_a], kind=Event.EventKind.GAME, title="U12 vs Rivals")
        self.call("put", f"/events/{game.pk}/attendance/{self.p1.pk}", self.head.user, {"status": "absent", "coach_note": ""})
        Attendance.objects.filter(event=game, member=self.p1).update(note="Sick")
        # Three past games (present, present, absent) and one past practice for p1: games 67%, practices too little data.
        for n, status in enumerate(("present", "present", "absent"), start=1):
            past = self.event(days=-n, teams=[self.team_a], kind=Event.EventKind.GAME, title=f"Old game {n}")
            Attendance.objects.update_or_create(event=past, member=self.p1, defaults={"status": status})
        practice = self.event(days=-5, teams=[self.team_a], kind=Event.EventKind.TRAINING, title="Practice")
        Attendance.objects.update_or_create(event=practice, member=self.p1, defaults={"status": "present"})
        rows = {m["member"]["id"]: m for m in self.call("get", f"/events/{game.pk}/lineup", self.head.user).json()["members"]}
        mine = rows[str(self.p1.pk)]
        self.assertEqual((mine["note"], mine["games_rate"], mine["practices_rate"]), ("Sick", 67, None))

    def test_lineup_flow(self):
        game = self.event(teams=[self.team_a], kind=Event.EventKind.GAME, title="U12 vs Rivals")
        r = self.call("get", f"/events/{game.pk}/lineup", self.head.user)
        self.assertEqual(r.status_code, 200)
        self.assertEqual((r.json()["available"], r.json()["status"], r.json()["team"]["name"]), (True, "none", "U12"))
        self.assertEqual([m["available"] for m in r.json()["members"]], [False, False, False])
        for p in (self.p1, self.p2):
            self.call("put", f"/events/{game.pk}/attendance/{p.pk}", self.head.user, {"status": "present"})
        self.assertEqual(self.call("put", f"/events/{game.pk}/lineup", self.head.user, {"member_ids": [str(self.p3.pk)]}).status_code, 422)  # not available
        self.assertEqual(self.call("put", f"/events/{game.pk}/lineup", self.assistant.user, {"member_ids": [str(self.p1.pk)]}).status_code, 403)
        self.assertEqual(self.call("post", f"/events/{game.pk}/lineup/publish", self.head.user).status_code, 422)  # nothing picked yet
        r = self.call("put", f"/events/{game.pk}/lineup", self.head.user, {"member_ids": [str(self.p1.pk)]})
        self.assertEqual((r.status_code, r.json()["status"]), (200, "draft"))
        picks = lambda user: {m["member"]["id"] for m in self.call("get", f"/events/{game.pk}/lineup", user).json()["members"] if m["selected"]}  # noqa: E731
        self.assertEqual(picks(self.head.user), {str(self.p1.pk)})
        self.assertEqual(picks(self.assistant.user), set())  # a draft is for the managers
        when = timezone.now() + datetime.timedelta(days=1)
        r = self.call("post", f"/events/{game.pk}/lineup/schedule", self.head.user, {"when": self.iso(when)})
        self.assertEqual((r.status_code, r.json()["status"]), (200, "scheduled"))
        self.assertEqual(self.call("post", f"/events/{game.pk}/lineup/schedule", self.head.user, {"when": self.iso(timezone.now() - datetime.timedelta(hours=1))}).status_code, 422)
        self.assertEqual(self.call("post", f"/events/{game.pk}/lineup/schedule", self.assistant.user, {"when": self.iso(when)}).status_code, 403)
        self.assertEqual(self.call("delete", f"/events/{game.pk}/lineup/schedule", self.head.user).json()["status"], "draft")
        r = self.call("post", f"/events/{game.pk}/lineup/publish", self.head.user)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual((r.json()["newly_selected"], r.json()["dropped"], r.json()["lineup"]["status"]), ([str(self.p1.pk)], [], "published"))
        self.assertEqual(dict(Attendance.objects.filter(event=game, member__in=[self.p1, self.p2]).values_list("member_id", "status")), {self.p1.pk: "selected", self.p2.pk: "not_selected"})
        self.assertEqual(picks(self.assistant.user), {str(self.p1.pk)})
        self.assertIn("You're in the line-up for U12 vs Rivals", self.inbox(self.p1))
        self.assertEqual(self.call("get", f"/events/{game.pk}", self.assistant.user).json()["lineup"]["status"], "published")
        attendance = self.call("get", f"/events/{game.pk}/attendance", self.assistant.user).json()
        self.assertEqual({r["member"]["id"] for r in attendance["rows"] if r["selected"]}, {str(self.p1.pk)})

        practice = self.event(teams=[self.team_a])
        self.assertFalse(self.call("get", f"/events/{practice.pk}/lineup", self.head.user).json()["available"])
        self.assertEqual(self.call("put", f"/events/{practice.pk}/lineup", self.head.user, {"member_ids": []}).status_code, 422)
        both = self.event(teams=[self.team_a, self.team_b], kind=Event.EventKind.GAME, title="Double")
        self.assertIn("team_id", self.call("put", f"/events/{both.pk}/lineup", self.head.user, {"member_ids": []}).json()["errors"])
        self.assertEqual(self.call("put", f"/events/{both.pk}/lineup", self.head.user, {"team_id": str(self.team_a.pk), "member_ids": []}).status_code, 200)
        self.assertTrue(Lineup.objects.filter(event=both, team=self.team_a).exists())

    # -- tasks --

    def test_tasks(self):
        e = self.event(teams=[self.team_a])
        self.assertEqual(self.call("post", f"/events/{e.pk}/tasks", self.assistant.user, {"title": "Fruit"}).status_code, 403)
        self.assertEqual(self.call("post", f"/events/{e.pk}/tasks", self.head.user, {"title": ""}).status_code, 422)
        r = self.call("post", f"/events/{e.pk}/tasks", self.head.user, {"title": "Fruit", "needed_quantity": 1})
        self.assertEqual(r.status_code, 201, r.content)
        task_id = r.json()["id"]
        r = self.call("post", f"/events/{e.pk}/tasks/{task_id}/claim", self.head.user, {"member_id": str(self.p1.pk)})
        self.assertEqual((r.status_code, r.json()["claimed_count"], r.json()["open_slots"]), (200, 1, 0))
        self.assertEqual(r.json()["claims"][0]["member_id"], str(self.p1.pk))
        listed = self.call("get", f"/events/{e.pk}/tasks", self.assistant.user).json()
        self.assertFalse(listed["can_manage"])
        self.assertEqual((listed["tasks"][0]["claims"][0]["label"], listed["tasks"][0]["claims"][0]["member_id"]), ("Ann Alpha", None))
        self.assertEqual(self.call("post", f"/events/{e.pk}/tasks/{task_id}/claim", self.assistant.user).status_code, 422)  # full (no body: for yourself)
        self.assertEqual(self.call("post", f"/events/{e.pk}/tasks/{task_id}/claim", self.assistant.user, {"member_id": str(self.p2.pk)}).status_code, 403)
        self.assertEqual(self.call("patch", f"/events/{e.pk}/tasks/{task_id}", self.head.user, {"needed_quantity": 2}).json()["open_slots"], 1)
        r = self.call("post", f"/events/{e.pk}/tasks/{task_id}/claim", self.assistant.user, {})  # for themself
        self.assertEqual((r.status_code, r.json()["claimed_by_me"]), (200, True))
        self.assertEqual(self.call("delete", f"/events/{e.pk}/tasks/{task_id}/claim", self.assistant.user).status_code, 204)
        self.assertEqual(self.call("delete", f"/events/{e.pk}/tasks/{task_id}/claim?member_id={self.p1.pk}", self.assistant.user).status_code, 403)
        self.assertEqual(self.call("delete", f"/events/{e.pk}/tasks/{task_id}/claim?member_id={self.p1.pk}", self.head.user).status_code, 204)
        self.assertEqual(self.call("patch", f"/events/{e.pk}/tasks/{task_id}", self.assistant.user, {"title": "X"}).status_code, 403)
        self.assertEqual(self.call("delete", f"/events/{e.pk}/tasks/{task_id}", self.assistant.user).status_code, 403)
        self.assertEqual(self.call("delete", f"/events/{e.pk}/tasks/{task_id}", self.head.user).status_code, 204)
        self.assertFalse(EventTask.objects.filter(pk=task_id).exists())
        other = self.event(teams=[self.team_b])
        self.assertEqual(self.call("delete", f"/events/{other.pk}/tasks/{task_id}", self.owner).status_code, 404)

    # -- attachments --

    def test_attachments(self):
        e = self.event(teams=[self.team_a])
        self.assertEqual(self.upload(f"/events/{e.pk}/attachments", self.assistant.user, [upload()]).status_code, 403)
        self.assertEqual(self.upload(f"/events/{e.pk}/attachments", self.head.user, [upload("x.html", b"<b>")]).status_code, 422)
        r = self.upload(f"/events/{e.pk}/attachments", self.head.user, [upload("a.pdf", b"aaa"), upload("map.png", b"png")])
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual([a["name"] for a in r.json()], ["a.pdf", "map.png"])
        listed = self.call("get", f"/events/{e.pk}/attachments", self.assistant.user).json()
        self.assertEqual((listed["can_manage"], len(listed["attachments"])), (False, 2))
        first = listed["attachments"][0]
        r = self.call("get", first["download_url"].removeprefix(BASE), self.assistant.user)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(b"".join(r.streaming_content), b"aaa")
        self.assertIn("attachment", r["Content-Disposition"])
        self.assertIn("a.pdf", r["Content-Disposition"])
        self.assertEqual((r["Content-Type"], r["X-Content-Type-Options"], r["Cache-Control"]), ("application/octet-stream", "nosniff", "private, no-store"))
        attachment = EventAttachment.objects.get(pk=first["id"])
        os.remove(os.path.join(self._tmp, attachment.file.name))
        self.assertEqual(self.call("get", first["download_url"].removeprefix(BASE), self.assistant.user).status_code, 404)
        self.assertEqual(self.call("delete", f"/events/{e.pk}/attachments/{first['id']}", self.assistant.user).status_code, 403)
        self.assertEqual(self.call("delete", f"/events/{e.pk}/attachments/{first['id']}", self.head.user).status_code, 204)
        self.assertEqual(len(self.call("get", f"/events/{e.pk}/attachments", self.head.user).json()["attachments"]), 1)

    # -- statistics --

    def _history(self):
        """Three past trainings of team A: Ann always present (one no-show), Bob always absent, Cas present twice."""
        events = [self.event(days=-d, teams=[self.team_a], title=f"T{d}") for d in (9, 6, 3)]
        answers = {self.p1: ["present", "present", "present"], self.p2: ["absent", "absent", "absent"], self.p3: ["present", "absent", "present"]}
        for member, statuses in answers.items():
            for event, status in zip(events, statuses, strict=True):
                Attendance.objects.create(event=event, member=member, status=status, coach_note="Hidden remark" if member == self.p1 else "")
        Attendance.objects.filter(event=events[-1], member=self.p1).update(showed_up=False)
        return events

    def test_team_attendance(self):
        self._history()
        r = self.call("get", f"/teams/{self.team_a.pk}/attendance", self.assistant.user)
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual((body["rate"], body["sessions"], body["season"]["id"]), (round(100 * 5 / 9), 3, str(self.season.pk)))
        self.assertEqual([p["name"] for p in body["top_attenders"]], ["Ann Alpha", "Cas Gamma", "Bob Beta"])
        self.assertEqual(body["top_attenders"][0], {"id": str(self.p1.pk), "name": "Ann Alpha", "rate": 100, "responses": 3})
        self.assertEqual(body["needs_attention"], [])  # every ranked player is already among the top 3
        self.assertEqual([p["name"] for p in body["missed_recent_practices"]], ["Bob Beta"])
        self.assertEqual((body["no_show_count"], body["no_shows"][0]["member"]["name"]), (1, "Ann Alpha"))
        self.assertEqual(self.call("get", f"/teams/{self.team_a.pk}/attendance?season=00000000-0000-0000-0000-000000000000", self.owner).status_code, 422)
        empty = self.call("get", f"/teams/{self.team_b.pk}/attendance", self.owner).json()
        self.assertEqual((empty["rate"], empty["sessions"], empty["top_attenders"]), (None, 0, []))

    def test_member_attendance(self):
        events = self._history()
        upcoming = self.event(teams=[self.team_a])
        r = self.call("get", f"/members/{self.p1.pk}/attendance", self.assistant.user)
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual(body["counts"], {"present": 3, "absent": 0, "no_reply": 0, "no_shows": 1, "excused": 0, "maybe": 0, "selected": 0})
        self.assertEqual(body["rate"], 100)
        self.assertEqual([b["id"] for b in body["sparkline"]], [str(e.pk) for e in events] + [str(upcoming.pk)])
        self.assertEqual(body["sparkline"][-1]["state"], "upcoming")
        self.assertEqual([x["event"]["title"] for x in body["recent"]], ["T3", "T6", "T9"])
        self.assertEqual(body["recent"][0]["showed_up"], False)
        self.assertNotIn("Hidden remark", r.content.decode())
        self.assertEqual(self.call("get", "/members/00000000-0000-0000-0000-000000000000/attendance", self.owner).status_code, 404)
        self.assertEqual(self.call("get", f"/members/{self.p1.pk}/attendance?season=00000000-0000-0000-0000-000000000000", self.owner).status_code, 422)

    def test_overview_attendance(self):
        self.assertIsNone(self.call("get", "/overview", self.owner).json()["attendance"])
        self._history()
        Event.objects.create(kind="training", title="Called off", start=timezone.now() - datetime.timedelta(days=1), cancelled=True)
        self.assertEqual(self.call("get", "/overview", self.head.user).json()["attendance"], {"rate": round(100 * 5 / 9), "sessions": 3})

    def test_me_is_unchanged(self):
        keys = set(self.call("get", "/me", self.head.user).json())
        self.assertFalse({k for k in keys if "event" in k or "attendance" in k})
