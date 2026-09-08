import datetime

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from club.models import Club, ClubMembership, Season
from formbuilder.models import Answer, Field, Form, FormSend, Submission
from members.models import Member

from .models import EvaluationChecklist, PlayerEvaluation
from .services import (
    EvaluationRubricNotConfigured,
    EvaluationSubmissionError,
    current_rubric_form,
    question_stats,
    results_matrix,
    start_new_rubric_version,
    submit_evaluation,
    walkthrough_queue,
)


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

    def make_checklist(self, name="U8", club=None):
        club = club or self.club
        return EvaluationChecklist.objects.create(club=club, name=name)

    def build_rubric(self, checklist=None, fields=None):
        """A Form + Fields set as ``checklist``'s current rubric, mirroring
        what evaluations.services.start_new_rubric_version would produce."""
        checklist = checklist or self.make_checklist()
        form = Form.objects.create(club=checklist.club, title="Player rubric")
        for order, (key, field_type, kwargs) in enumerate(fields or []):
            Field.objects.create(form=form, key=key, field_type=field_type, label=key, order=order, **kwargs)
        checklist.form = form
        checklist.save(update_fields=["form"])
        return checklist


class EvaluationChecklistModelTests(EvaluationsTestCase):
    def test_slug_is_derived_from_name_and_unique_per_club(self):
        first = EvaluationChecklist.objects.create(club=self.club, name="U8")
        second = EvaluationChecklist.objects.create(club=self.club, name="U8")

        self.assertEqual(first.slug, "u8")
        self.assertEqual(second.slug, "u8-2")

    def test_same_name_is_fine_in_a_different_club(self):
        EvaluationChecklist.objects.create(club=self.club, name="U8")
        other = EvaluationChecklist.objects.create(club=self.other_club, name="U8")

        self.assertEqual(other.slug, "u8")


class PlayerEvaluationModelTests(EvaluationsTestCase):
    def test_clean_rejects_a_player_from_another_club(self):
        checklist = self.build_rubric()
        send = FormSend.objects.create(club=self.club, form=checklist.form)
        submission = Submission.objects.create(send=send)
        outsider = self.make_member("outsider@example.com", club=self.other_club)

        evaluation = PlayerEvaluation(club=self.club, checklist=checklist, player=outsider, season=self.season, submission=submission)

        with self.assertRaises(ValidationError):
            evaluation.clean()

    def test_clean_rejects_a_submission_from_another_clubs_send(self):
        other_checklist = self.build_rubric(self.make_checklist(club=self.other_club))
        other_send = FormSend.objects.create(club=self.other_club, form=other_checklist.form)
        submission = Submission.objects.create(send=other_send)
        player = self.make_member("player@example.com")
        checklist = self.build_rubric()

        evaluation = PlayerEvaluation(club=self.club, checklist=checklist, player=player, season=self.season, submission=submission)

        with self.assertRaises(ValidationError):
            evaluation.clean()

    def test_clean_rejects_a_checklist_from_another_club(self):
        checklist = self.build_rubric()
        other_checklist = self.build_rubric(self.make_checklist(club=self.other_club))
        send = FormSend.objects.create(club=self.club, form=checklist.form)
        submission = Submission.objects.create(send=send)
        player = self.make_member("player0@example.com")

        evaluation = PlayerEvaluation(club=self.club, checklist=other_checklist, player=player, season=self.season, submission=submission)

        with self.assertRaises(ValidationError):
            evaluation.clean()

    def test_a_member_can_have_several_evaluations_the_same_season(self):
        # No uniqueness constraint by design -- several coaches' perspectives
        # on the same player/season are a feature, not a duplicate to reject.
        checklist = self.build_rubric()
        send = FormSend.objects.create(club=self.club, form=checklist.form)
        player = self.make_member("player2@example.com")

        for _ in range(3):
            submission = Submission.objects.create(send=send)
            PlayerEvaluation.objects.create(club=self.club, checklist=checklist, player=player, season=self.season, submission=submission)

        self.assertEqual(player.evaluations_received.count(), 3)


