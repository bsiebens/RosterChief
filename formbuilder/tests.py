"""Form builder services: audience, status, submission validation, reporting and CSV."""

import datetime
import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from formbuilder.models import Answer, Field, Form, FormSend
from formbuilder.services import audience, reporting
from formbuilder.services.submission import FormSubmissionError, submit_form
from members.models import Group, GroupMembership
from news.tests import NewsClubMixin

T = Field.FieldType
PRIVATE = tempfile.mkdtemp()


def names(members):
    return sorted(m.first_name for m in members)


@override_settings(PRIVATE_MEDIA_ROOT=PRIVATE)
class FormbuilderServiceTests(NewsClubMixin, TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(PRIVATE, ignore_errors=True)

    def make_form(self, *specs, **kw):
        form = Form.objects.create(title=kw.pop("title", "Survey"), **kw)
        for i, spec in enumerate(specs):
            Field.objects.create(form=form, order=i, **{"label": spec[0], "field_type": spec[1], **(spec[2] if len(spec) > 2 else {})})
        return form

    def make_send(self, form, *, teams=(), groups=(), invited=(), excluded=(), **kw):
        send = FormSend.objects.create(form=form, **kw)
        send.teams.set(teams)
        send.groups.set(groups)
        send.invited_members.set(invited)
        send.excluded_members.set(excluded)
        return send

    # --- models -------------------------------------------------------------------------------------------------

    def test_slugs_and_keys_are_unique(self):
        a, b = Form.objects.create(title="Big Survey"), Form.objects.create(title="Big Survey")
        self.assertEqual((a.slug, b.slug), ("big-survey", "big-survey-2"))
        f1 = Field.objects.create(form=a, label="Your name")
        f2 = Field.objects.create(form=a, label="Your name")
        self.assertEqual((f1.key, f2.key), ("your-name", "your-name-2"))
        self.assertTrue(len(FormSend.objects.create(form=a).public_token) >= 32)

    # --- audience -----------------------------------------------------------------------------------------------

    def test_audience_team_group_club_wide_invited_excluded(self):
        form = self.make_form()
        self.assertEqual(names(audience.effective_members(self.make_send(form, teams=[self.team]))), ["Ann", "Cara", "Dan", "Kid", "Kid2"])
        club = self.make_send(form, club_wide=True)
        self.assertEqual(names(audience.effective_members(club)), ["Ann", "Bob", "Cara", "Kid", "Kid2"])  # Dan is lapsed
        group = Group.objects.create(name="Coaches")
        GroupMembership.objects.create(group=group, member=self.eve)
        mixed = self.make_send(form, teams=[self.other_team], groups=[group], invited=[self.bob], excluded=[self.eve])
        self.assertEqual(names(audience.effective_members(mixed)), ["Bob"])
        self.assertEqual(audience.audience_count(self.make_send(form, teams=[self.team], excluded=[self.dan, self.kid])), 3)
        self.assertEqual(audience.audience_ids_by_send([club, mixed])[mixed.pk], {self.bob.pk})
        self.assertTrue(audience.addressed_to(club, [self.eve, self.bob]))
        self.assertFalse(audience.addressed_to(club, self.eve))

    def test_status_and_open_window(self):
        now = timezone.now()
        hour = datetime.timedelta(hours=1)
        form = self.make_form()
        for kw, status in (({}, "open"), ({"is_active": False}, "inactive"), ({"opens_at": now + hour}, "scheduled"), ({"closes_at": now - hour}, "closed"), ({"is_active": False, "closes_at": now - hour}, "inactive")):
            self.assertEqual(audience.send_status(self.make_send(form, **kw)), status)

    def test_guardians(self):
        self.assertTrue(audience.is_guardian_of(self.parent, self.kid))
        self.assertFalse(audience.is_guardian_of(self.ann, self.kid))
        self.assertEqual(names(audience.managed_children(self.parent)), ["Kid", "Kid2"])
        send = self.make_send(self.make_form(), teams=[self.team])
        self.assertEqual(names(audience.fillable_members(send, self.parent)), ["Kid", "Kid2"])

    # --- submission ---------------------------------------------------------------------------------------------

    def full_form(self):
        return self.make_form(
            ("Name", T.TEXT),
            ("Age", T.NUMBER, {"required": False}),
            ("Mail", T.EMAIL, {"required": False}),
            ("Born", T.DATE, {"required": False}),
            ("Size", T.CHOICE, {"options": ["S", {"value": "M", "label": "Medium"}], "required": False}),
            ("Days", T.MULTICHOICE, {"options": ["mon", "tue"], "required": False}),
            ("Agree", T.CHECKBOX, {"required": False}),
            ("Doc", T.FILE, {"required": False}),
        )

    def errors(self, send, member, data, **kw):
        with self.assertRaises(FormSubmissionError) as ctx:
            submit_form(send, member, data, **kw)
        return ctx.exception

    def test_valid_submission_with_every_type(self):
        send = self.make_send(self.full_form(), teams=[self.team])
        upload = SimpleUploadedFile("cert.pdf", b"%PDF-1.4 hi", content_type="application/pdf")
        sub = submit_form(send, self.ann, {"name": " Ann ", "age": "12,5", "mail": "a@b.co", "born": "2012-03-04", "size": "M", "days": ["mon", "tue"], "agree": "on"}, files={"doc": upload})
        values = {a.field.key: a for a in sub.answers.select_related("field")}
        self.assertEqual(values["name"].value, "Ann")
        self.assertEqual(str(values["age"].value), "12.5")
        self.assertEqual((values["born"].value, values["size"].value, values["days"].value, values["agree"].value), ("2012-03-04", "M", ["mon", "tue"], True))
        doc = Answer.objects.get(pk=values["doc"].pk)
        self.assertEqual(doc.value["name"], "cert.pdf")
        self.assertTrue(doc.file.name.startswith(f"formbuilder/{send.pk}/{sub.pk}/"))
        self.assertEqual(doc.file.read(), b"%PDF-1.4 hi")
        self.assertEqual(sub.submitted_by, self.ann)

    def test_blank_optional_not_stored_and_validation_errors(self):
        send = self.make_send(self.full_form(), teams=[self.team])
        self.assertEqual(sorted(a.field.key for a in submit_form(send, self.ann, {"name": "x"}).answers.select_related("field")), ["agree", "name"])  # an unchecked box is a stored "no"
        err = self.errors(send, self.cara, {"name": "", "age": "abc", "mail": "nope", "born": "31-02-2020", "size": "XL", "days": ["mon", "fri"]})
        self.assertEqual(set(err.errors), {"name", "age", "mail", "born", "size", "days"})
        self.assertEqual(err.code, "invalid")
        self.assertEqual(send.submissions.count(), 1)

    def test_required_checkbox_and_multichoice_and_file(self):
        form = self.make_form(("Ok", T.CHECKBOX), ("Pick", T.MULTICHOICE, {"options": ["a"]}), ("Doc", T.FILE))
        send = self.make_send(form, teams=[self.team])
        self.assertEqual(set(self.errors(send, self.ann, {"ok": "false"}).errors), {"ok", "pick", "doc"})

    def test_file_type_and_size(self):
        send = self.make_send(self.make_form(("Doc", T.FILE)), teams=[self.team])
        exe = SimpleUploadedFile("evil.exe", b"MZ")
        self.assertIn("doc", self.errors(send, self.ann, {}, files={"doc": exe}).errors)
        empty = SimpleUploadedFile("a.pdf", b"")
        self.assertIn("doc", self.errors(send, self.ann, {}, files={"doc": empty}).errors)
        big = SimpleUploadedFile("a.pdf", b"x" * (10 * 1024 * 1024 + 1))
        self.assertIn("doc", self.errors(send, self.ann, {}, files={"doc": big}).errors)

    def test_send_rules(self):
        now = timezone.now()
        hour = datetime.timedelta(hours=1)
        form = self.make_form(("Name", T.TEXT))
        data = {"name": "x"}
        self.assertEqual(self.errors(self.make_send(form, teams=[self.team], is_active=False), self.ann, data).code, "inactive")
        self.assertEqual(self.errors(self.make_send(form, teams=[self.team], opens_at=now + hour), self.ann, data).code, "not_open_yet")
        self.assertEqual(self.errors(self.make_send(form, teams=[self.team], closes_at=now - hour), self.ann, data).code, "closed")
        self.assertEqual(self.errors(self.make_send(form, teams=[self.team]), self.bob, data).code, "not_addressed")
        self.assertEqual(self.errors(self.make_send(form, teams=[self.team]), None, data).code, "login_required")
        limited = self.make_send(form, teams=[self.team], max_submissions_per_user=1)
        submit_form(limited, self.ann, data)
        self.assertEqual(self.errors(limited, self.ann, data).code, "max_submissions")
        submit_form(limited, self.cara, data)  # the limit is per member
        # `when` overrides the clock
        window = self.make_send(form, teams=[self.team], opens_at=now + hour)
        self.assertEqual(submit_form(window, self.ann, data, when=now + 2 * hour).member, self.ann)

    def test_anonymous_public_submission(self):
        form = self.make_form(("Name", T.TEXT))
        public = self.make_send(form, is_public=True)
        sub = submit_form(public, None, {"name": "Guest"})
        self.assertEqual((sub.member, sub.submitted_by), (None, None))
        form.login_required = True
        form.save()
        self.assertEqual(self.errors(public, None, {"name": "x"}).code, "login_required")

    def test_on_behalf_of_guardian(self):
        send = self.make_send(self.make_form(("Name", T.TEXT)), teams=[self.team])
        sub = submit_form(send, self.kid, {"name": "x"}, submitted_by=self.parent)
        self.assertEqual((sub.member, sub.submitted_by, sub.submitted_on_behalf_by), (self.kid, self.parent, self.parent))
        self.assertEqual(self.errors(send, self.kid2, {"name": "x"}, submitted_by=self.ann).code, "not_guardian")

    # --- reporting ----------------------------------------------------------------------------------------------

    def test_report_summaries_not_responded_and_csv(self):
        form = self.make_form(("Name", T.TEXT), ("Size", T.CHOICE, {"options": ["S", "M"]}), ("Days", T.MULTICHOICE, {"options": ["mon", "tue"], "required": False}), ("Doc", T.FILE, {"required": False}))
        send = self.make_send(form, teams=[self.team])
        submit_form(send, self.ann, {"name": "=SUM(1)", "size": "S", "days": ["mon", "tue"]}, files={"doc": SimpleUploadedFile("a, b.pdf", b"x")})
        submit_form(send, self.kid, {"name": "Kid", "size": "S", "days": ["mon"]}, submitted_by=self.parent)
        report = reporting.form_report(send)
        by_key = {c.key: c.pk for c in report.columns}
        self.assertEqual(report.count, 2)
        self.assertEqual(report.summaries[by_key["size"]], {"S": 2, "M": 0})
        self.assertEqual(report.summaries[by_key["days"]], {"mon": 2, "tue": 1})
        self.assertEqual(names(report.not_responded), ["Cara", "Dan", "Kid2"])
        ann_row = next(r for r in report.rows if r.submission.member == self.ann)
        self.assertEqual(ann_row.values[by_key["doc"]], "a, b.pdf")
        self.assertIn(by_key["doc"], ann_row.files)
        text = reporting.report_csv(report)
        self.assertTrue(text.startswith("﻿Submitted at,Member,Submitted by,Name,Size,Days,Doc"))
        self.assertIn("'=SUM(1)", text)
        self.assertIn('"mon, tue"', text)
        self.assertIn('"a, b.pdf"', text)
        self.assertIn("Parent Test", text)
