import datetime
from decimal import Decimal
from unittest import mock

from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from api.tests_registration import RegistrationMixin
from club.services import grant_login
from events import tasks
from events.models import Attendance, Event
from events.services import attendance
from members.models import ClubMembership, OnboardingRequirement
from members.services.errors import Invalid, NotAllowed
from notifications.models import InboxNotification
from registration.services import config, installments, payment_reminders


class InstallmentScheduleMathTests(SimpleTestCase):
    def test_rounding_keeps_every_cent(self):
        self.assertEqual(installments.split_amount(Decimal("100.00"), 3), [Decimal("33.33"), Decimal("33.34"), Decimal("33.33")])
        self.assertEqual(sum(installments.split_amount(Decimal("0.02"), 3)), Decimal("0.02"))


class RegistrationInstallmentTests(RegistrationMixin, TestCase):
    def test_installment_choice_and_policy_validation(self):
        response = self.register(use_installments=True)
        self.assertEqual((response.status_code, response.json()["code"]), (422, "installments_unavailable"))
        response = self.staff("put", "/registration/settings", {"installment_offsets": [30, 10], "installment_block_kinds": ["invalid"]})
        self.assertEqual(response.status_code, 422, response.content)
        response = self.staff("put", "/registration/settings", {"installment_offsets": [30, 60], "installment_grace_days": 2, "installment_block_kinds": ["training"]})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.public("get", "").json()["installment_offsets"], [30, 60])
        batch = self.batch(use_installments=True)
        config.update_settings(self.owner, installment_offsets=[20])
        self.assertEqual(self.public("get", f"/status/{batch.status_token}").json()["installment_offsets"], [30, 60])

    def test_current_plan_can_activate_after_first_payment_when_requirements_are_complete(self):
        config.update_settings(self.owner, installment_offsets=[30])
        batch = self.batch(use_installments=True)
        member = batch.details.get().member
        membership = batch.details.get().membership
        event = Event.objects.create(title="Club event", start=timezone.now() + datetime.timedelta(days=1), season=self.season, club_wide=True)
        confirm = self.staff("post", f"/registrations/{batch.pk}/confirm", {"send": False})
        self.assertEqual(confirm.status_code, 200, confirm.content)
        approve = f"/signups/{membership.pk}/approve"
        before = self.staff("post", approve)
        self.assertEqual((before.status_code, before.json()["code"]), (422, "installment_required"))

        payment = self.staff("post", f"/registrations/{batch.pk}/payments", {"amount": "50.00"})
        self.assertEqual(payment.status_code, 201, payment.content)
        requirement = OnboardingRequirement.objects.create(name="Medical clearance")
        still_pending = self.staff("post", approve)
        self.assertEqual(still_pending.json()["errors"]["requirements"], [requirement.name])
        requirement.delete()
        ready = self.staff("get", "/signups?only=clean")
        self.assertIn(str(membership.pk), [row["membership_id"] for row in ready.json()["results"]])
        with mock.patch.object(tasks.sync_club_wide_events, "delay") as sync, self.captureOnCommitCallbacks(execute=True):
            approved = self.staff("post", approve)
        self.assertEqual(approved.status_code, 200, approved.content)
        membership.refresh_from_db()
        self.assertEqual((membership.status, membership.fee_status), (ClubMembership.StatusChoices.ACTIVE, ClubMembership.FeeStatus.PARTIALLY_PAID))
        sync.assert_called_once_with()
        tasks.sync_club_wide_events()
        self.assertTrue(Attendance.objects.filter(event=event, member=member).exists())

    def test_staff_set_and_clear_a_plan_on_a_confirmed_invoice(self):
        batch = self.batch()
        url = f"/registrations/{batch.pk}/installments"
        early = self.staff("put", url, {"offsets": [30]})
        self.assertEqual((early.status_code, early.json()["code"]), (422, "not_confirmed"))
        self.assertEqual(self.staff("post", f"/registrations/{batch.pk}/confirm", {"send": False}).status_code, 200)
        bad = self.staff("put", url, {"offsets": [60, 30]})
        self.assertEqual(bad.status_code, 422, bad.content)
        planned = self.staff("put", url, {"offsets": [30, 60]})
        self.assertEqual(planned.status_code, 200, planned.content)
        body = planned.json()
        self.assertEqual(body["installment_offsets"], [30, 60])
        self.assertEqual(len(body["installments"]), 3)
        self.assertEqual(sum(Decimal(row["amount"]) for row in body["installments"]), Decimal(body["amount_due"]))
        cleared = self.staff("put", url, {"offsets": []})
        self.assertEqual((cleared.json()["installment_offsets"], cleared.json()["installments"]), ([], []))

    def test_finance_managers_hear_10_and_5_days_before_a_payment_falls_due(self):
        today = timezone.localdate()
        batch = self.batch()
        self.assertEqual(self.staff("post", f"/registrations/{batch.pk}/confirm", {"due_date": str(today + datetime.timedelta(days=10)), "send": False}).status_code, 200)
        inbox = InboxNotification.objects.filter(user=self.owner)
        self.assertEqual(payment_reminders.send_due(today)["notices"], 1)
        notice = inbox.get()
        self.assertEqual((notice.href, "10" in notice.title), ("/finance?payment=open", True))
        payment_reminders.send_due(today)  # a second run on the same day says nothing new
        self.assertEqual(inbox.count(), 1)
        self.assertEqual(payment_reminders.send_due(today + datetime.timedelta(days=1))["notices"], 0)  # 9 days left: not a lead time
        self.assertEqual(payment_reminders.send_due(today + datetime.timedelta(days=5))["notices"], 1)  # 5 days left
        self.assertEqual(inbox.count(), 2)
        self.assertEqual(self.staff("post", f"/registrations/{batch.pk}/mark-paid", {"method": "cash"}).status_code, 200)
        self.assertEqual(payment_reminders.send_due(today)["notices"], 0)  # nothing open: nothing to check

    def test_staff_see_a_late_instalment_on_the_checklist_and_in_signups(self):
        config.update_settings(self.owner, installment_offsets=[30], installment_grace_days=2, installment_block_kinds=["training"])
        batch = self.batch(use_installments=True)
        detail = batch.details.get()
        first_due = timezone.localdate() - datetime.timedelta(days=5)
        self.assertEqual(self.staff("post", f"/registrations/{batch.pk}/confirm", {"due_date": str(first_due), "send": False}).status_code, 200)
        checklist = self.staff("get", f"/requirements/members/{detail.member_id}/checklist?season_id={detail.membership.season_id}").json()
        self.assertEqual((checklist["overdue"]["due_date"], checklist["overdue"]["blocking"], checklist["overdue"]["block_kinds"]), (str(first_due), True, ["training"]))
        row = next(r for r in self.staff("get", "/signups").json()["results"] if r["membership_id"] == str(detail.membership_id))
        self.assertEqual(row["overdue"]["remaining"], checklist["overdue"]["remaining"])
        config.update_settings(self.owner, installment_grace_days=10)
        checklist = self.staff("get", f"/requirements/members/{detail.member_id}/checklist?season_id={detail.membership.season_id}").json()
        self.assertEqual((checklist["overdue"] is not None, checklist["overdue"]["blocking"]), (True, False))  # late, still inside the grace period
        self.assertEqual(self.staff("post", f"/registrations/{batch.pk}/mark-paid", {"method": "cash"}).status_code, 200)
        checklist = self.staff("get", f"/requirements/members/{detail.member_id}/checklist?season_id={detail.membership.season_id}").json()
        self.assertIsNone(checklist["overdue"])

    def test_an_instalment_plan_reminds_per_instalment(self):
        today = timezone.localdate()
        batch = self.batch()
        self.assertEqual(self.staff("post", f"/registrations/{batch.pk}/confirm", {"due_date": str(today - datetime.timedelta(days=20)), "send": False}).status_code, 200)
        self.assertEqual(self.staff("put", f"/registrations/{batch.pk}/installments", {"offsets": [30]}).status_code, 200)  # second part due in 10 days
        self.assertEqual(payment_reminders.send_due(today)["notices"], 1)
        self.assertEqual(InboxNotification.objects.filter(user=self.owner).count(), 1)

    def test_late_installment_blocks_guardian_and_clears_after_recorded_payment(self):
        config.update_settings(self.owner, installment_offsets=[30], installment_grace_days=2, installment_block_kinds=["training"])
        batch = self.batch(entries=[self.entry("First"), self.entry("Second")], use_installments=True)
        guardian, _ = grant_login(self.club, batch.contact)
        member = batch.details.first().member
        today = timezone.localdate()
        first_due = today - datetime.timedelta(days=2)
        confirmed = self.staff("post", f"/registrations/{batch.pk}/confirm", {"due_date": str(first_due), "send": False})
        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        self.assertEqual([row["amount"] for row in confirmed.json()["installments"]], ["100.00", "100.00"])
        self.assertTrue(all(line["next_installment"]["late"] for line in confirmed.json()["lines"]))
        event = Event.objects.create(title="Training", start=timezone.now() + datetime.timedelta(days=1), season=self.season, kind=Event.EventKind.TRAINING)
        event.invited_members.add(member)
        invitation = Attendance.objects.get(event=event, member=member)
        self.assertEqual(attendance.set_answer_as_member(guardian, invitation, "present").status, "present")
        with self.assertRaises(NotAllowed):
            attendance.set_answer_as_member(self.ann_user, invitation, "present")

        config.update_settings(self.owner, installment_grace_days=1)
        with self.assertRaises(Invalid) as refused:
            attendance.set_answer_as_member(guardian, invitation, "present")
        self.assertEqual(refused.exception.code, "late_installment")
        self.assertEqual(attendance.set_answer_as_member(guardian, invitation, "absent", "Away").status, "absent")
        event.kind = Event.EventKind.GAME
        event.save(update_fields=["kind"])
        self.assertEqual(attendance.set_answer_as_member(guardian, invitation, "maybe").status, "maybe")
        event.kind = Event.EventKind.TRAINING
        event.save(update_fields=["kind"])

        overdue = "/registrations?status=confirmed&payment=overdue"
        self.assertEqual(self.staff("get", overdue).json()["count"], 1)
        paid = self.staff("post", f"/registrations/{batch.pk}/payments", {"amount": "100.00"})
        self.assertEqual(paid.status_code, 201, paid.content)
        self.assertEqual([line["amount_paid"] for line in paid.json()["lines"]], ["50.00", "50.00"])
        self.assertEqual(paid.json()["next_installment"]["due_date"], str(first_due + datetime.timedelta(days=30)))
        self.assertEqual(self.staff("get", overdue).json()["count"], 0)
        self.assertEqual(attendance.set_answer_as_member(guardian, invitation, "present").status, "present")