class SubmitEvaluationTests(EvaluationsTestCase):
    def test_raises_when_the_checklist_has_no_rubric_configured_yet(self):
        checklist = self.make_checklist()
        player = self.make_member("player3@example.com")
        coach = self.make_member("coach@example.com")

        with self.assertRaises(EvaluationRubricNotConfigured):
            submit_evaluation(club=self.club, checklist=checklist, player=player, season=self.season, evaluator=coach, data={})

    def test_submits_and_stores_answers_against_the_current_rubric(self):
        checklist = self.build_rubric(fields=[("skill", Field.FieldType.NUMBER, {"required": True}), ("notes", Field.FieldType.TEXTAREA, {"required": False})])
        player = self.make_member("player4@example.com")
        coach = self.make_member("coach2@example.com")

        evaluation = submit_evaluation(club=self.club, checklist=checklist, player=player, season=self.season, evaluator=coach, data={"skill": "4", "notes": ""})

        self.assertEqual(evaluation.player, player)
        self.assertEqual(evaluation.checklist, checklist)
        self.assertEqual(evaluation.season, self.season)
        self.assertEqual(evaluation.submission.member, coach)
        # "notes" was blank -- validated but not stored, same rule as
        # formbuilder.services.submission._clean_answers.
        self.assertEqual(Answer.objects.filter(submission=evaluation.submission).count(), 1)
        self.assertEqual(Answer.objects.get(submission=evaluation.submission, field__key="skill").value, "4")

    def test_invalid_answers_raise_and_persist_nothing(self):
        checklist = self.build_rubric(fields=[("skill", Field.FieldType.NUMBER, {"required": True})])
        player = self.make_member("player5@example.com")
        coach = self.make_member("coach3@example.com")

        with self.assertRaises(EvaluationSubmissionError):
            submit_evaluation(club=self.club, checklist=checklist, player=player, season=self.season, evaluator=coach, data={"skill": "not-a-number"})

        self.assertEqual(PlayerEvaluation.objects.count(), 0)
        self.assertEqual(Submission.objects.count(), 0)

    def test_the_backing_formsend_never_surfaces_as_a_real_audience_send(self):
        """The plumbing FormSend evaluations create must stay invisible to
        formbuilder's own audience resolution -- otherwise every active club
        member would see "Player rubric" appear in their own general "Forms
        to complete" list. club_wide=False with no teams/groups/invited
        members means effective_members(send) is always empty."""
        from formbuilder.services.audience import effective_members

        checklist = self.build_rubric(fields=[("skill", Field.FieldType.NUMBER, {"required": True})])
        player = self.make_member("player6@example.com")
        coach = self.make_member("coach4@example.com")

        submit_evaluation(club=self.club, checklist=checklist, player=player, season=self.season, evaluator=coach, data={"skill": "3"})

        send = FormSend.objects.get(club=self.club)
        self.assertFalse(send.club_wide)
        self.assertFalse(send.is_active)
        self.assertEqual(effective_members(send).count(), 0)


class StartNewRubricVersionTests(EvaluationsTestCase):
    def test_first_version_has_no_fields_to_copy(self):
        checklist = self.make_checklist()
        form = start_new_rubric_version(checklist)

        self.assertEqual(current_rubric_form(checklist), form)
        self.assertEqual(form.fields.count(), 0)

    def test_new_version_copies_existing_fields_without_sharing_rows(self):
        checklist = self.build_rubric(fields=[("skill", Field.FieldType.NUMBER, {"required": True})])
        original = checklist.form

        new_form = start_new_rubric_version(checklist)

        self.assertNotEqual(new_form.pk, original.pk)
        self.assertEqual(current_rubric_form(checklist), new_form)
        self.assertEqual(new_form.fields.get(key="skill").field_type, Field.FieldType.NUMBER)
        # Editing the copy must never touch the original's row.
        copied_field = new_form.fields.get(key="skill")
        copied_field.label = "Renamed"
        copied_field.save()
        self.assertNotEqual(Field.objects.get(form=original, key="skill").label, "Renamed")

    def test_old_evaluations_keep_referencing_their_original_version(self):
        checklist = self.build_rubric(fields=[("skill", Field.FieldType.NUMBER, {"required": True})])
        original = checklist.form
        player = self.make_member("player7@example.com")
        coach = self.make_member("coach5@example.com")
        old_evaluation = submit_evaluation(club=self.club, checklist=checklist, player=player, season=self.season, evaluator=coach, data={"skill": "5"})

        start_new_rubric_version(checklist)

        old_evaluation.refresh_from_db()
        self.assertEqual(old_evaluation.submission.send.form, original)
        self.assertNotEqual(current_rubric_form(checklist), original)

    def test_two_checklists_version_independently(self):
        u8 = self.build_rubric(self.make_checklist("U8"), fields=[("skill", Field.FieldType.NUMBER, {"required": True})])
        u10 = self.build_rubric(self.make_checklist("U10"), fields=[("tactics", Field.FieldType.NUMBER, {"required": True})])

        start_new_rubric_version(u8)

        self.assertEqual(current_rubric_form(u10).fields.get().key, "tactics")


