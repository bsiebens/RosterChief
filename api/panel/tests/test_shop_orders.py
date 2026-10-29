import datetime
from decimal import Decimal
from unittest import mock

from django.core.cache import cache
from django.test import override_settings
from waffle import get_waffle_flag_model

from api.models import Job
from api.protected.tests import CLUB_HOST, AppApiTestCase
from club.models import Club, ClubRole, ShopManager
from management.shop_export import stash_production_export
from members.models import Member
from shop.models import Invoice, Order, OrderLine, Payment, Product, ProductionStatus, ProductVariant, Voucher
from shop.services.invoices import ShopInvoicePDFError

BASE = "/manage/shop/orders"
EAGER = override_settings(CELERY_TASK_ALWAYS_EAGER=True)
NOTIFY = "shop.services.orders.dispatch_order_ready_for_pickup_notification"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class ShopOrdersTestCase(AppApiTestCase):
    def setUp(self):
        super().setUp()
        get_waffle_flag_model().objects.get_or_create(name="shop")[0].clubs.add(self.club)
        self._token = None
        self.shirt = Product.objects.create(club=self.club, name="Jersey", price=Decimal("30.00"), personalization_enabled=True)
        self.small = ProductVariant.objects.create(product=self.shirt, name="S")
        self.large = ProductVariant.objects.create(product=self.shirt, name="L", price=Decimal("35.00"))
        self.fee = Product.objects.create(club=self.club, name="Fee", price=Decimal("10.00"), product_type=Product.ProductType.EVENT_FEE)
        self.order = self.make_order(self.member, "60.00", self.shirt, self.small, quantity=2, unit="30.00")

    def make_order(self, purchaser, total, product, variant, *, quantity=1, unit="30.00", club=None, **line):
        order = Order.objects.create(club=club or self.club, purchaser=purchaser, total=Decimal(total))
        OrderLine.objects.create(order=order, product=product, variant=variant, quantity=quantity, unit_price=Decimal(unit), line_total=Decimal(unit) * quantity, **line)
        return order

    @property
    def line(self):
        return self.order.order_items.get()

    def as_role(self, role):
        ClubRole.objects.update_or_create(club=self.club, member=self.member, defaults={"role": role})
        if self._token is None:
            self._token = self.login_with_totp()
        return self._token

    def admin(self):
        return self.as_role(ClubRole.Roles.ADMIN)

    def call(self, method, path, token=None, **kwargs):
        return self.api(method, path, token=token, host=CLUB_HOST, **kwargs)

    def stranger_order(self):
        other = Club.objects.create(name="Rival FC", slug="rival-fc")
        sam = Member.objects.create(first_name="Sam", last_name="Stranger")
        product = Product.objects.create(club=other, name="Cap", price=Decimal("5.00"))
        return self.make_order(sam, "5.00", product, None, unit="5.00", club=other)

    def refresh(self):
        self.order.refresh_from_db()
        return self.order


