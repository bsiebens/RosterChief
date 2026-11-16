"""Public form API (``/public/forms/{token}``): no login, anonymous submissions, 404 rules, states, files, rate limit."""

import datetime
import shutil
import tempfile

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from api.routers import public_forms
from formbuilder.models import Answer, Field, Form, FormSend, Submission
from news.tests import NewsClubMixin

BASE = "/api/v1/public/forms"
PRIVATE = tempfile.mkdtemp()
H = datetime.timedelta(hours=1)
T = Field.FieldType


@override_settings(PRIVATE_MEDIA_ROOT=PRIVATE)
class PublicFormsTests(NewsClubMixin, TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(PRIVATE, ignore_errors=True)

    def setUp(self):
        super().setUp()
        cache.clear()
        self.form = Form.objects.create(title="Camp signup", description="Summer camp")
        specs = [
            ("Name", T.TEXT, {"section": "You"}),
            ("Age", T.NUMBER, {"required": False, "section": "You"}),
            ("Mail", T.EMAIL, {"required": False}),
            ("Day", T.CHOICE, {"options": ["Mon", {"value": "tue", "label": "Tuesday"}]}),
            ("Extras", T.MULTICHOICE, {"options": ["a", "b"], "required": False}),
            ("Agree", T.CHECKBOX, {"required": False}),
            ("Photo", T.FILE, {"required": False, "help_text": "Optional"}),
        ]
        for i, (label, kind, extra) in enumerate(specs):
            Field.objects.create(form=self.form, label=label, field_type=kind, order=i, **extra)
        Field.objects.create(form=self.form, label="Hidden", is_active=False, order=99)
        self.send = FormSend.objects.create(form=self.form, is_public=True)

    def get(self, token=None):
        return Client().get(f"{BASE}/{token or self.send.public_token}", HTTP_HOST=self.host)

    def post(self, data=None, token=None, **extra):
        data = {"name": "Alice", "day": "Mon", **(data or {})}
        return Client().post(f"{BASE}/{token or self.send.public_token}", data, HTTP_HOST=self.host, **extra)

    # --- GET ----------------------------------------------------------------------------------------------------

    def test_get_describes_the_form_and_leaks_nothing_else(self):
        r = self.get()
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual((body["title"], body["description"], body["state"]["status"]), ("Camp signup", "Summer camp", "open"))
        self.assertEqual([f["key"] for f in body["fields"]], ["name", "age", "mail", "day", "extras", "agree", "photo"])  # inactive field hidden
        day = body["fields"][3]
        self.assertEqual((day["type"], day["options"]), ("choice", [{"value": "Mon", "label": "Mon"}, {"value": "tue", "label": "Tuesday"}]))
        self.assertEqual(body["fields"][0]["section"], "You")
        self.assertEqual(set(body), {"title", "description", "state", "fields", "max_file_mb", "allowed_file_extensions"})
        self.assertEqual(set(body["state"]), {"status", "opens_at", "closes_at"})
        self.assertEqual(set(body["fields"][0]), {"id", "key", "label", "type", "required", "help_text", "section", "options"})

    def test_states(self):
        now = timezone.now()
        cases = [({"is_active": False}, "inactive"), ({"closes_at": now - H}, "closed"), ({"opens_at": now + H}, "not_open_yet")]
        for kw, status in cases:
            FormSend.objects.filter(pk=self.send.pk).update(is_active=True, opens_at=None, closes_at=None)
            FormSend.objects.filter(pk=self.send.pk).update(**kw)
            self.assertEqual(self.get().json()["state"]["status"], status)

    def test_404_rules(self):
        self.assertEqual(self.get("nope").status_code, 404)
        private = FormSend.objects.create(form=self.form, is_public=False)
        self.assertEqual(self.get(private.public_token).status_code, 404)
        self.assertEqual(self.post(token=private.public_token).status_code, 404)
        locked = Form.objects.create(title="Members only", login_required=True)
        send = FormSend.objects.create(form=locked, is_public=True)
        self.assertEqual(self.get(send.public_token).status_code, 404)
        self.assertEqual(self.post(token=send.public_token).status_code, 404)
        self.assertEqual(Submission.objects.count(), 0)

    def test_platform_host_is_404(self):
        r = Client().get(f"{BASE}/{self.send.public_token}", HTTP_HOST="testserver")
        self.assertEqual(r.status_code, 404)

    # --- POST ---------------------------------------------------------------------------------------------------

    def test_submit_happy_path_is_anonymous(self):
        r = self.post({"age": "12", "extras": ["a", "b"], "agree": "on", "tue": ""})
        self.assertEqual((r.status_code, r.json()), (201, {"submitted": True}))
        sub = Submission.objects.get()
        self.assertEqual((sub.send_id, sub.member_id, sub.submitted_by_id), (self.send.pk, None, None))
        values = {a.field.key: a.value for a in sub.answers.all()}
        self.assertEqual((values["name"], values["day"], values["extras"], values["agree"]), ("Alice", "Mon", ["a", "b"], True))

    def test_file_answer_upload(self):
        upload = SimpleUploadedFile("pic.png", b"\x89PNG data", content_type="image/png")
        r = self.post({"photo": upload})
        self.assertEqual(r.status_code, 201, r.content)
        answer = Answer.objects.get(field__key="photo")
        self.assertEqual(answer.value["name"], "pic.png")
        self.assertTrue(answer.file.name.startswith(f"formbuilder/{self.send.pk}/"))
        self.assertNotIn("pic.png", r.content.decode())  # the response never names stored files

    def test_bad_file_type_is_a_field_error(self):
        r = self.post({"photo": SimpleUploadedFile("evil.exe", b"x")})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.json()["errors"], {"photo": ["This file type is not allowed."]})
        self.assertEqual(Submission.objects.count(), 0)

    def test_validation_errors_are_keyed_by_field(self):
        r = Client().post(f"{BASE}/{self.send.public_token}", {"mail": "nope", "day": "Fri"}, HTTP_HOST=self.host)
        self.assertEqual(r.status_code, 422)
        body = r.json()
        self.assertEqual(body["code"], "invalid")
        self.assertEqual(set(body["errors"]), {"name", "mail", "day"})
        self.assertEqual(Submission.objects.count(), 0)

    def test_closed_not_open_and_inactive_refuse_with_a_code(self):
        now = timezone.now()
        for kw, code in (({"is_active": False}, "inactive"), ({"closes_at": now - H}, "closed"), ({"opens_at": now + H}, "not_open_yet")):
            FormSend.objects.filter(pk=self.send.pk).update(is_active=True, opens_at=None, closes_at=None)
            FormSend.objects.filter(pk=self.send.pk).update(**kw)
            r = self.post()
            self.assertEqual((r.status_code, r.json()["code"]), (422, code), code)
        self.assertEqual(Submission.objects.count(), 0)

    def test_nothing_about_other_responses_is_exposed(self):
        self.post({"name": "Secret Sam"})
        for r in (self.get(), self.post({"name": "Bob"})):
            self.assertNotIn("Secret", r.content.decode())
        # no listing / download surface under the public prefix
        self.assertEqual(Client().get(f"{BASE}/{self.send.public_token}/responses", HTTP_HOST=self.host).status_code, 404)

    def test_rate_limit_per_address_and_link(self):
        for _ in range(public_forms.MAX_POSTS):
            self.assertEqual(self.post({"day": "bad"}, REMOTE_ADDR="10.0.0.1").status_code, 422)  # failures count too
        r = self.post(REMOTE_ADDR="10.0.0.1")
        self.assertEqual(r.status_code, 429)
        self.assertEqual(r.json()["code"], "rate_limited")
        self.assertTrue(int(r["Retry-After"]) > 0)
        self.assertEqual(Submission.objects.count(), 0)
        self.assertEqual(self.post(REMOTE_ADDR="10.0.0.2").status_code, 201)  # another address is unaffected
        other = FormSend.objects.create(form=self.form, is_public=True)
        self.assertEqual(self.post(token=other.public_token, REMOTE_ADDR="10.0.0.1").status_code, 201)  # another link too

    def test_forwarded_address_is_used(self):
        for _ in range(public_forms.MAX_POSTS):
            self.post({"day": "bad"}, HTTP_X_FORWARDED_FOR="203.0.113.9")
        self.assertEqual(self.post(HTTP_X_FORWARDED_FOR="203.0.113.9").status_code, 429)
        self.assertEqual(self.post(HTTP_X_FORWARDED_FOR="203.0.113.10").status_code, 201)
