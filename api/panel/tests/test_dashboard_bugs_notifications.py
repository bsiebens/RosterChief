import datetime
from decimal import Decimal

from api.panel.tests.base_slice import SliceTestCase
from bugs.models import BugNote, BugReport
from club.models import ClubMembership, ClubRole
from events.models import Event
from news.models import News
from notifications.models import Notification
from teams.models import Team


class PanelBase(SliceTestCase):
    """One TOTP login per test; the role is switched under the cached token."""

    _token = None

    def as_role(self, role):
        ClubRole.objects.update_or_create(club=self.club, member=self.member, defaults={"role": role})
        if self._token is None:
            self._token = self.login_with_totp()
        return self._token


class HomeTests(PanelBase):
    def test_no_token_is_401(self):
        self.assertError(self.api("get", "/manage/home", host="ajax-united.rosterchief.app"), 401, "unauthenticated")

    def test_plain_member_is_403(self):
        token = self.login()
        self.assertError(self.call("get", "/home", token), 403, "forbidden")

    def test_admin_sees_everything(self):
        team = Team.objects.create(club=self.club, name="Ajax First", short_name="AF")
        event = Event.objects.create(club=self.club, title="Practice", start=datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=2))
        event.teams.add(team)
        News.objects.create(club=self.club, title="Hello", status=News.Status.PUBLISHED, published_at=datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(hours=1))
        Notification.objects.create(club=self.club, member=self.member, title="Ping", body="x")
        token = self.admin()
        response = self.call("get", "/home", token)
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertIsNotNone(body["season"])
        self.assertEqual(body["kpis"]["member_count"], ClubMembership.objects.filter(club=self.club, season=self.season, kind="member").count())
        self.assertIsNone(body["kpis"]["missing_documentation_count"])
        self.assertEqual([e["title"] for e in body["upcoming_events"]], ["Practice"])
        self.assertEqual(body["upcoming_events"][0]["teams"][0]["short_name"], "AF")
        self.assertEqual([n["title"] for n in body["news"]], ["Hello"])
        self.assertIn("Shop", [g["title"] for g in body["groups"]])
        self.assertIsNotNone(body["finance"])
        self.assertEqual(Decimal(str(body["finance"]["outstanding"])), Decimal("0"))
        self.assertEqual(len(body["finance"]["aging"]), 3)
        self.assertEqual(body["attention"]["registrations_awaiting_count"], 0)
        self.assertEqual(body["unread_notifications"], 1)
        self.assertIsNone(body["billing"])

    def test_editor_gets_no_financial_sections(self):
        token = self.as_role(ClubRole.Roles.EDITOR)
        response = self.call("get", "/home", token)
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertIsNone(body["finance"])
        self.assertIsNone(body["billing"])
        self.assertIsNone(body["attention"]["registrations_awaiting_count"])
        self.assertNotIn("Shop", [g["title"] for g in body["groups"]])
        self.assertIn("kpis", body)

    def test_other_clubs_data_is_not_counted(self):
        other, _season, _stranger = self.other_club()
        Event.objects.create(club=other, title="Rival practice", start=datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=1))
        News.objects.create(club=other, title="Rival news", status=News.Status.PUBLISHED, published_at=datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(hours=1))
        body = self.call("get", "/home", self.admin()).json()
        self.assertEqual(body["upcoming_events"], [])
        self.assertEqual(body["news"], [])