class AccessTests(ShopOrdersTestCase):
    def endpoints(self, pk, line_pk, payment_pk):
        return (
            ("get", BASE, None),
            ("get", f"{BASE}/summary", None),
            ("get", f"{BASE}/ready-for-pickup", None),
            ("get", f"{BASE}/{pk}", None),
            ("post", f"{BASE}/{pk}/mark-paid", {}),
            ("post", f"{BASE}/{pk}/mark-ready-for-pickup", {}),
            ("post", f"{BASE}/{pk}/mark-delivered", None),
            ("post", f"{BASE}/{pk}/cancel", None),
            ("get", f"{BASE}/{pk}/invoice/pdf", None),
            ("patch", f"{BASE}/{pk}/lines/{line_pk}", {"quantity": 1}),
            ("post", f"{BASE}/{pk}/lines/{line_pk}/mark-received", None),
            ("post", f"{BASE}/{pk}/payments", {"amount": "5.00"}),
            ("patch", f"{BASE}/{pk}/payments/{payment_pk}", {"reference": "x"}),
            ("delete", f"{BASE}/{pk}/payments/{payment_pk}", None),
            ("post", f"{BASE}/bulk/mark-paid", {"order_ids": [str(pk)]}),
            ("post", f"{BASE}/bulk/mark-ready-for-pickup", {"order_ids": [str(pk)]}),
            ("post", f"{BASE}/export-production", {"product_ids": [str(self.shirt.pk)]}),
            ("get", f"{BASE}/export-production/abc", None),
            ("post", f"{BASE}/reprint-production", {"product_ids": [str(self.shirt.pk)]}),
            ("get", f"{BASE}/receive-production", None),
            ("post", f"{BASE}/receive-production", {"receipts": [{"product_id": str(self.shirt.pk), "quantity": 1}]}),
        )

    def all_endpoints(self):
        line = self.line
        payment = Payment.objects.create(order=self.order, amount=Decimal("1.00"), method="cash", status="confirmed")
        return self.endpoints(self.order.pk, line.pk, payment.pk)

    def test_no_token_is_401(self):
        for method, path, data in self.all_endpoints():
            self.assertError(self.call(method, path, data=data), 401, "unauthenticated")

    def test_plain_member_is_403(self):
        token = self.login()
        for method, path, data in self.all_endpoints():
            self.assertError(self.call(method, path, token, data=data), 403, "forbidden")

    def test_member_admin_is_403(self):
        token = self.as_role(ClubRole.Roles.MEMBER_ADMIN)
        for method, path, data in self.all_endpoints():
            self.assertError(self.call(method, path, token, data=data), 403, "forbidden")
        self.assertEqual(self.refresh().fulfillment_status, "not_ready")

    def test_shop_manager_without_a_role_may(self):
        ShopManager.objects.create(club=self.club, member=self.member)
        token = self.login_with_totp()
        self.assertEqual(self.call("get", BASE, token).status_code, 200)

    def test_feature_off_is_404(self):
        token = self.admin()
        get_waffle_flag_model().objects.get(name="shop").clubs.clear()
        cache.clear()
        for method, path, data in self.all_endpoints():
            self.assertError(self.call(method, path, token, data=data), 404, "feature_disabled")

    def test_other_clubs_order_is_404(self):
        other = self.stranger_order()
        token = self.admin()
        line = other.order_items.get()
        for method, path, data in self.endpoints(other.pk, line.pk, line.pk)[3:16]:
            if path.endswith(("bulk/mark-paid", "bulk/mark-ready-for-pickup")):
                continue
            self.assertError(self.call(method, path, token, data=data), 404, "not_found")
        self.assertEqual(Order.objects.get(pk=other.pk).fulfillment_status, "not_ready")

    def test_unknown_id_is_404(self):
        token = self.admin()
        response = self.call("get", f"{BASE}/00000000-0000-0000-0000-000000000000", token)
        self.assertError(response, 404, "not_found")


class ListTests(ShopOrdersTestCase):
    def test_list_filters_and_default_hides_closed(self):
        token = self.admin()
        closed = self.make_order(self.member, "10.00", self.fee, None, unit="10.00")
        Order.objects.filter(pk=closed.pk).update(payment_status="paid", fulfillment_status="delivered")
        self.stranger_order()

        ids = [row["id"] for row in self.call("get", BASE, token).json()["items"]]
        self.assertEqual(ids, [str(self.order.pk)])
        ids = [row["id"] for row in self.call("get", BASE + "?show=all", token).json()["items"]]
        self.assertEqual(set(ids), {str(self.order.pk), str(closed.pk)})
        ids = [row["id"] for row in self.call("get", BASE + "?payment_status=paid", token).json()["items"]]
        self.assertEqual(ids, [str(closed.pk)])
        ids = [row["id"] for row in self.call("get", BASE + "?fulfillment_status=delivered", token).json()["items"]]
        self.assertEqual(ids, [str(closed.pk)])
        item = self.call("get", BASE + "?q=ORD", token).json()["items"][0]
        self.assertEqual(item["purchaser_name"], str(self.member))
        self.assertTrue(item["has_production_lines"])
        self.assertEqual(self.call("get", BASE + "?q=zzz", token).json()["items"], [])
        self.assertEqual(self.call("get", BASE + "?q=Parent", token).json()["items"][0]["id"], str(self.order.pk))
        self.assertEqual(self.call("get", BASE + "?payment_status=bogus", token).status_code, 422)

    def test_keyset_paging_walks_every_order_once(self):
        token = self.admin()
        for _ in range(4):
            self.make_order(self.member, "30.00", self.shirt, self.small)
        seen, cursor = [], ""
        while True:
            body = self.call("get", f"{BASE}?limit=2&cursor={cursor}", token).json()
            seen += [row["id"] for row in body["items"]]
            if not body["next_cursor"]:
                break
            cursor = body["next_cursor"]
        self.assertEqual(len(seen), 5)
        self.assertEqual(len(set(seen)), 5)
        self.assertError(self.call("get", BASE + "?cursor=garbage", token), 400, "invalid_cursor")

    def test_summary(self):
        token = self.admin()
        body = self.call("get", f"{BASE}/summary", token).json()
        self.assertEqual((body["open_orders"], body["total_orders"]), (1, 1))
        self.assertEqual([(p["name"], p["pending_count"], p["in_production_count"]) for p in body["production_products"]], [("Jersey", 1, 0)])

    def test_detail(self):
        token = self.admin()
        Payment.objects.create(order=self.order, amount=Decimal("20.00"), method="cash", status="confirmed")
        body = self.call("get", f"{BASE}/{self.order.pk}", token).json()
        self.assertEqual(body["number"], self.order.number)
        self.assertEqual(Decimal(body["amount_due"]), Decimal("40.00"))
        self.assertEqual(Decimal(body["amount_paid"]), Decimal("20.00"))
        self.assertEqual(body["lines"][0]["variant_name"], "S")
        self.assertTrue(body["lines"][0]["personalization_enabled"])
        self.assertEqual(len(body["payments"]), 1)
        self.assertIsNone(body["invoice"])
        invoice = Invoice.objects.create(club=self.club, order=self.order)
        body = self.call("get", f"{BASE}/{self.order.pk}", token).json()
        self.assertEqual(body["invoice"]["id"], str(invoice.pk))


