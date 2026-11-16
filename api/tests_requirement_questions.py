"""Onboarding requirements that are QUESTIONS (extra information) rather than documents: the staff API, answer validation per type, the
effects shared with documents (expiry, reopen, bypass, event gating, reminders) and the registrant's public answer endpoint."""

import datetime
import json
from decimal import Decimal

from django.test import Client, TestCase

from api.tests_registration import RegistrationMixin
from controlpanel.tests import login_session
from members.models import ClubMembership, MemberRequirementStatus
from members.services import onboarding, requirement_reminders
from members.services.errors import Invalid
from notifications.models import InboxNotification

BASE = "/api/v1/requirements"
TODAY = datetime.date.today()


class QuestionMixin(RegistrationMixin):
    def req(self, method, path, data=None, *, user=None):
        headers = {"HTTP_HOST": self.host, "HTTP_X_SESSION_TOKEN": login_session(user or self.owner)}
        body = {"data": json.dumps(data), "content_type": "application/json"} if data is not None else {}
        return getattr(Client(), method)(BASE + path, **body, **headers)

    def question(self, name="Allergies", question_type="text", **extra):
        r = self.req("post", "", {"name": name, "kind": "question", "question_type": question_type, **extra})
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()

    def membership(self, member=None):
        return ClubMembership.objects.get(member=member or self.cara, season=self.season)

    def answer(self, requirement, value, member=None, **extra):
        return self.req("post", f"/memberships/{self.membership(member).pk}/{requirement['id']}/answer", {"answer": value, **extra})

    def item(self, requirement, member=None):
        r = self.req("get", f"/members/{(member or self.cara).pk}/checklist")
        self.assertEqual(r.status_code, 200, r.content)
        return next(i for i in r.json()["items"] if i["requirement"]["id"] == requirement["id"])


class QuestionRequirementTests(QuestionMixin, TestCase):
    def test_question_definition_rules(self):
        r = self.req("post", "", {"name": "Q", "kind": "question"})
        self.assertEqual(r.status_code, 422)
        self.assertIn("question_type", r.json()["errors"])
        r = self.req("post", "", {"name": "Q", "kind": "question", "question_type": "choice"})
        self.assertEqual(r.status_code, 422)
        self.assertIn("options", r.json()["errors"])
        r = self.req("post", "", {"name": "Q", "kind": "question", "question_type": "number", "min_value": "5", "max_value": "1"})
        self.assertEqual(r.status_code, 422)
        self.assertIn("max_value", r.json()["errors"])
        r = self.req("post", "", {"name": "Q", "kind": "question", "question_type": "nonsense"})
        self.assertEqual(r.status_code, 422)

    def test_a_question_never_has_a_file_and_clears_unrelated_fields(self):
        q = self.question("Shoe size", "number", min_value="20", max_value="50", requires_document=True, options=["a"])
        self.assertEqual((q["kind"], q["requires_document"], q["options"]), ("question", False, None))
        self.assertEqual((Decimal(q["min_value"]), Decimal(q["max_value"])), (Decimal("20"), Decimal("50")))
        text = self.question("Notes", "text", min_value="1")
        self.assertIsNone(text["min_value"])
        doc = self.req("post", "", {"name": "Passport", "question_type": "text", "options": ["x"], "requires_document": True}).json()
        self.assertEqual((doc["kind"], doc["question_type"], doc["options"], doc["requires_document"]), ("document", "", None, True))

    def test_choice_options_are_normalised(self):
        q = self.question("Size", "choice", options=["S", {"value": "m", "label": "Medium"}])
        self.assertEqual(q["options"], ["S", {"value": "m", "label": "Medium"}])
        self.assertEqual(self.req("post", "", {"name": "Dup", "kind": "question", "question_type": "choice", "options": ["a", "a"]}).status_code, 422)

    def test_kind_and_type_cannot_change_once_used(self):
        q = self.question("Allergies")
        self.assertEqual(self.req("patch", f"/{q['id']}", {"question_type": "textarea"}).status_code, 200)  # unused: free to change
        self.assertEqual(self.answer(q, "none").status_code, 200)
        r = self.req("patch", f"/{q['id']}", {"question_type": "number"})
        self.assertEqual((r.status_code, r.json()["code"]), (422, "in_use"))
        r = self.req("patch", f"/{q['id']}", {"kind": "document"})
        self.assertEqual((r.status_code, r.json()["code"]), (422, "in_use"))
        self.assertEqual(self.req("patch", f"/{q['id']}", {"description": "Tell us"}).status_code, 200)

    def test_only_managers_answer(self):
        q = self.question()
        r = self.answer(q, "x")
        self.assertEqual(r.status_code, 200, r.content)
        headers = {"HTTP_HOST": self.host, "HTTP_X_SESSION_TOKEN": login_session(self.cara_user)}
        r = Client().post(BASE + f"/memberships/{self.membership().pk}/{q['id']}/answer", data=json.dumps({"answer": "y"}), content_type="application/json", **headers)
        self.assertEqual(r.status_code, 403)


