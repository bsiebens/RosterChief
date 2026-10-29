import csv
import datetime
import io
import shutil
import tempfile

from django.core.files.base import ContentFile
from django.test import override_settings
from django.utils import timezone
from waffle import get_waffle_flag_model

from api.protected.tests import CLUB_HOST, AppApiTestCase
from club.models import Club, ClubMembership, ClubRole
from formbuilder.models import Answer, Field, Form, FormSend, Submission
from members.models import Member

FORMS = "/manage/forms"


class FormSubmissionsTestCase(AppApiTestCase):
    def setUp(self):
        super().setUp()
        get_waffle_flag_model().objects.get_or_create(name="formbuilder")[0].clubs.add(self.club)
        self.form = Form.objects.create(club=self.club, title="Camp survey")
        self.name = Field.objects.create(form=self.form, label="Name", field_type="text", order=1)
        self.kind = Field.objects.create(form=self.form, label="Meal", field_type="choice", order=2, options=["Meat", "Veg"])
        self.doc = Field.objects.create(form=self.form, label="Doc", field_type="file", order=3, required=False)
        self.send = FormSend.objects.create(club=self.club, form=self.form, club_wide=True)
        self.s1 = self.submit(self.member, "Pat", "Veg")
        self.s2 = self.submit(self.child, "=Kim", "Meat", by=self.member)
        self._token = None

    def submit(self, member, name, meal, by=None, send=None):
        submission = Submission.objects.create(send=send or self.send, member=member, submitted_by=by or member)
        Answer.objects.create(submission=submission, field=self.name, value=name)
        Answer.objects.create(submission=submission, field=self.kind, value=meal)
        return submission

    def as_role(self, role):
        ClubRole.objects.update_or_create(club=self.club, member=self.member, defaults={"role": role})
        if self._token is None:
            self._token = self.login_with_totp()
        return self._token

    def admin(self):
        return self.as_role(ClubRole.Roles.ADMIN)

    def call(self, method, path, token=None, **kwargs):
        return self.api(method, path, token=token, host=CLUB_HOST, **kwargs)

    def other_club_form(self):
        other = Club.objects.create(name="Rival FC", slug="rival-fc")
        form = Form.objects.create(club=other, title="Rival form")
        send = FormSend.objects.create(club=other, form=form)
        stranger = Member.objects.create(first_name="Sam", last_name="Stranger")
        submission = Submission.objects.create(send=send, member=stranger)
        return form, send, submission

    def endpoints(self, form, send, submission, answer):
        return (
            f"{FORMS}/{form.pk}/submissions",
            f"{FORMS}/{form.pk}/submissions/{submission.pk}",
            f"{FORMS}/{form.pk}/submissions/{submission.pk}/answers/{answer.pk}/file",
            f"{FORMS}/{form.pk}/sends/{send.pk}/report",
            f"{FORMS}/{form.pk}/sends/{send.pk}/pending",
            f"{FORMS}/{form.pk}/sends/{send.pk}/export",
        )


class AccessTests(FormSubmissionsTestCase):
    def paths(self):
        return self.endpoints(self.form, self.send, self.s1, self.s1.answers.first())

    def test_no_token_is_401(self):
        for path in self.paths():
            self.assertError(self.call("get", path), 401, "unauthenticated")

    def test_plain_member_is_403(self):
        token = self.login()
        for path in self.paths():
            self.assertError(self.call("get", path, token), 403, "forbidden")

    def test_member_admin_is_403(self):
        token = self.as_role(ClubRole.Roles.MEMBER_ADMIN)
        for path in self.paths():
            self.assertError(self.call("get", path, token), 403, "forbidden")

    def test_editor_is_allowed(self):
        token = self.as_role(ClubRole.Roles.EDITOR)
        self.assertEqual(self.call("get", f"{FORMS}/{self.form.pk}/submissions", token).status_code, 200)

    def test_feature_off_is_404(self):
        token = self.admin()
        get_waffle_flag_model().objects.get(name="formbuilder").clubs.remove(self.club)
        for path in self.paths():
            self.assertError(self.call("get", path, token), 404, "feature_disabled")

    def test_other_clubs_objects_are_404(self):
        form, send, submission = self.other_club_form()
        token = self.admin()
        for path in self.endpoints(form, send, submission, self.s1.answers.first()):
            self.assertError(self.call("get", path, token), 404, "not_found")

    def test_mismatched_form_is_404(self):
        token = self.admin()
        other = Form.objects.create(club=self.club, title="Other")
        for path in (f"{FORMS}/{other.pk}/submissions/{self.s1.pk}", f"{FORMS}/{other.pk}/sends/{self.send.pk}/report", f"{FORMS}/{other.pk}/sends/{self.send.pk}/export"):
            self.assertError(self.call("get", path, token), 404, "not_found")
        self.assertError(self.call("get", f"{FORMS}/{other.pk}/submissions?send_id={self.send.pk}", token), 404, "not_found")