class QuestionStatsTests(EvaluationsTestCase):
    def test_numeric_field_reports_average_min_max(self):
        checklist = self.build_rubric(fields=[("skill", Field.FieldType.NUMBER, {"required": True})])
        coach = self.make_member("coach6@example.com")
        for value, player_email in [("2", "p1@example.com"), ("4", "p2@example.com"), ("6", "p3@example.com")]:
            submit_evaluation(club=self.club, checklist=checklist, player=self.make_member(player_email), season=self.season, evaluator=coach, data={"skill": value})

        rows = question_stats(checklist)

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["kind"], "numeric")
        self.assertEqual(row["response_count"], 3)
        self.assertEqual(row["average"], 4)
        self.assertEqual(row["minimum"], 2)
        self.assertEqual(row["maximum"], 6)

    def test_choice_field_reports_a_distribution(self):
        checklist = self.build_rubric(fields=[("level", Field.FieldType.CHOICE, {"required": True, "options": ["Beginner", "Advanced"]})])
        coach = self.make_member("coach7@example.com")
        for value, player_email in [("Beginner", "p4@example.com"), ("Beginner", "p5@example.com"), ("Advanced", "p6@example.com")]:
            submit_evaluation(club=self.club, checklist=checklist, player=self.make_member(player_email), season=self.season, evaluator=coach, data={"level": value})

        row = question_stats(checklist)[0]

        self.assertEqual(row["kind"], "distribution")
        self.assertEqual(dict(row["distribution"]), {"Beginner": 2, "Advanced": 1})

    def test_spans_every_version_of_the_checklist_by_matching_field_key(self):
        checklist = self.build_rubric(fields=[("skill", Field.FieldType.NUMBER, {"required": True})])
        coach = self.make_member("coach8@example.com")
        submit_evaluation(club=self.club, checklist=checklist, player=self.make_member("p7@example.com"), season=self.season, evaluator=coach, data={"skill": "3"})

        # A new version keeps the same key -- the old Answer must still count.
        start_new_rubric_version(checklist)
        submit_evaluation(club=self.club, checklist=checklist, player=self.make_member("p8@example.com"), season=self.season, evaluator=coach, data={"skill": "5"})

        row = question_stats(checklist)[0]
        self.assertEqual(row["response_count"], 2)
        self.assertEqual(row["average"], 4)

    def test_a_second_checklists_answers_never_leak_in(self):
        u8 = self.build_rubric(self.make_checklist("U8"), fields=[("skill", Field.FieldType.NUMBER, {"required": True})])
        u10 = self.build_rubric(self.make_checklist("U10"), fields=[("skill", Field.FieldType.NUMBER, {"required": True})])
        coach = self.make_member("coach9@example.com")
        submit_evaluation(club=self.club, checklist=u8, player=self.make_member("p9@example.com"), season=self.season, evaluator=coach, data={"skill": "1"})
        submit_evaluation(club=self.club, checklist=u10, player=self.make_member("p10@example.com"), season=self.season, evaluator=coach, data={"skill": "9"})

        self.assertEqual(question_stats(u8)[0]["response_count"], 1)
        self.assertEqual(question_stats(u8)[0]["average"], 1)


class ResultsMatrixTests(EvaluationsTestCase):
    def test_shows_each_players_latest_answer_only(self):
        checklist = self.build_rubric(fields=[("skill", Field.FieldType.NUMBER, {"required": True})])
        coach = self.make_member("coach10@example.com")
        player = self.make_member("p11@example.com")
        submit_evaluation(club=self.club, checklist=checklist, player=player, season=self.season, evaluator=coach, data={"skill": "2"})
        submit_evaluation(club=self.club, checklist=checklist, player=player, season=self.season, evaluator=coach, data={"skill": "9"})

        matrix = results_matrix(checklist)

        self.assertEqual(len(matrix["rows"]), 1)
        self.assertEqual(matrix["rows"][0]["values"], ["9"])


