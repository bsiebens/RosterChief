"""Sponsor agreements: the installment schedule, the daily invoicing (idempotent), the status that follows the payments, and the grace period that
makes a sponsor inactive and active again. Plus the one-off invoice for someone who is not a member."""

import datetime
from decimal import Decimal

from django.utils import timezone

from api.tests_charges import ChargesBase
from members.models import Member
from shop.models import Invoice, Order
from shop.services import charges
from sponsoring import billing
from sponsoring.models import Sponsor, SponsorInstallment

TODAY = timezone.localdate()


def days(n):
    return TODAY + datetime.timedelta(days=n)


class SponsorBillingTests(ChargesBase):
    def setUp(self):
        super().setUp()
        self.sponsor = Sponsor.objects.create(name="Acme Bakery")

    def agreement(self, rows=((10, "100.00"), (100, "100.00")), **extra):
        body = {"contact_name": "Ann Baker", "contact_email": "ann@acme.test", "description": "Sponsorship 2026-27", "lead_days": 30, "grace_days": 14,
                "installments": [{"due_date": str(days(d)), "amount": a} for d, a in rows], **extra}  # fmt: skip
        return self.call("put", f"/sponsor-management/{self.sponsor.pk}/agreement", who="owner", data=body)

    def installments(self):
        return list(SponsorInstallment.objects.filter(agreement__sponsor=self.sponsor).order_by("due_date"))

    def pay(self, installment, amount):
        installment.refresh_from_db()
        charges.record_payment(installment.order, amount=Decimal(amount), method="cash")

    def make_overdue(self, installment, ago):
        """Pretend the installment's invoice has been due for ``ago`` days."""
        installment.refresh_from_db()
        Invoice.objects.filter(order=installment.order).update(due_date=days(-ago))
        SponsorInstallment.objects.filter(pk=installment.pk).update(due_date=days(-ago))

    # ------------------------------------------------------------------------------------------ the schedule

    def test_the_agreement_and_schedule_are_saved_read_and_validated(self):
        r = self.agreement()
        self.assertEqual(r.status_code, 200, r.content)
        data = r.json()
        self.assertEqual((data["contact_email"], data["grace_days"], [(i["due_date"], i["amount"], i["status"]) for i in data["installments"]]), ("ann@acme.test", 14, [(str(days(10)), "100.00", "scheduled"), (str(days(100)), "100.00", "scheduled")]))
        self.assertEqual(data["billing"]["status"], "paid_up")
        self.assertEqual(self.call("get", f"/sponsor-management/{self.sponsor.pk}/agreement", who="owner").json()["contact_name"], "Ann Baker")
        # replacing the schedule: keep the first (by id), drop the second, add a third
        first = data["installments"][0]
        r = self.call("put", f"/sponsor-management/{self.sponsor.pk}/agreement", who="owner", data={
            "contact_name": "Ann Baker", "contact_email": "ann@acme.test", "installments": [{"id": first["id"], "due_date": first["due_date"], "amount": "100.00"}, {"due_date": str(days(200)), "amount": "50.00"}]})  # fmt: skip
        self.assertEqual([i["amount"] for i in r.json()["installments"]], ["100.00", "50.00"])
        self.assertEqual(len(self.installments()), 2)
        # validation: no email, no installments, a zero amount
        self.assertEqual(self.agreement(contact_email="").status_code, 422)
        self.assertEqual(self.agreement(rows=()).status_code, 422)
        self.assertEqual(self.agreement(rows=((10, "0.00"),)).status_code, 422)

    def test_only_sponsor_managers_may_touch_agreements(self):
        self.assertEqual(self.call("get", f"/sponsor-management/{self.sponsor.pk}/agreement", who="plain").status_code, 403)
        self.assertEqual(self.call("put", f"/sponsor-management/{self.sponsor.pk}/agreement", who="charges", data={"contact_name": "x", "contact_email": "x@x.test", "installments": [{"due_date": str(days(5)), "amount": "5.00"}]}).status_code, 403)

    def test_an_invoiced_installment_is_fixed(self):
        self.agreement(rows=((10, "100.00"),))
        billing.invoice_due(TODAY)
        row = self.installments()[0]
        keep = {"id": str(row.pk), "due_date": str(row.due_date), "amount": "100.00"}
        self.assertEqual(self.call("put", f"/sponsor-management/{self.sponsor.pk}/agreement", who="owner", data={"contact_name": "A", "contact_email": "a@a.test", "installments": [{**keep, "amount": "90.00"}]}).status_code, 422)
        self.assertEqual(self.call("put", f"/sponsor-management/{self.sponsor.pk}/agreement", who="owner", data={"contact_name": "A", "contact_email": "a@a.test", "installments": [{"due_date": str(days(50)), "amount": "5.00"}]}).status_code, 422)
        self.assertEqual(self.call("put", f"/sponsor-management/{self.sponsor.pk}/agreement", who="owner", data={"contact_name": "A", "contact_email": "a@a.test", "installments": [keep]}).status_code, 200)

    # ------------------------------------------------------------------------------------------ the daily invoicing

    def test_invoicing_waits_for_the_lead_time_and_is_idempotent(self):
        self.agreement()  # due in 10 and 100 days, invoiced 30 days ahead
        first, second = self.installments()
        self.assertEqual(billing.invoice_due(TODAY), 1)
        self.assertEqual(billing.invoice_due(TODAY), 0)  # a second run changes nothing
        self.assertEqual(billing.invoice_due(days(60)), 0)  # still 40 days before the second
        self.assertEqual(Order.objects.filter(kind=Order.Kind.CHARGE).count(), 1)
        first.refresh_from_db()
        order = first.order
        self.assertEqual((order.total, order.purchaser.email, order.purchaser.first_name, order.purchaser.last_name), (Decimal("100.00"), "ann@acme.test", "Ann", "Baker"))
        self.assertIsNone(order.purchaser.user_id if hasattr(order.purchaser, "user_id") else None)  # a login-less record
        self.assertEqual(Invoice.objects.get(order=order).due_date, first.due_date)
        self.assertEqual(billing.invoice_due(days(75)), 1)
        second.refresh_from_db()
        self.assertEqual(second.order.purchaser_id, order.purchaser_id)  # the same payer record
        self.assertEqual(Member.objects.filter(email="ann@acme.test").count(), 1)
        self.assertEqual(billing.invoice_due(days(75)), 0)

    def test_a_cancelled_invoice_is_made_again_and_the_send_button_resends_an_open_one(self):
        self.agreement(rows=((10, "100.00"),))
        billing.invoice_due(TODAY)
        row = self.installments()[0]
        row.refresh_from_db()
        url = f"/sponsor-management/{self.sponsor.pk}/installments/{row.pk}/invoice"
        reminders = Invoice.objects.get(order=row.order).reminders_sent
        r = self.call("post", url, who="owner")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(Invoice.objects.get(order=row.order).reminders_sent, reminders + 1)  # sent again, no second invoice
        self.assertEqual(Order.objects.filter(kind=Order.Kind.CHARGE).count(), 1)
        charges.cancel(self.owner, row.order)
        self.assertEqual(self.call("get", f"/sponsor-management/{self.sponsor.pk}/agreement", who="owner").json()["installments"][0]["status"], "scheduled")
        self.assertEqual(billing.invoice_due(TODAY), 1)
        self.assertEqual(Order.objects.filter(kind=Order.Kind.CHARGE).count(), 2)
        row.refresh_from_db()
        self.pay(row, "100.00")
        self.assertEqual(self.call("post", url, who="owner").status_code, 409)  # paid: nothing to send

    # ------------------------------------------------------------------------------------------ status from the payments

    def test_the_status_follows_the_payments(self):
        self.agreement(rows=((10, "100.00"), (100, "60.00")))
        first, second = self.installments()
        self.assertEqual(billing.sponsor_status(self.sponsor)["status"], "paid_up")
        billing.invoice_due(TODAY)
        first.refresh_from_db()
        self.assertEqual((billing.status_of(first)["status"], billing.sponsor_status(self.sponsor)["status"]), ("open", "open"))
        self.pay(first, "40.00")
        first = SponsorInstallment.objects.select_related("order").get(pk=first.pk)
        self.assertEqual((billing.status_of(first)["status"], billing.status_of(first)["due"]), ("partially_paid", Decimal("60.00")))
        self.pay(first, "60.00")
        first = SponsorInstallment.objects.select_related("order").get(pk=first.pk)
        self.assertEqual((billing.status_of(first)["status"], billing.sponsor_status(self.sponsor)["status"]), ("paid", "paid_up"))
        self.assertEqual(billing.status_of(second)["status"], "scheduled")
        data = self.call("get", f"/sponsor-management/{self.sponsor.pk}/agreement", who="owner").json()
        self.assertEqual([i["status"] for i in data["installments"]], ["paid", "scheduled"])
        self.assertTrue(data["installments"][0]["invoice_number"])

    def test_overdue_counts_days_past_the_date(self):
        self.agreement(rows=((10, "100.00"),))
        billing.invoice_due(TODAY)
        row = self.installments()[0]
        self.make_overdue(row, 5)
        row.refresh_from_db()
        state = billing.status_of(row)
        self.assertEqual((state["status"], state["overdue_days"]), ("overdue", 5))
        self.assertEqual((billing.sponsor_status(self.sponsor)["status"], billing.sponsor_status(self.sponsor)["overdue_days"]), ("overdue", 5))

    # ------------------------------------------------------------------------------------------ the grace period

    def test_a_sponsor_past_its_grace_period_is_made_inactive_and_active_again_when_paid(self):
        self.agreement(rows=((10, "100.00"), (100, "100.00")), grace_days=14)
        billing.invoice_due(TODAY)
        row = self.installments()[0]
        self.make_overdue(row, 10)  # late, but within the grace period
        self.assertEqual(billing.enforce_grace(TODAY), {"suspended": 0, "reactivated": 0})
        self.assertIn(self.sponsor, Sponsor.active())
        self.make_overdue(row, 15)  # past it
        self.assertEqual(billing.enforce_grace(TODAY), {"suspended": 1, "reactivated": 0})
        self.assertEqual(billing.enforce_grace(TODAY), {"suspended": 0, "reactivated": 0})  # once
        self.sponsor.refresh_from_db()
        self.assertIsNotNone(self.sponsor.suspended_at)
        self.assertNotIn(self.sponsor, Sponsor.active())
        self.assertEqual(billing.sponsor_status(self.sponsor)["status"], "suspended")
        listed = next(s for s in self.call("get", "/sponsor-management", who="owner").json() if s["id"] == str(self.sponsor.pk))
        self.assertEqual((listed["status"], listed["billing"]["status"]), ("suspended", "suspended"))
        # a partial payment is not enough; paying the arrears reactivates at once (the payment hook), without waiting for the daily job
        self.pay(row, "50.00")
        self.sponsor.refresh_from_db()
        self.assertIsNotNone(self.sponsor.suspended_at)
        self.pay(row, "50.00")
        self.sponsor.refresh_from_db()
        self.assertIsNone(self.sponsor.suspended_at)
        self.assertIn(self.sponsor, Sponsor.active())

    def test_the_daily_job_reactivates_a_sponsor_whose_arrears_are_gone_and_leaves_manual_dates_alone(self):
        self.agreement(rows=((10, "100.00"),), grace_days=3)
        billing.invoice_due(TODAY)
        row = self.installments()[0]
        self.make_overdue(row, 4)
        billing.enforce_grace(TODAY)
        self.sponsor.refresh_from_db()
        self.assertIsNotNone(self.sponsor.suspended_at)
        SponsorInstallment.objects.filter(pk=row.pk).update(due_date=days(20))  # the schedule moved (invoice date too)
        Invoice.objects.filter(order=row.order).update(due_date=days(20))
        self.assertEqual(billing.enforce_grace(TODAY), {"suspended": 0, "reactivated": 1})
        self.sponsor.refresh_from_db()
        self.assertIsNone(self.sponsor.suspended_at)
        # a sponsor that is past its end date stays ended whatever the payments say
        other = Sponsor.objects.create(name="Old", start_date=days(-300), end_date=days(-10))
        self.assertEqual(billing.enforce_grace(TODAY), {"suspended": 0, "reactivated": 0})
        self.assertNotIn(other, Sponsor.active())

    def test_a_late_invoice_gets_its_grace_from_the_day_it_is_made(self):
        self.agreement(rows=((10, "100.00"),), grace_days=7, lead_days=0)
        row = self.installments()[0]
        SponsorInstallment.objects.filter(pk=row.pk).update(due_date=days(-20))  # the agreement was written after the date
        self.assertEqual(billing.invoice_due(TODAY), 1)
        row.refresh_from_db()
        self.assertEqual(billing.due_date_of(row), TODAY)
        self.assertEqual(billing.enforce_grace(TODAY), {"suspended": 0, "reactivated": 0})
        self.assertEqual(billing.enforce_grace(days(8)), {"suspended": 1, "reactivated": 0})