class ActionTests(ShopOrdersTestCase):
    def test_mark_paid_settles_the_remainder(self):
        token = self.admin()
        Payment.objects.create(order=self.order, amount=Decimal("20.00"), method="cash", status="confirmed")
        response = self.call("post", f"{BASE}/{self.order.pk}/mark-paid", token, data={"method": "bank_transfer", "reference": "R1"})
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["payment_status"], "paid")
        self.assertEqual(Decimal(body["amount_due"]), 0)
        self.assertEqual(sorted(Decimal(p["amount"]) for p in body["payments"]), [Decimal("20.00"), Decimal("40.00")])
        again = self.call("post", f"{BASE}/{self.order.pk}/mark-paid", token, data={})
        self.assertError(again, 409, "nothing_to_collect")

    def test_mark_paid_rejects_voucher_method(self):
        token = self.admin()
        response = self.call("post", f"{BASE}/{self.order.pk}/mark-paid", token, data={"method": "voucher"})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(Payment.objects.count(), 0)

    def test_mark_paid_idempotency(self):
        token = self.admin()
        first = self.call("post", f"{BASE}/{self.order.pk}/mark-paid", token, data={}, HTTP_IDEMPOTENCY_KEY="k1")
        second = self.call("post", f"{BASE}/{self.order.pk}/mark-paid", token, data={}, HTTP_IDEMPOTENCY_KEY="k1")
        self.assertEqual((first.status_code, second.status_code), (200, 200))
        self.assertEqual(first.json(), second.json())
        self.assertEqual(Payment.objects.count(), 1)

    def test_mark_ready_for_pickup(self):
        token = self.admin()
        with mock.patch(NOTIFY) as notify:
            response = self.call("post", f"{BASE}/{self.order.pk}/mark-ready-for-pickup", token, data={"pickup_instructions": "Clubhouse desk"})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["fulfillment_status"], "ready_for_pickup")
        self.assertEqual(self.refresh().pickup_instructions, "Clubhouse desk")
        notify.assert_called_once_with(self.order.pk)

    def test_mark_ready_replay_notifies_once(self):
        token = self.admin()
        with mock.patch(NOTIFY) as notify:
            for _ in range(2):
                self.call("post", f"{BASE}/{self.order.pk}/mark-ready-for-pickup", token, data={}, HTTP_IDEMPOTENCY_KEY="r1")
        self.assertEqual(notify.call_count, 1)

    def test_mark_delivered(self):
        token = self.admin()
        response = self.call("post", f"{BASE}/{self.order.pk}/mark-delivered", token)
        self.assertEqual(response.json()["fulfillment_status"], "delivered")
        self.assertEqual(self.refresh().payment_status, "pending")

    def test_cancel_returns_stock(self):
        token = self.admin()
        Product.objects.filter(pk=self.shirt.pk).update(inventory_managed=True)
        response = self.call("post", f"{BASE}/{self.order.pk}/cancel", token)
        self.assertEqual(response.json()["fulfillment_status"], "cancelled")

    def test_invoice_pdf(self):
        token = self.admin()
        self.assertError(self.call("get", f"{BASE}/{self.order.pk}/invoice/pdf", token), 404, "not_found")
        invoice = Invoice.objects.create(club=self.club, order=self.order)
        with mock.patch("api.panel.routers.shop_orders.render_invoice_pdf", return_value=b"%PDF-1.4 x"):
            response = self.call("get", f"{BASE}/{self.order.pk}/invoice/pdf", token)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn(invoice.number, response["Content-Disposition"])
        with mock.patch("api.panel.routers.shop_orders.render_invoice_pdf", side_effect=ShopInvoicePDFError("no pango")):
            self.assertError(self.call("get", f"{BASE}/{self.order.pk}/invoice/pdf", token), 503, "pdf_unavailable")


