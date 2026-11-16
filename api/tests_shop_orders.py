"""Shop orders, payments, online payment providers, production, invoices and stats (``/shop/orders|production|invoices|stats``)."""

import datetime
import hashlib
import hmac
import json
import uuid
from decimal import Decimal
from unittest import mock

from django.db import connection
from django.test import Client, TestCase

from controlpanel.tests import login_session
from features.entitlements import Entitlements
from shop.models import Discount, InventoryMovement, Invoice, Order, OrderLine, Payment, ProductionBatch, Voucher
from shop.services import inventory
from shop.tests import ShopClubMixin

BASE = "/api/v1"


class ShopOrdersApiTests(ShopClubMixin, TestCase):
    def setUp(self):
        super().setUp()
        p = mock.patch("shop.services.notifications.dispatch")
        self.dispatch = p.start()
        self.addCleanup(p.stop)

    def call(self, method, path, user=None, data=None, who=None, raw=None, headers=None):
        h = {"HTTP_HOST": self.host, **(headers or {})}
        user = user or (self.users[who] if who and who != "owner" else self.owner if who == "owner" else self.users["orders"])
        h["HTTP_X_SESSION_TOKEN"] = login_session(user)
        body = {"data": json.dumps(data), "content_type": "application/json"} if data is not None else {}
        response = getattr(Client(), method)(BASE + path, **body, **h)
        connection.set_tenant(self.club)
        return response

    def create(self, lines=None, **extra):
        lines = lines or [{"product_id": str(self.merch().pk), "quantity": 2}]
        r = self.call("post", "/shop/orders", data={"purchaser_id": str(self.buyer.pk), "lines": lines, **extra})
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()

    # --- permissions / feature ------------------------------------------------------------------------------------

    def test_permission_matrix_and_feature_gate(self):
        oid = uuid.uuid4()
        endpoints = [
            ("get", "/shop/orders", None, {"orders"}), ("get", f"/shop/orders/{oid}", None, {"orders"}), ("get", "/shop/production/lines", None, {"orders"}),
            ("get", "/shop/production/batches", None, {"orders"}), ("get", "/shop/settings", None, {"orders"}), ("get", "/shop/invoices", None, {"invoices"}),
            ("post", f"/shop/orders/{oid}/invoice", {}, {"invoices"}), ("get", f"/shop/invoices/{oid}/pdf", None, {"invoices"}),
            ("post", f"/shop/orders/{oid}/payment-link", {"return_url": "https://x.test"}, {"orders"}),
        ]  # fmt: skip
        for method, path, body, allowed in endpoints:
            for key in ["plain", "invoices", "products", "inventory", "orders", "discounts"]:
                status = self.call(method, path, who=key, data=body).status_code
                if key in allowed:
                    self.assertNotEqual(status, 403, (key, path))
                else:
                    self.assertEqual(status, 403, (key, path))
            for key in ("owner", "superset"):
                self.assertNotEqual(self.call(method, path, who=key, data=body).status_code, 403, (key, path))
        # stats: any shop permission
        self.assertEqual(self.call("get", "/shop/stats", who="invoices").status_code, 200)
        self.assertEqual(self.call("get", "/shop/stats", who="plain").status_code, 403)
        self.features = Entitlements({})
        self.assertEqual(self.call("get", "/shop/orders", who="owner").status_code, 404)
        self.assertEqual(self.call("get", "/shop/stats", who="owner").status_code, 404)
        self.assertEqual(self.call("get", "/shop/orders", who="plain").status_code, 403)

    # --- orders ------------------------------------------------------------------------------------------------------

    def test_create_order_totals_snapshot_and_discount(self):
        shirt = self.merch("Shirt", "20.00")
        Discount.objects.create(name="Ten", code="TEN", discount_type="percentage", discount_amount=Decimal("10"))
        order = self.create([{"product_id": str(shirt.pk), "quantity": 3}], discount_code="ten", notes="hi")
        self.assertEqual((order["subtotal"], order["discount_total"], order["total"]), ("60.00", "6.00", "54.00"))
        self.assertTrue(order["number"].startswith("ORD-"))
        self.assertEqual(order["lines"][0]["unit_price"], "20.00")
        self.assertEqual(order["applied_discounts"][0]["amount"], "6.00")
        shirt.price = Decimal("99.00")
        shirt.save()
        self.assertEqual(self.call("get", f"/shop/orders/{order['id']}").json()["total"], "54.00")
        self.assertEqual(order["production_status"], "pending")
        self.assertEqual(order["amount_due"], "54.00")

    def test_create_order_validation_and_preview(self):
        shirt = self.merch("Shirt", "20.00")
        self.assertEqual(self.call("post", "/shop/orders", data={"purchaser_id": str(self.buyer.pk), "lines": []}).status_code, 422)
        r = self.call("post", "/shop/orders", data={"purchaser_id": str(self.buyer.pk), "lines": [{"product_id": str(shirt.pk), "quantity": 1}], "discount_code": "NOPE"})
        self.assertEqual((r.status_code, "discount_code" in r.json()["errors"]), (422, True))
        r = self.call("post", "/shop/orders/preview", data={"purchaser_id": str(self.buyer.pk), "lines": [{"product_id": str(shirt.pk), "quantity": 2}]})
        self.assertEqual((r.status_code, r.json()["total"]), (200, "40.00"))
        self.assertEqual(Order.objects.count(), 0)

    def test_stock_is_booked_and_cancel_returns_it(self):
        ball = self.merch("Ball", "5.00", inventory_managed=True)
        inventory.record_movement(ball, None, 10, InventoryMovement.Reason.RECEIVED)
        order = self.create([{"product_id": str(ball.pk), "quantity": 4}])
        self.assertEqual(inventory.stock_for(ball), 6)
        self.assertIsNone(order["production_status"])
        r = self.call("post", "/shop/orders", data={"purchaser_id": str(self.buyer.pk), "lines": [{"product_id": str(ball.pk), "quantity": 7}]})
        self.assertEqual((r.status_code, r.json()["code"]), (409, "insufficient_stock"))
        self.assertEqual(inventory.stock_for(ball), 6)
        r = self.call("post", f"/shop/orders/{order['id']}/cancel")
        self.assertEqual(r.json()["fulfillment_status"], "cancelled")
        self.assertEqual(inventory.stock_for(ball), 10)
        self.assertEqual(self.call("post", f"/shop/orders/{order['id']}/cancel").status_code, 200)
        self.assertEqual(inventory.stock_for(ball), 10)

    def test_edit_lines_while_unpaid(self):
        shirt = self.merch("Shirt", "20.00")
        order = self.create([{"product_id": str(shirt.pk), "quantity": 1}])
        oid = order["id"]
        r = self.call("post", f"/shop/orders/{oid}/lines", data={"product_id": str(self.merch("Cap", "10.00").pk), "quantity": 2})
        self.assertEqual((r.status_code, r.json()["total"]), (201, "40.00"))
        line = r.json()["lines"][0]["id"]
        r = self.call("patch", f"/shop/orders/{oid}/lines/{line}", data={"quantity": 3})
        self.assertEqual(r.json()["total"], "80.00")
        r = self.call("delete", f"/shop/orders/{oid}/lines/{line}")
        self.assertEqual(r.json()["total"], "20.00")
        last = r.json()["lines"][0]["id"]
        self.assertEqual(self.call("delete", f"/shop/orders/{oid}/lines/{last}").status_code, 422)
        self.call("post", f"/shop/orders/{oid}/mark-paid", data={"method": "cash"})
        r = self.call("patch", f"/shop/orders/{oid}/lines/{last}", data={"quantity": 2})
        self.assertEqual((r.status_code, r.json()["code"]), (409, "not_editable"))

    def test_fulfilment_transitions_and_member_status(self):
        order = self.create()
        oid = order["id"]
        r = self.call("post", f"/shop/orders/{oid}/ready", data={"pickup_instructions": "Desk"}).json()
        self.assertEqual((r["fulfillment_status"], r["member_status"], r["pickup_instructions"]), ("ready_for_pickup", "ready_for_pickup", "Desk"))
        self.assertEqual(self.call("post", f"/shop/orders/{oid}/deliver").json()["member_status"], "completed")
        self.assertEqual(self.call("post", f"/shop/orders/{oid}/cancel").status_code, 409)
        self.assertEqual(self.call("post", f"/shop/orders/{oid}/reopen").json()["fulfillment_status"], "ready_for_pickup")  # picked up -> back to ready
        self.assertEqual(self.call("post", f"/shop/orders/{oid}/reopen").json()["fulfillment_status"], "not_ready")
        listed = self.call("get", "/shop/orders?closed=false").json()
        self.assertEqual(listed["count"], 1)
        self.assertEqual(self.call("get", "/shop/orders?q=Buyer").json()["count"], 1)
        self.assertEqual(self.call("get", "/shop/orders?payment_status=paid").json()["count"], 0)

    def test_bulk_actions(self):
        a, b = self.create()["id"], self.create()["id"]
        self.assertEqual(self.call("post", "/shop/orders/bulk/mark-paid", data={"order_ids": [a, b], "method": "cash"}).json(), {"count": 2})
        self.assertEqual(self.call("post", "/shop/orders/bulk/mark-paid", data={"order_ids": [a, b], "method": "cash"}).json(), {"count": 0})
        self.assertEqual(self.call("post", "/shop/orders/bulk/ready", data={"order_ids": [a], "pickup_instructions": "x"}).json(), {"count": 1})
        self.assertEqual(self.call("get", f"/shop/orders/{a}").json()["fulfillment_status"], "ready_for_pickup")

    # --- payments ----------------------------------------------------------------------------------------------------

    def test_payments_derive_status_and_vouchers(self):
        order = self.create()  # 80.00
        oid = order["id"]
        pay = lambda **kw: self.call("post", f"/shop/orders/{oid}/payments", data=kw)  # noqa: E731
        state = lambda: self.call("get", f"/shop/orders/{oid}").json()  # noqa: E731
        p1 = pay(amount="30.00", method="cash")
        self.assertEqual((p1.status_code, state()["payment_status"]), (201, "partially_paid"))
        voucher = Voucher.objects.create(amount=Decimal("50"), expiry_date=datetime.date.today() + datetime.timedelta(days=30))
        self.assertEqual(pay(amount="50.00", method="voucher").status_code, 422)
        p2 = pay(amount="50.00", method="voucher", voucher_number=voucher.number)
        self.assertEqual((p2.status_code, state()["payment_status"], state()["amount_due"]), (201, "paid", "0.00"))
        voucher.refresh_from_db()
        self.assertEqual(voucher.consumed_amount, Decimal("50"))
        r = self.call("patch", f"/shop/orders/{oid}/payments/{p1.json()['id']}", data={"amount": "20.00", "reference": "r"})
        self.assertEqual((r.status_code, r.json()["amount"], state()["payment_status"]), (200, "20.00", "partially_paid"))
        self.assertEqual(self.call("post", f"/shop/orders/{oid}/payments/{p2.json()['id']}/refund").json()["status"], "refunded")
        voucher.refresh_from_db()
        self.assertEqual(voucher.consumed_amount, Decimal("0"))
        self.assertEqual(self.call("delete", f"/shop/orders/{oid}/payments/{p1.json()['id']}").status_code, 204)
        self.assertEqual(state()["payment_status"], "refunded")
        self.assertEqual(pay(amount="0", method="cash").status_code, 422)
        self.assertEqual(pay(amount="5", method="online").status_code, 422)

    def test_mark_paid_nothing_due(self):
        oid = self.create()["id"]
        self.assertEqual(self.call("post", f"/shop/orders/{oid}/mark-paid", data={"method": "bank_transfer"}).json()["payment_status"], "paid")
        self.assertEqual(self.call("post", f"/shop/orders/{oid}/mark-paid", data={"method": "cash"}).json()["code"], "nothing_due")

    # --- online provider ---------------------------------------------------------------------------------------------

    def configure_provider(self):
        r = self.call("put", "/shop/settings", data={"payment_provider": "manual_test", "credentials": {"webhook_secret": "s3cret"}})
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def test_settings_never_return_secrets(self):
        before = self.call("get", "/shop/settings").json()
        self.assertEqual((before["payment_provider"], before["credentials_configured"]), ("", False))
        after = self.configure_provider()
        self.assertTrue(after["credentials_configured"])
        self.assertNotIn("s3cret", json.dumps(after))
        self.assertNotIn("s3cret", Payment.__module__ and __import__("shop.settings_models", fromlist=["x"]).ShopSettings.current().credentials_encrypted)
        self.assertEqual(self.call("put", "/shop/settings", data={"payment_provider": "nope"}).status_code, 422)
        self.assertEqual(self.call("put", "/shop/settings", data={"credentials": {"bad": "x"}}).status_code, 422)

    def test_shop_open_closed(self):
        from formbuilder.services.management import Conflict
        from shop.services import orders as order_service

        public = lambda: Client().get(BASE + "/public/shop/status", HTTP_HOST=self.host)  # noqa: E731
        settings = self.call("get", "/shop/settings").json()
        self.assertEqual((settings["shop_open"], settings["closed_message"]), (True, ""))
        self.assertEqual(public().json(), {"open": True, "message": ""})
        connection.set_tenant(self.club)
        order_service.ensure_shop_open()  # open: no error

        r = self.call("put", "/shop/settings", data={"shop_open": False, "closed_message": "  Back after the summer break.  "})
        self.assertEqual((r.status_code, r.json()["shop_open"], r.json()["closed_message"]), (200, False, "Back after the summer break."))
        self.assertEqual(public().json(), {"open": False, "message": "Back after the summer break."})
        connection.set_tenant(self.club)
        with self.assertRaises(Conflict) as caught:
            order_service.ensure_shop_open()
        self.assertEqual(caught.exception.code, "shop_closed")
        # closed to members, but staff can still register an order and payments keep working
        order = self.create()
        self.assertEqual(self.call("post", f"/shop/orders/{order['id']}/mark-paid", data={"method": "cash"}).status_code, 200)
        # settings changes that do not mention the switch leave it alone
        self.assertFalse(self.call("put", "/shop/settings", data={"invoice_due_days": 30}).json()["shop_open"])
        # reopening clears the public message
        self.call("put", "/shop/settings", data={"shop_open": True})
        self.assertEqual(public().json(), {"open": True, "message": ""})
        # the status is public but only exists for clubs with the shop feature
        self.assertEqual(Client().get(BASE + "/public/shop/status", HTTP_HOST="testserver").status_code, 404)

    def webhook(self, body, secret="s3cret", signature=None):
        raw = json.dumps(body).encode()
        sig = signature if signature is not None else hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
        r = Client().post(BASE + "/public/shop/payments/webhook/manual_test", data=raw, content_type="application/json", HTTP_HOST=self.host, HTTP_X_SIGNATURE=sig)
        connection.set_tenant(self.club)
        return r

    def test_payment_link_and_webhook_idempotent(self):
        oid = self.create()["id"]
        r = self.call("post", f"/shop/orders/{oid}/payment-link", data={"return_url": "https://club.test/done"})
        self.assertEqual((r.status_code, r.json()["code"]), (409, "no_provider"))
        self.configure_provider()
        r = self.call("post", f"/shop/orders/{oid}/payment-link", data={"return_url": "https://club.test/done"})
        self.assertEqual(r.status_code, 201, r.content)
        link = r.json()
        self.assertEqual((link["status"], link["method"], link["amount"]), ("pending", "online", "80.00"))
        self.assertTrue(link["payment_url"].startswith("https://"))
        ext = Payment.objects.get(pk=link["id"]).provider_payment_id
        self.assertEqual(self.call("get", f"/shop/orders/{oid}").json()["payment_status"], "pending")
        self.assertEqual(self.webhook({"external_id": ext, "status": "paid"}, signature="bad").status_code, 400)
        self.assertEqual(self.webhook({"external_id": ext, "status": "paid"}, secret="wrong").status_code, 400)
        self.assertEqual(self.call("get", f"/shop/orders/{oid}").json()["payment_status"], "pending")
        self.assertEqual(self.webhook({"external_id": ext, "status": "paid"}).json(), {"result": "confirmed"})
        self.assertEqual(self.webhook({"external_id": ext, "status": "paid"}).json(), {"result": "unchanged"})
        self.assertEqual(self.webhook({"external_id": "zzz", "status": "paid"}).json(), {"result": "unknown"})
        self.assertEqual(Payment.objects.filter(order_id=oid, status="confirmed").count(), 1)
        self.assertEqual(self.call("get", f"/shop/orders/{oid}").json()["payment_status"], "paid")
        self.assertEqual(self.webhook({"external_id": ext, "status": "refunded"}).json(), {"result": "refunded"})
        self.assertEqual(self.call("get", f"/shop/orders/{oid}").json()["payment_status"], "refunded")

    def test_webhook_unknown_provider_and_feature(self):
        self.assertEqual(Client().post(BASE + "/public/shop/payments/webhook/nope", data="{}", content_type="application/json", HTTP_HOST=self.host).status_code, 404)
        self.features = Entitlements({})
        self.assertEqual(self.webhook({"external_id": "x", "status": "paid"}).status_code, 404)

    # --- production --------------------------------------------------------------------------------------------------

    def test_production_flow_does_not_auto_ready(self):
        jersey = self.merch("Jersey", "40.00")
        o1 = self.create([{"product_id": str(jersey.pk), "quantity": 2}])
        o2 = self.create([{"product_id": str(jersey.pk), "quantity": 1}])
        lines = self.call("get", "/shop/production/lines").json()
        self.assertEqual(lines["count"], 2)
        ids = [ln["id"] for ln in lines["results"]]
        r = self.call("post", "/shop/production/batches", data={"line_ids": ids, "reference": "PO-1", "supplier": "Acme"})
        self.assertEqual(r.status_code, 201, r.content)
        batch = r.json()
        self.assertEqual((batch["status"], batch["quantity"], batch["number"][:3]), ("draft", 3, "PB-"))
        self.assertEqual(self.call("get", "/shop/production/lines").json()["count"], 0)
        self.assertEqual(self.call("post", "/shop/production/batches", data={"line_ids": ids}).json()["code"], "lines_unavailable")
        self.assertEqual(self.call("post", f"/shop/production/batches/{batch['id']}/receive", data={}).json()["code"], "not_ordered")
        csv = self.call("get", f"/shop/production/batches/{batch['id']}/export")
        self.assertEqual((csv.status_code, csv["Content-Type"].split(";")[0]), (200, "text/csv"))
        self.assertIn("Jersey", csv.content.decode())
        b = self.call("post", f"/shop/production/batches/{batch['id']}/mark-ordered").json()
        self.assertEqual(b["status"], "ordered")
        self.assertEqual(self.call("get", f"/shop/orders/{o1['id']}").json()["production_status"], "ordered")
        self.assertEqual(self.call("post", f"/shop/production/batches/{batch['id']}/mark-ordered").json()["code"], "already_ordered")
        l1 = next(ln for ln in b["lines"] if ln["quantity"] == 2)
        r = self.call("post", f"/shop/production/batches/{batch['id']}/receive", data={"items": [{"line_id": l1["id"], "quantity": 1}]})
        self.assertEqual(r.json()["status"], "partially_received")
        self.assertEqual(self.call("get", f"/shop/orders/{o1['id']}").json()["fulfillment_status"], "not_ready")
        self.assertEqual(self.call("post", f"/shop/production/batches/{batch['id']}/receive", data={"items": [{"line_id": l1["id"], "quantity": 5}]}).status_code, 422)
        r = self.call("post", f"/shop/production/batches/{batch['id']}/receive", data={})
        self.assertEqual((r.json()["status"], r.json()["received_quantity"]), ("received", 3))
        for o in (o1, o2):
            d = self.call("get", f"/shop/orders/{o['id']}").json()
            # received, but staff confirm pickup themselves in the ready-for-pickup workflow
            self.assertEqual((d["production_status"], d["fulfillment_status"]), ("received", "not_ready"))
        self.assertEqual(self.call("get", "/shop/production/batches?status=received").json()["count"], 1)
        waiting = self.call("get", "/shop/pickup").json()
        self.assertEqual(sorted(o["id"] for o in waiting["orders"]), sorted([o1["id"], o2["id"]]))

    def test_pickup_workflow_and_default_texts(self):
        from shop.services import defaults

        # default texts: pickup from the shop settings, payment from the club
        club = self.club
        club.payment_instructions = "IBAN BE00 0000 0000 0000"
        club.save(update_fields=["payment_instructions"])
        connection.set_tenant(club)
        self.assertEqual(self.call("put", "/shop/settings", data={"pickup_instructions": "  Desk at the club house  "}).json()["pickup_instructions"], "Desk at the club house")
        settings = self.call("get", "/shop/settings").json()
        self.assertEqual(settings["club_payment_instructions"], "IBAN BE00 0000 0000 0000")
        connection.set_tenant(club)
        self.assertEqual((defaults.pickup_instructions(), defaults.payment_instructions()), ("Desk at the club house", "IBAN BE00 0000 0000 0000"))

        # waiting: one in-stock order (no made-to-order lines) and one still in production; received production orders join them
        in_stock = self.merch("Cap", "10.00", inventory_managed=True)
        from shop.services import inventory

        connection.set_tenant(club)
        inventory.receive_stock(self.owner, [(in_stock, None, 5)])
        stock_order = self.create([{"product_id": str(in_stock.pk), "quantity": 1}])
        made = self.create([{"product_id": str(self.merch("Jersey", "40.00").pk), "quantity": 1}])
        waiting = self.call("get", "/shop/pickup").json()
        self.assertEqual([o["id"] for o in waiting["orders"]], [stock_order["id"]])
        self.assertEqual((waiting["pickup_instructions"], waiting["payment_instructions"]), ("Desk at the club house", "IBAN BE00 0000 0000 0000"))
        self.assertEqual(set(waiting["orders"][0]), {"id", "number", "purchaser", "beneficiaries", "line_count", "quantity", "total", "amount_due", "payment_status", "created"})

        # confirm: marks ready, stores the text, skips what is not waiting, nothing for the order still in production
        r = self.call("post", "/shop/pickup/confirm", data={"order_ids": [stock_order["id"], made["id"]], "pickup_instructions": "Tuesday 18:00", "payment_instructions": "Pay at pickup", "save_as_default": True})
        self.assertEqual((r.status_code, r.json()), (200, {"count": 1, "skipped": 1}))
        done = self.call("get", f"/shop/orders/{stock_order['id']}").json()
        self.assertEqual((done["fulfillment_status"], done["pickup_instructions"]), ("ready_for_pickup", "Tuesday 18:00"))
        self.assertEqual(self.call("get", f"/shop/orders/{made['id']}").json()["fulfillment_status"], "not_ready")
        self.assertEqual(self.call("get", "/shop/pickup").json()["orders"], [])
        self.assertEqual(self.call("get", "/shop/pickup").json()["pickup_instructions"], "Tuesday 18:00")  # saved as the new default
        # marked ready: the invoice is issued automatically (and an existing one is kept)
        self.assertIsNotNone(done["invoice"])
        self.assertEqual(Invoice.objects.filter(order_id=stock_order["id"]).count(), 1)
        self.assertEqual(self.call("get", f"/shop/orders/{made['id']}").json()["invoice"], None)  # not ready yet: no invoice
        first_invoice = done["invoice"]["number"]
        # marking ready without text falls back to the order's own text, then the shop default
        self.assertEqual(self.call("post", f"/shop/orders/{made['id']}/ready", data={}).json()["pickup_instructions"], "Tuesday 18:00")
        self.assertIsNotNone(self.call("get", f"/shop/orders/{made['id']}").json()["invoice"])
        # an order that already had an invoice keeps it: ready again does not issue a second one
        self.call("post", f"/shop/orders/{stock_order['id']}/reopen")
        again = self.call("post", f"/shop/orders/{stock_order['id']}/ready", data={}).json()
        self.assertEqual((again["invoice"]["number"], Invoice.objects.filter(order_id=stock_order["id"]).count()), (first_invoice, 1))
        # the order page carries both default texts for the dialogs
        detail = self.call("get", f"/shop/orders/{made['id']}").json()
        self.assertEqual((detail["payment_instructions"], detail["default_pickup_instructions"]), ("IBAN BE00 0000 0000 0000", "Tuesday 18:00"))
        # permissions
        self.assertEqual(self.call("get", "/shop/pickup", who="products").status_code, 403)
        self.assertEqual(self.call("post", "/shop/pickup/confirm", who="invoices", data={"order_ids": [stock_order["id"]]}).status_code, 403)

    def test_ready_notification_carries_payment_instructions_for_unpaid_orders_only(self):
        from members.models import Member
        from notifications.models import InboxNotification
        from shop.services import notifications

        member = Member.objects.get(user=self.users["orders"])
        member.language = "en"
        member.save()
        unpaid = Order.objects.create(purchaser=member, total=Decimal("5"), pickup_instructions="Desk")
        paid = Order.objects.create(purchaser=member, total=Decimal("5"), pickup_instructions="Desk", payment_status=Order.PaymentStatus.PAID)
        self.assertEqual(notifications.send("ready", unpaid, {"payment_instructions": "Pay at pickup"}), 1)
        self.assertEqual(notifications.send("ready", paid, {"payment_instructions": "Pay at pickup"}), 1)
        unpaid_note, paid_note = InboxNotification.objects.order_by("created")
        self.assertIn("How to pay:\nPay at pickup", unpaid_note.body)
        self.assertNotIn("Pay at pickup", paid_note.body)
        # an explicitly empty text means "none"; no text given means the club default
        self.assertEqual(notifications._how_to_pay({"payment_instructions": ""}), "")

    def test_cancel_blocked_for_customised_items_in_production(self):
        plain = self.merch("Scarf", "15.00")
        custom = self.merch("Jersey", "40.00", personalization_enabled=True)

        def line(product, **extra):
            return {"product_id": str(product.pk), "quantity": 1, **extra}

        # no customised items: cancelling is fine, even when the plain item is already in production
        o_plain = self.create([line(plain)])
        detail = self.call("get", f"/shop/orders/{o_plain['id']}").json()
        self.assertEqual((detail["can_cancel"], detail["cancel_blocked_reason"]), (True, ""))

        # customised item, not ordered yet: allowed, and the line leaves its draft batch
        o_draft = self.create([line(custom, personalization_number="98", personalization_name="Siebens A.")])
        draft_line = OrderLine.objects.get(order_id=o_draft["id"]).pk
        batch = self.call("post", "/shop/production/batches", data={"line_ids": [str(draft_line)], "supplier": "Acme"}).json()
        self.assertEqual(self.call("get", f"/shop/orders/{o_draft['id']}").json()["can_cancel"], True)
        r = self.call("post", f"/shop/orders/{o_draft['id']}/cancel")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["fulfillment_status"], "cancelled")
        self.assertIsNone(OrderLine.objects.get(order_id=o_draft["id"]).production_batch_id)
        self.assertTrue(ProductionBatch.objects.filter(pk=batch["id"]).exists())

        # customised item that went into production: blocked, with a clear reason, in the API and on the order page
        o_blocked = self.create([line(plain), line(custom, personalization_number="7", personalization_name="Doe")])
        ids = [str(ln.pk) for ln in OrderLine.objects.filter(order_id=o_blocked["id"])]
        b = self.call("post", "/shop/production/batches", data={"line_ids": ids}).json()
        self.assertTrue(self.call("get", f"/shop/orders/{o_blocked['id']}").json()["can_cancel"])  # a draft batch is not production yet
        self.call("post", f"/shop/production/batches/{b['id']}/mark-ordered")
        detail = self.call("get", f"/shop/orders/{o_blocked['id']}").json()
        self.assertFalse(detail["can_cancel"])
        self.assertIn("customised items that are already in production", detail["cancel_blocked_reason"])
        self.assertIn("Jersey #7 Doe", detail["cancel_blocked_reason"])
        self.assertNotIn("Scarf", detail["cancel_blocked_reason"])  # only the customised item is named
        r = self.call("post", f"/shop/orders/{o_blocked['id']}/cancel")
        self.assertEqual((r.status_code, r.json()["code"]), (409, "customized_in_production"))
        self.assertEqual(r.json()["detail"], detail["cancel_blocked_reason"])
        self.assertEqual(self.call("get", f"/shop/orders/{o_blocked['id']}").json()["fulfillment_status"], "not_ready")  # untouched
        # still blocked once received
        self.call("post", f"/shop/production/batches/{b['id']}/receive", data={})
        self.assertEqual(self.call("post", f"/shop/orders/{o_blocked['id']}/cancel").json()["code"], "customized_in_production")

        # the plain item alone in production does not block: only customised items do
        o_scarf = self.create([line(plain)])
        sl = [str(ln.pk) for ln in OrderLine.objects.filter(order_id=o_scarf["id"])]
        sb = self.call("post", "/shop/production/batches", data={"line_ids": sl}).json()
        self.call("post", f"/shop/production/batches/{sb['id']}/mark-ordered")
        self.assertEqual(self.call("post", f"/shop/orders/{o_scarf['id']}/cancel").status_code, 200)

    def test_cancel_override_needs_an_explicit_flag_and_leaves_a_note(self):
        custom = self.merch("Jersey", "40.00", personalization_enabled=True)
        order = self.create([{"product_id": str(custom.pk), "quantity": 1, "personalization_number": "7", "personalization_name": "Doe"}])
        ids = [str(ln.pk) for ln in OrderLine.objects.filter(order_id=order["id"])]
        batch = self.call("post", "/shop/production/batches", data={"line_ids": ids}).json()
        self.call("post", f"/shop/production/batches/{batch['id']}/mark-ordered")
        detail = self.call("get", f"/shop/orders/{order['id']}").json()
        self.assertEqual((detail["can_cancel"], detail["can_override_cancel"]), (False, True))
        # without the flag (no body, or override false) it stays refused
        self.assertEqual(self.call("post", f"/shop/orders/{order['id']}/cancel").json()["code"], "customized_in_production")
        self.assertEqual(self.call("post", f"/shop/orders/{order['id']}/cancel", data={"override": False}).status_code, 409)
        r = self.call("post", f"/shop/orders/{order['id']}/cancel", data={"override": True})
        self.assertEqual((r.status_code, r.json()["fulfillment_status"]), (200, "cancelled"))
        self.assertIn("although customised items were already in production (Jersey #7 Doe)", r.json()["notes"])
        self.assertEqual((r.json()["can_cancel"], r.json()["can_override_cancel"]), (False, False))
        # a delivered order can't be cancelled, not even with the override
        done = self.create([{"product_id": str(custom.pk), "quantity": 1, "personalization_number": "8", "personalization_name": "Roe"}])
        self.call("post", f"/shop/orders/{done['id']}/deliver")
        r = self.call("post", f"/shop/orders/{done['id']}/cancel", data={"override": True})
        self.assertEqual((r.status_code, r.json()["code"]), (409, "delivered"))
        self.assertFalse(self.call("get", f"/shop/orders/{done['id']}").json()["can_override_cancel"])

    def test_delivery_desk(self):
        unpaid = self.create()
        paid = self.create()
        waiting = self.create()  # never marked ready
        for o in (unpaid, paid):
            self.call("post", f"/shop/orders/{o['id']}/ready", data={"pickup_instructions": "Desk"})
        self.call("post", f"/shop/orders/{paid['id']}/mark-paid", data={"method": "cash"})

        desk = self.call("get", "/shop/delivery").json()
        self.assertEqual(sorted(o["id"] for o in desk["orders"]), sorted([unpaid["id"], paid["id"]]))
        self.assertEqual(desk["counts"], {"all": 2, "unpaid": 1, "paid": 1})
        row = next(o for o in desk["orders"] if o["id"] == unpaid["id"])
        self.assertEqual((row["amount_due"], row["amount_paid"], row["pickup_instructions"], row["items"][0]["quantity"]), ("80.00", "0.00", "Desk", 2))
        self.assertEqual(row["purchaser"]["name"], "Bea Buyer")
        # filters: payment tab and search (the counts ignore the tab but follow the search)
        only_unpaid = self.call("get", "/shop/delivery?payment=unpaid").json()
        self.assertEqual(([o["id"] for o in only_unpaid["orders"]], only_unpaid["counts"]["all"]), ([unpaid["id"]], 2))
        self.assertEqual([o["id"] for o in self.call("get", "/shop/delivery?payment=paid").json()["orders"]], [paid["id"]])
        self.assertEqual(self.call("get", "/shop/delivery?q=Buyer").json()["counts"]["all"], 2)
        self.assertEqual(self.call("get", "/shop/delivery?q=nobody").json()["orders"], [])

        # handing over: unpaid needs the payment or an explicit "unpaid"
        r = self.call("post", f"/shop/delivery/{unpaid['id']}/hand-over", data={})
        self.assertEqual((r.status_code, r.json()["code"]), (409, "unpaid"))
        self.assertIn("EUR 80.00 is still due", r.json()["detail"])
        self.assertEqual(self.call("post", f"/shop/delivery/{unpaid['id']}/hand-over", data={"method": "paypal"}).status_code, 422)
        self.assertEqual(self.call("get", f"/shop/orders/{unpaid['id']}").json()["fulfillment_status"], "ready_for_pickup")  # untouched
        r = self.call("post", f"/shop/delivery/{unpaid['id']}/hand-over", data={"method": "cash", "reference": "till 2"})
        self.assertEqual(r.status_code, 200, r.content)
        done = r.json()["order"]
        self.assertEqual((done["fulfillment_status"], done["payment_status"], done["amount_due"]), ("delivered", "paid", "0.00"))
        self.assertEqual([p["method"] for p in done["payments"]], ["cash"])
        # a paid order hands over directly; unpaid can be handed over with the explicit flag
        self.assertEqual(self.call("post", f"/shop/delivery/{paid['id']}/hand-over", data={}).json()["order"]["fulfillment_status"], "delivered")
        owed = self.create()
        self.call("post", f"/shop/orders/{owed['id']}/ready", data={})
        r = self.call("post", f"/shop/delivery/{owed['id']}/hand-over", data={"allow_unpaid": True}).json()["order"]
        self.assertEqual((r["fulfillment_status"], r["payment_status"]), ("delivered", "pending"))
        # not waiting (never ready, or already handed over): refused; the desk is empty now
        self.assertEqual(self.call("post", f"/shop/delivery/{waiting['id']}/hand-over", data={}).json()["code"], "not_ready_for_pickup")
        self.assertEqual(self.call("post", f"/shop/delivery/{paid['id']}/hand-over", data={}).json()["code"], "not_ready_for_pickup")
        self.assertEqual(self.call("get", "/shop/delivery").json()["counts"], {"all": 0, "unpaid": 0, "paid": 0})
        # permissions
        self.assertEqual(self.call("get", "/shop/delivery", who="products").status_code, 403)
        self.assertEqual(self.call("post", f"/shop/delivery/{waiting['id']}/hand-over", who="invoices", data={}).status_code, 403)

    def test_discount_code_and_voucher_on_an_existing_unpaid_order(self):
        order = self.create()  # 2 x 40.00 = 80.00
        oid = order["id"]
        Discount.objects.create(name="Spring", code="SPRING10", discount_amount=Decimal("10"))  # 10 %
        Discount.objects.create(name="Other", code="OTHER", discount_amount=Decimal("5"))
        detail = self.call("get", f"/shop/orders/{oid}").json()
        self.assertEqual((detail["can_edit_discounts"], detail["can_apply_voucher"]), (True, True))

        # discount code: validated, case-insensitive, one per order, total recomputed, removable
        self.assertEqual(self.call("post", f"/shop/orders/{oid}/discount", data={"code": "nope"}).status_code, 422)
        self.assertEqual(self.call("post", f"/shop/orders/{oid}/discount", data={"code": "  "}).status_code, 422)
        r = self.call("post", f"/shop/orders/{oid}/discount", data={"code": "spring10"})
        self.assertEqual((r.status_code, r.json()["total"], r.json()["discount_total"]), (200, "72.00", "8.00"))
        applied = r.json()["applied_discounts"][0]
        self.assertEqual((applied["description"], applied["amount"]), ("Spring", "8.00"))
        self.assertEqual(self.call("post", f"/shop/orders/{oid}/discount", data={"code": "OTHER"}).json()["code"], "discount_exists")
        r = self.call("delete", f"/shop/orders/{oid}/discounts/{applied['id']}")
        self.assertEqual((r.status_code, r.json()["total"], r.json()["applied_discounts"]), (200, "80.00", []))
        self.assertEqual(self.call("delete", f"/shop/orders/{oid}/discounts/{applied['id']}").json()["code"], "not_found")
        self.call("post", f"/shop/orders/{oid}/discount", data={"code": "SPRING10"})

        # voucher: unknown / empty / amount over what is due are refused; default takes min(balance, due)
        expiry = datetime.date.today() + datetime.timedelta(days=30)
        small = Voucher.objects.create(amount=Decimal("20"), expiry_date=expiry)
        big = Voucher.objects.create(amount=Decimal("100"), expiry_date=expiry)
        self.assertEqual(self.call("post", f"/shop/orders/{oid}/voucher", data={"number": "NOPE"}).status_code, 422)
        self.assertEqual(self.call("post", f"/shop/orders/{oid}/voucher", data={"number": small.number, "amount": "500.00"}).status_code, 422)
        r = self.call("post", f"/shop/orders/{oid}/voucher", data={"number": small.number})
        self.assertEqual((r.status_code, r.json()["amount_due"], r.json()["payment_status"]), (200, "52.00", "partially_paid"))
        self.assertEqual([(p["method"], p["amount"]) for p in r.json()["payments"]], [("voucher", "20.00")])
        self.assertEqual(r.json()["payments"][0]["note"], "")  # the method column already names the voucher
        small.refresh_from_db()
        self.assertEqual(small.available_amount, Decimal("0"))
        self.assertEqual(self.call("post", f"/shop/orders/{oid}/voucher", data={"number": small.number}).status_code, 422)  # used up
        # a discount can't change anymore once something is paid, but a voucher still works
        r = self.call("get", f"/shop/orders/{oid}").json()
        self.assertEqual((r["can_edit_discounts"], r["can_apply_voucher"]), (False, True))
        self.assertEqual(self.call("delete", f"/shop/orders/{oid}/discounts/{applied['id']}").status_code, 409)
        r = self.call("post", f"/shop/orders/{oid}/voucher", data={"number": big.number})
        self.assertEqual((r.json()["amount_due"], r.json()["payment_status"]), ("0.00", "paid"))
        big.refresh_from_db()
        self.assertEqual(big.available_amount, Decimal("48.00"))  # only what was due was taken
        self.assertEqual(self.call("post", f"/shop/orders/{oid}/voucher", data={"number": big.number}).json()["code"], "nothing_due")
        # permissions: manage_orders only
        self.assertEqual(self.call("post", f"/shop/orders/{oid}/discount", who="discounts", data={"code": "x"}).status_code, 403)
        self.assertEqual(self.call("post", f"/shop/orders/{oid}/voucher", who="invoices", data={"number": big.number}).status_code, 403)
        # cancelled orders take neither
        cancelled = self.create()
        self.call("post", f"/shop/orders/{cancelled['id']}/cancel")
        self.assertEqual(self.call("post", f"/shop/orders/{cancelled['id']}/discount", data={"code": "OTHER"}).json()["code"], "not_editable")
        self.assertEqual(self.call("post", f"/shop/orders/{cancelled['id']}/voucher", data={"number": big.number}).json()["code"], "not_editable")

    def test_available_vouchers_for_the_purchaser_and_family(self):
        from members.models import Family, FamilyMembership, Member

        order = self.create()
        buyer = self.buyer
        parent = Member.objects.create(first_name="Pat", last_name="Buyer", email="pat@shop.test")
        stranger = Member.objects.create(first_name="Sam", last_name="Other", email="sam@shop.test")
        family = Family.objects.create(name="Buyer")
        FamilyMembership.objects.create(family=family, member=buyer, role="child")
        FamilyMembership.objects.create(family=family, member=parent, role="parent")
        soon, later = (datetime.date.today() + datetime.timedelta(days=d) for d in (10, 60))
        own = Voucher.objects.create(amount=Decimal("30"), expiry_date=later, issued_to=buyer)
        sibling_parent = Voucher.objects.create(amount=Decimal("20"), expiry_date=soon, issued_to=parent)
        general = Voucher.objects.create(amount=Decimal("15"), expiry_date=soon)
        Voucher.objects.create(amount=Decimal("99"), expiry_date=soon, issued_to=stranger)  # someone else's
        Voucher.objects.create(amount=Decimal("10"), expiry_date=datetime.date.today() - datetime.timedelta(days=1), issued_to=buyer)  # expired
        Voucher.objects.create(amount=Decimal("10"), expiry_date=later, issued_to=buyer, is_active=False)  # inactive
        used = Voucher.objects.create(amount=Decimal("10"), expiry_date=later, issued_to=buyer, consumed_amount=Decimal("10"))  # used up
        r = self.call("get", f"/shop/orders/{order['id']}/vouchers")
        self.assertEqual(r.status_code, 200, r.content)
        rows = r.json()
        self.assertEqual([(v["number"], v["scope"]) for v in rows], [(own.number, "own"), (sibling_parent.number, "family"), (general.number, "general")])
        self.assertNotIn(used.number, [v["number"] for v in rows])
        self.assertEqual((rows[0]["available_amount"], rows[1]["issued_to"]["name"], rows[2]["issued_to"]), ("30.00", "Pat Buyer", None))
        # partly used vouchers show what is left, and picking one pays with it
        sibling_parent.consumed_amount = Decimal("5")
        sibling_parent.save()
        self.assertEqual(self.call("get", f"/shop/orders/{order['id']}/vouchers").json()[1]["available_amount"], "15.00")
        self.assertEqual(self.call("post", f"/shop/orders/{order['id']}/voucher", data={"number": sibling_parent.number}).json()["amount_due"], "65.00")
        self.assertEqual(self.call("get", f"/shop/orders/{order['id']}/vouchers", who="products").status_code, 403)

    def test_cancel_can_refund_voucher_payments(self):
        expiry = datetime.date.today() + datetime.timedelta(days=30)
        v1 = Voucher.objects.create(amount=Decimal("30"), expiry_date=expiry)
        v2 = Voucher.objects.create(amount=Decimal("100"), expiry_date=expiry)

        def order_with_vouchers():
            o = self.create()  # 80.00
            self.call("post", f"/shop/orders/{o['id']}/voucher", data={"number": v1.number})  # 30.00
            self.call("post", f"/shop/orders/{o['id']}/voucher", data={"number": v2.number, "amount": "20.00"})
            self.call("post", f"/shop/orders/{o['id']}/payments", data={"amount": "10.00", "method": "cash"})
            return o

        # a plain cancel leaves the payments alone; the order tells what could be refunded to vouchers
        keep = order_with_vouchers()
        detail = self.call("get", f"/shop/orders/{keep['id']}").json()
        self.assertEqual((detail["voucher_refund_count"], detail["voucher_refund_total"]), (2, "50.00"))
        r = self.call("post", f"/shop/orders/{keep['id']}/cancel")
        self.assertEqual((r.status_code, r.json()["fulfillment_status"]), (200, "cancelled"))
        self.assertEqual(sorted(p["status"] for p in r.json()["payments"]), ["confirmed", "confirmed", "confirmed"])
        v1.refresh_from_db()
        v2.refresh_from_db()
        self.assertEqual((v1.available_amount, v2.available_amount), (Decimal("0"), Decimal("80.00")))
        # with refund_vouchers: the voucher payments are refunded and the balances come back; the cash payment stays
        again = order_with_vouchers()
        v1.refresh_from_db()
        v2.refresh_from_db()
        self.assertEqual(v2.available_amount, Decimal("60.00"))  # 80 - 20
        r = self.call("post", f"/shop/orders/{again['id']}/cancel", data={"refund_vouchers": True})
        self.assertEqual((r.status_code, r.json()["fulfillment_status"]), (200, "cancelled"))
        by_method = {p["method"]: p["status"] for p in r.json()["payments"]}
        self.assertEqual(by_method, {"voucher": "refunded", "cash": "confirmed"})
        self.assertEqual((r.json()["voucher_refund_count"], r.json()["voucher_refund_total"]), (0, "0.00"))
        v2.refresh_from_db()
        self.assertEqual(v2.available_amount, Decimal("80.00"))  # the 20.00 is back on the voucher
        # works together with the customised-items override
        custom = self.merch("Shirt", "40.00", personalization_enabled=True)
        o = self.create([{"product_id": str(custom.pk), "quantity": 1, "personalization_number": "1", "personalization_name": "Z"}])
        self.call("post", f"/shop/orders/{o['id']}/voucher", data={"number": v2.number})
        ids = [str(ln.pk) for ln in OrderLine.objects.filter(order_id=o["id"])]
        b = self.call("post", "/shop/production/batches", data={"line_ids": ids}).json()
        self.call("post", f"/shop/production/batches/{b['id']}/mark-ordered")
        self.assertEqual(self.call("post", f"/shop/orders/{o['id']}/cancel", data={"refund_vouchers": True}).status_code, 409)
        r = self.call("post", f"/shop/orders/{o['id']}/cancel", data={"override": True, "refund_vouchers": True})
        self.assertEqual((r.status_code, [p["status"] for p in r.json()["payments"]]), (200, ["refunded"]))
        v2.refresh_from_db()
        self.assertEqual(v2.available_amount, Decimal("80.00"))

    def test_members_are_told_when_their_items_go_into_production(self):
        from members.models import Member
        from notifications.models import InboxNotification
        from shop.services import notifications

        jersey = self.merch("Jersey", "40.00", personalization_enabled=True)
        scarf = self.merch("Scarf", "15.00")
        order = self.create([
            {"product_id": str(jersey.pk), "quantity": 1, "personalization_number": "7", "personalization_name": "Doe"},
            {"product_id": str(scarf.pk), "quantity": 2},
        ])
        lines = {ln.product.name: str(ln.pk) for ln in OrderLine.objects.filter(order_id=order["id"]).select_related("product")}
        # first batch holds only the jersey: part of the order goes into production
        b1 = self.call("post", "/shop/production/batches", data={"line_ids": [lines["Jersey"]]}).json()
        with self.captureOnCommitCallbacks(execute=True):
            self.call("post", f"/shop/production/batches/{b1['id']}/mark-ordered")
        (call,) = [c for c in self.dispatch.call_args_list if c.args[0].startswith("production")]
        self.assertEqual((call.args[0], str(call.args[1])), ("production_partial", order["id"]))
        self.assertEqual(call.args[2], {"items": "Jersey #7 Doe"})
        # the second batch completes it: the whole order is now in production, with the items of that batch
        self.dispatch.reset_mock()
        b2 = self.call("post", "/shop/production/batches", data={"line_ids": [lines["Scarf"]]}).json()
        with self.captureOnCommitCallbacks(execute=True):
            self.call("post", f"/shop/production/batches/{b2['id']}/mark-ordered")
        (call,) = [c for c in self.dispatch.call_args_list if c.args[0].startswith("production")]
        self.assertEqual((call.args[0], call.args[2]), ("production", {"items": "Scarf x2"}))

        # the text reaches the purchaser in their own language
        member = Member.objects.get(user=self.users["orders"])
        member.language = "en"
        member.save()
        mine = Order.objects.create(purchaser=member, total=Decimal("5"))
        self.assertEqual(notifications.send("production_partial", mine, {"items": "Jersey #7 Doe"}), 1)
        note = InboxNotification.objects.get(user=self.users["orders"])
        self.assertEqual(note.title, f"Part of order {mine.number} is in production")
        self.assertIn("Jersey #7 Doe", note.body)

    def test_available_vouchers_include_those_of_the_people_the_order_is_for(self):
        from members.models import Family, FamilyMembership, Member

        manager = self.buyer  # places the order for somebody else
        player = Member.objects.create(first_name="Pia", last_name="Player", email="pia@shop.test")
        parent = Member.objects.create(first_name="Paul", last_name="Player", email="paul@shop.test")
        stranger = Member.objects.create(first_name="Sam", last_name="Other", email="sam@shop.test")
        family = Family.objects.create(name="Player")
        FamilyMembership.objects.create(family=family, member=player, role="child")
        FamilyMembership.objects.create(family=family, member=parent, role="parent")
        expiry = datetime.date.today() + datetime.timedelta(days=30)
        own = Voucher.objects.create(amount=Decimal("5"), expiry_date=expiry, issued_to=manager)
        for_player = Voucher.objects.create(amount=Decimal("30"), expiry_date=expiry, issued_to=player)
        of_parent = Voucher.objects.create(amount=Decimal("20"), expiry_date=expiry, issued_to=parent)
        Voucher.objects.create(amount=Decimal("99"), expiry_date=expiry, issued_to=stranger)  # unrelated
        general = Voucher.objects.create(amount=Decimal("15"), expiry_date=expiry)
        jersey = self.merch("Jersey", "40.00")
        order = self.create([{"product_id": str(jersey.pk), "quantity": 1, "beneficiary_id": str(player.pk)}])
        rows = self.call("get", f"/shop/orders/{order['id']}/vouchers").json()
        self.assertEqual([(v["number"], v["scope"]) for v in rows], [(own.number, "own"), (for_player.number, "beneficiary"), (of_parent.number, "family"), (general.number, "general")])
        self.assertEqual(rows[1]["issued_to"]["name"], "Pia Player")
        # without a beneficiary the player's vouchers are not offered
        plain = self.create([{"product_id": str(jersey.pk), "quantity": 1}])
        numbers = [v["number"] for v in self.call("get", f"/shop/orders/{plain['id']}/vouchers").json()]
        self.assertEqual(numbers, [own.number, general.number])
        # and the beneficiary's voucher pays the order
        r = self.call("post", f"/shop/orders/{order['id']}/voucher", data={"number": for_player.number})
        self.assertEqual((r.status_code, r.json()["amount_due"]), (200, "10.00"))

    def test_ready_to_confirm_flag_and_filter(self):
        in_stock = self.merch("Cap", "10.00", inventory_managed=True)
        connection.set_tenant(self.club)
        inventory.receive_stock(self.owner, [(in_stock, None, 5)])
        stock_order = self.create([{"product_id": str(in_stock.pk), "quantity": 1}])
        made = self.create([{"product_id": str(self.merch("Jersey", "40.00").pk), "quantity": 1}])
        flag = lambda o: self.call("get", f"/shop/orders/{o['id']}").json()["ready_to_confirm"]  # noqa: E731
        self.assertEqual((flag(stock_order), flag(made)), (True, False))  # in stock: waiting to be confirmed; made to order: not yet
        listed = self.call("get", "/shop/orders?ready_to_confirm=true").json()
        self.assertEqual([o["id"] for o in listed["results"]], [stock_order["id"]])
        self.assertTrue(listed["results"][0]["ready_to_confirm"])
        self.assertEqual([o["id"] for o in self.call("get", "/shop/orders?ready_to_confirm=false").json()["results"]], [made["id"]])
        # production received: it joins; marked ready: it leaves
        line = OrderLine.objects.get(order_id=made["id"]).pk
        batch = self.call("post", "/shop/production/batches", data={"line_ids": [str(line)]}).json()
        self.call("post", f"/shop/production/batches/{batch['id']}/mark-ordered")
        self.assertFalse(flag(made))
        self.call("post", f"/shop/production/batches/{batch['id']}/receive", data={})
        self.assertTrue(flag(made))
        self.assertEqual(self.call("get", "/shop/orders?ready_to_confirm=true").json()["count"], 2)
        self.call("post", f"/shop/orders/{stock_order['id']}/ready", data={})
        self.assertFalse(flag(stock_order))
        self.assertEqual([o["id"] for o in self.call("get", "/shop/orders?ready_to_confirm=true").json()["results"]], [made["id"]])

    def test_numbering_schemes_in_the_settings(self):
        year = datetime.date.today().year
        yy = f"{year % 100:02d}"
        settings = self.call("get", "/shop/settings").json()
        by_kind = {n["kind"]: n for n in settings["numbering"]}
        self.assertEqual(sorted(by_kind), ["batch", "invoice", "order", "voucher"])
        self.assertEqual((by_kind["order"]["pattern"], by_kind["order"]["next_number"], by_kind["order"]["resets"]), ("ORD-{YYYY}-{NNNNN}", f"ORD-{year}-00001", "yearly"))
        self.assertIn("{NNNNN}", [p["token"] for p in settings["numbering_placeholders"]])

        # existing orders are counted: a new scheme continues after the numbers already issued
        self.create()
        self.create()
        self.assertEqual(self.call("get", "/shop/settings").json()["numbering"][0]["next_number"], f"ORD-{year}-00003")

        # invalid patterns change nothing and say what to fix (and a wrong entry anywhere stops the whole save)
        bad = self.call("put", "/shop/settings", data={"numbering": {"order": {"pattern": "ORD-{YYYY}"}}})
        self.assertEqual(bad.status_code, 422)
        self.assertIn("sequence placeholder", bad.json()["errors"]["numbering.order.pattern"][0])
        self.assertEqual(self.call("put", "/shop/settings", data={"numbering": {"order": {"pattern": "A/B?{NNN}"}}}).status_code, 422)
        self.assertEqual(self.call("put", "/shop/settings", data={"numbering": {"nope": {"pattern": "X{N}"}}}).status_code, 422)
        self.assertEqual(self.call("put", "/shop/settings", data={"numbering": {"order": {"pattern": "SHOP-{YY}{MM}-{NNN}"}, "invoice": {"pattern": "bad"}}}, who="owner").status_code, 422)
        self.assertEqual(self.call("get", "/shop/settings").json()["numbering"][0]["pattern"], "ORD-{YYYY}-{NNNNN}")

        # a new pattern: monthly counter, own text; the next order uses it (counting from what fits the new pattern, i.e. from 1)
        r = self.call("put", "/shop/settings", data={"numbering": {"order": {"pattern": "SHOP-{YY}{MM}-{NNN}"}}})
        self.assertEqual(r.status_code, 200, r.content)
        month = f"{datetime.date.today().month:02d}"
        self.assertEqual(next(n for n in r.json()["numbering"] if n["kind"] == "order")["next_number"], f"SHOP-{yy}{month}-001")
        self.assertEqual(next(n for n in r.json()["numbering"] if n["kind"] == "order")["resets"], "monthly")
        order = self.create()
        self.assertEqual(order["number"], f"SHOP-{yy}{month}-001")
        self.assertEqual(self.create()["number"], f"SHOP-{yy}{month}-002")
        # the old numbers stay as they were
        self.assertEqual(sorted(o["number"] for o in self.call("get", "/shop/orders?limit=10").json()["results"])[-1][:4], "SHOP")
        self.assertTrue(Order.objects.filter(number=f"ORD-{year}-00001").exists())

        # next number: forward only
        r = self.call("put", "/shop/settings", data={"numbering": {"order": {"next": 100}}})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.create()["number"], f"SHOP-{yy}{month}-100")
        back = self.call("put", "/shop/settings", data={"numbering": {"order": {"next": 50}}})
        self.assertEqual(back.status_code, 422)
        self.assertIn("Cannot go back", back.json()["errors"]["numbering.order.next"][0])
        # switching back to a pattern that fits existing numbers continues after the highest of them, not from 1
        self.call("put", "/shop/settings", data={"numbering": {"order": {"pattern": "ORD-{YYYY}-{NNNNN}"}}})
        self.assertEqual(self.create()["number"], f"ORD-{year}-00003")

        # other document types use their own pattern and counter
        self.call("put", "/shop/settings", who="owner", data={"numbering": {"voucher": {"pattern": "GIFT-{NNNN}"}, "invoice": {"pattern": "F{YY}/{N}"}}})
        v = Voucher.objects.create(amount=Decimal("5"), expiry_date=datetime.date.today() + datetime.timedelta(days=5))
        self.assertEqual(v.number, "GIFT-0001")
        inv_order = self.create()
        self.call("post", f"/shop/orders/{inv_order['id']}/ready", data={})  # issues the invoice
        self.assertEqual(self.call("get", f"/shop/orders/{inv_order['id']}").json()["invoice"]["number"], f"F{yy}/1")

        # preview: valid patterns show the next number, invalid ones say why (always 200)
        r = self.call("post", "/shop/settings/numbering-preview", data={"kind": "batch", "pattern": "B{YYYY}.{NN}"})
        self.assertEqual((r.status_code, r.json()["valid"], r.json()["number"], r.json()["resets"]), (200, True, f"B{year}.01", "yearly"))
        r = self.call("post", "/shop/settings/numbering-preview", data={"kind": "batch", "pattern": "{NN}{NN}"})
        self.assertEqual((r.json()["valid"], bool(r.json()["message"])), (False, True))
        self.assertEqual(self.call("post", "/shop/settings/numbering-preview", data={"kind": "nope", "pattern": "X{N}"}).status_code, 422)
        self.assertEqual(self.call("post", "/shop/settings/numbering-preview", data={"kind": "batch", "pattern": "B{N}"}, who="invoices").status_code, 403)

    def test_invoice_numbering_needs_the_whole_shop_permission(self):
        change = {"numbering": {"invoice": {"pattern": "F{YY}/{N}"}}}
        # order managers can see it but not change it: refused before anything is saved
        seen = self.call("get", "/shop/settings", who="orders").json()
        self.assertFalse(seen["can_edit_invoice_numbering"])
        r = self.call("put", "/shop/settings", who="orders", data=change)
        self.assertEqual(r.status_code, 403, r.content)
        self.assertEqual(self.call("put", "/shop/settings", who="orders", data={"numbering": {"invoice": {"next": 50}}}).status_code, 403)
        # the refusal also stops the other changes in the same request
        r = self.call("put", "/shop/settings", who="orders", data={"numbering": {"voucher": {"pattern": "G-{NNN}"}, **change["numbering"]}})
        self.assertEqual(r.status_code, 403)
        by_kind = {n["kind"]: n for n in self.call("get", "/shop/settings").json()["numbering"]}
        self.assertEqual((by_kind["invoice"]["pattern"], by_kind["voucher"]["pattern"]), ("INV-{YYYY}-{NNNNN}", "VCH-{YYYY}-{NNNNN}"))
        # the invoice manager alone is not enough either
        self.assertEqual(self.call("put", "/shop/settings", who="invoices", data=change).status_code, 403)
        # other document types stay with the order managers
        r = self.call("put", "/shop/settings", who="orders", data={"numbering": {"voucher": {"pattern": "G-{NNN}"}}})
        self.assertEqual(r.status_code, 200, r.content)
        # whoever manages the whole shop (the superset permission) or is the club Owner can change it
        self.assertTrue(self.call("get", "/shop/settings", who="superset").json()["can_edit_invoice_numbering"])
        r = self.call("put", "/shop/settings", who="superset", data=change)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(next(n for n in r.json()["numbering"] if n["kind"] == "invoice")["pattern"], "F{YY}/{N}")
        self.assertTrue(self.call("get", "/shop/settings", who="owner").json()["can_edit_invoice_numbering"])
        self.assertEqual(self.call("put", "/shop/settings", who="owner", data={"numbering": {"invoice": {"next": 40}}}).status_code, 200)

    def test_delete_draft_batch(self):
        self.create()
        line = self.call("get", "/shop/production/lines").json()["results"][0]["id"]
        bid = self.call("post", "/shop/production/batches", data={"line_ids": [line]}).json()["id"]
        self.assertEqual(self.call("delete", f"/shop/production/batches/{bid}").status_code, 204)
        self.assertEqual(self.call("get", "/shop/production/lines").json()["count"], 1)
        self.assertEqual(ProductionBatch.objects.count(), 0)

    # --- invoices ----------------------------------------------------------------------------------------------------

    def test_invoice_create_sequence_409_and_pdf(self):
        a, b = self.create()["id"], self.create()["id"]
        r = self.call("post", f"/shop/orders/{a}/invoice", who="invoices", data={})
        self.assertEqual(r.status_code, 201, r.content)
        year = datetime.date.today().year
        self.assertEqual((r.json()["number"], r.json()["amount_due"], r.json()["is_overdue"]), (f"INV-{year}-00001", "80.00", False))
        self.assertEqual(self.call("post", f"/shop/orders/{b}/invoice", who="invoices", data={"due_date": "2020-01-01"}).json()["number"], f"INV-{year}-00002")
        again = self.call("post", f"/shop/orders/{a}/invoice", who="invoices", data={})
        self.assertEqual((again.status_code, again.json()["code"]), (409, "invoice_exists"))
        self.assertEqual(self.call("get", f"/shop/orders/{a}", who="orders").json()["invoice"]["number"], f"INV-{year}-00001")
        listed = self.call("get", "/shop/invoices?status=overdue", who="invoices").json()
        self.assertEqual([i["number"] for i in listed["results"]], [f"INV-{year}-00002"])
        self.assertEqual(self.call("get", "/shop/invoices?status=unpaid", who="invoices").json()["count"], 2)
        self.call("post", f"/shop/orders/{a}/mark-paid", data={"method": "cash"})
        self.assertEqual(self.call("get", "/shop/invoices?status=paid", who="invoices").json()["count"], 1)
        inv = Invoice.objects.get(order_id=a)
        detail = self.call("get", f"/shop/invoices/{inv.pk}", who="invoices").json()
        self.assertEqual((detail["lines"][0]["quantity"], detail["billing_snapshot"]["purchaser"]["name"]), (2, "Bea Buyer"))
        pdf = self.call("get", f"/shop/invoices/{inv.pk}/pdf", who="invoices")
        try:
            import weasyprint  # noqa: F401
        except ImportError, OSError:
            self.assertEqual((pdf.status_code, pdf.json()["code"]), (503, "pdf_unavailable"))
            return
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf["Content-Type"], "application/pdf")
        self.assertTrue(pdf.content.startswith(b"%PDF"))
        self.assertEqual(self.call("get", f"/shop/invoices/{inv.pk}/pdf", who="invoices").content[:4], b"%PDF")  # cached
        # recreate: the cached file is dropped and built again; permission is manage_invoices
        self.assertEqual(self.call("post", f"/shop/invoices/{inv.pk}/recreate-pdf", who="orders").status_code, 403)
        r = self.call("post", f"/shop/invoices/{inv.pk}/recreate-pdf", who="invoices")
        self.assertEqual((r.status_code, r.json()["number"]), (200, inv.number))
        self.assertEqual(self.call("get", f"/shop/invoices/{inv.pk}/pdf", who="invoices").content[:4], b"%PDF")

    def test_invoice_lists_the_payments_made(self):
        from shop.services import invoices

        expiry = datetime.date.today() + datetime.timedelta(days=30)
        voucher = Voucher.objects.create(amount=Decimal("20"), expiry_date=expiry)
        order = self.create()  # 80.00
        self.call("post", f"/shop/orders/{order['id']}/voucher", data={"number": voucher.number})
        self.call("post", f"/shop/orders/{order['id']}/payments", data={"amount": "10.00", "method": "cash", "reference": "till 2"})
        self.call("post", f"/shop/orders/{order['id']}/payments", data={"amount": "5.00", "method": "bank_transfer", "reference": "BE00 1234"})
        refunded = self.call("post", f"/shop/orders/{order['id']}/payments", data={"amount": "1.00", "method": "cash"}).json()
        self.call("post", f"/shop/orders/{order['id']}/payments/{refunded['id']}/refund")
        connection.set_tenant(self.club)
        rows = invoices.payments_for(Order.objects.get(pk=order["id"]))
        # confirmed payments only (the refunded one is left out); the voucher is named by its number
        self.assertEqual([(r["label"], r["reference"], r["amount"]) for r in rows], [(f"Voucher {voucher.number}", "", Decimal("20.00")), ("Cash", "till 2", Decimal("10.00")), ("Bank transfer", "BE00 1234", Decimal("5.00"))])

        # and the PDF renders with them (skipped when WeasyPrint is not installed here)
        try:
            import weasyprint  # noqa: F401
        except ImportError, OSError:
            return
        self.call("post", f"/shop/orders/{order['id']}/invoice", who="invoices", data={})
        inv = Invoice.objects.get(order_id=order["id"])
        pdf = self.call("get", f"/shop/invoices/{inv.pk}/pdf", who="invoices")
        self.assertEqual((pdf.status_code, pdf.content[:4]), (200, b"%PDF"))

    def test_invoice_cancelled_order(self):
        oid = self.create()["id"]
        self.call("post", f"/shop/orders/{oid}/cancel")
        self.assertEqual(self.call("post", f"/shop/orders/{oid}/invoice", who="invoices", data={}).json()["code"], "order_cancelled")

    # --- stats / notifications ---------------------------------------------------------------------------------------

    def test_stats(self):
        a = self.create()["id"]
        self.create()
        self.call("post", f"/shop/orders/{a}/mark-paid", data={"method": "cash"})
        s = self.call("get", "/shop/stats").json()
        self.assertEqual((s["orders_total"], s["revenue"], s["outstanding"], s["unpaid_count"], s["items_sold"], s["lines_to_order_count"]), (2, "80.00", "80.00", 1, 2, 2))

    def test_notification_goes_to_purchaser_in_their_language(self):
        from notifications.models import InboxNotification
        from shop.services import notifications

        order = Order.objects.create(purchaser=self.users["orders"].member if hasattr(self.users["orders"], "member") else self.buyer, total=Decimal("5"))
        from members.models import Member

        member = Member.objects.get(user=self.users["orders"])
        order.purchaser = member
        member.language = "en"
        member.save()
        order.save()
        self.assertEqual(notifications.send("ready", order), 1)
        n = InboxNotification.objects.get(user=self.users["orders"])
        self.assertIn("ready for pickup", n.title)
