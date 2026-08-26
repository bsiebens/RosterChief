import datetime
from datetime import timedelta

from django import forms
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError
from django.db.models import ProtectedError
from django.test import TestCase
from django.utils import timezone

from club.models import Club, ClubMembership, Season
from members.models import Group, GroupMembership, Member
from notifications.models import Notification
from teams.models import Team, TeamMembership

from .models import Answer, Field, Form, FormSend, Submission
from .services import (
    FormSubmissionError,
    build_form,
    build_form_class,
    effective_members,
    field_choices,
    form_report,
    members_not_yet_submitted,
    pending_sends_for,
    resolve_season,
    submit_form,
)
from .services.notifications import notify_form_send


class FormbuilderTestBase(TestCase):
    # One two-field form with one open, club-wide-reaching send, shared by
    # every test here (invited_members carries self.member into the audience
    # directly, without needing a Season/ClubMembership fixture in every
    # test -- see FormSendAudienceTests for the team/group/club_wide
    # resolution itself). Tests that reconfigure the form/send (closing the
    # window, flipping login_required) mutate a per-test copy handed out by
    # setUpTestData, and their saves roll back with the transaction.
    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        cls.form = Form.objects.create(club=cls.club, title="Sign-up", slug="sign-up")
        cls.name = Field.objects.create(form=cls.form, key="name", label="Name", field_type=Field.FieldType.TEXT, required=True, order=1)
        cls.size = Field.objects.create(form=cls.form, key="size", label="Shirt size", field_type=Field.FieldType.CHOICE, required=False, order=2, options=["S", "M", "L"])
        cls.member = Member.objects.create(first_name="Jane", last_name="Doe")
        cls.send = FormSend.objects.create(club=cls.club, form=cls.form)
        cls.send.invited_members.add(cls.member)

    def fresh_send(self):
        """A brand-new FormSend instance for ``self.send``'s pk -- used after
        mutating ``self.form``/``self.send`` mid-test, so the service layer
        can't see a stale cached ``.form`` relation from before the change."""
        return FormSend.objects.get(pk=self.send.pk)


class ModelTests(FormbuilderTestBase):
    def test_str_methods(self):
        submission = Submission.objects.create(send=self.send, member=self.member)
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
        submission = Submission.objects.create(send=self.send, member=self.member)
        Answer.objects.create(submission=submission, field=self.name, value="Jane")

        with self.assertRaises(IntegrityError):
            Answer.objects.create(submission=submission, field=self.name, value="Again")

    def test_deleting_form_cascades_through_sends_to_submissions(self):
        Submission.objects.create(send=self.send, member=self.member)

        self.form.delete()

        self.assertFalse(Field.objects.exists())
        self.assertFalse(FormSend.objects.exists())
        self.assertFalse(Submission.objects.exists())

    def test_deleting_a_send_cascades_to_its_submissions_only(self):
        other_send = FormSend.objects.create(club=self.club, form=self.form)
        Submission.objects.create(send=self.send, member=self.member)
        kept = Submission.objects.create(send=other_send, member=self.member)

        self.send.delete()

        self.assertEqual(list(Submission.objects.all()), [kept])

    def test_field_referenced_by_answer_is_protected(self):
        submission = Submission.objects.create(send=self.send, member=self.member)
        Answer.objects.create(submission=submission, field=self.name, value="Jane")

        with self.assertRaises(ProtectedError):
            self.name.delete()

    def test_send_rejects_a_form_from_another_club(self):
        other_club = Club.objects.create(name="Rival FC", slug="rival-fc-2")
        other_form = Form.objects.create(club=other_club, title="Other", slug="other")
        send = FormSend(club=self.club, form=other_form)

        with self.assertRaises(ValidationError) as ctx:
            send.full_clean()
        self.assertIn("form", ctx.exception.error_dict)


