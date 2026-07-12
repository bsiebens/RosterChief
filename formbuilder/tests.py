from datetime import timedelta

from django import forms
from django.db import IntegrityError
from django.db.models import ProtectedError
from django.test import TestCase
from django.utils import timezone

from club.models import Club
from members.models import Member

from .models import Answer, Field, Form, Submission
from .services import (
    FormSubmissionError,
    build_form,
    build_form_class,
    field_choices,
    form_report,
    submit_form,
)


class FormbuilderTestBase(TestCase):
    def setUp(self):
        self.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        self.form = Form.objects.create(club=self.club, title="Sign-up", slug="sign-up")
        self.name = Field.objects.create(form=self.form, key="name", label="Name", field_type=Field.FieldType.TEXT, required=True, order=1)
        self.size = Field.objects.create(form=self.form, key="size", label="Shirt size", field_type=Field.FieldType.CHOICE, required=False, order=2, options=["S", "M", "L"])
        self.member = Member.objects.create(first_name="Jane", last_name="Doe")


class ModelTests(FormbuilderTestBase):
    def test_str_methods(self):
        submission = Submission.objects.create(form=self.form, member=self.member)
        answer = Answer.objects.create(submission=submission, field=self.name, value="Jane")

        self.assertEqual(str(self.form), "Sign-up")
        self.assertEqual(str(self.name), "Name")
        self.assertEqual(str(submission), "Sign-up - Jane Doe")
        self.assertEqual(str(answer), "Sign-up - Jane Doe - Name")

    def test_slug_is_unique_per_club_not_globally(self):
        other_club = Club.objects.create(name="Rival FC", slug="rival-fc")
        Form.objects.create(club=other_club, title="Sign-up", slug="sign-up")

        with self.assertRaises(IntegrityError):
            Form.objects.create(club=self.club, title="Another", slug="sign-up")

    def test_key_is_unique_per_form_not_globally(self):
        other_form = Form.objects.create(club=self.club, title="Other", slug="other")
        Field.objects.create(form=other_form, key="name", label="Name", order=1)

        with self.assertRaises(IntegrityError):
            Field.objects.create(form=self.form, key="name", label="Duplicate", order=9)

    def test_answer_is_unique_per_field_per_submission(self):
        submission = Submission.objects.create(form=self.form, member=self.member)
        Answer.objects.create(submission=submission, field=self.name, value="Jane")

        with self.assertRaises(IntegrityError):
            Answer.objects.create(submission=submission, field=self.name, value="Again")

    def test_deleting_form_cascades_to_fields_and_submissions(self):
        Submission.objects.create(form=self.form, member=self.member)

        self.form.delete()

        self.assertFalse(Field.objects.exists())
        self.assertFalse(Submission.objects.exists())

    def test_field_referenced_by_answer_is_protected(self):
        submission = Submission.objects.create(form=self.form, member=self.member)
        Answer.objects.create(submission=submission, field=self.name, value="Jane")

        with self.assertRaises(ProtectedError):
            self.name.delete()


class FieldChoicesTests(FormbuilderTestBase):
    def test_string_options(self):
        self.assertEqual(field_choices(self.size), [("S", "S"), ("M", "M"), ("L", "L")])

    def test_dict_options(self):
        field = Field.objects.create(form=self.form, key="tier", label="Tier", field_type=Field.FieldType.CHOICE, order=3, options=[{"value": "a", "label": "Gold"}, {"value": "b", "label": "Silver"}])

        self.assertEqual(field_choices(field), [("a", "Gold"), ("b", "Silver")])

    def test_no_options(self):
        self.assertEqual(field_choices(self.name), [])