class PaymentTests(ShopOrdersTestCase):
    def test_add_partial_payment_then_rest(self):
        token = self.admin()
        response = self.call("post", f"{BASE}/{self.order.pk}/payments", token, data={"amount": "25.00", "method": "cash", "reference": "r"})
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual(body["payment"]["reference"], "r")
        self.assertEqual(body["order"]["payment_status"], "partially_paid")
        self.assertEqual(Decimal(body["order"]["amount_due"]), Decimal("35.00"))
        response = self.call("post", f"{BASE}/{self.order.pk}/payments", token, data={"amount": "35.00"})
        self.assertEqual(response.json()["order"]["payment_status"], "paid")

    def test_add_payment_validation(self):
        token = self.admin()
        for data, field in (({"amount": "0"}, "amount"), ({"amount": "5", "method": "voucher"}, "voucher_id"), ({"amount": "5", "method": "cash", "voucher_id": "00000000-0000-0000-0000-000000000000"}, "voucher_id")):
            response = self.call("post", f"{BASE}/{self.order.pk}/payments", token, data=data)
            self.assertEqual(response.status_code, 422, (data, response.content))
            self.assertIn(field, response.json()["error"]["fields"])
        self.assertEqual(Payment.objects.count(), 0)

    def test_voucher_payment_and_delete_restores_voucher(self):
        token = self.admin()
        voucher = Voucher.objects.create(club=self.club, issued_to=self.member, amount=Decimal("20.00"), expiry_date=datetime.date.today() + datetime.timedelta(days=30))
        response = self.call("post", f"{BASE}/{self.order.pk}/payments", token, data={"amount": "15.00", "method": "voucher", "voucher_id": str(voucher.pk)})
        self.assertEqual(response.status_code, 201, response.content)
        payment = response.json()["payment"]
        self.assertEqual(payment["voucher_number"], voucher.number)
        voucher.refresh_from_db()
        self.assertEqual(voucher.consumed_amount, Decimal("15.00"))
        too_much = self.call("post", f"{BASE}/{self.order.pk}/payments", token, data={"amount": "10.00", "method": "voucher", "voucher_id": str(voucher.pk)})
        self.assertEqual(too_much.status_code, 422)

        deleted = self.call("delete", f"{BASE}/{self.order.pk}/payments/{payment['id']}", token)
        self.assertEqual(deleted.status_code, 200, deleted.content)
        self.assertEqual(deleted.json()["payment_status"], "pending")
        self.assertEqual(deleted.json()["payments"], [])
        voucher.refresh_from_db()
        self.assertEqual(voucher.consumed_amount, 0)

    def test_payment_idempotency(self):
        token = self.admin()
        for _ in range(2):
            response = self.call("post", f"{BASE}/{self.order.pk}/payments", token, data={"amount": "5.00"}, HTTP_IDEMPOTENCY_KEY="p1")
            self.assertEqual(response.status_code, 201)
        self.assertEqual(Payment.objects.count(), 1)

    def test_edit_payment_method_and_reference(self):
        token = self.admin()
        payment = Payment.objects.create(order=self.order, amount=Decimal("5.00"), method="cash", status="confirmed", reference="old")
        response = self.call("patch", f"{BASE}/{self.order.pk}/payments/{payment.pk}", token, data={"reference": "new"})
        self.assertEqual(response.status_code, 200, response.content)
        payment.refresh_from_db()
        self.assertEqual((payment.method, payment.reference, payment.amount), ("cash", "new", Decimal("5.00")))
        bad = self.call("patch", f"{BASE}/{self.order.pk}/payments/{payment.pk}", token, data={"method": "voucher"})
        self.assertEqual(bad.status_code, 422)

    def test_delete_keeps_refunded_status(self):
        token = self.admin()
        payment = Payment.objects.create(order=self.order, amount=Decimal("5.00"), method="cash", status="confirmed")
        Order.objects.filter(pk=self.order.pk).update(payment_status="refunded")
        response = self.call("delete", f"{BASE}/{self.order.pk}/payments/{payment.pk}", token)
        self.assertEqual(response.json()["payment_status"], "refunded")


