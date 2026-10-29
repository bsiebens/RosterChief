import datetime
import io
import json

import openpyxl
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile

from api.models import Job
from api.panel.tests.test_members import MembersPanelTestCase
from api.protected.tests import CLUB_HOST
from club.models import ClubMembership, ClubRole, Season
from management.bulk_import import TEMPLATE_COLUMNS
from members.models import Family, FamilyMembership, Member

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
URL = "/manage/members/import"


def workbook(*rows, columns=TEMPLATE_COLUMNS):
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(list(columns))
    for row in rows:
        sheet.append([row.get(column, "") for column in columns])
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def xlsx(*rows, name="members.xlsx", **kwargs):
    return SimpleUploadedFile(name, workbook(*rows, **kwargs), content_type=XLSX)


class ImportTestCase(MembersPanelTestCase):
    def upload(self, token, file, **fields):
        return self.client.post(f"/api/v1{URL}", data={"file": file, **fields}, HTTP_HOST=CLUB_HOST, HTTP_AUTHORIZATION=f"Bearer {token}")

    def start(self, token, file, **fields):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.upload(token, file, **fields)
        return response


class ImportAccessTests(ImportTestCase):
    def test_no_token_is_401(self):
        self.assertError(self.api("get", URL + "/template", host=CLUB_HOST), 401, "unauthenticated")
        self.assertEqual(self.client.post(f"/api/v1{URL}", data={"file": xlsx()}, HTTP_HOST=CLUB_HOST).status_code, 401)

    def test_plain_member_is_403(self):
        token = self.login()
        self.assertError(self.api("get", URL + "/template", token=token, host=CLUB_HOST), 403, "forbidden")
        self.assertError(self.upload(token, xlsx({"first_name": "A", "last_name": "B"})), 403, "forbidden")

    def test_coach_may_download_template_but_not_upload(self):
        from teams.models import Position, StaffAssignment, Team

        team = Team.objects.create(club=self.club, name="U12")
        position = Position.objects.create(club=self.club, name="Assistant", short_name="AC", staff_position=True)
        StaffAssignment.objects.create(team=team, member=self.member, season=self.season, position=position)
        token = self.login()
        response = self.api("get", URL + "/template", token=token, host=CLUB_HOST)
        self.assertEqual(response.status_code, 200)
        self.assertError(self.upload(token, xlsx({"first_name": "A", "last_name": "B"})), 403, "forbidden")


class TemplateTests(ImportTestCase):
    def test_template_is_a_workbook(self):
        token = self.admin()
        response = self.api("get", URL + "/template", token=token, host=CLUB_HOST)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], XLSX)
        self.assertIn("member_import_template.xlsx", response["Content-Disposition"])
        sheet = openpyxl.load_workbook(io.BytesIO(response.content)).active
        self.assertEqual([cell.value for cell in sheet[1]], TEMPLATE_COLUMNS)


class PreviewTests(ImportTestCase):
    def test_dry_run_previews_without_creating(self):
        token = self.admin()
        file = xlsx({"first_name": "Nia", "last_name": "New", "email": "nia@example.com"}, {"first_name": "", "last_name": "Broken"})
        response = self.upload(token, file, dry_run="true")
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual((body["valid_count"], body["skipped_count"]), (1, 1))
        self.assertTrue(body["rows"][0]["valid"])
        self.assertEqual(body["rows"][0]["line_number"], 2)
        self.assertFalse(body["rows"][1]["valid"])
        self.assertTrue(body["rows"][1]["errors"])
        self.assertEqual(body["season"], str(self.season))
        self.assertFalse(Member.objects.filter(first_name="Nia").exists())
        self.assertEqual(Job.objects.count(), 0)

    def test_dry_run_suggests_existing_families(self):
        token = self.admin()
        file = xlsx({"first_name": "Lee", "last_name": "Parent", "family_group": "Parent household", "family_role": "child"})
        body = self.upload(token, file, dry_run="true").json()
        group = body["family_groups"][0]
        self.assertEqual(group["family_group"], "Parent household")
        self.assertEqual(group["candidates"][0]["family_id"], str(Family.objects.get(club=self.club).pk))

    def test_duplicate_email_is_reported(self):
        token = self.admin()
        self.child.email = "kim@example.com"
        self.child.save()
        body = self.upload(token, xlsx({"first_name": "Kim", "last_name": "Two", "email": "kim@example.com"}), dry_run="true").json()
        self.assertFalse(body["rows"][0]["valid"])