class SubmitFormTests(FormbuilderTestBase):
    def test_successful_submission_creates_submission_and_answers(self):
        submission = submit_form(self.form, self.member, {"name": "Jane", "size": "M"})

        self.assertEqual(submission.form, self.form)
        self.assertEqual(submission.member, self.member)
        self.assertEqual(submission.answers.count(), 2)
        self.assertEqual(submission.answers.get(field=self.name).value, "Jane")

    def test_optional_field_can_be_omitted(self):
        submission = submit_form(self.form, self.member, {"name": "Jane"})

        self.assertEqual(submission.answers.count(), 1)

    def test_missing_required_field_is_rejected(self):
        with self.assertRaises(FormSubmissionError) as ctx:
            submit_form(self.form, self.member, {"size": "M"})

        self.assertIn("name", ctx.exception.errors)
        self.assertFalse(Submission.objects.exists())

    def test_inactive_form_is_rejected(self):
        self.form.is_active = False
        self.form.save()

        with self.assertRaises(FormSubmissionError):
            submit_form(self.form, self.member, {"name": "Jane"})

    def test_login_required_without_member_is_rejected(self):
        self.form.login_required = True
        self.form.save()

        with self.assertRaises(FormSubmissionError):
            submit_form(self.form, None, {"name": "Jane"})

    def test_anonymous_submission_allowed_when_login_not_required(self):
        submission = submit_form(self.form, None, {"name": "Jane"})

        self.assertIsNone(submission.member)

    def test_not_open_yet_is_rejected(self):
        self.form.opens_at = timezone.now() + timedelta(days=1)
        self.form.save()

        with self.assertRaises(FormSubmissionError):
            submit_form(self.form, self.member, {"name": "Jane"})

    def test_closed_form_is_rejected(self):
        self.form.closes_at = timezone.now() - timedelta(days=1)
        self.form.save()

        with self.assertRaises(FormSubmissionError):
            submit_form(self.form, self.member, {"name": "Jane"})

    def test_open_window_respects_when_argument(self):
        self.form.opens_at = timezone.now() + timedelta(days=1)
        self.form.save()

        submission = submit_form(self.form, self.member, {"name": "Jane"}, when=timezone.now() + timedelta(days=2))

        self.assertTrue(Submission.objects.filter(pk=submission.pk).exists())

    def test_max_submissions_per_user_is_enforced(self):
        self.form.max_submissions_per_user = 1
        self.form.save()
        submit_form(self.form, self.member, {"name": "Jane"})

        with self.assertRaises(FormSubmissionError):
            submit_form(self.form, self.member, {"name": "Jane"})

    def test_invalid_choice_is_rejected(self):
        with self.assertRaises(FormSubmissionError) as ctx:
            submit_form(self.form, self.member, {"name": "Jane", "size": "XL"})

        self.assertIn("size", ctx.exception.errors)

    def test_multichoice_validation(self):
        field = Field.objects.create(form=self.form, key="days", label="Days", field_type=Field.FieldType.MULTICHOICE, required=False, order=3, options=["mon", "tue", "wed"])

        submit_form(self.form, self.member, {"name": "Jane", "days": ["mon", "wed"]})
        with self.assertRaises(FormSubmissionError):
            submit_form(self.form, self.member, {"name": "Joe", "days": ["mon", "sun"]})
        self.assertEqual(Answer.objects.filter(field=field).count(), 1)

    def test_inactive_field_is_ignored(self):
        Field.objects.create(form=self.form, key="secret", label="Secret", field_type=Field.FieldType.TEXT, required=True, is_active=False, order=4)

        submission = submit_form(self.form, self.member, {"name": "Jane"})

        self.assertFalse(submission.answers.filter(field__key="secret").exists())

    def test_unknown_keys_are_ignored(self):
        submission = submit_form(self.form, self.member, {"name": "Jane", "bogus": "x"})

        self.assertEqual(submission.answers.count(), 1)