class ListTests(FormSubmissionsTestCase):
    def list(self, query="", token=None):
        response = self.call("get", f"{FORMS}/{self.form.pk}/submissions{query}", token or self.admin())
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def test_lists_newest_first_with_values(self):
        body = self.list()
        self.assertEqual([row["id"] for row in body["items"]], [str(self.s2.pk), str(self.s1.pk)])
        first = body["items"][0]
        self.assertEqual(first["member"]["name"], "Kim Parent")
        self.assertEqual(first["submitted_by"]["name"], "Pat Parent")
        self.assertIsNone(body["items"][1]["submitted_by"])
        self.assertEqual(first["values"][str(self.name.pk)], "=Kim")
        self.assertIsNone(body["next_cursor"])

    def test_keyset_pagination_walks_every_row_once(self):
        for index in range(4):
            self.submit(self.member, f"n{index}", "Veg")
        seen, cursor = [], ""
        while True:
            body = self.list(f"?limit=2{cursor}")
            seen += [row["id"] for row in body["items"]]
            if not body["next_cursor"]:
                break
            cursor = f"&cursor={body['next_cursor']}"
        self.assertEqual(len(seen), 6)
        self.assertEqual(len(set(seen)), 6)

    def test_bad_cursor_is_400(self):
        self.assertError(self.call("get", f"{FORMS}/{self.form.pk}/submissions?cursor=zzz", self.admin()), 400, "invalid_cursor")

    def test_filters(self):
        second_send = FormSend.objects.create(club=self.club, form=self.form)
        other = self.submit(self.member, "Late", "Veg", send=second_send)
        self.assertEqual(len(self.list()["items"]), 3)
        self.assertEqual([r["id"] for r in self.list(f"?send_id={second_send.pk}")["items"]], [str(other.pk)])
        self.assertEqual({r["id"] for r in self.list(f"?member_id={self.child.pk}")["items"]}, {str(self.s2.pk)})
        self.assertEqual({r["id"] for r in self.list("?q=kim")["items"]}, {str(self.s2.pk)})
        self.assertEqual(self.list("?q=nobody")["items"], [])
        today = timezone.localdate()
        self.assertEqual(len(self.list(f"?submitted_from={today}&submitted_to={today}")["items"]), 3)
        self.assertEqual(self.list(f"?submitted_to={today - datetime.timedelta(days=1)}")["items"], [])
        self.assertEqual(self.list(f"?submitted_from={today + datetime.timedelta(days=1)}")["items"], [])

    def test_bad_date_is_422(self):
        self.assertEqual(self.call("get", f"{FORMS}/{self.form.pk}/submissions?submitted_from=nope", self.admin()).status_code, 422)

    def test_other_clubs_send_filter_is_404(self):
        _, send, _ = self.other_club_form()
        self.assertError(self.call("get", f"{FORMS}/{self.form.pk}/submissions?send_id={send.pk}", self.admin()), 404, "not_found")