class UploadValidationTests(ImportTestCase):
    def test_wrong_extension_is_415(self):
        token = self.admin()
        file = SimpleUploadedFile("members.csv", b"a,b", content_type="text/csv")
        self.assertError(self.upload(token, file), 415, "unsupported_media_type")

    def test_empty_file_is_422(self):
        token = self.admin()
        response = self.upload(token, SimpleUploadedFile("members.xlsx", b"", content_type=XLSX))
        self.assertError(response, 422, "validation_error")
        self.assertIn("file", response.json()["error"]["fields"])

    def test_too_large_is_413(self):
        token = self.admin()
        file = SimpleUploadedFile("members.xlsx", b"x" * (5 * 1024 * 1024 + 1), content_type=XLSX)
        self.assertError(self.upload(token, file), 413, "file_too_large")

    def test_garbage_workbook_is_422(self):
        token = self.admin()
        response = self.upload(token, SimpleUploadedFile("members.xlsx", b"not a workbook", content_type=XLSX))
        self.assertError(response, 422, "validation_error")
        self.assertIn("file", response.json()["error"]["fields"])

    def test_missing_template_columns_is_422(self):
        token = self.admin()
        response = self.upload(token, xlsx({"email": "a@example.com"}, columns=["email"]))
        self.assertError(response, 422, "validation_error")

    def test_missing_file_is_422(self):
        token = self.admin()
        response = self.client.post(f"/api/v1{URL}", data={"dry_run": "true"}, HTTP_HOST=CLUB_HOST, HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(response.status_code, 422)

    def test_bad_family_choices_is_422(self):
        token = self.admin()
        response = self.upload(token, xlsx({"first_name": "A", "last_name": "B"}), family_choices="[1]")
        self.assertError(response, 422, "validation_error")
        self.assertIn("family_choices", response.json()["error"]["fields"])


class ImportJobTests(ImportTestCase):
    def poll(self, token, job_id):
        return self.api("get", f"/manage/jobs/{job_id}", token=token, host=CLUB_HOST)

    def test_import_runs_as_job(self):
        token = self.admin()
        rows = (
            {"first_name": "Nia", "last_name": "New", "email": "nia@example.com"},
            {"first_name": "Bad", "last_name": "", "email": "bad@example.com"},
        )
        response = self.start(token, xlsx(*rows))
        self.assertEqual(response.status_code, 202, response.content)
        job = self.poll(token, response.json()["id"]).json()
        self.assertEqual(job["status"], "succeeded", job)
        self.assertEqual((job["result"]["created"], job["result"]["skipped"], job["result"]["rostered"]), (1, 1, True))
        self.assertEqual(job["result"]["skipped_rows"][0]["line_number"], 3)
        nia = Member.objects.get(first_name="Nia")
        self.assertTrue(ClubMembership.objects.filter(club=self.club, member=nia, season=self.season).exists())

    def test_family_group_creates_family_or_links_chosen(self):
        token = self.admin()
        existing = Family.objects.get(club=self.club)
        rows = (
            {"first_name": "Tay", "last_name": "Doe", "email": "tay@example.com", "family_group": "Doe", "family_role": "parent", "membership_kind": "guardian"},
            {"first_name": "Jam", "last_name": "Doe", "family_group": "Doe", "family_role": "child"},
        )
        response = self.start(token, xlsx(*rows), family_choices=json.dumps({"Doe": "new"}))
        job = self.poll(token, response.json()["id"]).json()
        self.assertEqual(job["result"]["created"], 2, job)
        doe = FamilyMembership.objects.get(member__first_name="Jam").family
        self.assertNotEqual(doe.pk, existing.pk)
        self.assertEqual(FamilyMembership.objects.filter(family=doe).count(), 2)
        self.assertTrue(Member.objects.get(first_name="Tay").user_id)

        rows = ({"first_name": "Zoe", "last_name": "Parent", "family_group": "Parents", "family_role": "child"},)
        job = self.poll(token, self.start(token, xlsx(*rows), family_choices=json.dumps({"Parents": str(existing.pk)})).json()["id"]).json()
        self.assertEqual(job["result"]["created"], 1)
        self.assertTrue(FamilyMembership.objects.filter(family=existing, member__first_name="Zoe").exists())

    def test_foreign_or_garbage_family_choice_makes_a_new_family(self):
        token = self.admin()
        before = Family.objects.count()
        rows = ({"first_name": "Zoe", "last_name": "Parent", "family_group": "G", "family_role": "child"},)
        job = self.poll(token, self.start(token, xlsx(*rows), family_choices=json.dumps({"G": "not-a-uuid"})).json()["id"]).json()
        self.assertEqual(job["result"]["created"], 1)
        self.assertEqual(Family.objects.count(), before + 1)

    def test_member_admin_cannot_set_status_or_fee(self):
        token = self.as_role(ClubRole.Roles.MEMBER_ADMIN)
        rows = ({"first_name": "Nia", "last_name": "New", "status": "active", "fee_status": "paid"},)
        job = self.poll(token, self.start(token, xlsx(*rows)).json()["id"]).json()
        membership = ClubMembership.objects.get(member__first_name="Nia")
        self.assertEqual(job["result"]["created"], 1)
        self.assertNotEqual(membership.fee_status, "paid")

    def test_admin_status_and_fee_columns_apply(self):
        token = self.admin()
        rows = ({"first_name": "Nia", "last_name": "New", "status": "active", "fee_status": "paid"},)
        self.poll(token, self.start(token, xlsx(*rows)).json()["id"])
        membership = ClubMembership.objects.get(member__first_name="Nia")
        self.assertEqual((membership.status, membership.fee_status), ("active", "paid"))

    def test_without_a_season_people_are_created_unrostered(self):
        token = self.admin()
        past = datetime.date.today() - datetime.timedelta(days=500)
        Season.objects.filter(pk=self.season.pk).update(start_date=past, end_date=past + datetime.timedelta(days=10))
        job = self.poll(token, self.start(token, xlsx({"first_name": "Nia", "last_name": "New"})).json()["id"]).json()
        self.assertEqual((job["result"]["created"], job["result"]["rostered"]), (1, False))
        self.assertFalse(ClubMembership.objects.filter(member__first_name="Nia").exists())

    def test_job_belongs_to_its_starter(self):
        token = self.admin()
        job_id = self.start(token, xlsx({"first_name": "Nia", "last_name": "New"})).json()["id"]
        other = get_user_model().objects.create_user(email="other@example.com", password="x")
        Job.objects.filter(pk=job_id).update(user=other)
        self.assertError(self.poll(token, job_id), 404, "not_found")
