"""The central invoices list (``/finance/invoices``): registrations, shop invoices and charges in one place, each shown only to who may see its kind."""

import datetime
from decimal import Decimal
from unittest import mock

from django.test import TestCase
from django_tenants.utils import schema_context

from api.tests_charges import ChargesBase
from api.tests_registration import RegistrationMixin
from authorization.services import assign_role, create_role
from members.models import Member
from shop.models import Invoice
from shop.services import charges, orders


class CentralInvoicesTests(RegistrationMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        with schema_context(cls.club.schema_name):
            finance = create_role("Finance", ["registration.manage_finance"], slug="finance")
            charge_role = create_role("Charges", ["finance.manage_charges"], slug="charges")
            assign_role(cls.dan_user, finance)
            assign_role(cls.bob_user, charge_role)

    def setUp(self):
        super().setUp()
        patcher = mock.patch("shop.services.notifications.dispatch")
        patcher.start()
        self.addCleanup(patcher.stop)
        commit = mock.patch("django.db.transaction.on_commit", side_effect=lambda fn, *a, **k: fn())
        commit.start()
        self.addCleanup(commit.stop)

    def invoices(self, user=None, query=""):
        r = self.staff("get", "/finance/invoices" + query, user=user)
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def registration(self, tag="a"):
        batch = self.batch(entries=[self.entry(f"One{tag}"), self.entry(f"Two{tag}")], contact=self.contact(email=f"pat{tag}@join.test"))
        self.assertEqual(self.staff("post", f"/registrations/{batch.pk}/confirm", {"send": False}).status_code, 200)
        batch.refresh_from_db()
        return batch

    def charge(self, amount="25.00", send=True):
        member = Member.objects.create(first_name="Fined", last_name="Person")
        order = charges.create_draft(self.owner, member=member, lines=[charges.ChargeLine("Fine: late", 1, Decimal(amount))])
        if send:
            charges.send(self.owner, order)
        return order

    def test_registrations_and_charges_are_listed_together_newest_first(self):
        batch = self.registration()  # 200
        sent = self.charge()
        draft = self.charge(send=False)
        data = self.invoices()
        self.assertEqual((data["count"], sorted(data["types"])), (3, ["charge", "registration"]))
        by_id = {row["id"]: row for row in data["results"]}
        reg = by_id[str(batch.pk)]
        self.assertEqual((reg["type"], reg["number"], reg["status"], reg["total"], reg["amount_due"], reg["href"]), ("registration", batch.invoice_number, "open", "200.00", "200.00", f"/finance/{batch.pk}"))
        self.assertEqual((by_id[str(sent.pk)]["type"], by_id[str(sent.pk)]["status"], by_id[str(sent.pk)]["href"]), ("charge", "open", f"/finance/charges/{sent.pk}"))
        self.assertEqual((by_id[str(draft.pk)]["status"], by_id[str(draft.pk)]["number"]), ("draft", None))
        issued = [row["issued_at"] or "" for row in data["results"] if row["issued_at"]]
        self.assertEqual(issued, sorted(issued, reverse=True))

    def test_filters_by_type_status_and_search(self):
        batch = self.registration()
        paid = self.charge("10.00")
        self.staff("post", f"/finance/charges/{paid.pk}/payments", {"amount": "10.00", "method": "cash"})
        overdue = self.charge("40.00")
        Invoice.objects.filter(order=overdue).update(due_date=datetime.date.today() - datetime.timedelta(days=4))
        self.charge("5.00", send=False)
        types = {t: self.invoices(query=f"?type={t}")["count"] for t in ("registration", "charge")}
        self.assertEqual(types, {"registration": 1, "charge": 3})
        by_status = {s: [r["id"] for r in self.invoices(query=f"?status={s}")["results"]] for s in ("open", "overdue", "paid", "draft", "cancelled")}
        self.assertEqual((len(by_status["draft"]), by_status["overdue"], by_status["paid"], by_status["cancelled"]), (1, [str(overdue.pk)], [str(paid.pk)], []))
        self.assertIn(str(batch.pk), by_status["open"])
        self.assertEqual({r["id"] for r in self.invoices(query="?status=open&type=charge")["results"]}, {str(overdue.pk)})  # overdue ones are open too
        self.assertEqual(self.invoices(query=f"?q={batch.invoice_number}")["count"], 1)
        self.assertEqual(self.invoices(query="?q=Fined")["count"], 3)
        self.assertEqual(self.invoices(query="?q=nobody-here")["count"], 0)

    def test_paging_merges_the_kinds(self):
        self.registration()
        for _ in range(3):
            self.charge()
        first = self.invoices(query="?limit=2")
        second = self.invoices(query="?limit=2&offset=2")
        self.assertEqual((first["count"], len(first["results"]), len(second["results"])), (4, 2, 2))
        ids = [r["id"] for r in first["results"] + second["results"]]
        self.assertEqual(len(set(ids)), 4)

    def test_the_summary_counts_and_adds_up_each_kind(self):
        batch = self.registration()
        self.staff("post", f"/registrations/{batch.pk}/payments", {"amount": "50.00", "method": "cash"})
        self.charge("25.00")
        summary = self.invoices()["summary"]
        self.assertEqual((summary["registration"]["open"], summary["registration"]["open_amount"], summary["registration"]["overdue"]), (1, "150.00", 0))
        self.assertEqual((summary["charge"]["open"], summary["charge"]["open_amount"], summary["charge"]["drafts"]), (1, "25.00", 0))

    def test_each_kind_is_shown_only_to_who_may_see_it(self):
        batch = self.registration()
        order = self.charge()
        only_finance = self.invoices(user=self.dan_user)
        self.assertEqual((only_finance["types"], [r["id"] for r in only_finance["results"]]), (["registration"], [str(batch.pk)]))
        only_charges = self.invoices(user=self.bob_user)
        self.assertEqual((only_charges["types"], [r["id"] for r in only_charges["results"]]), (["charge"], [str(order.pk)]))
        self.assertEqual(self.invoices(user=self.bob_user, query="?type=registration")["count"], 0)  # asking for a kind that is not theirs gives nothing
        self.assertEqual(self.staff("get", "/finance/invoices", user=self.ann_user).status_code, 403)


class ShopInCentralTests(ChargesBase):
    def test_shop_invoices_need_the_shop_feature_and_permission_and_are_listed_with_charges(self):
        product = self.merch("Scarf", "30.00")
        from shop.services.orders import LineSpec

        order = orders.create_order(self.owner, purchaser=self.buyer, specs=[LineSpec(product, None, 2)])
        from shop.services import invoices

        invoices.create_invoice(order)
        charge = charges.create_draft(self.owner, member=self.buyer, lines=[charges.ChargeLine("Fine", 1, Decimal("12.00"))])
        charges.send(self.owner, charge)
        data = self.call("get", "/finance/invoices", who="owner").json()
        self.assertEqual(sorted(data["types"]), ["charge", "registration", "shop"])  # the club owner may see every kind
        rows = {r["type"]: r for r in data["results"]}
        self.assertEqual((rows["shop"]["summary"], rows["shop"]["total"], rows["shop"]["href"]), ("Scarf", "60.00", f"/shop/orders/{order.pk}"))
        self.assertEqual(rows["charge"]["summary"], "Fine")
        # permissions: shop invoices for shop.manage_invoices, charges for finance.manage_charges
        only_shop = self.call("get", "/finance/invoices", who="invoices").json()
        self.assertEqual((only_shop["types"], [r["type"] for r in only_shop["results"]]), (["shop"], ["shop"]))
        self.assertEqual([r["type"] for r in self.call("get", "/finance/invoices", who="charges").json()["results"]], ["charge"])
        self.assertEqual(self.call("get", "/finance/invoices", who="orders").status_code, 403)
        # without the shop feature the shop kind disappears, charges stay
        from features.entitlements import Entitlements

        self.features = Entitlements({})
        self.assertEqual(sorted(self.call("get", "/finance/invoices", who="owner").json()["types"]), ["charge", "registration"])