class LineTests(ShopOrdersTestCase):
    def test_edit_recomputes_prices_and_totals(self):
        token = self.admin()
        response = self.call("patch", f"{BASE}/{self.order.pk}/lines/{self.line.pk}", token, data={"variant_id": str(self.large.pk), "quantity": 3, "personalization_name": "KIM", "personalization_number": "7"})
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(Decimal(body["total"]), Decimal("105.00"))
        line = body["lines"][0]
        self.assertEqual((line["variant_name"], line["quantity"], Decimal(line["unit_price"]), line["personalization_name"]), ("L", 3, Decimal("35.00"), "KIM"))

    def test_edit_validation(self):
        token = self.admin()
        other_variant = ProductVariant.objects.create(product=self.fee, name="X")
        for data, field in (({"variant_id": str(other_variant.pk)}, "variant_id"), ({"quantity": 0}, "quantity"), ({"production_status": "bogus"}, "production_status")):
            response = self.call("patch", f"{BASE}/{self.order.pk}/lines/{self.line.pk}", token, data=data)
            self.assertEqual(response.status_code, 422, (data, response.content))
        response = self.call("patch", f"{BASE}/{self.order.pk}/lines/{self.line.pk}", token, data={"variant_id": str(other_variant.pk)})
        self.assertIn("variant_id", response.json()["error"]["fields"])

    def test_edit_without_stock_is_409(self):
        token = self.admin()
        Product.objects.filter(pk=self.shirt.pk).update(inventory_managed=True)
        response = self.call("patch", f"{BASE}/{self.order.pk}/lines/{self.line.pk}", token, data={"quantity": 5})
        self.assertError(response, 409, "not_enough_stock")
        self.assertEqual(self.line.quantity, 2)

    def test_mark_received_syncs_order(self):
        token = self.admin()
        OrderLine.objects.filter(pk=self.line.pk).update(production_status=ProductionStatus.IN_PRODUCTION)
        response = self.call("post", f"{BASE}/{self.order.pk}/lines/{self.line.pk}/mark-received", token)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["lines"][0]["production_status"], "received")
        self.assertEqual(response.json()["production_status"], "received")

    def test_line_of_another_order_is_404(self):
        token = self.admin()
        mine = self.make_order(self.member, "30.00", self.shirt, self.small)
        response = self.call("post", f"{BASE}/{mine.pk}/lines/{self.line.pk}/mark-received", token)
        self.assertError(response, 404, "not_found")