class AnswerValidationTests(QuestionMixin, TestCase):
    def test_text_and_textarea(self):
        text = self.question("Nickname", "text")
        self.assertEqual(self.answer(text, "  Sharky  ").json()["answer"], "Sharky")
        self.assertEqual(self.answer(text, "").status_code, 422)
        self.assertEqual(self.answer(text, "x" * 1001).status_code, 422)
        long = self.question("Story", "textarea")
        self.assertEqual(self.answer(long, "x" * 1001).status_code, 200)

    def test_number_within_the_range(self):
        q = self.question("Shoe size", "number", min_value="20", max_value="50")
        r = self.answer(q, "10")
        self.assertEqual((r.status_code, "answer" in r.json()["errors"]), (422, True))
        self.assertEqual(self.answer(q, 51).status_code, 422)
        self.assertEqual(self.answer(q, "abc").status_code, 422)
        r = self.answer(q, "42,5")
        self.assertEqual((r.status_code, r.json()["answer"]), (200, "42.5"))
        self.assertEqual(self.answer(q, 42).json()["answer"], "42")
        self.assertEqual(self.answer(q, "30.0").json()["answer"], "30")

    def test_date(self):
        q = self.question("Surgery date", "date")
        self.assertEqual(self.answer(q, "2026-03-04").json()["answer"], "2026-03-04")
        self.assertEqual(self.answer(q, "04/03/2026").status_code, 422)

    def test_choice_must_be_one_of_the_options(self):
        q = self.question("Size", "choice", options=["S", {"value": "m", "label": "Medium"}])
        self.assertEqual(self.answer(q, "m").json()["answer"], "m")
        self.assertEqual(self.answer(q, "XL").status_code, 422)
        self.assertEqual(self.answer(q, ["S", "m"]).status_code, 422)  # a single choice takes one value

    def test_multichoice(self):
        q = self.question("Days", "multichoice", options=["mon", "tue", "wed"])
        self.assertEqual(self.answer(q, ["mon", "wed", "mon"]).json()["answer"], ["mon", "wed"])
        self.assertEqual(self.answer(q, ["mon", "fri"]).status_code, 422)
        self.assertEqual(self.answer(q, []).status_code, 422)

    def test_yes_no_is_stored_as_a_boolean(self):
        q = self.question("Photo OK?", "yes_no")
        self.assertIs(self.answer(q, True).json()["answer"], True)
        self.assertIs(self.answer(q, "no").json()["answer"], False)  # "No" is a real answer, not an empty one
        self.assertEqual(self.item(q)["display"], "complete")
        self.assertEqual(self.answer(q, "maybe").status_code, 422)

    def test_a_document_cannot_be_answered_and_a_question_cannot_be_ticked_off(self):
        doc = self.req("post", "", {"name": "Passport", "requires_document": True}).json()
        r = self.answer(doc, "x")
        self.assertEqual((r.status_code, r.json()["code"]), (422, "not_a_question"))
        q = self.question()
        r = Client().post(BASE + f"/memberships/{self.membership().pk}/{q['id']}/complete", data={"note": "done"}, HTTP_HOST=self.host, HTTP_X_SESSION_TOKEN=login_session(self.owner))
        self.assertEqual((r.status_code, r.json()["code"]), (422, "use_answer"))


