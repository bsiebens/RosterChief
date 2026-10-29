from waffle import get_waffle_flag_model

from api.panel.tests.base_slice import SliceTestCase
from club.models import ClubRole
from formbuilder.models import Answer, Field, Form, FormSend, Submission

URL = "/forms"


class FormsAdminTests(SliceTestCase):
    def setUp(self):
        super().setUp()
        get_waffle_flag_model().objects.get_or_create(name="formbuilder")[0].clubs.add(self.club)
        self._token = None

    def token(self, role=ClubRole.Roles.ADMIN):
        ClubRole.objects.update_or_create(club=self.club, member=self.member, defaults={"role": role})
        if self._token is None:
            self._token = self.login_with_totp()
        return self._token

    def make_form(self, club=None, title="Camp", fields=("Shirt size", "Allergies")):
        form = Form.objects.create(club=club or self.club, title=title)
        for index, label in enumerate(fields):
            Field.objects.create(form=form, label=label, order=index)
        return form

    def test_no_token_is_401(self):
        for method in ("get", "post"):
            self.assertError(self.api(method, "/manage/forms", host="ajax-united.rosterchief.app"), 401, "unauthenticated")

    def test_plain_member_and_non_managers_are_403(self):
        self.assertError(self.call("get", URL, self.login()), 403, "forbidden")
        form = self.make_form()
        token = self.token(ClubRole.Roles.MEMBER_ADMIN)
        for method, path, data in (("get", URL, None), ("post", URL, {"title": "X"}), ("get", f"{URL}/{form.pk}", None), ("delete", f"{URL}/{form.pk}", None)):
            self.assertError(self.call(method, path, token, data=data), 403, "forbidden")

    def test_editor_manages_forms(self):
        token = self.token(ClubRole.Roles.EDITOR)
        self.assertEqual(self.call("post", URL, token, data={"title": "Editor form"}).status_code, 201)
        self.assertEqual(self.call("get", URL, token).status_code, 200)

    def test_feature_off_is_404(self):
        token = self.token()
        form = self.make_form()
        get_waffle_flag_model().objects.get(name="formbuilder").clubs.clear()
        for method, path, data in (("get", URL, None), ("post", URL, {"title": "X"}), ("get", f"{URL}/{form.pk}", None), ("post", f"{URL}/{form.pk}/fields", {"label": "Q"})):
            self.assertError(self.call(method, path, token, data=data), 404, "feature_disabled")

    def test_other_clubs_form_is_404(self):
        other, _season, _stranger = self.other_club()
        form = self.make_form(club=other)
        field = form.fields.first()
        token = self.token()
        for method, path, data in (
            ("get", f"{URL}/{form.pk}", None),
            ("patch", f"{URL}/{form.pk}", {"title": "X"}),
            ("delete", f"{URL}/{form.pk}", None),
            ("post", f"{URL}/{form.pk}/fields", {"label": "Q"}),
            ("patch", f"{URL}/{form.pk}/fields/{field.pk}", {"label": "Q"}),
            ("delete", f"{URL}/{form.pk}/fields/{field.pk}", None),
            ("post", f"{URL}/{form.pk}/fields/reorder", {"field_ids": [str(field.pk)]}),
        ):
            self.assertError(self.call(method, path, token, data=data), 404, "not_found")
        self.assertTrue(Form.objects.filter(pk=form.pk).exists())

    def test_field_of_another_form_is_404(self):
        mine, theirs = self.make_form(title="A"), self.make_form(title="B")
        token = self.token()
        self.assertError(self.call("patch", f"{URL}/{mine.pk}/fields/{theirs.fields.first().pk}", token, data={"label": "Z"}), 404, "not_found")

    def test_create_with_fields_and_detail(self):
        token = self.token()
        payload = {
            "title": "Summer camp",
            "description": "Sign up",
            "login_required": True,
            "fields": [
                {"label": "Name", "field_type": "text"},
                {"label": "Size", "field_type": "choice", "options": ["S", "M"], "required": False, "section": "Kit", "help_text": "Pick one"},
            ],
        }
        created = self.call("post", URL, token, data=payload)
        self.assertEqual(created.status_code, 201, created.content)
        body = created.json()
        self.assertEqual((body["title"], body["slug"], body["login_required"], body["is_active"], body["send_count"]), ("Summer camp", "summer-camp", True, True, 0))
        self.assertEqual([f["key"] for f in body["fields"]], ["name", "size"])
        size = body["fields"][1]
        self.assertEqual((size["options"], size["required"], size["section"], size["help_text"], size["order"]), (["S", "M"], False, "Kit", "Pick one", 1))
        self.assertEqual(Form.objects.get(pk=body["id"]).club, self.club)
        self.assertEqual(self.call("get", f"{URL}/{body['id']}", token).json()["fields"][0]["label"], "Name")

    def test_create_without_fields_and_list(self):
        token = self.token()
        self.assertEqual(self.call("post", URL, token, data={"title": "Empty"}).json()["fields"], [])
        form = self.make_form(title="Archived")
        Form.objects.filter(pk=form.pk).update(is_active=False)
        FormSend.objects.create(club=self.club, form=form)
        other, _season, _stranger = self.other_club()
        self.make_form(club=other, title="Rival")
        listed = self.call("get", URL, token).json()["items"]
        self.assertEqual([(f["title"], f["send_count"]) for f in listed], [("Archived", 1), ("Empty", 0)])
        live = self.call("get", URL + "?is_active=true", token).json()["items"]
        self.assertEqual([f["title"] for f in live], ["Empty"])

    def test_create_validation_422(self):
        token = self.token()
        self.assertError(self.call("post", URL, token, data={"title": ""}), 422, "validation_error")
        choice = self.call("post", URL, token, data={"title": "X", "fields": [{"label": "Size", "field_type": "choice"}]})
        self.assertError(choice, 422, "validation_error")
        self.assertIn("fields.0.options", choice.json()["error"]["fields"])
        twice = self.call("post", URL, token, data={"title": "X", "fields": [{"label": "Age"}, {"label": " age "}]})
        self.assertError(twice, 422, "validation_error")
        self.assertIn("fields.1.label", twice.json()["error"]["fields"])
        self.assertError(self.call("post", URL, token, data={"title": "X", "fields": [{"label": "A", "field_type": "bogus"}]}), 422, "validation_error")
        self.assertFalse(Form.objects.filter(club=self.club).exists())

    def test_create_is_idempotent(self):
        token = self.token()
        first = self.call("post", URL, token, data={"title": "Once"}, HTTP_IDEMPOTENCY_KEY="f-1")
        second = self.call("post", URL, token, data={"title": "Once"}, HTTP_IDEMPOTENCY_KEY="f-1")
        self.assertEqual((first.status_code, second.status_code), (201, 201))
        self.assertEqual(first.json()["id"], second.json()["id"])
        self.assertEqual(Form.objects.filter(title="Once").count(), 1)

    def test_patch_form_archive_and_publish(self):
        form = self.make_form()
        token = self.token()
        patched = self.call("patch", f"{URL}/{form.pk}", token, data={"is_active": False, "description": "Old"})
        self.assertEqual(patched.status_code, 200, patched.content)
        self.assertEqual((patched.json()["is_active"], patched.json()["description"], patched.json()["title"]), (False, "Old", "Camp"))
        self.assertTrue(self.call("patch", f"{URL}/{form.pk}", token, data={"is_active": True, "login_required": True}).json()["is_active"])
        form.refresh_from_db()
        self.assertTrue(form.login_required)
        self.assertError(self.call("patch", f"{URL}/{form.pk}", token, data={"title": ""}), 422, "validation_error")

    def test_delete_form(self):
        free, sent = self.make_form(title="Free"), self.make_form(title="Sent")
        FormSend.objects.create(club=self.club, form=sent)
        token = self.token()
        self.assertEqual(self.call("delete", f"{URL}/{free.pk}", token).status_code, 204)
        self.assertFalse(Form.objects.filter(pk=free.pk).exists())
        self.assertError(self.call("delete", f"{URL}/{sent.pk}", token), 409, "form_in_use")
        self.assertTrue(Form.objects.filter(pk=sent.pk).exists())

    def test_delete_form_backing_a_checklist_is_409(self):
        from evaluations.models import EvaluationChecklist

        form = self.make_form()
        EvaluationChecklist.objects.create(club=self.club, name="U8", form=form)
        self.assertError(self.call("delete", f"{URL}/{form.pk}", self.token()), 409, "form_in_use")

    def test_add_field_appends(self):
        form = self.make_form()
        token = self.token()
        added = self.call("post", f"{URL}/{form.pk}/fields", token, data={"label": "Age", "field_type": "number", "section": "About"}, HTTP_IDEMPOTENCY_KEY="a-1")
        self.assertEqual(added.status_code, 201, added.content)
        body = added.json()
        self.assertEqual((body["key"], body["field_type"], body["section"], body["order"], body["is_active"]), ("age", "number", "About", 2, True))
        again = self.call("post", f"{URL}/{form.pk}/fields", token, data={"label": "Age", "field_type": "number", "section": "About"}, HTTP_IDEMPOTENCY_KEY="a-1")
        self.assertEqual(again.json()["id"], body["id"])
        self.assertEqual(form.fields.count(), 3)
        self.assertError(self.call("post", f"{URL}/{form.pk}/fields", token, data={"label": "Pick", "field_type": "multichoice"}), 422, "validation_error")

    def test_patch_field(self):
        form = self.make_form(fields=())
        field = Field.objects.create(form=form, label="Size", field_type="choice", options=[{"value": "S", "label": "Small"}], order=3)
        token = self.token()
        path = f"{URL}/{form.pk}/fields/{field.pk}"
        patched = self.call("patch", path, token, data={"label": "Shirt size", "required": False, "section": "Kit", "order": 0})
        self.assertEqual(patched.status_code, 200, patched.content)
        body = patched.json()
        self.assertEqual((body["label"], body["required"], body["section"], body["order"], body["key"], body["options"]), ("Shirt size", False, "Kit", 0, "size", ["S"]))
        field.refresh_from_db()
        self.assertEqual(field.options, [{"value": "S", "label": "Small"}])  # untouched choices keep their labels
        replaced = self.call("patch", path, token, data={"options": ["S", "M", "L"]})
        self.assertEqual(replaced.json()["options"], ["S", "M", "L"])
        self.assertFalse(self.call("patch", path, token, data={"is_active": False}).json()["is_active"])
        self.assertTrue(self.call("patch", path, token, data={"is_active": True}).json()["is_active"])
        cleared = self.call("patch", path, token, data={"options": []})
        self.assertError(cleared, 422, "validation_error")
        self.assertIn("options", cleared.json()["error"]["fields"])
        self.assertError(self.call("patch", path, token, data={"label": ""}), 422, "validation_error")

    def test_patch_field_of_answered_question_keeps_answers(self):
        form = self.make_form(fields=("Q",))
        field = form.fields.get()
        submission = Submission.objects.create(send=FormSend.objects.create(club=self.club, form=form), member=self.member)
        Answer.objects.create(submission=submission, field=field, value="x")
        patched = self.call("patch", f"{URL}/{form.pk}/fields/{field.pk}", self.token(), data={"label": "Question?"})
        self.assertEqual(patched.status_code, 200)
        self.assertEqual(Answer.objects.get().field_id, field.pk)

    def test_delete_field(self):
        form = self.make_form(fields=("Free", "Answered"))
        free, answered = form.fields.order_by("order")
        submission = Submission.objects.create(send=FormSend.objects.create(club=self.club, form=form), member=self.member)
        Answer.objects.create(submission=submission, field=answered, value="x")
        token = self.token()
        self.assertEqual(self.call("delete", f"{URL}/{form.pk}/fields/{free.pk}", token).status_code, 204)
        self.assertError(self.call("delete", f"{URL}/{form.pk}/fields/{answered.pk}", token), 409, "field_in_use")
        self.assertEqual(list(form.fields.values_list("label", flat=True)), ["Answered"])

    def test_reorder(self):
        form = self.make_form(fields=("A", "B", "C"))
        a, b, c = form.fields.order_by("order")
        token = self.token()
        path = f"{URL}/{form.pk}/fields/reorder"
        done = self.call("post", path, token, data={"field_ids": [str(c.pk), str(a.pk), str(b.pk)]})
        self.assertEqual(done.status_code, 200, done.content)
        self.assertEqual([f["label"] for f in done.json()["fields"]], ["C", "A", "B"])
        for ids in ([str(a.pk), str(b.pk)], [str(a.pk), str(a.pk), str(b.pk), str(c.pk)], [str(a.pk), str(b.pk), "00000000-0000-0000-0000-000000000000"]):
            self.assertError(self.call("post", path, token, data={"field_ids": ids}), 422, "validation_error")
        self.assertEqual([f.label for f in form.fields.order_by("order")], ["C", "A", "B"])