class WalkthroughQueueTests(EvaluationsTestCase):
    def test_excludes_players_never_evaluated_on_this_checklist(self):
        checklist = self.build_rubric(fields=[("skill", Field.FieldType.NUMBER, {"required": True})])
        self.make_member("never-evaluated@example.com")

        queue = list(walkthrough_queue(checklist, cutoff_date=timezone.localdate() + datetime.timedelta(days=1)))

        self.assertEqual(queue, [])

    def test_excludes_players_evaluated_after_the_cutoff(self):
        checklist = self.build_rubric(fields=[("skill", Field.FieldType.NUMBER, {"required": True})])
        coach = self.make_member("coach11@example.com")
        player = self.make_member("p12@example.com")
        submit_evaluation(club=self.club, checklist=checklist, player=player, season=self.season, evaluator=coach, data={"skill": "5"})

        queue = list(walkthrough_queue(checklist, cutoff_date=timezone.localdate() - datetime.timedelta(days=1)))

        self.assertEqual(queue, [])

    def test_includes_players_evaluated_before_the_cutoff_most_overdue_first(self):
        checklist = self.build_rubric(fields=[("skill", Field.FieldType.NUMBER, {"required": True})])
        coach = self.make_member("coach12@example.com")
        older, newer = self.make_member("older@example.com"), self.make_member("newer@example.com")
        evaluation_older = submit_evaluation(club=self.club, checklist=checklist, player=older, season=self.season, evaluator=coach, data={"skill": "5"})
        evaluation_newer = submit_evaluation(club=self.club, checklist=checklist, player=newer, season=self.season, evaluator=coach, data={"skill": "5"})
        PlayerEvaluation.objects.filter(pk=evaluation_older.pk).update(created=timezone.now() - datetime.timedelta(days=10))
        PlayerEvaluation.objects.filter(pk=evaluation_newer.pk).update(created=timezone.now() - datetime.timedelta(days=1))

        queue = list(walkthrough_queue(checklist, cutoff_date=timezone.localdate() + datetime.timedelta(days=1)))

        self.assertEqual([row["player_id"] for row in queue], [older.pk, newer.pk])

    def test_exclude_player_ids_drops_a_player_from_the_queue(self):
        checklist = self.build_rubric(fields=[("skill", Field.FieldType.NUMBER, {"required": True})])
        coach = self.make_member("coach13@example.com")
        player = self.make_member("p13@example.com")
        submit_evaluation(club=self.club, checklist=checklist, player=player, season=self.season, evaluator=coach, data={"skill": "5"})

        queue = list(walkthrough_queue(checklist, cutoff_date=timezone.localdate() + datetime.timedelta(days=1), exclude_player_ids=[player.pk]))

        self.assertEqual(queue, [])

    def test_an_evaluation_on_a_different_checklist_does_not_resolve_this_ones_staleness(self):
        """Each checklist's queue is scoped strictly to its own evaluations
        -- a fresh U10 evaluation doesn't retroactively clear a stale U8
        entry for the same player. In practice a promoted player's old
        checklist simply stops being visited (nobody files new U8
        evaluations for them any more), not that the system removes them;
        an archived checklist is the deliberate way to stop offering it at
        all (EvaluationWalkthroughView.blocked_reason)."""
        u8 = self.build_rubric(self.make_checklist("U8"), fields=[("skill", Field.FieldType.NUMBER, {"required": True})])
        u10 = self.build_rubric(self.make_checklist("U10"), fields=[("skill", Field.FieldType.NUMBER, {"required": True})])
        coach = self.make_member("coach14@example.com")
        player = self.make_member("p14@example.com")
        old_evaluation = submit_evaluation(club=self.club, checklist=u8, player=player, season=self.season, evaluator=coach, data={"skill": "5"})
        PlayerEvaluation.objects.filter(pk=old_evaluation.pk).update(created=timezone.now() - datetime.timedelta(days=200))
        submit_evaluation(club=self.club, checklist=u10, player=player, season=self.season, evaluator=coach, data={"skill": "7"})

        u8_queue = list(walkthrough_queue(u8, cutoff_date=timezone.localdate()))

        self.assertEqual([row["player_id"] for row in u8_queue], [player.pk])