class ExternalInvoiceTests(ChargesBase):
    """A charge for someone who is not a member: the payer record is made behind the scenes."""

    LINE = {"description": "Pitch rental", "quantity": 2, "unit_price": "50.00"}

    def test_an_invoice_for_an_outside_payer_makes_the_login_less_record_and_reuses_it(self):
        body = {"payer": {"name": "Chris Outsider", "email": "Chris@Outside.test"}, "lines": [self.LINE]}
        r = self.call("post", "/finance/charges", data=body)
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual((r.json()["member"]["name"], r.json()["total"], r.json()["status"], r.json()["recipient_email"]), ("Chris Outsider", "100.00", "draft", "Chris@Outside.test"))
        again = self.call("post", "/finance/charges", data=body).json()
        self.assertEqual(again["member"]["id"], r.json()["member"]["id"])
        self.assertEqual(Member.objects.filter(email__iexact="chris@outside.test").count(), 1)
        sent = self.call("post", f"/finance/charges/{r.json()['id']}/send", data={})
        self.assertEqual(sent.status_code, 200, sent.content)

    def test_it_needs_a_member_or_a_payer_with_a_name_and_an_email(self):
        for body in ({"lines": [self.LINE]}, {"payer": {"name": " ", "email": "a@b.test"}, "lines": [self.LINE]}, {"payer": {"name": "A B", "email": "nope"}, "lines": [self.LINE]}):
            self.assertEqual(self.call("post", "/finance/charges", data=body).status_code, 422, body)
        self.assertEqual(self.call("post", "/finance/charges", data={"member_id": str(self.buyer.pk), "lines": [self.LINE]}).status_code, 201)