class FormSlugTests(FormbuilderTestBase):
    def test_slug_auto_populated_from_title(self):
        form = Form.objects.create(club=self.club, title="Registration Form")

        self.assertEqual(form.slug, "registration-form")

    def test_explicit_slug_is_preserved(self):
        form = Form.objects.create(club=self.club, title="Registration", slug="reg")

        self.assertEqual(form.slug, "reg")

    def test_slug_is_unique_per_club_with_suffix(self):
        first = Form.objects.create(club=self.club, title="Registration")
        second = Form.objects.create(club=self.club, title="Registration")

        self.assertEqual(first.slug, "registration")
        self.assertEqual(second.slug, "registration-2")


class FieldKeyTests(FormbuilderTestBase):
    def test_key_auto_populated_from_label(self):
        field = Field.objects.create(form=self.form, label="First Name", order=5)

        self.assertEqual(field.key, "first-name")

    def test_explicit_key_is_preserved(self):
        field = Field.objects.create(form=self.form, key="fn", label="First Name", order=5)

        self.assertEqual(field.key, "fn")

    def test_key_is_unique_per_form_with_suffix(self):
        first = Field.objects.create(form=self.form, label="First Name", order=5)
        second = Field.objects.create(form=self.form, label="First Name", order=6)

        self.assertEqual(first.key, "first-name")
        self.assertEqual(second.key, "first-name-2")

    def test_same_key_allowed_in_a_different_form(self):
        other_form = Form.objects.create(club=self.club, title="Other", slug="other")
        here = Field.objects.create(form=self.form, label="First Name", order=5)
        there = Field.objects.create(form=other_form, label="First Name", order=1)

        self.assertEqual(here.key, there.key)


class FieldChoicesTests(FormbuilderTestBase):
    def test_string_options(self):
        self.assertEqual(field_choices(self.size), [("S", "S"), ("M", "M"), ("L", "L")])

    def test_dict_options(self):
        field = Field.objects.create(form=self.form, key="plan", label="Plan", field_type=Field.FieldType.CHOICE, order=3, options=[{"value": "a", "label": "Gold"}, {"value": "b", "label": "Silver"}])

        self.assertEqual(field_choices(field), [("a", "Gold"), ("b", "Silver")])

    def test_no_options(self):
        self.assertEqual(field_choices(self.name), [])


