"""The finance side of registrations: its own permission, balances, payments against the invoice (full, partial, per person, with a shop voucher)."""

import datetime
from decimal import Decimal
from unittest import mock

from django.test import TestCase
from django_tenants.utils import schema_context

from api.tests_registration import RegistrationMixin
from authorization.services import assign_role, create_role
from members.models import ClubMembership, FeePayment
from registration.models import RegistrationBatch
from shop.models import Voucher

NEXT_MONTH = datetime.date.today() + datetime.timedelta(days=30)


class FinanceTests(RegistrationMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        with schema_context(cls.club.schema_name):
            finance = create_role("Finance", ["registration.manage_finance"], slug="finance")
            members = create_role("Member admins", ["members.manage_members"], slug="member-admins")
        cls.finance_user, cls.admin_user = cls.dan_user, cls.bob_user
        with schema_context(cls.club.schema_name):
            assign_role(cls.finance_user, finance)
            assign_role(cls.admin_user, members)

    def two(self, tag=""):
        """A confirmed registration of two players at 100 each."""
        batch = self.batch(entries=[self.entry(f"One{tag}"), self.entry(f"Two{tag}")], contact=self.contact(email=f"pat{tag}@join.test"))
        self.assertEqual(self.staff("post", f"/registrations/{batch.pk}/confirm", {"send": False}).status_code, 200)
        return batch

    def pay(self, batch, body, user=None):
        return self.staff("post", f"/registrations/{batch.pk}/payments", body, user=user or self.finance_user)

    def test_permission_split(self):
        batch = self.two()
        for method, path in (("get", "/registrations"), ("get", f"/registrations/{batch.pk}"), ("post", f"/registrations/{batch.pk}/cancel")):
            body = {} if method == "post" else None
            self.assertEqual(self.staff(method, path, body, user=self.admin_user).status_code, 403, path)
            self.assertNotEqual(self.staff(method, path, body, user=self.finance_user).status_code, 403, path)
        self.assertEqual(self.pay(batch, {"amount": "10"}, user=self.admin_user).status_code, 403)
        self.assertEqual(self.staff("get", "/registration/settings", user=self.admin_user).status_code, 200)
        self.assertEqual(self.staff("get", "/registration/settings", user=self.finance_user).status_code, 403)
        self.assertTrue(self.staff("get", "/me", user=self.finance_user).json()["can_manage_finance"])
        self.assertFalse(self.staff("get", "/me", user=self.admin_user).json()["can_manage_finance"])

    def test_summary_totals_follow_the_invoices_and_the_filters(self):
        first, second = self.two("a"), self.two("b")  # 200 each
        self.pay(first, {"amount": "150"})
        self.pay(second, {"amount": "200"})
        self.batch()  # still awaiting review: not counted
        summary = self.staff("get", "/finance/summary", user=self.finance_user)
        self.assertEqual(summary.status_code, 200, summary.content)
        self.assertEqual(summary.json(), {"invoiced": "400.00", "received": "350.00", "open": "50.00", "overdue": "0.00", "overdue_count": 0})
        RegistrationBatch.objects.filter(pk=first.pk).update(invoice_due_date=datetime.date.today() - datetime.timedelta(days=3))
        late = self.staff("get", "/finance/summary", user=self.finance_user).json()
        self.assertEqual((late["overdue"], late["overdue_count"], late["open"]), ("50.00", 1, "50.00"))
        first.refresh_from_db()
        only_first = self.staff("get", f"/finance/summary?q={first.invoice_number}", user=self.finance_user).json()
        self.assertEqual((only_first["invoiced"], only_first["received"]), ("200.00", "150.00"))
        season = self.staff("get", f"/finance/summary?season_id={first.season_id}&from={datetime.date.today()}&to={datetime.date.today()}", user=self.finance_user).json()
        self.assertEqual(season["invoiced"], "400.00")
        self.assertEqual(self.staff("get", f"/finance/summary?from={datetime.date.today() + datetime.timedelta(days=1)}", user=self.finance_user).json()["invoiced"], "0.00")
        self.staff("post", f"/registrations/{second.pk}/cancel", user=self.finance_user)
        self.assertEqual(self.staff("get", "/finance/summary", user=self.finance_user).json()["invoiced"], "200.00")

    def test_summary_needs_the_finance_permission(self):
        self.assertEqual(self.staff("get", "/finance/summary", user=self.admin_user).status_code, 403)

    def test_partial_then_full_payment_spreads_over_the_people(self):
        batch = self.two()
        r = self.pay(batch, {"amount": "150.00", "method": "bank_transfer", "reference": "INV 1"})
        self.assertEqual(r.status_code, 201, r.content)
        body = r.json()
        self.assertEqual((body["amount_due"], body["amount_paid"], body["balance"], body["payment_status"]), ("200.00", "150.00", "50.00", "partial"))
        one, two = body["lines"]
        self.assertEqual((one["fee_status"], one["balance"]), ("paid", "0.00"))
        self.assertEqual((two["fee_status"], two["balance"], two["amount_paid"]), ("partially_paid", "50.00", "50.00"))
        self.assertEqual(len(body["payments"]), 2)
        r = self.pay(batch, {"amount": "50.00"})
        self.assertEqual((r.json()["payment_status"], r.json()["balance"]), ("paid", "0.00"))

    def test_more_than_open_is_refused(self):
        batch = self.two()
        r = self.pay(batch, {"amount": "200.01"})
        self.assertEqual(r.status_code, 422, r.content)
        self.assertEqual(FeePayment.objects.count(), 0)

    def test_per_person_allocation(self):
        batch = self.two()
        one, two = (d.membership_id for d in batch.details.order_by("created"))
        r = self.pay(batch, {"amount": "70", "allocations": [{"membership_id": str(two), "amount": "70"}]})
        self.assertEqual(r.status_code, 201, r.content)
        lines = {line["membership_id"]: line for line in r.json()["lines"]}
        self.assertEqual((lines[str(one)]["amount_paid"], lines[str(two)]["amount_paid"]), ("0.00", "70.00"))
        # must add up, must not exceed what the person owes, must be on the invoice
        self.assertEqual(self.pay(batch, {"amount": "70", "allocations": [{"membership_id": str(one), "amount": "60"}]}).status_code, 422)
        self.assertEqual(self.pay(batch, {"amount": "40", "allocations": [{"membership_id": str(two), "amount": "40"}]}).status_code, 422)
        self.assertEqual(self.pay(batch, {"amount": "10", "allocations": [{"membership_id": str(self.ann.club_memberships.first().pk), "amount": "10"}]}).status_code, 422)

    def test_mark_paid_settles_the_rest(self):
        batch = self.two()
        self.pay(batch, {"amount": "30"})
        r = self.staff("post", f"/registrations/{batch.pk}/mark-paid", {"method": "cash"}, user=self.finance_user)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual((r.json()["payment_status"], r.json()["amount_paid"]), ("paid", "200.00"))
        again = self.staff("post", f"/registrations/{batch.pk}/mark-paid", {"method": "cash"}, user=self.finance_user)
        self.assertEqual(again.status_code, 422)

    def test_delete_payment(self):
        batch = self.two()
        paid = self.pay(batch, {"amount": "100"}).json()["payments"][0]
        r = self.staff("delete", f"/registrations/{batch.pk}/payments/{paid['id']}", user=self.finance_user)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual((r.json()["amount_paid"], r.json()["payment_status"], r.json()["payments"]), ("0.00", "unpaid", []))

    def test_unconfirmed_and_cancelled_cannot_be_paid(self):
        batch = self.batch()
        self.assertEqual(self.pay(batch, {"amount": "10"}).status_code, 422)
        self.staff("post", f"/registrations/{batch.pk}/cancel", user=self.finance_user)
        self.assertEqual(self.pay(batch, {"amount": "10"}).status_code, 422)

    @mock.patch("features.entitlements.Entitlements.has", return_value=True)
    def test_voucher_payment_takes_and_returns_the_balance(self, _has):
        voucher = Voucher.objects.create(number="V-1", amount=Decimal("120"), expiry_date=NEXT_MONTH)
        batch = self.two()
        lookup = self.staff("get", "/registrations/vouchers/lookup?number=v-1", user=self.finance_user)
        self.assertEqual((lookup.status_code, lookup.json()["available"], lookup.json()["status"]), (200, "120.00", "usable"))
        r = self.pay(batch, {"amount": "150", "method": "voucher", "voucher_number": "V-1"})
        self.assertEqual(r.status_code, 422, "more than the voucher holds")
        r = self.pay(batch, {"amount": "120", "method": "voucher", "voucher_number": "V-1"})
        self.assertEqual(r.status_code, 201, r.content)
        voucher.refresh_from_db()
        self.assertEqual(voucher.consumed_amount, Decimal("120"))
        self.assertEqual({p["voucher_number"] for p in r.json()["payments"]}, {"V-1"})
        self.assertEqual(self.pay(batch, {"amount": "10", "method": "voucher"}).status_code, 422, "needs a voucher number")
        for p in r.json()["payments"]:
            self.staff("delete", f"/registrations/{batch.pk}/payments/{p['id']}", user=self.finance_user)
        voucher.refresh_from_db()
        self.assertEqual(voucher.consumed_amount, Decimal("0"))

    def test_list_filters_and_amounts(self):
        paid, part, open_ = self.two("a"), self.two("b"), self.two("c")
        self.pay(paid, {"amount": "200"})
        self.pay(part, {"amount": "50"})
        RegistrationBatch.objects.filter(pk=open_.pk).update(invoice_due_date=datetime.date.today() - datetime.timedelta(days=1))

        def ids(**q):
            query = "&".join(f"{k}={v}" for k, v in {"status": "confirmed", **q}.items())
            return {row["id"] for row in self.staff("get", f"/registrations?{query}", user=self.finance_user).json()["results"]}

        self.assertEqual(ids(payment="paid"), {str(paid.pk)})
        self.assertEqual(ids(payment="partial"), {str(part.pk)})
        self.assertEqual(ids(payment="unpaid"), {str(open_.pk)})
        self.assertEqual(ids(payment="open"), {str(part.pk), str(open_.pk)})
        self.assertEqual(ids(payment="overdue"), {str(open_.pk)})
        self.assertEqual(ids(), {str(paid.pk), str(part.pk), str(open_.pk)})
        row = next(r for r in self.staff("get", "/registrations?status=confirmed", user=self.finance_user).json()["results"] if r["id"] == str(part.pk))
        self.assertEqual((row["amount_due"], row["amount_paid"], row["balance"], row["payment_status"]), ("200.00", "50.00", "150.00", "partial"))

    def test_member_admin_can_still_record_a_fee_payment_on_the_member_page(self):
        batch = self.two()
        mid = batch.details.first().membership_id
        r = self.staff("post", f"/memberships/{mid}/payments", {"amount": "10"}, user=self.admin_user)
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(ClubMembership.objects.get(pk=mid).amount_paid, Decimal("10.00"))

    def test_early_bird_shows_while_it_applies_and_goes_when_passed(self):
        from api.tests_registration import TODAY
        from shop.models import DiscountType

        self.product.early_bird_discount_type, self.product.early_bird_discount_amount, self.product.early_bird_deadline = DiscountType.FIXED_AMOUNT, Decimal("15"), TODAY + datetime.timedelta(days=3)
        self.product.save()
        batch = self.two()
        status = lambda: self.public("get", f"/status/{batch.status_token}").json()  # noqa: E731
        detail = lambda: self.staff("get", f"/registrations/{batch.pk}", user=self.finance_user).json()  # noqa: E731
        for body in (status(), detail()):
            self.assertEqual({k: body["early_bird"][k] for k in ("discount", "amount_due", "balance")}, {"discount": "30.00", "amount_due": "170.00", "balance": "170.00"})
            self.assertEqual(body["early_bird"]["deadline"], str(TODAY + datetime.timedelta(days=3)))
        ClubMembership.objects.update(early_payment_deadline=TODAY - datetime.timedelta(days=1))
        self.assertIsNone(status()["early_bird"])
        self.assertIsNone(detail()["early_bird"])

    def test_a_person_with_nothing_to_pay_reads_as_paid_once_confirmed(self):
        batch = self.batch(entries=[{"who": "self", "kind": "volunteer", "variant_id": str(self.free_variant.pk)}])
        m = batch.details.get().membership
        self.assertEqual(ClubMembership.objects.get(pk=m.pk).fee_status, "unpaid")  # not confirmed yet: the price is still under review
        self.assertEqual(self.staff("post", f"/registrations/{batch.pk}/confirm", {"send": False}).status_code, 200)
        self.assertEqual(ClubMembership.objects.get(pk=m.pk).fee_status, "paid")
        self.assertEqual(self.public("get", f"/status/{batch.status_token}").json()["people"][0]["fee"]["fee_status"], "paid")
        row = self.staff("get", "/signups").json()["results"][0]
        self.assertEqual(row["fee_status"], "paid")

    def test_members_with_an_open_fee_outside_registrations(self):
        from members.models import Member
        from members.services import memberships

        batch = self.two("z")  # on a registration: not listed here
        with schema_context(self.club.schema_name):
            lone = Member.objects.create(first_name="Lone", last_name="Wolf", email="lone@x.test")
            ms = memberships.create_membership(self.owner, lone, self.season, fee_amount=80)
        rows = self.staff("get", "/finance/open-members", user=self.finance_user).json()
        self.assertEqual([(r["member"]["name"], r["balance"]) for r in rows["results"]], [("Lone Wolf", "80.00")])
        self.assertEqual(self.staff("get", "/finance/open-members", user=self.admin_user).status_code, 403)
        pay = lambda amount: self.staff("post", f"/finance/memberships/{ms.pk}/payments", {"amount": amount}, user=self.finance_user)  # noqa: E731
        self.assertEqual(pay("90").status_code, 422)
        r = pay("30")
        self.assertEqual((r.status_code, r.json()["balance"], r.json()["fee_status"]), (201, "50.00", "partially_paid"))
        self.assertEqual(pay("50").status_code, 201)
        self.assertEqual(self.staff("get", "/finance/open-members", user=self.finance_user).json()["count"], 0)
        self.assertEqual(batch.details.count(), 2)