class BuildFormTests(FormbuilderTestBase):
    def make_all_field_types_form(self):
        form = Form.objects.create(club=self.club, title="All", slug="all")
        specs = [
            ("f_text", Field.FieldType.TEXT, forms.CharField),
            ("f_area", Field.FieldType.TEXTAREA, forms.CharField),
            ("f_num", Field.FieldType.NUMBER, forms.DecimalField),
            ("f_email", Field.FieldType.EMAIL, forms.EmailField),
            ("f_date", Field.FieldType.DATE, forms.DateField),
            ("f_choice", Field.FieldType.CHOICE, forms.ChoiceField),
            ("f_multi", Field.FieldType.MULTICHOICE, forms.MultipleChoiceField),
            ("f_check", Field.FieldType.CHECKBOX, forms.BooleanField),
            ("f_file", Field.FieldType.FILE, forms.FileField),
        ]
        for i, (key, field_type, _) in enumerate(specs):
            Field.objects.create(form=form, key=key, label=key, field_type=field_type, required=False, order=i, options=["a", "b"])
        return form, specs

    def test_field_classes_map_from_field_type(self):
        form, specs = self.make_all_field_types_form()

        instance = build_form_class(form)()

        for key, _, expected in specs:
            with self.subTest(key=key):
                self.assertIsInstance(instance.fields[key], expected)

    def test_required_and_labels_propagate(self):
        instance = build_form_class(self.form)()

        self.assertTrue(instance.fields["name"].required)
        self.assertFalse(instance.fields["size"].required)
        self.assertEqual(instance.fields["name"].label, "Name")

    def test_choice_field_gets_choices(self):
        instance = build_form(self.form)

        self.assertEqual(list(instance.fields["size"].choices), [("S", "S"), ("M", "M"), ("L", "L")])

    def test_bound_form_validates(self):
        bound = build_form(self.form, data={"name": "Jane", "size": "M"})

        self.assertTrue(bound.is_valid())
        self.assertEqual(bound.cleaned_data["name"], "Jane")

    def test_bound_form_reports_required_error(self):
        bound = build_form(self.form, data={"size": "M"})

        self.assertFalse(bound.is_valid())
        self.assertIn("name", bound.errors)

    def test_inactive_fields_are_excluded(self):
        Field.objects.create(form=self.form, key="hidden", label="Hidden", is_active=False, order=9)

        instance = build_form(self.form)

        self.assertNotIn("hidden", instance.fields)


class FormReportTests(FormbuilderTestBase):
    def test_columns_are_fields_in_order(self):
        report = form_report(self.form)

        self.assertEqual(report.columns, [self.name, self.size])

    def test_rows_carry_answer_values(self):
        submit_form(self.form, self.member, {"name": "Jane", "size": "M"})

        report = form_report(self.form)

        self.assertEqual(report.count, 1)
        row = report.rows[0]
        self.assertEqual(row.values[self.name.id], "Jane")
        self.assertEqual(row.values[self.size.id], "M")

    def test_choice_field_is_summarised(self):
        submit_form(self.form, self.member, {"name": "Jane", "size": "M"})
        submit_form(self.form, Member.objects.create(first_name="Joe", last_name="Roe"), {"name": "Joe", "size": "M"})
        submit_form(self.form, Member.objects.create(first_name="Kim", last_name="Ash"), {"name": "Kim", "size": "L"})

        report = form_report(self.form)

        self.assertEqual(report.summaries[self.size.id], {"M": 2, "L": 1})
        self.assertNotIn(self.name.id, report.summaries)

    def test_multichoice_values_are_tallied_per_option(self):
        days = Field.objects.create(form=self.form, key="days", label="Days", field_type=Field.FieldType.MULTICHOICE, required=False, order=3, options=["mon", "tue"])
        submit_form(self.form, self.member, {"name": "Jane", "days": ["mon", "tue"]})

        report = form_report(self.form)

        self.assertEqual(report.summaries[days.id], {"mon": 1, "tue": 1})

    def test_empty_form_reports_no_rows(self):
        report = form_report(self.form)

        self.assertEqual(report.count, 0)
        self.assertEqual(report.rows, [])