class FormSendAudienceTests(FormbuilderTestBase):
    """formbuilder.services.audience -- effective_members/members_not_yet_submitted/
    resolve_season/pending_sends_for. Same union/subtract shape as events'
    own effective_members, verified independently here rather than assumed."""

    def make_season(self, **kwargs):
        kwargs.setdefault("club", self.club)
        kwargs.setdefault("start_date", timezone.localdate() - datetime.timedelta(days=30))
        kwargs.setdefault("end_date", timezone.localdate() + datetime.timedelta(days=300))
        return Season.objects.create(**kwargs)

    def test_resolve_season_falls_back_to_current_season(self):
        season = self.make_season()

        self.assertEqual(resolve_season(self.send), season)

    def test_resolve_season_prefers_an_explicit_season(self):
        self.make_season()
        other_season = self.make_season(start_date=timezone.localdate() + datetime.timedelta(days=400), end_date=timezone.localdate() + datetime.timedelta(days=700))
        self.send.season = other_season
        self.send.save(update_fields=["season"])

        self.assertEqual(resolve_season(self.fresh_send()), other_season)

    def test_effective_members_includes_team_rosters_for_the_sends_season(self):
        season = self.make_season()
        team = Team.objects.create(club=self.club, name="U16")
        rostered = Member.objects.create(first_name="Alex", last_name="Roe")
        TeamMembership.objects.create(team=team, member=rostered, season=season)
        send = FormSend.objects.create(club=self.club, form=self.form)
        send.teams.add(team)

        self.assertIn(rostered, effective_members(send))

    def test_effective_members_includes_group_members_regardless_of_season(self):
        group = Group.objects.create(club=self.club, name="Committee")
        grouped = Member.objects.create(first_name="Sam", last_name="Lane")
        GroupMembership.objects.create(group=group, member=grouped)
        send = FormSend.objects.create(club=self.club, form=self.form)
        send.groups.add(group)

        self.assertIn(grouped, effective_members(send))

    def test_club_wide_send_includes_every_active_member(self):
        season = self.make_season()
        active = Member.objects.create(first_name="Cy", last_name="Active")
        ClubMembership.objects.create(club=self.club, member=active, season=season, status=ClubMembership.StatusChoices.ACTIVE)
        send = FormSend.objects.create(club=self.club, form=self.form, club_wide=True)

        self.assertIn(active, effective_members(send))

    def test_club_wide_send_excludes_an_inactive_membership(self):
        season = self.make_season()
        inactive = Member.objects.create(first_name="Ivy", last_name="Inactive")
        ClubMembership.objects.create(club=self.club, member=inactive, season=season, status=ClubMembership.StatusChoices.PENDING)
        send = FormSend.objects.create(club=self.club, form=self.form, club_wide=True)

        self.assertNotIn(inactive, effective_members(send))

    def test_effective_members_excludes_explicitly_excluded_members(self):
        self.send.excluded_members.add(self.member)

        self.assertNotIn(self.member, effective_members(self.fresh_send()))

    def test_members_not_yet_submitted_excludes_anyone_with_an_existing_submission(self):
        Submission.objects.create(send=self.send, member=self.member)

        self.assertNotIn(self.member, members_not_yet_submitted(self.send))

    def test_pending_sends_for_includes_an_open_send_the_member_hasnt_answered(self):
        self.assertEqual(pending_sends_for([self.member], self.club), [self.send])

    def test_pending_sends_for_omits_a_send_already_submitted(self):
        Submission.objects.create(send=self.send, member=self.member)

        self.assertEqual(pending_sends_for([self.member], self.club), [])

    def test_pending_sends_for_omits_a_send_thats_not_currently_active(self):
        self.send.is_active = False
        self.send.save(update_fields=["is_active"])

        self.assertEqual(pending_sends_for([self.member], self.fresh_send().club), [])

    def test_pending_sends_for_omits_a_send_outside_its_window(self):
        self.send.closes_at = timezone.now() - timedelta(days=1)
        self.send.save(update_fields=["closes_at"])

        self.assertEqual(pending_sends_for([self.member], self.club), [])

    def test_pending_sends_for_ignores_the_forms_own_template_lifecycle_flag(self):
        # A retired template shouldn't retroactively hide a send already out --
        # Form.is_active only governs whether it can be picked for a *new* send.
        self.form.is_active = False
        self.form.save(update_fields=["is_active"])

        self.assertEqual(pending_sends_for([self.member], self.club), [self.send])


