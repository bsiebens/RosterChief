"""Charge invoices (``/finance/charges``): drafts, sending, payments, reminders, emails, their separation from the shop, and shop VAT."""

import datetime
import json
import uuid
from decimal import Decimal
from unittest import mock

from django.core import mail
from django.db import connection
from django.test import Client, TestCase
from django.utils import timezone
from django_tenants.utils import schema_context

from authorization.services import assign_role, create_role
from club.services import grant_login
from controlpanel.tests import login_session
from features.entitlements import Entitlements
from members.models import Member
from shop.models import Discount, DiscountType, Invoice, Order, OrderLine, Payment, Product
from shop.services import charges, emails, invoices, notifications, orders
from shop.services import payments as payment_svc
from shop.settings_models import ShopSettings
from shop.tests import ShopClubMixin

BASE = "/api/v1"
FINE = {"description": "Fine: unsportsmanlike conduct", "quantity": 1, "unit_price": "25.00"}


class ChargesBase(ShopClubMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        with schema_context(cls.club.schema_name):
            member = Member.objects.create(first_name="Fiona", last_name="Finance", email="fiona@shop.test")
            role = create_role("Charges", ["finance.manage_charges"])
        user, _ = grant_login(cls.club, member)
        with schema_context(cls.club.schema_name):
            assign_role(user, role)
        cls.users["charges"] = user

    def setUp(self):
        super().setUp()
        ShopSettings.current()  # the settings row exists (tests change it with queryset updates)
        patcher = mock.patch("shop.services.notifications.dispatch")
        self.dispatch = patcher.start()
        self.addCleanup(patcher.stop)
        # a test runs inside one transaction, where on_commit callbacks would never fire: run them at once
        commit = mock.patch("django.db.transaction.on_commit", side_effect=lambda fn, *a, **k: fn())
        commit.start()
        self.addCleanup(commit.stop)

    def call(self, method, path, who="charges", data=None):
        user = self.owner if who == "owner" else self.users[who]
        body = {"data": json.dumps(data), "content_type": "application/json"} if data is not None else {}
        response = getattr(Client(), method)(BASE + path, **body, HTTP_HOST=self.host, HTTP_X_SESSION_TOKEN=login_session(user))
        connection.set_tenant(self.club)
        return response

    def draft(self, lines=None, **extra):
        r = self.call("post", "/finance/charges", data={"member_id": str(self.buyer.pk), "lines": lines or [FINE], **extra})
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()

    def sent(self, **extra):
        charge = self.draft(**extra)
        r = self.call("post", f"/finance/charges/{charge['id']}/send", data={})
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()


class ChargeApiTests(ChargesBase):
    def test_only_the_charges_permission_opens_it_and_the_shop_feature_is_not_needed(self):
        cid = uuid.uuid4()
        for method, path, body in [("get", "/finance/charges", None), ("get", f"/finance/charges/{cid}", None), ("post", "/finance/charges", {"member_id": str(uuid.uuid4()), "lines": [FINE]}), ("get", "/finance/charges/settings", None)]:
            for who in ("plain", "orders", "superset", "invoices"):
                self.assertEqual(self.call(method, path, who=who, data=body).status_code, 403, (who, path))
            for who in ("charges", "owner"):
                self.assertNotEqual(self.call(method, path, who=who, data=body).status_code, 403, (who, path))
        self.features = Entitlements({})  # a club without the shop can still write invoices
        self.assertEqual(self.call("get", "/finance/charges").status_code, 200)
        me = self.call("get", "/me").json()
        self.assertTrue(me["can_manage_charges"])
        self.assertFalse(self.call("get", "/me", who="plain").json()["can_manage_charges"])

    def test_a_draft_can_be_written_changed_and_deleted_and_nothing_is_sent(self):
        charge = self.draft(lines=[FINE, {"description": "Late registration", "quantity": 2, "unit_price": "7.50"}], notes="Match of 3 Nov")
        self.assertEqual((charge["status"], charge["number"], charge["is_draft"]), ("draft", None, True))
        self.assertEqual((charge["total"], charge["summary"]), ("40.00", "Fine: unsportsmanlike conduct +1"))
        self.assertEqual([ln["line_total"] for ln in charge["lines"]], ["25.00", "15.00"])
        self.assertFalse(Invoice.objects.exists())
        self.dispatch.assert_not_called()
        r = self.call("put", f"/finance/charges/{charge['id']}", data={"lines": [{"description": "Lost jersey", "quantity": 1, "unit_price": "60.00"}], "notes": "Replaced"})
        self.assertEqual((r.status_code, r.json()["total"], r.json()["notes"]), (200, "60.00", "Replaced"))
        self.assertEqual(Order.objects.get(pk=charge["id"]).lines.count(), 1)
        self.assertEqual(self.call("delete", f"/finance/charges/{charge['id']}").status_code, 204)
        self.assertFalse(Order.objects.filter(pk=charge["id"]).exists())

    def test_the_input_is_checked(self):
        member = str(self.buyer.pk)
        bad = [
            ({"member_id": member, "lines": []}, "lines"),
            ({"member_id": member, "lines": [{**FINE, "unit_price": "0"}]}, "lines"),
            ({"member_id": member, "lines": [{**FINE, "description": "  "}]}, "lines.0"),
            ({"member_id": str(uuid.uuid4()), "lines": [FINE]}, "member_id"),
            ({"member_id": member, "lines": [FINE], "due_date": str(timezone.localdate() - datetime.timedelta(days=1))}, "due_date"),
        ]
        for body, _field in bad:
            r = self.call("post", "/finance/charges", data=body)
            self.assertEqual(r.status_code, 422, (body, r.content))
        self.assertFalse(Order.objects.filter(kind="charge").exists())

    def test_sending_issues_the_invoice_tells_the_member_and_fixes_the_lines(self):
        due = timezone.localdate() + datetime.timedelta(days=30)
        charge = self.sent(due_date=str(due))
        self.assertEqual((charge["status"], charge["is_draft"], charge["due_date"]), ("sent", False, str(due)))
        self.assertTrue(charge["number"].startswith("INV-"))
        self.assertEqual(charge["recipient_email"], "bea@shop.test")
        self.dispatch.assert_called_once()
        self.assertEqual(self.dispatch.call_args.args[0], "charge")
        for method, body in [("put", {"notes": "x"}), ("delete", None), ("post", {})]:
            path = f"/finance/charges/{charge['id']}" + ("/send" if method == "post" else "")
            self.assertEqual(self.call(method, path, data=body).status_code, 409, (method, path))

    def test_default_due_date_follows_the_shop_terms(self):
        ShopSettings.current()
        ShopSettings.objects.update(invoice_due_days=21)
        charge = self.sent()
        self.assertEqual(charge["due_date"], str(timezone.localdate() + datetime.timedelta(days=21)))

    def test_payments_settle_it_and_a_paid_or_part_paid_invoice_cannot_be_cancelled(self):
        draft = self.draft()
        self.assertEqual(self.call("post", f"/finance/charges/{draft['id']}/payments", data={"amount": "5.00", "method": "cash"}).status_code, 409)  # nothing to pay yet
        charge = self.sent()
        cid = charge["id"]
        r = self.call("post", f"/finance/charges/{cid}/payments", data={"amount": "10.00", "method": "bank_transfer", "reference": "OGM"})
        self.assertEqual(r.status_code, 201, r.content)
        detail = self.call("get", f"/finance/charges/{cid}").json()
        self.assertEqual((detail["status"], detail["amount_paid"], detail["amount_due"], detail["can_cancel"]), ("partially_paid", "10.00", "15.00", False))
        self.assertEqual(self.call("post", f"/finance/charges/{cid}/cancel").status_code, 409)
        self.call("post", f"/finance/charges/{cid}/payments", data={"amount": "15.00", "method": "cash"})
        detail = self.call("get", f"/finance/charges/{cid}").json()
        self.assertEqual((detail["status"], detail["amount_due"], detail["can_remind"]), ("paid", "0.00", False))
        # an unpaid one can be cancelled; the member is told
        other = self.sent()
        r = self.call("post", f"/finance/charges/{other['id']}/cancel")
        self.assertEqual((r.status_code, r.json()["status"]), (200, "cancelled"))
        self.assertIn("charge_cancelled", [c.args[0] for c in self.dispatch.call_args_list])
        self.assertEqual(self.call("post", f"/finance/charges/{other['id']}/payments", data={"amount": "1.00", "method": "cash"}).status_code, 409)

    def test_the_list_filters_by_status_and_adds_up_what_is_open(self):
        self.draft()
        open_one = self.sent()
        paid = self.sent()
        self.call("post", f"/finance/charges/{paid['id']}/payments", data={"amount": "25.00", "method": "cash"})
        overdue = self.sent()
        Invoice.objects.filter(order_id=overdue["id"]).update(due_date=timezone.localdate() - datetime.timedelta(days=5))
        listing = self.call("get", "/finance/charges").json()
        self.assertEqual(listing["count"], 4)
        s = listing["summary"]
        self.assertEqual((s["drafts"], s["open"], s["overdue"], s["paid"], s["cancelled"]), (1, 2, 1, 1, 0))
        self.assertEqual((s["open_amount"], s["overdue_amount"]), ("50.00", "25.00"))
        by_status = {st: [c["id"] for c in self.call("get", f"/finance/charges?status={st}").json()["results"]] for st in ("draft", "open", "overdue", "paid")}
        self.assertEqual((len(by_status["draft"]), by_status["overdue"], by_status["paid"]), (1, [overdue["id"]], [paid["id"]]))
        self.assertIn(open_one["id"], by_status["open"])
        self.assertEqual(self.call("get", "/finance/charges?q=bea").json()["count"], 4)
        self.assertEqual(self.call("get", "/finance/charges?q=nobody-here").json()["count"], 0)

    def test_charges_stay_out_of_the_shop(self):
        merch = self.merch("Jersey", "40.00")
        shop_order, _ = self.make_order(merch)
        charge = self.sent()
        self.assertEqual([o["id"] for o in self.call("get", "/shop/orders", who="orders").json()["results"]], [str(shop_order.pk)])
        self.assertEqual(self.call("get", f"/shop/orders/{charge['id']}", who="orders").status_code, 404)
        self.assertEqual(self.call("get", "/shop/invoices", who="owner").json()["count"], 0)
        self.assertEqual(self.call("get", f"/shop/orders/{charge['id']}/invoice", who="owner").status_code, 404)
        names = [p["name"] for p in self.call("get", "/shop/products", who="products").json()["results"]]
        self.assertEqual(names, ["Jersey"])  # the hidden charge product is not listed
        stats = self.call("get", "/shop/stats", who="orders").json()
        self.assertEqual((stats["orders_total"], stats["unpaid_count"], stats["outstanding"]), (1, 1, "40.00"))  # the unpaid charge is not counted
        self.assertEqual(self.call("post", "/shop/products", who="products", data={"name": "X", "price": "1.00", "product_type": "charge"}).status_code, 422)

    def test_the_pdf_is_served_for_a_sent_invoice_only(self):
        draft = self.draft()
        self.assertEqual(self.call("get", f"/finance/charges/{draft['id']}/pdf").status_code, 409)
        charge = self.sent()
        r = self.call("get", f"/finance/charges/{charge['id']}/pdf")
        self.assertIn(r.status_code, (200, 503))  # 503 where WeasyPrint's system libraries are missing
        if r.status_code == 200:
            self.assertEqual(r["Content-Type"], "application/pdf")
            self.assertTrue(r.content.startswith(b"%PDF"))

    def test_reminder_settings_round_trip(self):
        r = self.call("put", "/finance/charges/settings", data={"after_days": 5, "repeat_days": 10, "max_reminders": 2, "reminders_enabled": False})
        self.assertEqual(r.json()["after_days"], 5)
        again = self.call("get", "/finance/charges/settings").json()
        self.assertEqual((again["repeat_days"], again["max_reminders"], again["reminders_enabled"]), (10, 2, False))
        self.assertEqual(self.call("put", "/finance/charges/settings", data={"repeat_days": 0}).status_code, 422)


class ChargeReminderTests(ChargesBase):
    def overdue_invoice(self, days=10):
        order = charges.create_draft(self.owner, member=self.buyer, lines=[charges.ChargeLine("Fine", 1, Decimal("20.00"))])
        invoice = charges.send(self.owner, order)
        Invoice.objects.filter(pk=invoice.pk).update(due_date=timezone.localdate() - datetime.timedelta(days=days))
        return Invoice.objects.get(pk=invoice.pk)

    def sweep(self, today=None):
        self.dispatch.reset_mock()
        result = charges.send_due_reminders(today)
        return result["reminders"], [c.args[0] for c in self.dispatch.call_args_list]

    def test_the_schedule_first_after_the_delay_then_every_repeat_up_to_the_maximum(self):
        ShopSettings.current()
        ShopSettings.objects.update(charge_reminder_after_days=3, charge_reminder_repeat_days=7, charge_reminder_max=2)
        invoice = self.overdue_invoice(days=2)
        today = timezone.localdate()
        self.assertEqual(self.sweep(today)[0], 0)  # not yet 3 days past due
        Invoice.objects.filter(pk=invoice.pk).update(due_date=today - datetime.timedelta(days=3))
        self.assertEqual(self.sweep(today), (1, ["charge_reminder"]))
        self.assertEqual(self.sweep(today)[0], 0)  # not twice the same day
        self.assertEqual(self.sweep(today + datetime.timedelta(days=6))[0], 0)
        self.assertEqual(self.sweep(today + datetime.timedelta(days=7))[0], 1)
        self.assertEqual(self.sweep(today + datetime.timedelta(days=30))[0], 0)  # the maximum of 2 is reached
        invoice.refresh_from_db()
        self.assertEqual(invoice.reminders_sent, 2)
        self.assertEqual(self.dispatch.call_args_list, [])

    def test_nobody_is_reminded_about_what_is_paid_cancelled_not_due_or_switched_off(self):
        paid = self.overdue_invoice()
        payment_svc.record_payment(paid.order, amount=Decimal("20.00"), method="cash")
        cancelled = self.overdue_invoice()
        charges.cancel(self.owner, cancelled.order)
        not_due = self.overdue_invoice(days=0)
        draft = charges.create_draft(self.owner, member=self.buyer, lines=[charges.ChargeLine("Fine", 1, Decimal("5.00"))])
        shop_order, _ = self.make_order(self.merch())
        Invoice.objects.create(order=shop_order, due_date=timezone.localdate() - datetime.timedelta(days=20))  # a shop invoice is not a charge
        self.assertEqual(self.sweep()[0], 0)
        self.assertEqual((not_due.reminders_sent, Order.objects.get(pk=draft.pk).kind), (0, "charge"))
        due = self.overdue_invoice()
        ShopSettings.objects.update(charge_reminders_enabled=False)
        self.assertEqual(self.sweep()[0], 0)
        ShopSettings.objects.update(charge_reminders_enabled=True)
        self.assertEqual(self.sweep()[0], 1)
        due.refresh_from_db()
        self.assertEqual(due.reminders_sent, 1)

    def test_a_manual_reminder_counts_and_only_makes_sense_while_something_is_owed(self):
        charge = self.sent()
        r = self.call("post", f"/finance/charges/{charge['id']}/remind")
        self.assertEqual((r.status_code, r.json()["reminders_sent"]), (200, 1))
        self.assertEqual(self.dispatch.call_args.args[:1], ("charge_reminder",))
        self.call("post", f"/finance/charges/{charge['id']}/payments", data={"amount": "25.00", "method": "cash"})
        self.assertEqual(self.call("post", f"/finance/charges/{charge['id']}/remind").status_code, 409)
        draft = self.draft()
        self.assertEqual(self.call("post", f"/finance/charges/{draft['id']}/remind").status_code, 409)

    def test_the_daily_task_is_scheduled(self):
        from rosterchief.celery import app

        self.assertEqual(app.conf.beat_schedule["shop-send-charge-reminders"]["task"], "shop.send_charge_reminders")


class ChargeEmailTests(ChargesBase):
    def test_the_member_gets_a_branded_email_with_the_invoice_and_a_reminder_later(self):
        order = charges.create_draft(self.owner, member=self.buyer, lines=[charges.ChargeLine("Fine: late for the bus", 1, Decimal("15.00"))])
        invoice = charges.send(self.owner, order, due_date=timezone.localdate() + datetime.timedelta(days=14))
        self.dispatch.reset_mock()
        with mock.patch.object(emails, "_invoice_attachment", return_value=(f"{invoice.number}.pdf", b"%PDF-1.4")):
            notifications.send("charge", order)
            notifications.send("charge_reminder", order, {"reminder": 1})
        self.assertEqual(len(mail.outbox), 2)
        first, reminder = mail.outbox
        self.assertEqual(first.to, ["bea@shop.test"])
        self.assertIn(invoice.number, first.subject)
        self.assertIn("Fine: late for the bus", first.body)
        self.assertIn(invoice.due_date.strftime("%d/%m/%Y"), first.body)
        self.assertEqual([a[0] for a in first.attachments], [f"{invoice.number}.pdf"])
        self.assertIn("vervallen", reminder.subject)

    def test_a_cancelled_invoice_is_announced_without_an_email(self):
        order = charges.create_draft(self.owner, member=self.buyer, lines=[charges.ChargeLine("Fine", 1, Decimal("15.00"))])
        charges.send(self.owner, order)
        charges.cancel(self.owner, order)
        self.dispatch.reset_mock()
        notifications.send("charge_cancelled", order)
        self.assertEqual(len(mail.outbox), 0)


class ShopVatTests(ChargesBase):
    BE = {"vat_mode": "included", "club": {"vat_id": "BE0123456789"}}

    def priced_order(self, price="12.10", rate="21.00", discount=None):
        product = self.merch("Scarf", price, vat_rate=Decimal(rate))
        from shop.services.orders import LineSpec

        order = orders.create_order(self.owner, purchaser=self.buyer, specs=[LineSpec(product, None, 1)], discount_code=discount.code if discount else "")
        return order

    def test_vat_is_off_by_default_and_a_product_rate_is_then_ignored(self):
        self.assertEqual(ShopSettings.current().vat_mode, "off")
        order = self.priced_order()
        self.assertEqual(order.lines.get().vat_rate, Decimal("0"))
        self.assertEqual(invoices.vat_rows(order, self.BE), [])

    def test_included_prices_show_the_vat_inside_the_price_per_rate(self):
        ShopSettings.objects.update(vat_mode="included")
        order = self.priced_order()
        self.assertEqual(order.lines.get().vat_rate, Decimal("21.00"))
        self.assertEqual(order.total, Decimal("12.10"))  # the price does not change
        self.assertEqual(invoices.vat_rows(order, self.BE), [{"rate": Decimal("21.00"), "net": Decimal("10.00"), "vat": Decimal("2.10"), "gross": Decimal("12.10")}])
        # a discount is spread over the lines before the VAT is taken out
        discount = Discount.objects.create(name="Ten", code="TEN", discount_type=DiscountType.PERCENTAGE, discount_amount=Decimal("10"))
        discounted = self.priced_order(discount=discount)
        rows = invoices.vat_rows(discounted, self.BE)
        self.assertEqual((discounted.total, rows[0]["gross"], rows[0]["vat"], rows[0]["net"]), (Decimal("10.89"), Decimal("10.89"), Decimal("1.89"), Decimal("9.00")))
        # no VAT number, no VAT shown; and a line made while VAT was on keeps its rate when it is later switched off
        self.assertEqual(invoices.vat_rows(order, {"vat_mode": "included", "club": {}}), [])
        ShopSettings.objects.update(vat_mode="off")
        self.assertEqual(OrderLine.objects.get(order=order).vat_rate, Decimal("21.00"))
        self.assertEqual(invoices.vat_rows(order, {"vat_mode": "off", "club": {"vat_id": "BE0123456789"}}), [])

    def test_an_invoice_remembers_the_vat_mode_it_was_issued_under(self):
        ShopSettings.objects.update(vat_mode="included")
        order = self.priced_order()
        invoice = invoices.create_invoice(order)
        ShopSettings.objects.update(vat_mode="off")
        self.assertEqual(invoice.billing_snapshot["vat_mode"], "included")
        self.assertEqual(invoices.billing_snapshot(order)["vat_mode"], "off")

    def test_a_charge_never_carries_vat(self):
        ShopSettings.objects.update(vat_mode="included")
        order = charges.create_draft(self.owner, member=self.buyer, lines=[charges.ChargeLine("Fine", 1, Decimal("25.00"))])
        self.assertEqual(order.lines.get().vat_rate, Decimal("0"))
        self.assertEqual(invoices.vat_rows(order, self.BE), [])

    def test_only_whoever_manages_the_whole_shop_switches_vat_and_products_hold_the_rate(self):
        self.assertEqual(self.call("put", "/shop/settings", who="orders", data={"vat_mode": "included"}).status_code, 403)
        self.assertEqual(self.call("put", "/shop/settings", who="superset", data={"vat_mode": "sometimes"}).status_code, 422)
        r = self.call("put", "/shop/settings", who="superset", data={"vat_mode": "included"})
        self.assertEqual((r.status_code, r.json()["vat_mode"], r.json()["can_edit_vat"]), (200, "included", True))
        self.assertEqual(self.call("get", "/shop/settings", who="orders").json()["can_edit_vat"], False)
        product = self.call("post", "/shop/products", who="products", data={"name": "Scarf", "price": "12.10", "vat_rate": "21.00"})
        self.assertEqual(product.status_code, 201, product.content)
        self.assertEqual(product.json()["vat_rate"], "21.00")
        self.assertEqual(Product.objects.get(name="Scarf").vat_rate, Decimal("21.00"))
        self.assertEqual(self.call("post", "/shop/products", who="products", data={"name": "Bad", "price": "1.00", "vat_rate": "150"}).status_code, 422)
        self.assertEqual(Payment.objects.count(), 0)


class ChargePortalTests(ChargesBase):
    def test_a_member_sees_their_sent_charges_in_the_app_and_downloads_the_pdf_nobody_else_can(self):
        me = Member.objects.get(user=self.users["plain"])
        sent = charges.create_draft(self.owner, member=me, lines=[charges.ChargeLine("Fine: late for the bus", 1, Decimal("12.00")), charges.ChargeLine("Lost bib", 1, Decimal("5.00"))])
        charges.send(self.owner, sent)
        charges.create_draft(self.owner, member=me, lines=[charges.ChargeLine("Not sent yet", 1, Decimal("9.00"))])  # a draft stays hidden
        cancelled = charges.create_draft(self.owner, member=me, lines=[charges.ChargeLine("Cancelled", 1, Decimal("4.00"))])
        charges.send(self.owner, cancelled)
        charges.cancel(self.owner, cancelled)
        someone_else = charges.create_draft(self.owner, member=self.buyer, lines=[charges.ChargeLine("Someone else's", 1, Decimal("7.00"))])
        charges.send(self.owner, someone_else)
        payment_svc.record_payment(sent, amount=Decimal("5.00"), method="cash")
        home = self.call("get", "/portal/home", who="plain").json()
        self.assertEqual(len(home["charges"]), 1)
        row = home["charges"][0]
        self.assertEqual((row["summary"], row["total"], row["paid"], row["due"], row["status"]), ("Fine: late for the bus +1", "17.00", "5.00", "12.00", "partially_paid"))
        self.assertTrue(row["number"].startswith("INV-"))
        r = self.call("get", f"/portal/charges/{sent.pk}.pdf", who="plain")
        self.assertIn(r.status_code, (200, 503))  # 503 where WeasyPrint's system libraries are missing
        for other in (someone_else.pk, cancelled.pk, uuid.uuid4()):
            self.assertEqual(self.call("get", f"/portal/charges/{other}.pdf", who="plain").status_code, 404)
        self.assertEqual(self.call("get", f"/portal/charges/{sent.pk}.pdf", who="orders").status_code, 404)