class QuestionLifecycleTests(QuestionMixin, TestCase):
    def test_answering_completes_replaces_and_reopen_clears(self):
        q = self.question("Allergies")
        self.assertEqual((self.item(q)["display"], self.item(q)["answer"]), ("missing", None))
        r = self.answer(q, "peanuts", note="by phone")
        self.assertEqual((r.status_code, r.json()["state"], r.json()["display"], r.json()["answer"], r.json()["note"]), (200, "complete", "complete", "peanuts", "by phone"))
        self.assertEqual(self.answer(q, "none").json()["answer"], "none")  # replaced
        self.assertEqual(MemberRequirementStatus.objects.filter(requirement_id=q["id"]).count(), 1)
        r = self.req("post", f"/memberships/{self.membership().pk}/{q['id']}/reopen")
        self.assertEqual((r.json()["state"], r.json()["answer"]), ("open", None))
        status = MemberRequirementStatus.objects.get(requirement_id=q["id"])
        self.assertEqual((status.answer, status.is_complete), (None, False))

    def test_bypass_drops_the_answer(self):
        q = self.question()
        self.answer(q, "x")
        r = self.req("post", f"/memberships/{self.membership().pk}/{q['id']}/bypass", {"note": "not applicable"})
        self.assertEqual((r.json()["state"], r.json()["display"], r.json()["answer"]), ("bypassed", "bypassed", None))

    def test_the_answer_expires_like_a_document(self):
        q = self.question("Address confirmed", "yes_no", valid_for_months=12, reminder_days_before=30)
        self.answer(q, True)
        status = MemberRequirementStatus.objects.get(requirement_id=q["id"])
        self.assertIsNotNone(status.valid_until)
        MemberRequirementStatus.objects.update(valid_until=TODAY - datetime.timedelta(days=1))
        self.assertEqual((self.item(q)["state"], self.item(q)["display"]), ("open", "expired"))  # open again
        MemberRequirementStatus.objects.update(valid_until=TODAY + datetime.timedelta(days=5))
        self.assertEqual(self.item(q)["display"], "expiring_soon")

    def test_an_unanswered_question_keeps_a_member_out_of_that_kind_of_event(self):
        q = self.question("Allergies", blocked_event_kinds=["game"])
        key = ("game", self.season.pk)
        self.assertEqual(onboarding.blocked_member_ids({key: {self.cara.pk}})[key], {self.cara.pk})
        self.answer(q, "none")
        self.assertEqual(onboarding.blocked_member_ids({key: {self.cara.pk}})[key], set())
        MemberRequirementStatus.objects.update(valid_until=TODAY - datetime.timedelta(days=1))
        self.assertEqual(onboarding.blocked_member_ids({key: {self.cara.pk}})[key], {self.cara.pk})  # an expired answer blocks again

    def test_open_count_includes_unanswered_questions(self):
        q = self.question()
        before = len(onboarding.unresolved(self.membership()))
        self.answer(q, "x")
        self.assertEqual(len(onboarding.unresolved(self.membership())), before - 1)

    def test_validate_answer_service_is_plain(self):
        q = onboarding.create_requirement(self.owner, name="Age", kind="question", question_type="number", min_value=5, max_value=99)
        self.assertEqual(onboarding.validate_answer(q, "12"), "12")
        with self.assertRaises(Invalid):
            onboarding.validate_answer(q, "1")


class QuestionReminderTests(QuestionMixin, TestCase):
    def test_reminder_asks_to_answer_instead_of_provide(self):
        self.question("Allergies")
        self.assertGreaterEqual(requirement_reminders.send_due()["reminders"], 1)
        mine = InboxNotification.objects.filter(user=self.cara_user)
        titles = " | ".join(mine.values_list("title", flat=True))
        self.assertTrue(any(w in titles for w in ("Question not answered", "Vraag niet beantwoord")), titles)
        self.assertNotIn("Document", titles)

    def test_documents_keep_their_wording(self):
        onboarding.create_requirement(self.owner, name="Passport")
        requirement_reminders.send_due()
        titles = " | ".join(InboxNotification.objects.filter(user=self.cara_user).values_list("title", flat=True))
        self.assertTrue(any(w in titles for w in ("Document missing", "Document ontbreekt")), titles)