class SubmitFormTests(FormbuilderTestBase):
    def test_successful_submission_creates_submission_and_answers(self):
        submission = submit_form(self.send, self.member, {"name": "Jane", "size": "M"})

        self.assertEqual(submission.send, self.send)
        self.assertEqual(submission.member, self.member)
        self.assertEqual(submission.answers.count(), 2)
        self.assertEqual(submission.answers.get(field=self.name).value, "Jane")

    def test_optional_field_can_be_omitted(self):
        submission = submit_form(self.send, self.member, {"name": "Jane"})

        self.assertEqual(submission.answers.count(), 1)

    def test_missing_required_field_is_rejected(self):
        with self.assertRaises(FormSubmissionError) as ctx:
            submit_form(self.send, self.member, {"size": "M"})

        self.assertIn("name", ctx.exception.errors)
        self.assertFalse(Submission.objects.exists())

    def test_inactive_send_is_rejected(self):
        self.send.is_active = False
        self.send.save()

        with self.assertRaises(FormSubmissionError):
            submit_form(self.fresh_send(), self.member, {"name": "Jane"})

    def test_login_required_without_member_is_rejected(self):
        self.form.login_required = True
        self.form.save()

        with self.assertRaises(FormSubmissionError):
            submit_form(self.fresh_send(), None, {"name": "Jane"})

    def test_anonymous_submission_allowed_when_login_not_required(self):
        submission = submit_form(self.send, None, {"name": "Jane"})

        self.assertIsNone(submission.member)

    def test_not_open_yet_is_rejected(self):
        self.send.opens_at = timezone.now() + timedelta(days=1)
        self.send.save()

        with self.assertRaises(FormSubmissionError):
            submit_form(self.fresh_send(), self.member, {"name": "Jane"})

    def test_closed_send_is_rejected(self):
        self.send.closes_at = timezone.now() - timedelta(days=1)
        self.send.save()

        with self.assertRaises(FormSubmissionError):
            submit_form(self.fresh_send(), self.member, {"name": "Jane"})

    def test_open_window_respects_when_argument(self):
        self.send.opens_at = timezone.now() + timedelta(days=1)
        self.send.save()

        submission = submit_form(self.fresh_send(), self.member, {"name": "Jane"}, when=timezone.now() + timedelta(days=2))

        self.assertTrue(Submission.objects.filter(pk=submission.pk).exists())

    def test_max_submissions_per_user_is_scoped_to_one_send_not_the_whole_form(self):
        self.send.max_submissions_per_user = 1
        self.send.save()
        other_send = FormSend.objects.create(club=self.club, form=self.form, max_submissions_per_user=1)
        other_send.invited_members.add(self.member)
        submit_form(self.fresh_send(), self.member, {"name": "Jane"})

        with self.assertRaises(FormSubmissionError):
            submit_form(self.fresh_send(), self.member, {"name": "Jane"})
        # A second send of the same form has its own, independent quota.
        submit_form(other_send, self.member, {"name": "Jane"})

    def test_invalid_choice_is_rejected(self):
        with self.assertRaises(FormSubmissionError) as ctx:
            submit_form(self.send, self.member, {"name": "Jane", "size": "XL"})

        self.assertIn("size", ctx.exception.errors)

    def test_number_field_rejects_non_numeric_input(self):
        # Validation goes through the same dynamic Django Form the UI renders, so a
        # NUMBER field is checked as a decimal — not merely "present".
        Field.objects.create(form=self.form, key="age", label="Age", field_type=Field.FieldType.NUMBER, required=True, order=3)

        with self.assertRaises(FormSubmissionError) as ctx:
            submit_form(self.send, self.member, {"name": "Jane", "age": "not-a-number"})

        self.assertIn("age", ctx.exception.errors)

    def test_email_field_rejects_an_invalid_address(self):
        Field.objects.create(form=self.form, key="contact", label="Contact", field_type=Field.FieldType.EMAIL, required=True, order=3)

        with self.assertRaises(FormSubmissionError) as ctx:
            submit_form(self.send, self.member, {"name": "Jane", "contact": "not-an-email"})

        self.assertIn("contact", ctx.exception.errors)

    def test_multichoice_validation(self):
        field = Field.objects.create(form=self.form, key="days", label="Days", field_type=Field.FieldType.MULTICHOICE, required=False, order=3, options=["mon", "tue", "wed"])
        other_member = Member.objects.create(first_name="Joe", last_name="Roe")
        self.send.invited_members.add(other_member)

        submit_form(self.fresh_send(), self.member, {"name": "Jane", "days": ["mon", "wed"]})
        with self.assertRaises(FormSubmissionError):
            submit_form(self.fresh_send(), other_member, {"name": "Joe", "days": ["mon", "sun"]})
        self.assertEqual(Answer.objects.filter(field=field).count(), 1)

    def test_inactive_field_is_ignored(self):
        Field.objects.create(form=self.form, key="secret", label="Secret", field_type=Field.FieldType.TEXT, required=True, is_active=False, order=4)

        submission = submit_form(self.send, self.member, {"name": "Jane"})

        self.assertFalse(submission.answers.filter(field__key="secret").exists())

    def test_unknown_keys_are_ignored(self):
        submission = submit_form(self.send, self.member, {"name": "Jane", "bogus": "x"})

        self.assertEqual(submission.answers.count(), 1)

    def test_a_member_outside_the_audience_is_rejected(self):
        outsider = Member.objects.create(first_name="Out", last_name="Sider")

        with self.assertRaises(FormSubmissionError):
            submit_form(self.send, outsider, {"name": "Jane"})
        self.assertFalse(Submission.objects.filter(member=outsider).exists())

    def test_an_excluded_member_is_rejected_even_if_otherwise_invited(self):
        self.send.excluded_members.add(self.member)

        with self.assertRaises(FormSubmissionError):
            submit_form(self.fresh_send(), self.member, {"name": "Jane"})

    def test_anonymous_submission_skips_the_audience_check(self):
        # member=None has no audience to check against -- not raised, even
        # though nobody was ever added to this send's audience for it.
        empty_send = FormSend.objects.create(club=self.club, form=self.form)

        submission = submit_form(empty_send, None, {"name": "Jane"})

        self.assertIsNotNone(submission)


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
        report = form_report(self.send)

        self.assertEqual(report.columns, [self.name, self.size])

    def test_rows_carry_answer_values(self):
        submit_form(self.send, self.member, {"name": "Jane", "size": "M"})

        report = form_report(self.send)

        self.assertEqual(report.count, 1)
        row = report.rows[0]
        self.assertEqual(row.values[self.name.id], "Jane")
        self.assertEqual(row.values[self.size.id], "M")

    def test_choice_field_is_summarised(self):
        joe = Member.objects.create(first_name="Joe", last_name="Roe")
        kim = Member.objects.create(first_name="Kim", last_name="Ash")
        self.send.invited_members.add(joe, kim)
        submit_form(self.fresh_send(), self.member, {"name": "Jane", "size": "M"})
        submit_form(self.fresh_send(), joe, {"name": "Joe", "size": "M"})
        submit_form(self.fresh_send(), kim, {"name": "Kim", "size": "L"})

        report = form_report(self.fresh_send())

        self.assertEqual(report.summaries[self.size.id], {"M": 2, "L": 1})
        self.assertNotIn(self.name.id, report.summaries)

    def test_multichoice_values_are_tallied_per_option(self):
        days = Field.objects.create(form=self.form, key="days", label="Days", field_type=Field.FieldType.MULTICHOICE, required=False, order=3, options=["mon", "tue"])
        submit_form(self.send, self.member, {"name": "Jane", "days": ["mon", "tue"]})

        report = form_report(self.send)

        self.assertEqual(report.summaries[days.id], {"mon": 1, "tue": 1})

    def test_empty_send_reports_no_rows(self):
        report = form_report(self.send)

        self.assertEqual(report.count, 0)
        self.assertEqual(report.rows, [])

    def test_report_is_scoped_to_one_send_not_every_send_of_the_form(self):
        other_send = FormSend.objects.create(club=self.club, form=self.form)
        other_send.invited_members.add(self.member)
        submit_form(self.send, self.member, {"name": "Jane"})
        submit_form(other_send, self.member, {"name": "Jane again"})

        report = form_report(self.send)

        self.assertEqual(report.count, 1)

    def test_form_property_reaches_the_underlying_form(self):
        report = form_report(self.send)

        self.assertEqual(report.form, self.form)


