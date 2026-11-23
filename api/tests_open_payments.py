""""Open payments": the Payments page and its badge on the club side, the Payments tab on the member side. Every sent, unpaid or partly paid, not
cancelled invoice counts: charges (fines, tournament team fees) and shop orders as well as registrations."""

from decimal import Decimal

from api.tests_charges import ChargesBase
from shop.services import charges, invoices, orders


class OpenPaymentsTests(ChargesBase):
    def shop_invoice(self, qty=2):
        from shop.services.orders import LineSpec

        order = orders.create_order(self.owner, purchaser=self.buyer, specs=[LineSpec(self.merch("Scarf", "30.00"), None, qty)])
        invoices.create_invoice(order)
        return order

    def charge(self, amount="12.00", send=True):
        order = charges.create_draft(self.owner, member=self.buyer, lines=[charges.ChargeLine("Fine", 1, Decimal(amount))])
        if send:
            charges.send(self.owner, order)
        return order

    def test_orders_type_lists_open_charges_and_shop_orders_not_drafts_paid_or_cancelled(self):
        shop = self.shop_invoice()
        sent = self.charge("12.00")
        part = self.charge("40.00")
        charges.record_payment(part, amount=Decimal("15.00"), method="cash")
        paid = self.charge("5.00")
        charges.record_payment(paid, amount=Decimal("5.00"), method="cash")
        cancelled = self.charge("7.00")
        charges.cancel(self.owner, cancelled)
        self.charge("9.00", send=False)
        data = self.call("get", "/finance/invoices?type=orders&status=open", who="owner").json()
        self.assertEqual({r["id"] for r in data["results"]}, {str(shop.pk), str(sent.pk), str(part.pk)})
        self.assertEqual({r["type"] for r in data["results"]}, {"shop", "charge"})
        summary = data["summary"]
        self.assertEqual((summary["shop"]["open"], summary["shop"]["open_amount"]), (1, "60.00"))
        self.assertEqual((summary["charge"]["open"], summary["charge"]["open_amount"]), (2, "37.00"))  # 12 + (40 - 15)
        self.assertEqual((summary["charge"]["invoiced_amount"], summary["charge"]["received_amount"]), ("57.00", "20.00"))  # cancelled and draft left out

    def test_the_member_payments_list_has_open_charges_and_shop_orders_and_never_cuts_an_open_one_off(self):
        from api.routers import portal

        shop = self.shop_invoice()
        first = self.charge("12.00")
        for _ in range(22):  # more paid invoices than the recent list shows
            done = self.charge("1.00")
            charges.record_payment(done, amount=Decimal("1.00"), method="cash")
        people = {self.buyer.pk: self.buyer}
        rows = portal._open_and_recent_charges(people)
        open_rows = [r for r in rows if r["status"] != "paid"]
        self.assertEqual({r["id"] for r in open_rows}, {shop.pk, first.pk})
        self.assertEqual({r["kind"] for r in open_rows}, {"shop", "charge"})
        self.assertEqual(sum(Decimal(r["due"]) for r in open_rows), Decimal("72.00"))
        self.assertEqual(len([r for r in rows if r["status"] == "paid"]), 20)
        charges.cancel(self.owner, first)
        self.assertNotIn(first.pk, {r["id"] for r in portal._open_and_recent_charges(people)})