@EAGER
class BulkTests(ShopOrdersTestCase):
    def start(self, path, token, data):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.call("post", path, token, data=data)
        return response

    def test_bulk_mark_paid_job(self):
        token = self.admin()
        second = self.make_order(self.member, "30.00", self.shirt, self.small)
        paid = self.make_order(self.member, "30.00", self.shirt, self.small)
        Payment.objects.create(order=paid, amount=Decimal("30.00"), method="cash", status="confirmed")
        Order.objects.filter(pk=paid.pk).update(payment_status="paid")
        cancelled = self.make_order(self.member, "30.00", self.shirt, self.small)
        Order.objects.filter(pk=cancelled.pk).update(fulfillment_status="cancelled")
        stranger = self.stranger_order()
        ids = [str(o.pk) for o in (self.order, second, paid, cancelled, stranger)]

        response = self.start(f"{BASE}/bulk/mark-paid", token, {"order_ids": ids, "method": "bank_transfer"})
        self.assertEqual(response.status_code, 202, response.content)
        job = Job.objects.get(pk=response.json()["id"])
        self.assertEqual((job.status, job.result), ("succeeded", {"updated": 2}))
        self.assertEqual(Order.objects.filter(payment_status="paid", club=self.club).count(), 3)
        self.assertEqual(Order.objects.get(pk=cancelled.pk).payment_status, "pending")
        self.assertEqual(Order.objects.get(pk=stranger.pk).payment_status, "pending")
        self.assertEqual(self.call("get", f"/manage/jobs/{job.pk}", token).json()["status"], "succeeded")

    def test_bulk_mark_ready_job(self):
        token = self.admin()
        cancelled = self.make_order(self.member, "30.00", self.shirt, self.small)
        Order.objects.filter(pk=cancelled.pk).update(fulfillment_status="cancelled")
        with mock.patch(NOTIFY) as notify:
            response = self.start(f"{BASE}/bulk/mark-ready-for-pickup", token, {"order_ids": [str(self.order.pk), str(cancelled.pk)], "pickup_instructions": "Desk"})
        self.assertEqual(response.status_code, 202, response.content)
        self.assertEqual(Job.objects.get(pk=response.json()["id"]).result, {"updated": 1})
        self.assertEqual(self.refresh().fulfillment_status, "ready_for_pickup")
        self.assertEqual(self.order.pickup_instructions, "Desk")
        notify.assert_called_once_with(self.order.pk)

    def test_bulk_validation(self):
        token = self.admin()
        self.assertEqual(self.call("post", f"{BASE}/bulk/mark-paid", token, data={"order_ids": []}).status_code, 422)
        self.assertEqual(self.call("post", f"{BASE}/bulk/mark-paid", token, data={"order_ids": [str(self.order.pk)], "method": "voucher"}).status_code, 422)
        self.assertEqual(self.call("post", f"{BASE}/bulk/mark-ready-for-pickup", token, data={}).status_code, 422)
        self.assertEqual(Job.objects.count(), 0)

    def test_bulk_idempotency_starts_one_job(self):
        token = self.admin()
        for _ in range(2):
            with self.captureOnCommitCallbacks(execute=True):
                response = self.call("post", f"{BASE}/bulk/mark-paid", token, data={"order_ids": [str(self.order.pk)]}, HTTP_IDEMPOTENCY_KEY="b1")
            self.assertEqual(response.status_code, 202)
        self.assertEqual(Job.objects.count(), 1)

    def test_ready_for_pickup_queue(self):
        token = self.admin()
        self.assertEqual(self.call("get", f"{BASE}/ready-for-pickup", token).json()["items"], [])
        Order.objects.filter(pk=self.order.pk).update(production_status=ProductionStatus.RECEIVED)
        OrderLine.objects.filter(pk=self.line.pk).update(personalization_name="KIM")
        other = self.make_order(self.member, "30.00", self.shirt, self.small)
        Order.objects.filter(pk=other.pk).update(production_status=ProductionStatus.RECEIVED, fulfillment_status="ready_for_pickup")
        items = self.call("get", f"{BASE}/ready-for-pickup", token).json()["items"]
        self.assertEqual([(i["id"], i["has_personalized_lines"]) for i in items], [(str(self.order.pk), True)])