class DetailAndFileTests(FormSubmissionsTestCase):
    def setUp(self):
        super().setUp()
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, True)
        override = override_settings(PRIVATE_MEDIA_ROOT=self.media, MEDIA_ROOT=self.media)
        override.enable()
        self.addCleanup(override.disable)

    def test_detail_lists_answers_in_field_order(self):
        body = self.call("get", f"{FORMS}/{self.form.pk}/submissions/{self.s2.pk}", self.admin()).json()
        self.assertEqual([a["label"] for a in body["answers"]], ["Name", "Meal"])
        self.assertEqual(body["answers"][1]["value"], "Meat")
        self.assertEqual(body["submitted_by"]["name"], "Pat Parent")
        self.assertEqual(body["form_id"], str(self.form.pk))
        self.assertIsNone(body["answers"][0]["file_url"])

    def test_file_answer_downloads(self):
        answer = Answer(submission=self.s1, field=self.doc, value={"name": "cert.pdf", "size": 3, "content_type": "application/pdf"})
        answer.file.save("cert.pdf", ContentFile(b"abc"), save=False)
        answer.save()
        token = self.admin()
        detail = self.call("get", f"{FORMS}/{self.form.pk}/submissions/{self.s1.pk}", token).json()
        doc = next(a for a in detail["answers"] if a["label"] == "Doc")
        self.assertEqual(doc["display"], "cert.pdf")
        self.assertEqual(doc["file_url"], f"{FORMS}/{self.form.pk}/submissions/{self.s1.pk}/answers/{answer.pk}/file")
        response = self.call("get", doc["file_url"], token)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"abc")
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertIn("cert.pdf", response["Content-Disposition"])

    def test_answer_without_file_is_404(self):
        answer = self.s1.answers.first()
        self.assertError(self.call("get", f"{FORMS}/{self.form.pk}/submissions/{self.s1.pk}/answers/{answer.pk}/file", self.admin()), 404, "not_found")

    def test_answer_of_another_submission_is_404(self):
        answer = self.s2.answers.first()
        self.assertError(self.call("get", f"{FORMS}/{self.form.pk}/submissions/{self.s1.pk}/answers/{answer.pk}/file", self.admin()), 404, "not_found")


class ReportTests(FormSubmissionsTestCase):
    def setUp(self):
        super().setUp()
        self.lonely = Member.objects.create(first_name="Lo", last_name="Nely")
        ClubMembership.objects.create(club=self.club, member=self.lonely, season=self.season, status=ClubMembership.StatusChoices.ACTIVE)

    def test_report_counts_and_tallies(self):
        body = self.call("get", f"{FORMS}/{self.form.pk}/sends/{self.send.pk}/report", self.admin()).json()
        self.assertEqual(body["title"], "Camp survey")
        self.assertEqual(body["submission_count"], 2)
        self.assertEqual(body["audience_count"], 3)
        self.assertEqual(body["not_yet_count"], 1)
        columns = {c["label"]: c for c in body["columns"]}
        self.assertEqual(columns["Meal"]["summary"], {"Meat": 1, "Veg": 1})
        self.assertIsNone(columns["Name"]["summary"])
        self.assertEqual([c["label"] for c in body["columns"]], ["Name", "Meal", "Doc"])

    def test_pending_lists_members_without_a_response(self):
        body = self.call("get", f"{FORMS}/{self.form.pk}/sends/{self.send.pk}/pending", self.admin()).json()
        names = {item["name"] for item in body["items"]}
        self.assertNotIn("Pat Parent", names)
        self.assertNotIn("Kim Parent", names)
        self.assertEqual(names, {"Lo Nely"})
        self.assertEqual(body["total"], len(body["items"]))

    def test_export_is_csv_with_neutralised_formulas(self):
        response = self.call("get", f"{FORMS}/{self.form.pk}/sends/{self.send.pk}/export", self.admin())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")
        self.assertIn("camp-survey-responses.csv", response["Content-Disposition"])
        rows = list(csv.reader(io.StringIO(response.content.decode())))
        self.assertEqual(rows[0][3:], ["Name", "Meal", "Doc"])
        self.assertEqual(len(rows), 3)
        kim = next(row for row in rows[1:] if row[1] == "Kim Parent")
        self.assertEqual(kim[2], "Pat Parent")
        self.assertEqual(kim[3], "'=Kim")
