import datetime

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from club.models import Club, ClubMembership, Season
from formbuilder.models import Answer, Field, Form, FormSend, Submission
from members.models import Member

from .models import EvaluationSettings, PlayerEvaluation
from .services import EvaluationRubricNotConfigured, EvaluationSubmissionError, current_rubric_form, start_new_rubric_version, submit_evaluation


class EvaluationsTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        cls.other_club = Club.objects.create(name="Rival FC", slug="rival-fc")
        today = timezone.localdate()
        cls.season = Season.objects.create(club=cls.club, start_date=today, end_date=today + datetime.timedelta(days=300))

    def make_member(self, email, *, club=None):
        user = get_user_model().objects.create_user(email=email, password="pw")
        member = Member.objects.create(user=user, first_name=email.split("@")[0].title(), last_name="Doe")
        ClubMembership.objects.create(club=club or self.club, member=member, season=self.season, status=ClubMembership.StatusChoices.ACTIVE)
        return member

    def build_rubric(self, club=None, fields=None):
        """A Form + Fields set as the club's current rubric, mirroring what
        evaluations.services.start_new_rubric_version would produce."""
        club = club or self.club
        form = Form.objects.create(club=club, title="Player rubric")
        for order, (key, field_type, kwargs) in enumerate(fields or []):
            Field.objects.create(form=form, key=key, field_type=field_type, label=key, order=order, **kwargs)
        EvaluationSettings.objects.create(club=club, form=form)
        return form


class PlayerEvaluationModelTests(EvaluationsTestCase):
    def test_clean_rejects_a_player_from_another_club(self):
        form = self.build_rubric()
        send = FormSend.objects.create(club=self.club, form=form)
        submission = Submission.objects.create(send=send)
        outsider = self.make_member("outsider@example.com", club=self.other_club)

        evaluation = PlayerEvaluation(club=self.club, player=outsider, season=self.season, submission=submission)

        with self.assertRaises(ValidationError):
            evaluation.clean()

    def test_clean_rejects_a_submission_from_another_clubs_send(self):
        other_form = Form.objects.create(club=self.other_club, title="Other club's rubric")
        other_send = FormSend.objects.create(club=self.other_club, form=other_form)
        submission = Submission.objects.create(send=other_send)
        player = self.make_member("player@example.com")

        evaluation = PlayerEvaluation(club=self.club, player=player, season=self.season, submission=submission)

        with self.assertRaises(ValidationError):
            evaluation.clean()

    def test_a_member_can_have_several_evaluations_the_same_season(self):
        # No uniqueness constraint by design -- several coaches' perspectives
        # on the same player/season are a feature, not a duplicate to reject.
        form = self.build_rubric()
        send = FormSend.objects.create(club=self.club, form=form)
        player = self.make_member("player2@example.com")

        for _ in range(3):
            submission = Submission.objects.create(send=send)
            PlayerEvaluation.objects.create(club=self.club, player=player, season=self.season, submission=submission)

        self.assertEqual(player.evaluations_received.count(), 3)


class SubmitEvaluationTests(EvaluationsTestCase):
    def test_raises_when_the_club_has_no_rubric_configured_yet(self):
        player = self.make_member("player3@example.com")
        coach = self.make_member("coach@example.com")

        with self.assertRaises(EvaluationRubricNotConfigured):
            submit_evaluation(club=self.club, player=player, season=self.season, evaluator=coach, data={})

    def test_submits_and_stores_answers_against_the_current_rubric(self):
        self.build_rubric(fields=[("skill", Field.FieldType.NUMBER, {"required": True}), ("notes", Field.FieldType.TEXTAREA, {"required": False})])
        player = self.make_member("player4@example.com")
        coach = self.make_member("coach2@example.com")

        evaluation = submit_evaluation(club=self.club, player=player, season=self.season, evaluator=coach, data={"skill": "4", "notes": ""})

        self.assertEqual(evaluation.player, player)
        self.assertEqual(evaluation.season, self.season)
        self.assertEqual(evaluation.submission.member, coach)
        # "notes" was blank -- validated but not stored, same rule as
        # formbuilder.services.submission._clean_answers.
        self.assertEqual(Answer.objects.filter(submission=evaluation.submission).count(), 1)
        self.assertEqual(Answer.objects.get(submission=evaluation.submission, field__key="skill").value, "4")

    def test_invalid_answers_raise_and_persist_nothing(self):
        self.build_rubric(fields=[("skill", Field.FieldType.NUMBER, {"required": True})])
        player = self.make_member("player5@example.com")
        coach = self.make_member("coach3@example.com")

        with self.assertRaises(EvaluationSubmissionError):
            submit_evaluation(club=self.club, player=player, season=self.season, evaluator=coach, data={"skill": "not-a-number"})

        self.assertEqual(PlayerEvaluation.objects.count(), 0)
        self.assertEqual(Submission.objects.count(), 0)

    def test_the_backing_formsend_never_surfaces_as_a_real_audience_send(self):
        """The plumbing FormSend evaluations create must stay invisible to
        formbuilder's own audience resolution -- otherwise every active club
        member would see "Player rubric" appear in their own general "Forms
        to complete" list. club_wide=False with no teams/groups/invited
        members means effective_members(send) is always empty."""
        from formbuilder.services.audience import effective_members

        self.build_rubric(fields=[("skill", Field.FieldType.NUMBER, {"required": True})])
        player = self.make_member("player6@example.com")
        coach = self.make_member("coach4@example.com")

        submit_evaluation(club=self.club, player=player, season=self.season, evaluator=coach, data={"skill": "3"})

        send = FormSend.objects.get(club=self.club)
        self.assertFalse(send.club_wide)
        self.assertFalse(send.is_active)
        self.assertEqual(effective_members(send).count(), 0)


class StartNewRubricVersionTests(EvaluationsTestCase):
    def test_first_version_has_no_fields_to_copy(self):
        form = start_new_rubric_version(self.club)

        self.assertEqual(current_rubric_form(self.club), form)
        self.assertEqual(form.fields.count(), 0)

    def test_new_version_copies_existing_fields_without_sharing_rows(self):
        original = self.build_rubric(fields=[("skill", Field.FieldType.NUMBER, {"required": True})])

        new_form = start_new_rubric_version(self.club)

        self.assertNotEqual(new_form.pk, original.pk)
        self.assertEqual(current_rubric_form(self.club), new_form)
        self.assertEqual(new_form.fields.get(key="skill").field_type, Field.FieldType.NUMBER)
        # Editing the copy must never touch the original's row.
        copied_field = new_form.fields.get(key="skill")
        copied_field.label = "Renamed"
        copied_field.save()
        self.assertNotEqual(Field.objects.get(form=original, key="skill").label, "Renamed")

    def test_old_evaluations_keep_referencing_their_original_version(self):
        original = self.build_rubric(fields=[("skill", Field.FieldType.NUMBER, {"required": True})])
        player = self.make_member("player7@example.com")
        coach = self.make_member("coach5@example.com")
        old_evaluation = submit_evaluation(club=self.club, player=player, season=self.season, evaluator=coach, data={"skill": "5"})

        start_new_rubric_version(self.club)

        old_evaluation.refresh_from_db()
        self.assertEqual(old_evaluation.submission.send.form, original)
        self.assertNotEqual(current_rubric_form(self.club), original)
