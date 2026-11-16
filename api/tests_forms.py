"""Forms API (``/forms``, ``/form-sends``, ``/form-submissions``, ``/form-answers``)."""

import datetime
import json
import shutil
import tempfile
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from django_tenants.utils import schema_context

from authorization.services import assign_role, create_role
from controlpanel.tests import login_session
from formbuilder.models import Answer, Form
from formbuilder.services.submission import submit_form
from news.tests import NewsClubMixin

BASE = "/api/v1"
PRIVATE = tempfile.mkdtemp()


@override_settings(PRIVATE_MEDIA_ROOT=PRIVATE)
class FormsApiTests(NewsClubMixin, TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(PRIVATE, ignore_errors=True)

    def setUp(self):
        super().setUp()
        door = mock.patch("members.services.access.can_access_management", return_value=True)
        door.start()
        self.addCleanup(door.stop)

    def call(self, method, path, user=None, data=None):
        headers = {"HTTP_HOST": self.host, "HTTP_X_SESSION_TOKEN": login_session(user or self.owner)}
        body = {"data": json.dumps(data), "content_type": "application/json"} if data is not None else {}
        return getattr(Client(), method)(BASE + path, **body, **headers)

    def form(self, title="Survey", fields=None, **extra):
        r = self.call("post", "/forms", data={"title": title, **extra})
        self.assertEqual(r.status_code, 201, r.content)
        fid = r.json()["id"]
        if fields is not None:
            r = self.call("put", f"/forms/{fid}/fields", data={"fields": fields})
            self.assertEqual(r.status_code, 200, r.content)
        return self.call("get", f"/forms/{fid}").json()

    def send(self, form_id, **extra):
        with mock.patch("formbuilder.services.notifications.dispatch_send_notification"):
            return self.call("post", f"/forms/{form_id}/sends", data={"team_ids": [str(self.team.pk)], **extra})

    # --- permission ---------------------------------------------------------------------------------------------

    def test_permission(self):
        pk = self.club.pk
        paths = [("get", "/forms"), ("post", "/forms"), ("get", "/form-sends"), ("get", f"/forms/{pk}"), ("delete", f"/form-submissions/{pk}"), ("get", f"/form-answers/{pk}/file")]
        for method, path in paths:
            self.assertEqual(self.call(method, path, self.bob_user, data={"title": "x"} if method == "post" else None).status_code, 403, path)
        with schema_context(self.club.schema_name):
            assign_role(self.dan_user, create_role("Formers", ["forms.manage_forms"], slug="formers"))
        self.assertEqual(self.call("get", "/forms", self.dan_user).status_code, 200)
        me = lambda user: self.call("get", "/me", user).json()["can_manage_forms"]  # noqa: E731
        self.assertEqual((me(self.owner), me(self.dan_user), me(self.bob_user)), (True, True, False))

    def test_unknown_ids_404(self):
        missing = "00000000-0000-0000-0000-000000000000"
        calls = [("get", f"/forms/{missing}"), ("delete", f"/forms/{missing}"), ("get", f"/form-sends/{missing}"), ("get", f"/form-sends/{missing}/report")]
        calls += [("delete", f"/form-submissions/{missing}"), ("get", f"/form-answers/{missing}/file")]
        for method, path in calls:
            self.assertEqual(self.call(method, path).status_code, 404, path)

    # --- forms and fields ---------------------------------------------------------------------------------------

    def test_form_crud_and_list(self):
        a, b = self.form("Alpha"), self.form("Beta", description="second")
        self.assertEqual((a["slug"], a["field_count"], a["send_count"], a["response_count"], a["fields"]), ("alpha", 0, 0, 0, []))
        self.assertEqual(self.call("post", "/forms", data={"title": " "}).status_code, 422)
        r = self.call("patch", f"/forms/{b['id']}", data={"is_active": False, "login_required": True}).json()
        self.assertEqual((r["is_active"], r["login_required"], r["title"]), (False, True, "Beta"))
        titles = lambda q: [f["title"] for f in self.call("get", "/forms" + q).json()["results"]]  # noqa: E731
        self.assertEqual(titles(""), ["Alpha", "Beta"])
        self.assertEqual(titles("?active=false"), ["Beta"])
        self.assertEqual(titles("?q=second"), ["Beta"])
        self.assertEqual(self.call("get", "/forms?limit=1").json()["count"], 2)
        self.assertEqual(self.call("delete", f"/forms/{a['id']}").status_code, 204)

    def test_put_fields(self):
        form = self.form(fields=[{"label": "Name", "field_type": "text"}, {"label": "Size", "field_type": "choice", "options": ["S", {"value": "M", "label": "Medium"}]}])
        name, size = form["fields"]
        self.assertEqual((name["key"], name["order"], size["order"], size["options"], size["required"]), ("name", 0, 1, ["S", {"value": "M", "label": "Medium"}], True))
        # reorder, edit, add; key stays stable
        r = self.call(
            "put",
            f"/forms/{form['id']}/fields",
            data={
                "fields": [
                    {"id": size["id"], "key": "hacked", "label": "T-shirt", "field_type": "choice", "options": ["S", "M", "L"]},
                    {"id": name["id"], "label": "Full name", "field_type": "text", "required": False},
                    {"label": "Born", "field_type": "date", "key": "dob"},
                ]
            },
        ).json()
        self.assertEqual([(f["label"], f["key"], f["order"]) for f in r["fields"]], [("T-shirt", "size", 0), ("Full name", "name", 1), ("Born", "dob", 2)])
        self.assertEqual(r["field_count"], 3)
        # omitted without answers: deleted
        r = self.call("put", f"/forms/{form['id']}/fields", data={"fields": [{"id": name["id"], "label": "Full name", "field_type": "text"}]}).json()
        self.assertEqual([f["label"] for f in r["fields"]], ["Full name"])

    def test_put_fields_with_answers_deactivates(self):
        form = self.form(fields=[{"label": "Name", "field_type": "text"}, {"label": "Nick", "field_type": "text", "required": False}])
        send = self.send(form["id"]).json()
        with schema_context(self.club.schema_name):
            from formbuilder.models import FormSend

            submit_form(FormSend.objects.get(pk=send["id"]), self.ann, {"name": "Ann", "nick": "A"})
        name = form["fields"][0]
        r = self.call("put", f"/forms/{form['id']}/fields", data={"fields": [{"id": name["id"], "label": "Name", "field_type": "text"}]}).json()
        self.assertEqual([(f["label"], f["is_active"], f["order"]) for f in r["fields"]], [("Name", True, 0), ("Nick", False, 1)])
        self.assertEqual(r["field_count"], 1)
        # type change refused once answered
        r = self.call("put", f"/forms/{form['id']}/fields", data={"fields": [{"id": name["id"], "label": "Name", "field_type": "number"}]})
        self.assertEqual(r.status_code, 422)

    def test_put_fields_validation(self):
        form = self.form()
        put = lambda fields: self.call("put", f"/forms/{form['id']}/fields", data={"fields": fields})  # noqa: E731
        for bad in (
            [{"label": " ", "field_type": "text"}],
            [{"label": "x", "field_type": "choice"}],
            [{"label": "x", "field_type": "choice", "options": []}],
            [{"label": "x", "field_type": "text", "options": ["a"]}],
            [{"label": "x", "field_type": "choice", "options": ["a", "a"]}],
            [{"label": "x", "field_type": "choice", "options": [{"value": "a"}, "a"]}],
            [{"label": "x", "field_type": "weird"}],
            [{"label": "x", "field_type": "text", "id": "00000000-0000-0000-0000-000000000000"}],
            [{"label": "x", "field_type": "text", "key": "k"}, {"label": "y", "field_type": "text", "key": "k"}],
        ):
            self.assertEqual(put(bad).status_code, 422, bad)
        self.assertEqual(put([{"label": "x", "field_type": "multichoice", "options": ["a"]}]).status_code, 200)

    def test_duplicate_and_delete_with_responses(self):
        form = self.form("Orig", fields=[{"label": "Name", "field_type": "text"}], login_required=True)
        copy = self.call("post", f"/forms/{form['id']}/duplicate", data={}).json()
        self.assertEqual((copy["title"], copy["slug"], [f["key"] for f in copy["fields"]], copy["login_required"], copy["send_count"]), ("Orig (copy)", "orig-copy", ["name"], True, 0))
        self.assertNotEqual(copy["fields"][0]["id"], form["fields"][0]["id"])
        self.assertEqual(self.call("post", f"/forms/{form['id']}/duplicate", data={"title": "Mine"}).json()["title"], "Mine")
        send = self.send(form["id"]).json()
        with schema_context(self.club.schema_name):
            from formbuilder.models import FormSend

            submit_form(FormSend.objects.get(pk=send["id"]), self.ann, {"name": "Ann"})
        r = self.call("delete", f"/forms/{form['id']}")
        self.assertEqual((r.status_code, r.json()["code"]), (409, "has_responses"))
        r = self.call("delete", f"/form-sends/{send['id']}")
        self.assertEqual((r.status_code, r.json()["code"]), (409, "has_responses"))

    # --- sends --------------------------------------------------------------------------------------------------

    def test_create_send_and_detail(self):
        form = self.form(fields=[{"label": "Name", "field_type": "text"}])
        with mock.patch("formbuilder.services.notifications.dispatch_send_notification") as hook, self.captureOnCommitCallbacks(execute=True):
            r = self.call("post", f"/forms/{form['id']}/sends", data={"team_ids": [str(self.team.pk)], "invited_member_ids": [str(self.bob.pk)], "excluded_member_ids": [str(self.dan.pk)], "max_submissions_per_user": 2})
        self.assertEqual(r.status_code, 201, r.content)
        hook.assert_called_once()
        s = r.json()
        self.assertEqual((s["audience_count"], s["audience_label"], s["status"], s["response_count"], s["club_wide"]), (5, "Under 12 · +1 invited", "open", 0, False))
        self.assertEqual((s["teams"][0]["name"], s["invited_members"][0]["name"], s["excluded_members"][0]["name"], s["created_by"]["name"]), ("Under 12", "Bob Test", "Dan Test", "Olivia Owner"))
        self.assertEqual(s["public_url_path"], f"/f/{s['public_token']}")
        self.assertEqual(self.call("get", f"/form-sends/{s['id']}").json()["max_submissions_per_user"], 2)
        # notify=false
        with mock.patch("formbuilder.services.notifications.dispatch_send_notification") as hook, self.captureOnCommitCallbacks(execute=True):
            self.call("post", f"/forms/{form['id']}/sends", data={"club_wide": True, "notify": False})
        hook.assert_not_called()
        club_wide = next(x for x in self.call("get", f"/forms/{form['id']}/sends").json()["results"] if x["audience_label"] == "Whole club")
        self.assertEqual(club_wide["audience_count"], 5)
        self.assertEqual(self.call("get", "/form-sends").json()["count"], 2)

    def test_send_validation(self):
        form = self.form()
        now = timezone.now()
        post = lambda **kw: self.send(form["id"], **kw).status_code  # noqa: E731
        self.assertEqual(post(club_wide=True), 422)  # with a team
        self.assertEqual(self.call("post", f"/forms/{form['id']}/sends", data={}).status_code, 422)  # nobody
        self.assertEqual(post(opens_at=now.isoformat(), closes_at=(now - datetime.timedelta(hours=1)).isoformat()), 422)
        self.assertEqual(post(team_ids=["00000000-0000-0000-0000-000000000000"]), 422)
        self.assertEqual(post(max_submissions_per_user=0), 422)
        self.assertEqual(self.call("post", f"/forms/{form['id']}/sends", data={"is_public": True}).status_code, 201)  # public needs no audience
        login = self.form("Login", login_required=True)
        self.assertEqual(self.call("post", f"/forms/{login['id']}/sends", data={"club_wide": True, "is_public": True}).status_code, 422)
        self.call("patch", f"/forms/{form['id']}", data={"is_active": False})
        self.assertEqual(post(), 422)
        self.assertEqual(self.call("post", f"/forms/{self.club.pk}/sends", data={"club_wide": True}).status_code, 404)

    def test_statuses_filter_patch_close_reopen(self):
        form = self.form()
        now = timezone.now()
        hour = datetime.timedelta(hours=1)
        open_ = self.send(form["id"]).json()
        scheduled = self.send(form["id"], opens_at=(now + hour).isoformat()).json()
        closed = self.send(form["id"], closes_at=(now + hour).isoformat()).json()
        self.assertEqual((open_["status"], scheduled["status"]), ("open", "scheduled"))
        r = self.call("post", f"/form-sends/{closed['id']}/close").json()
        self.assertEqual(r["status"], "closed")
        self.assertIsNotNone(r["closes_at"])
        ids = lambda status: {s["id"] for s in self.call("get", f"/form-sends?status={status}").json()["results"]}  # noqa: E731
        self.assertEqual(ids("open"), {open_["id"], scheduled["id"]})
        self.assertEqual(ids("closed"), {closed["id"]})
        self.assertEqual(len(ids("all")), 3)
        r = self.call("patch", f"/form-sends/{open_['id']}", data={"is_active": False}).json()
        self.assertEqual(r["status"], "inactive")
        r = self.call("post", f"/form-sends/{open_['id']}/reopen").json()
        self.assertEqual((r["status"], r["closes_at"]), ("open", None))
        r = self.call("post", f"/form-sends/{closed['id']}/reopen").json()
        self.assertEqual(r["status"], "open")
        # patch audience and window; bad combination rejected
        r = self.call("patch", f"/form-sends/{open_['id']}", data={"club_wide": True, "team_ids": [], "max_submissions_per_user": 3}).json()
        self.assertEqual((r["audience_label"], r["max_submissions_per_user"], r["teams"]), ("Whole club", 3, []))
        self.assertEqual(self.call("patch", f"/form-sends/{open_['id']}", data={"team_ids": [str(self.team.pk)]}).status_code, 422)
        self.assertEqual(self.call("patch", f"/form-sends/{open_['id']}", data={"max_submissions_per_user": None}).json()["max_submissions_per_user"], None)
        # reactivating with notify
        self.call("patch", f"/form-sends/{open_['id']}", data={"is_active": False})
        with mock.patch("formbuilder.services.notifications.dispatch_send_notification") as hook, self.captureOnCommitCallbacks(execute=True):
            self.call("patch", f"/form-sends/{open_['id']}", data={"is_active": True, "notify": True})
        hook.assert_called_once()
        self.assertEqual(self.call("delete", f"/form-sends/{scheduled['id']}").status_code, 204)

    # --- responses ----------------------------------------------------------------------------------------------

    def test_report_csv_delete_and_file(self):
        form = self.form("Camp", fields=[{"label": "Name", "field_type": "text"}, {"label": "Size", "field_type": "choice", "options": ["S", "M"]}, {"label": "Doc", "field_type": "file", "required": False}])
        send = self.send(form["id"]).json()
        key = {f["key"]: f["id"] for f in form["fields"]}
        with schema_context(self.club.schema_name):
            from formbuilder.models import FormSend

            obj = FormSend.objects.get(pk=send["id"])
            submit_form(obj, self.ann, {"name": "Ann", "size": "S"}, files={"doc": SimpleUploadedFile("pass.pdf", b"PDFDATA")})
            submit_form(obj, self.kid, {"name": "Kid", "size": "S"}, submitted_by=self.parent)
            answer_id = str(Answer.objects.get(field_id=key["doc"]).pk)
        r = self.call("get", f"/form-sends/{send['id']}/report").json()
        self.assertEqual((r["count"], r["send"]["response_count"], [c["key"] for c in r["columns"]]), (2, 2, ["name", "size", "doc"]))
        self.assertEqual(r["summaries"], {key["size"]: {"S": 2, "M": 0}})
        self.assertEqual(sorted(m["name"] for m in r["not_responded"]), ["Cara Test", "Dan Test", "Kid2 Test"])
        rows = {row["member"]["name"]: row for row in r["rows"]}
        self.assertEqual((rows["Kid Test"]["on_behalf"], rows["Kid Test"]["submitted_by"]["name"], rows["Ann Test"]["on_behalf"]), (True, "Parent Test", False))
        self.assertEqual(rows["Ann Test"]["values"][key["name"]], "Ann")
        self.assertEqual(rows["Ann Test"]["files"][key["doc"]], {"answer_id": answer_id, "name": "pass.pdf"})
        csv = self.call("get", f"/form-sends/{send['id']}/report.csv")
        self.assertIn("text/csv", csv["Content-Type"])
        self.assertTrue(csv.content.startswith("﻿".encode()))
        self.assertIn(b"pass.pdf", csv.content)
        # file download
        f = self.call("get", f"/form-answers/{answer_id}/file")
        self.assertEqual(f.status_code, 200)
        self.assertEqual(b"".join(f.streaming_content), b"PDFDATA")
        self.assertIn("pass.pdf", f["Content-Disposition"])
        self.assertEqual(self.call("get", f"/form-answers/{answer_id}/file", self.bob_user).status_code, 403)
        # delete a submission: row and file go
        self.assertEqual(self.call("delete", f"/form-submissions/{rows['Ann Test']['submission_id']}").status_code, 204)
        self.assertEqual(self.call("get", f"/form-sends/{send['id']}/report").json()["count"], 1)
        self.assertEqual(self.call("get", f"/form-answers/{answer_id}/file").status_code, 404)
        with schema_context(self.club.schema_name):
            self.assertEqual(Form.objects.count(), 1)