class AnswerCleanTests(FormbuilderTestBase):
    def test_rejects_field_from_another_form(self):
        other_form = Form.objects.create(club=self.club, title="Other", slug="other")
        other_field = Field.objects.create(form=other_form, key="x", label="X", order=1)
        submission = Submission.objects.create(send=self.send, member=self.member)
        answer = Answer(submission=submission, field=other_field, value="v")

        with self.assertRaises(ValidationError) as ctx:
            answer.full_clean()
        self.assertIn("field", ctx.exception.error_dict)

    def test_accepts_field_from_the_submissions_sends_form(self):
        submission = Submission.objects.create(send=self.send, member=self.member)

        Answer(submission=submission, field=self.name, value="v").full_clean()


class NotifyFormSendTests(FormbuilderTestBase):
    """formbuilder.services.notifications.notify_form_send -- one Notification
    per resolved audience member, source pointing back at the send."""

    def test_notifies_every_resolved_audience_member(self):
        other = Member.objects.create(first_name="Joe", last_name="Roe")
        self.send.invited_members.add(other)

        result = notify_form_send(str(self.send.pk))

        self.assertEqual(Notification.objects.filter(member=self.member).count(), 1)
        self.assertEqual(Notification.objects.filter(member=other).count(), 1)
        self.assertEqual(result, "Notified 2 member(s).")

    def test_notification_source_points_at_the_send(self):
        notify_form_send(str(self.send.pk))

        notification = Notification.objects.get(member=self.member)
        self.assertEqual(notification.source, self.send)
        self.assertEqual(notification.title, self.form.title)

    def test_deadline_is_mentioned_when_the_send_has_one(self):
        self.send.closes_at = timezone.now() + timedelta(days=3)
        self.send.save()

        notify_form_send(str(self.fresh_send().pk))

        notification = Notification.objects.get(member=self.member)
        self.assertIn(str(timezone.localtime(self.send.closes_at).day), notification.body)

    def test_a_send_with_no_audience_notifies_nobody(self):
        empty_send = FormSend.objects.create(club=self.club, form=self.form)

        result = notify_form_send(str(empty_send.pk))

        self.assertFalse(Notification.objects.filter(object_id=str(empty_send.pk)).exists())
        self.assertEqual(result, "Skipped: no one to notify.")

    def test_a_deleted_send_is_skipped_not_errored(self):
        result = notify_form_send("00000000-0000-0000-0000-000000000000")

        self.assertEqual(result, "Skipped: send no longer exists.")