class PublicAnswerTests(QuestionMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.q = onboarding.create_requirement(self.owner, name="Allergies", kind="question", question_type="textarea")
        self.size = onboarding.create_requirement(self.owner, name="Shirt size", kind="question", question_type="choice", options=["S", {"value": "m", "label": "Medium"}])
        self.doc = onboarding.create_requirement(self.owner, name="Medical certificate", requires_document=True)

    def post(self, batch, requirement, value, token=None, **extra):
        body = {"answer": value, **extra}
        return Client().post(BASE.replace("requirements", f"public/registration/status/{token or batch.status_token}/answers/{requirement.pk}"), data=json.dumps(body), content_type="application/json", HTTP_HOST=self.host)

    def rows(self, batch, person=0):
        body = self.public("get", f"/status/{batch.status_token}").json()
        return {i["name"]: i for i in body["people"][person]["checklist"]}

    def test_the_status_page_lists_questions_with_their_shape(self):
        batch = self.batch()
        rows = self.rows(batch)
        self.assertEqual((rows["Allergies"]["kind"], rows["Allergies"]["question_type"], rows["Allergies"]["can_answer"], rows["Allergies"]["answer"]), ("question", "textarea", True, None))
        self.assertEqual(rows["Shirt size"]["options"], ["S", {"value": "m", "label": "Medium"}])
        self.assertEqual((rows["Medical certificate"]["kind"], rows["Medical certificate"]["can_answer"], rows["Medical certificate"]["can_upload"]), ("document", False, True))
        self.assertFalse(rows["Allergies"]["can_upload"])

    def test_the_registrant_answers_and_may_change_their_own_answer(self):
        batch = self.batch()
        r = self.post(batch, self.q, "hay fever")
        self.assertEqual(r.status_code, 201, r.content)
        row = next(i for i in r.json()["people"][0]["checklist"] if i["name"] == "Allergies")
        self.assertEqual((row["state"], row["answer"], row["can_answer"]), ("complete", "hay fever", True))
        status = batch.details.get().membership.requirement_statuses.get(requirement=self.q)
        self.assertEqual((status.is_complete, status.completed_by, "registrant" in status.note), (True, None, True))
        again = self.post(batch, self.q, "hay fever and nuts")
        self.assertEqual(again.status_code, 201)
        self.assertEqual(self.rows(batch)["Allergies"]["answer"], "hay fever and nuts")

    def test_staff_answers_and_bypass_close_the_question_for_the_registrant(self):
        batch = self.batch()
        membership = batch.details.get().membership
        onboarding.answer(self.owner, membership, self.q, "staff entered this")
        r = self.post(batch, self.q, "mine")
        self.assertEqual((r.status_code, r.json()["code"]), (422, "already_resolved"))
        self.assertFalse(self.rows(batch)["Allergies"]["can_answer"])
        onboarding.bypass(self.owner, membership, self.size, note="not needed")
        self.assertEqual(self.post(batch, self.size, "S").json()["code"], "already_resolved")

    def test_invalid_answers_are_keyed_answer(self):
        batch = self.batch()
        r = self.post(batch, self.size, "XL")
        self.assertEqual(r.status_code, 422)
        self.assertIn("answer", r.json()["errors"])
        self.assertEqual(self.post(batch, self.q, "").status_code, 422)
        self.assertEqual(self.rows(batch)["Shirt size"]["answer"], None)

    def test_rules(self):
        batch = self.batch()
        self.assertEqual(self.post(batch, self.doc, "x").json()["code"], "not_a_question")
        self.assertEqual(self.post(batch, self.q, "x", member_id="00000000-0000-0000-0000-000000000000").json()["code"], "unknown_person")
        self.assertEqual(self.post(batch, self.q, "x", token="bad-token").status_code, 404)
        inactive = onboarding.create_requirement(self.owner, name="Old question", kind="question", question_type="text", is_active=False)
        self.assertEqual(self.post(batch, inactive, "x").json()["code"], "unknown_requirement")
        self.staff("post", f"/registrations/{batch.pk}/cancel")
        self.assertEqual(self.post(batch, self.q, "x").json()["code"], "cancelled")

    def test_with_two_people_the_answer_says_who(self):
        batch = self.batch(entries=[self.entry("One"), self.entry("Two")])
        _, two = list(batch.details.order_by("created"))
        self.assertEqual(self.post(batch, self.q, "x").json()["code"], "unknown_person")
        self.assertEqual(self.post(batch, self.q, "x", member_id=str(two.member_id)).status_code, 201)
        body = self.public("get", f"/status/{batch.status_token}").json()
        answers = {p["name"]: next(i for i in p["checklist"] if i["name"] == "Allergies")["answer"] for p in body["people"]}
        self.assertEqual(answers, {"One Sharks": None, "Two Sharks": "x"})