class BugTests(PanelBase):
    def test_no_token_is_401(self):
        for method in ("get", "post"):
            self.assertError(self.api(method, "/manage/bugs", host="ajax-united.rosterchief.app"), 401, "unauthenticated")

    def test_plain_member_is_403(self):
        token = self.login()
        self.assertError(self.call("get", "/bugs", token), 403, "forbidden")
        self.assertError(self.call("post", "/bugs", token, data={"title": "a", "description": "b"}), 403, "forbidden")

    def test_list_is_own_and_this_club_only(self):
        other, _season, stranger = self.other_club()
        mine = BugReport.objects.create(club=self.club, reported_by=self.member, title="Mine", description="d", priority="high")
        BugNote.objects.create(bug=mine, body="Looking into it")
        BugReport.objects.create(club=other, reported_by=self.member, title="Elsewhere", description="d")
        BugReport.objects.create(club=self.club, reported_by=stranger, title="Someone else's", description="d")
        token = self.as_role(ClubRole.Roles.EDITOR)
        response = self.call("get", "/bugs", token)
        self.assertEqual(response.status_code, 200, response.content)
        items = response.json()["items"]
        self.assertEqual([b["title"] for b in items], ["Mine"])
        self.assertEqual(items[0]["notes"][0]["body"], "Looking into it")
        self.assertNotIn("priority", items[0])
        self.assertNotIn("reported_by", items[0])

    def test_report_files_a_bug_and_is_idempotent(self):
        token = self.admin()
        headers = {"HTTP_IDEMPOTENCY_KEY": "bug-1"}
        first = self.call("post", "/bugs", token, data={"title": "Broken", "description": "It broke"}, **headers)
        self.assertEqual(first.status_code, 201, first.content)
        again = self.call("post", "/bugs", token, data={"title": "Broken", "description": "It broke"}, **headers)
        self.assertEqual(again.status_code, 201)
        self.assertEqual(again.json()["id"], first.json()["id"])
        bug = BugReport.objects.get()
        self.assertEqual((bug.club, bug.reported_by, bug.status), (self.club, self.member, "submitted"))

    def test_report_validation_422(self):
        token = self.admin()
        response = self.call("post", "/bugs", token, data={"title": "", "description": "x"})
        self.assertError(response, 422, "validation_error")
        self.assertIn("title", response.json()["error"]["fields"])
        self.assertFalse(BugReport.objects.exists())


class NotificationTests(PanelBase):
    def make(self, member=None, club=None, **extra):
        return Notification.objects.create(club=club or self.club, member=member or self.member, title=extra.pop("title", "T"), body="b", **extra)

    def test_no_token_is_401(self):
        for method, path in (("get", ""), ("get", "/unread-count"), ("post", "/read-all"), ("post", "/clear-all")):
            self.assertError(self.api(method, "/manage/notifications" + path, host="ajax-united.rosterchief.app"), 401, "unauthenticated")

    def test_plain_member_is_403(self):
        token = self.login()
        for method, path in (("get", ""), ("get", "/unread-count"), ("post", "/read-all"), ("post", "/clear-all")):
            self.assertError(self.call(method, "/notifications" + path, token), 403, "forbidden")

    def test_list_is_own_and_this_club_only(self):
        other, _season, _stranger = self.other_club()
        self.make(title="Mine")
        self.make(title="Child's", member=self.child)
        self.make(title="Elsewhere", club=other, member=self.member)
        token = self.as_role(ClubRole.Roles.EDITOR)
        response = self.call("get", "/notifications", token)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual([n["title"] for n in response.json()["items"]], ["Mine"])
        self.assertEqual(self.call("get", "/notifications/unread-count", token).json(), {"unread": 1})

    def test_pagination(self):
        for index in range(3):
            self.make(title=f"N{index}")
        token = self.admin()
        first = self.call("get", "/notifications?limit=2", token).json()
        self.assertEqual(len(first["items"]), 2)
        second = self.call("get", f"/notifications?limit=2&cursor={first['next_cursor']}", token).json()
        self.assertEqual(len(second["items"]), 1)
        self.assertIsNone(second["next_cursor"])

    def test_read_all_touches_only_own_unread(self):
        other, _season, _stranger = self.other_club()
        self.make()
        self.make()
        theirs = self.make(member=self.child)
        elsewhere = self.make(club=other)
        token = self.admin()
        self.assertEqual(self.call("post", "/notifications/read-all", token).json(), {"count": 2})
        self.assertEqual(self.call("get", "/notifications/unread-count", token).json(), {"unread": 0})
        self.assertEqual(self.call("post", "/notifications/read-all", token).json(), {"count": 0})
        for untouched in (theirs, elsewhere):
            untouched.refresh_from_db()
            self.assertIsNone(untouched.read_at)

    def test_clear_all_deletes_only_own(self):
        other, _season, _stranger = self.other_club()
        self.make()
        self.make()
        theirs = self.make(member=self.child)
        elsewhere = self.make(club=other)
        token = self.admin()
        self.assertEqual(self.call("post", "/notifications/clear-all", token).json(), {"count": 2})
        self.assertEqual(self.call("get", "/notifications", token).json()["items"], [])
        self.assertTrue(Notification.objects.filter(pk__in=[theirs.pk, elsewhere.pk]).count() == 2)