class SendFormRemindersTests(FormbuilderTestBase):
    """formbuilder.management.commands.send_form_reminders -- one reminder
    push per send, FORM_REMINDER_LEAD_TIME before it closes, to whoever
    hasn't submitted yet. Same idempotent-via-timestamp shape as events'
    own send_deadline_reminders."""

    def setUp(self):
        # Maintenance.save() writes through to the cache -- a DB rollback
        # between tests doesn't clear it, see events.tests's own identical
        # comment on this.
        cache.clear()
        self.addCleanup(cache.clear)

    def test_reminds_within_the_window_before_closing(self):
        self.send.closes_at = timezone.now() + timedelta(days=2)
        self.send.save()

        result = call_command("send_form_reminders")

        self.assertTrue(Notification.objects.filter(member=self.member, title=self.form.title).exists())
        self.assertIn("Reminded 1 member(s) across 1 send(s)", result)

    def test_does_not_remind_before_the_window_opens(self):
        self.send.closes_at = timezone.now() + timedelta(days=10)
        self.send.save()

        call_command("send_form_reminders")

        self.assertFalse(Notification.objects.exists())

    def test_does_not_remind_after_closing(self):
        self.send.closes_at = timezone.now() - timedelta(hours=1)
        self.send.save()

        call_command("send_form_reminders")

        self.assertFalse(Notification.objects.exists())

    def test_a_send_with_no_closes_at_is_never_picked_up(self):
        call_command("send_form_reminders")

        self.assertFalse(Notification.objects.exists())

    def test_does_not_remind_someone_who_already_submitted(self):
        self.send.closes_at = timezone.now() + timedelta(days=2)
        self.send.save()
        submit_form(self.fresh_send(), self.member, {"name": "Jane"})
        Notification.objects.all().delete()

        call_command("send_form_reminders")

        self.assertFalse(Notification.objects.filter(member=self.member).exists())

    def test_does_not_remind_the_same_send_twice(self):
        self.send.closes_at = timezone.now() + timedelta(days=2)
        self.send.save()

        call_command("send_form_reminders")
        Notification.objects.all().delete()
        call_command("send_form_reminders")

        self.assertFalse(Notification.objects.exists())

    def test_marks_processed_even_when_nobody_needed_reminding(self):
        self.send.closes_at = timezone.now() + timedelta(days=2)
        self.send.save()
        submit_form(self.fresh_send(), self.member, {"name": "Jane"})

        call_command("send_form_reminders")

        self.send.refresh_from_db()
        self.assertIsNotNone(self.send.reminder_sent_at)

    def test_an_inactive_send_is_skipped(self):
        self.send.closes_at = timezone.now() + timedelta(days=2)
        self.send.is_active = False
        self.send.save()

        call_command("send_form_reminders")

        self.assertFalse(Notification.objects.exists())