@EAGER
class ProductionTests(ShopOrdersTestCase):
    def test_export_job_marks_lines_and_download_is_one_time(self):
        token = self.admin()
        with self.captureOnCommitCallbacks(execute=True):
            response = self.call("post", f"{BASE}/export-production", token, data={"product_ids": [str(self.shirt.pk), str(self.fee.pk)]})
        self.assertEqual(response.status_code, 202, response.content)
        job = Job.objects.get(pk=response.json()["id"])
        self.assertEqual(job.status, "succeeded", job.error_message)
        self.assertEqual(job.result["lines"], 1)
        self.assertEqual(self.line.production_status, ProductionStatus.IN_PRODUCTION)
        self.assertEqual(self.refresh().production_status, ProductionStatus.IN_PRODUCTION)

        download = self.call("get", f"{BASE}/export-production/{job.result['token']}", token)
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download["Content-Type"], XLSX)
        self.assertIn("order-production-list.xlsx", download["Content-Disposition"])
        self.assertTrue(download.content.startswith(b"PK"))
        self.assertError(self.call("get", f"{BASE}/export-production/{job.result['token']}", token), 404, "not_found")

        # nothing left to export
        again = self.call("post", f"{BASE}/export-production", token, data={"product_ids": [str(self.shirt.pk)]})
        self.assertError(again, 422, "nothing_to_export")

    def test_export_validation(self):
        token = self.admin()
        for ids in ([], [str(self.fee.pk)], ["00000000-0000-0000-0000-000000000000"]):
            response = self.call("post", f"{BASE}/export-production", token, data={"product_ids": ids})
            self.assertEqual(response.status_code, 422, (ids, response.content))
        self.assertEqual(Job.objects.count(), 0)
        self.assertEqual(self.line.production_status, ProductionStatus.PENDING)

    def test_download_token_is_club_bound(self):
        token = self.admin()
        other = Club.objects.create(name="Rival FC", slug="rival-fc")
        import openpyxl

        stashed = stash_production_export(other, openpyxl.Workbook(), "x.xlsx")
        self.assertError(self.call("get", f"{BASE}/export-production/{stashed}", token), 404, "not_found")

    def test_reprint_does_not_mark_anything(self):
        token = self.admin()
        data = {"product_ids": [str(self.shirt.pk)]}
        self.assertError(self.call("post", f"{BASE}/reprint-production", token, data=data), 422, "nothing_to_download")
        OrderLine.objects.filter(pk=self.line.pk).update(production_status=ProductionStatus.IN_PRODUCTION)
        response = self.call("post", f"{BASE}/reprint-production", token, data=data)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response["Content-Type"], XLSX)
        self.assertTrue(response.content.startswith(b"PK"))
        self.assertEqual(self.line.production_status, ProductionStatus.IN_PRODUCTION)
        self.assertEqual(self.call("post", f"{BASE}/reprint-production", token, data={"product_ids": []}).status_code, 422)

    def test_receive_summary_and_receipt(self):
        token = self.admin()
        OrderLine.objects.filter(pk=self.line.pk).update(production_status=ProductionStatus.IN_PRODUCTION)
        second = self.make_order(self.member, "30.00", self.shirt, self.small)
        OrderLine.objects.filter(order=second).update(production_status=ProductionStatus.IN_PRODUCTION)
        for order in (self.order, second):
            Order.objects.filter(pk=order.pk).update(production_status=ProductionStatus.IN_PRODUCTION)

        summary = self.call("get", f"{BASE}/receive-production", token).json()["items"]
        self.assertEqual(len(summary), 1)
        self.assertEqual(summary[0]["variants"], [{"variant_id": str(self.small.pk), "variant_name": "S", "expected_quantity": 3}])

        receipt = {"product_id": str(self.shirt.pk), "variant_id": str(self.small.pk), "quantity": 2}
        response = self.call("post", f"{BASE}/receive-production", token, data={"receipts": [receipt]})
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        row = body["results"][0]
        # FIFO: the 2-unit order is oldest and fits; the 1-unit one is short
        self.assertEqual((row["expected_quantity"], row["received_quantity"], row["shortfall"], row["surplus"], row["has_discrepancy"]), (3, 2, 1, 0, True))
        self.assertEqual((len(row["allocated_line_ids"]), len(row["remaining_line_ids"]), body["allocated_lines"], body["discrepancies"]), (1, 1, 1, 1))
        self.assertEqual(self.line.production_status, ProductionStatus.RECEIVED)
        self.assertEqual(self.refresh().production_status, ProductionStatus.RECEIVED)

    def test_receive_validation_and_idempotency(self):
        token = self.admin()
        OrderLine.objects.filter(pk=self.line.pk).update(production_status=ProductionStatus.IN_PRODUCTION)
        good = {"product_id": str(self.shirt.pk), "variant_id": str(self.small.pk), "quantity": 2}
        for receipts in ([{**good, "variant_id": str(self.large.pk)}], [good, good], [{**good, "quantity": -1}], []):
            response = self.call("post", f"{BASE}/receive-production", token, data={"receipts": receipts})
            self.assertEqual(response.status_code, 422, (receipts, response.content))
        self.assertEqual(self.line.production_status, ProductionStatus.IN_PRODUCTION)
        first = self.call("post", f"{BASE}/receive-production", token, data={"receipts": [good]}, HTTP_IDEMPOTENCY_KEY="rc1")
        second = self.call("post", f"{BASE}/receive-production", token, data={"receipts": [good]}, HTTP_IDEMPOTENCY_KEY="rc1")
        self.assertEqual(first.json(), second.json())
