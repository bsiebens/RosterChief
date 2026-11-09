"""Member import: parsing (CSV / xlsx), preview statuses, commit and permissions."""

import datetime
import io
import json
import uuid
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import Client, TestCase
from django_tenants.utils import schema_context

from authorization.services import assign_role, create_role
from club.models import Club, Domain
from club.services import create_club, grant_login
from controlpanel.tests import login_session, make_user
from events.models import Season
from members.models import AuditEntry, ClubMembership, Family, FamilyMembership, Member
from teams.models import Team, TeamMembership

BASE = "/api/v1/members/import"
CSV = (
    "first_name,last_name,email,phone,date_of_birth,language,team,family,family_role\n"
    "Jane,Doe,jane@imp.test,0470 12 34 56,2014-03-01,nl,U12,Doe,\n"
    "John,Doe,john@imp.test,+32 470 98 76 54,15/06/1980,en,,Doe,parent\n"
    "Existing,Person,exists@imp.test,,,,,,\n"
    "Jane,Again,JANE@imp.test,,,,,,\n"
    "Bad,Row,not-an-email,12,2030-01-01,xx,Nope,,\n"
    ",NoFirst,,,,,,,\n"
)


class ImportTests(TestCase):
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
        cls.owner = make_user("owner@imp.test", first="Olivia", last="Owner")
        public = Club(schema_name="public", name="Platform", owner=cls.owner)
        public.auto_create_schema = False
        public.save()
        Domain.objects.create(domain="testserver", tenant=public, is_primary=True)
        cls.club = create_club("Import FC", cls.owner)
        cls.host = Domain.objects.get(tenant=cls.club, is_primary=True).domain
        today = datetime.date.today()
        with schema_context(cls.club.schema_name):
            cls.season = Season.objects.create(start_date=today - datetime.timedelta(days=30), end_date=today + datetime.timedelta(days=300))
            Team.objects.create(name="U12", short_name="U12")
            Member.objects.create(first_name="Existing", last_name="Person", email="exists@imp.test")
            coach = Member.objects.create(first_name="Coach", last_name="Test", email="coach@imp.test")
        cls.coach_user, _ = grant_login(cls.club, coach)
        with schema_context(cls.club.schema_name):
            assign_role(cls.coach_user, create_role("Evaluator", ["evaluations.execute_evaluations"]))
        cls.owner = type(cls.owner).objects.get(pk=cls.owner.pk)

    def setUp(self):
        door = mock.patch("members.services.access.can_access_management", return_value=True)
        door.start()
        self.addCleanup(door.stop)

    def call(self, method, path, user, data=None, files=None):
        headers = {"HTTP_HOST": self.host, "HTTP_X_SESSION_TOKEN": login_session(user)}
        if files is not None:
            return getattr(Client(), method)(BASE + path, {**(data or {}), **files}, **headers)
        body = {"data": json.dumps(data), "content_type": "application/json"} if data is not None else {}
        return getattr(Client(), method)(BASE + path, **body, **headers)

    def upload(self, content=CSV, name="members.csv", user=None, season=True):
        data = content.encode() if isinstance(content, str) else content
        f = SimpleUploadedFile(name, data)
        return self.call("post", "/preview", user or self.owner, {"season_id": str(self.season.pk)} if season else {}, {"file": f})

    def commit(self, rows, season=True, user=None):
        """POST /commit, run the queued job the way a worker would, and return the finished job."""
        from members.tasks import run_member_import

        body = {"season_id": str(self.season.pk), "rows": rows} if season else {"rows": rows}
        with mock.patch("members.tasks.run_member_import.delay", side_effect=lambda job_id: run_member_import(job_id)), self.captureOnCommitCallbacks(execute=True):
            r = self.call("post", "/commit", user or self.owner, body)
        self.assertEqual(r.status_code, 202, r.content)
        self.assertEqual(r.json()["status"], "queued")
        job = self.call("get", f"/jobs/{r.json()['id']}", user or self.owner).json()
        return job

    # -- preview --

    def test_preview_statuses(self):
        r = self.upload()
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual(body["counts"], {"new": 2, "duplicate": 2, "error": 2})
        rows = {x["line"]: x for x in body["rows"]}
        self.assertEqual(rows[2]["status"], "new")
        self.assertEqual(rows[2]["phone"], "+32470123456")  # Belgian local number normalised
        self.assertEqual(rows[2]["family_role"], "child")  # under 18 and no role given
        self.assertEqual(rows[3]["date_of_birth"], "1980-06-15")
        self.assertEqual((rows[4]["status"], rows[4]["note"]), ("duplicate", "Already a member."))
        self.assertEqual((rows[5]["status"], rows[5]["note"]), ("duplicate", "Appears earlier in this file."))
        self.assertEqual(set(rows[6]["errors"]), {"email", "phone", "date_of_birth", "language", "team"})
        self.assertEqual(set(rows[7]["errors"]), {"first_name"})
        with schema_context(self.club.schema_name):
            self.assertFalse(Member.objects.filter(email="jane@imp.test").exists())  # a preview never saves

    def test_semicolon_csv_and_aliases(self):
        r = self.upload("Voornaam;Achternaam;E-mail\nAnna;Peeters;anna@imp.test\n")
        self.assertEqual(r.json()["counts"]["new"], 1)

    def test_xlsx(self):
        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        ws.append(["first_name", "last_name", "email", "date_of_birth"])
        ws.append(["Xavier", "Excel", "x@imp.test", datetime.date(2012, 5, 4)])
        buf = io.BytesIO()
        wb.save(buf)
        r = self.upload(buf.getvalue(), "members.xlsx")
        self.assertEqual(r.status_code, 200, r.content)
        row = r.json()["rows"][0]
        self.assertEqual((row["status"], row["date_of_birth"]), ("new", "2012-05-04"))

    def test_bad_files(self):
        self.assertEqual(self.upload("a,b\n1,2\n").status_code, 422)  # no name columns
        self.assertEqual(self.upload("first_name,last_name\n").status_code, 422)  # no rows
        self.assertEqual(self.upload("x", "members.pdf").status_code, 422)
        self.assertEqual(self.upload(b"not a zip", "members.xlsx").status_code, 422)

    # -- commit --

    def test_commit_creates_everything_and_skips_the_rest(self):
        rows = self.upload().json()["rows"]
        job = self.commit(rows)
        self.assertEqual((job["status"], job["processed"], job["total"]), ("done", 6, 6))
        self.assertEqual(job["result"]["counts"], {"created": 2, "duplicate": 2, "error": 2})
        with schema_context(self.club.schema_name):
            jane = Member.objects.get(email="jane@imp.test")
            self.assertEqual((jane.language, str(jane.phone)), ("nl", "+32470123456"))
            self.assertTrue(ClubMembership.objects.filter(member=jane, season=self.season).exists())
            self.assertTrue(TeamMembership.objects.filter(member=jane, team__name="U12", season=self.season).exists())
            fam = Family.objects.get(name="Doe")
            self.assertEqual(sorted(FamilyMembership.objects.filter(family=fam).values_list("role", flat=True)), ["child", "parent"])
            self.assertEqual(Member.objects.filter(email__iexact="exists@imp.test").count(), 1)
            self.assertEqual(AuditEntry.objects.filter(action="club.members_imported").count(), 1)
        again = self.commit(rows)
        self.assertEqual(again["result"]["counts"]["created"], 0)  # a second run only finds duplicates
        with schema_context(self.club.schema_name):
            self.assertEqual(Family.objects.filter(name="Doe").count(), 1)

    def test_commit_revalidates(self):
        bad = {"line": 2, "first_name": "Evil", "last_name": "Row", "email": "nope"}
        job = self.commit([bad], season=False)
        self.assertEqual(job["result"]["counts"], {"created": 0, "duplicate": 0, "error": 1})
        self.assertEqual(self.call("post", "/commit", self.owner, {"rows": []}).status_code, 422)  # nothing to import

    def test_the_job_tells_the_starter_and_reports_failures(self):
        from notifications.models import InboxNotification

        job = self.commit(self.upload().json()["rows"])
        self.assertEqual(job["status"], "done")
        note = InboxNotification.objects.filter(user=self.owner, title="Import finished").first()
        self.assertIsNotNone(note)
        self.assertIn("2 members were imported", note.body)
        with mock.patch("members.services.importing.commit", side_effect=RuntimeError("boom")):
            failed = self.commit([{"line": 2, "first_name": "A", "last_name": "B"}], season=False)
        self.assertEqual((failed["status"], failed["error"]), ("failed", "boom"))
        self.assertTrue(InboxNotification.objects.filter(user=self.owner, title="Import failed").exists())

    def test_team_needs_a_season(self):
        r = self.upload(season=False)
        self.assertIn("team", {x["line"]: x for x in r.json()["rows"]}[2]["errors"])

    # -- permissions --

    def test_needs_manage_members(self):
        self.assertEqual(self.upload(user=self.coach_user).status_code, 403)
        self.assertEqual(self.call("post", "/commit", self.coach_user, {"rows": []}).status_code, 403)
        self.assertEqual(self.call("get", f"/jobs/{uuid.uuid4()}", self.coach_user).status_code, 403)
        self.assertEqual(self.call("get", f"/jobs/{uuid.uuid4()}", self.owner).status_code, 404)
        self.assertEqual(self.call("get", "/template", self.coach_user).status_code, 403)
        r = self.call("get", "/template", self.owner)
        self.assertEqual(r.status_code, 200)
        self.assertIn("first_name,last_name", r.content.decode())
