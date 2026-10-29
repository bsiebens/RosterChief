import datetime
from decimal import Decimal

from waffle import get_waffle_flag_model

from api.models import IdempotencyRecord
from api.panel.tests.base_slice import SliceTestCase
from club.models import ClubRole, ShopManager
from members.models import Member
from shop.models import AppliedDiscount, Discount, Order, Payment, Voucher, VoucherConsumption

DISCOUNTS = "/shop/discounts"
VOUCHERS = "/shop/vouchers"
TODAY = datetime.date.today()


class ShopCase(SliceTestCase):
    def setUp(self):
        super().setUp()
        get_waffle_flag_model().objects.get_or_create(name="shop")[0].clubs.add(self.club)
        self._token = None

    def token(self, role=ClubRole.Roles.ADMIN):
        ClubRole.objects.update_or_create(club=self.club, member=self.member, defaults={"role": role})
        if self._token is None:
            self._token = self.login_with_totp()
        return self._token

    def discount(self, club=None, **kwargs):
        return Discount.objects.create(club=club or self.club, **{"name": "Summer", "code": "SUMMER10", "discount_amount": 10, **kwargs})

    def voucher(self, club=None, **kwargs):
        return Voucher.objects.create(club=club or self.club, **{"amount": Decimal("50.00"), "expiry_date": TODAY + datetime.timedelta(days=30), **kwargs})


class AccessTests(ShopCase):
    def endpoints(self, did, vid, cid):
        return (
            ("get", DISCOUNTS, None),
            ("post", DISCOUNTS, {"name": "X", "code": "X1"}),
            ("get", f"{DISCOUNTS}/{did}", None),
            ("patch", f"{DISCOUNTS}/{did}", {"name": "Y"}),
            ("post", f"{DISCOUNTS}/{did}/toggle-active", None),
            ("get", VOUCHERS, None),
            ("post", VOUCHERS, {"amount": "5", "expiry_date": "2030-01-01"}),
            ("get", f"{VOUCHERS}/{vid}", None),
            ("patch", f"{VOUCHERS}/{vid}", {"notes": "n"}),
            ("post", f"{VOUCHERS}/{vid}/consumptions", {"amount": "1", "note": "n"}),
            ("delete", f"{VOUCHERS}/{vid}/consumptions/{cid}", None),
        )

    def setUp(self):
        super().setUp()
        self.d = self.discount()
        self.v = self.voucher()
        self.c = VoucherConsumption.objects.create(voucher=self.v, amount=Decimal("1.00"), note="x")

    def test_no_token_is_401(self):
        for method, path, data in self.endpoints(self.d.pk, self.v.pk, self.c.pk):
            self.assertError(self.call(method, path, None, data=data), 401, "unauthenticated")

    def test_plain_member_and_editor_are_403(self):
        token = self.login()
        for method, path, data in self.endpoints(self.d.pk, self.v.pk, self.c.pk):
            self.assertError(self.call(method, path, token, data=data), 403, "forbidden")
        token = self.token(ClubRole.Roles.EDITOR)
        for method, path, data in self.endpoints(self.d.pk, self.v.pk, self.c.pk):
            self.assertError(self.call(method, path, token, data=data), 403, "forbidden")

    def test_shop_manager_is_allowed(self):
        token = self.token(ClubRole.Roles.EDITOR)
        ShopManager.objects.create(club=self.club, member=self.member)
        self.assertEqual(self.call("get", DISCOUNTS, token).status_code, 200)
        self.assertEqual(self.call("get", VOUCHERS, token).status_code, 200)

    def test_feature_off_is_404(self):
        token = self.token()
        get_waffle_flag_model().objects.get(name="shop").clubs.clear()
        for method, path, data in self.endpoints(self.d.pk, self.v.pk, self.c.pk):
            self.assertError(self.call(method, path, token, data=data), 404, "feature_disabled")

    def test_other_club_objects_are_404(self):
        other, _season, _stranger = self.other_club()
        od, ov = self.discount(club=other, code="OTHER"), self.voucher(club=other)
        oc = VoucherConsumption.objects.create(voucher=ov, amount=Decimal("1.00"), note="x")
        token = self.token()
        for method, path, data in self.endpoints(od.pk, ov.pk, oc.pk):
            if "{" in path or "/consumptions" in path or path.count("/") > 2:
                self.assertError(self.call(method, path, token, data=data), 404, "not_found")
        self.assertError(self.call("delete", f"{VOUCHERS}/{self.v.pk}/consumptions/{oc.pk}", token), 404, "not_found")


class DiscountTests(ShopCase):
    def test_list_with_use_count_and_other_club_hidden(self):
        d = self.discount()
        self.discount(code="B", name="Zed")
        other, _s, _m = self.other_club()
        self.discount(club=other, code="OTHER")
        buyer = Member.objects.create(first_name="Bo", last_name="Buyer")
        for status in (Order.FulfillmentStatus.NOT_READY, Order.FulfillmentStatus.CANCELLED):
            order = Order.objects.create(club=self.club, purchaser=buyer, total=Decimal("10.00"), fulfillment_status=status)
            AppliedDiscount.objects.create(order=order, discount=d, discount_type="percentage", discount_amount=10)
        body = self.call("get", DISCOUNTS, self.token()).json()
        self.assertEqual([i["code"] for i in body["items"]], ["SUMMER10", "B"])
        self.assertEqual([i["use_count"] for i in body["items"]], [1, 0])
        self.assertIsNone(body["next_cursor"])
        page = self.call("get", f"{DISCOUNTS}?limit=1", self.token()).json()
        self.assertEqual(len(page["items"]), 1)
        self.assertIsNotNone(page["next_cursor"])

    def test_create_uppercases_code_and_scopes_to_club(self):
        response = self.call("post", DISCOUNTS, self.token(), data={"name": "Winter", "code": " winter5 ", "discount_type": "fixed_amount", "discount_amount": "5.00", "max_uses": 3, "valid_from": "2030-01-01"})
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual((body["code"], body["discount_type"], body["discount_amount"], body["max_uses"], body["use_count"]), ("WINTER5", "fixed_amount", "5.00", 3, 0))
        self.assertTrue(body["is_active"] and body["once_per_member"])
        self.assertEqual(Discount.objects.get(pk=body["id"]).club, self.club)

    def test_duplicate_code_is_422_on_code(self):
        self.discount()
        response = self.call("post", DISCOUNTS, self.token(), data={"name": "Other", "code": "summer10"})
        self.assertError(response, 422, "validation_error")
        self.assertIn("code", response.json()["error"]["fields"])
        other, _s, _m = self.other_club()
        self.discount(club=other, code="SHARED")
        self.assertEqual(self.call("post", DISCOUNTS, self.token(), data={"name": "Mine", "code": "shared"}).status_code, 201)

    def test_validation_errors(self):
        token = self.token()
        response = self.call("post", DISCOUNTS, token, data={"name": "Bad", "code": "BAD", "valid_from": "2030-02-01", "valid_until": "2030-01-01"})
        self.assertError(response, 422, "validation_error")
        self.assertIn("valid_until", response.json()["error"]["fields"])
        self.assertEqual(self.call("post", DISCOUNTS, token, data={"name": "Bad", "discount_type": "nope", "code": "X"}).status_code, 422)

    def test_create_is_idempotent(self):
        token = self.token()
        first = self.call("post", DISCOUNTS, token, data={"name": "Idem", "code": "IDEM"}, HTTP_IDEMPOTENCY_KEY="k1")
        second = self.call("post", DISCOUNTS, token, data={"name": "Idem", "code": "IDEM"}, HTTP_IDEMPOTENCY_KEY="k1")
        self.assertEqual((first.status_code, second.status_code), (201, 201))
        self.assertEqual(first.json(), second.json())
        self.assertEqual(Discount.objects.filter(code="IDEM").count(), 1)

    def test_patch_merges_and_keeps_own_code(self):
        d = self.discount(valid_until=TODAY + datetime.timedelta(days=5), max_uses=4)
        token = self.token()
        response = self.call("patch", f"{DISCOUNTS}/{d.pk}", token, data={"name": "Renamed", "code": "summer10", "valid_until": None})
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual((body["name"], body["code"], body["valid_until"], body["max_uses"]), ("Renamed", "SUMMER10", None, 4))
        self.assertError(self.call("patch", f"{DISCOUNTS}/{d.pk}", token, data={"name": None}), 422, "validation_error")
        clash = self.discount(code="OTHER", name="Other")
        self.assertError(self.call("patch", f"{DISCOUNTS}/{clash.pk}", token, data={"code": "Summer10"}), 422, "validation_error")

    def test_toggle_active_and_replay(self):
        d = self.discount()
        token = self.token()
        self.assertFalse(self.call("post", f"{DISCOUNTS}/{d.pk}/toggle-active", token).json()["is_active"])
        self.assertTrue(self.call("post", f"{DISCOUNTS}/{d.pk}/toggle-active", token).json()["is_active"])
        one = self.call("post", f"{DISCOUNTS}/{d.pk}/toggle-active", token, HTTP_IDEMPOTENCY_KEY="t1")
        two = self.call("post", f"{DISCOUNTS}/{d.pk}/toggle-active", token, HTTP_IDEMPOTENCY_KEY="t1")
        self.assertEqual(one.json(), two.json())
        d.refresh_from_db()
        self.assertFalse(d.is_active)
        self.assertEqual(IdempotencyRecord.objects.filter(key="t1").count(), 1)


class VoucherTests(ShopCase):
    def test_create_generates_number_and_validates(self):
        token = self.token()
        buyer = Member.objects.create(first_name="Bo", last_name="Buyer")
        response = self.call("post", VOUCHERS, token, data={"issued_to_id": str(self.child.pk), "amount": "25.00", "expiry_date": "2030-01-01", "notes": "gift"})
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertTrue(body["number"].startswith("VCH-"))
        self.assertEqual((body["amount"], body["available_amount"], body["is_usable"], body["issued_to"]["id"]), ("25.00", "25.00", True, str(self.child.pk)))
        self.assertEqual(Voucher.objects.get(pk=body["id"]).club, self.club)
        # someone outside the club
        response = self.call("post", VOUCHERS, token, data={"issued_to_id": str(buyer.pk), "amount": "5", "expiry_date": "2030-01-01"})
        self.assertError(response, 422, "validation_error")
        self.assertIn("issued_to_id", response.json()["error"]["fields"])
        self.assertEqual(self.call("post", VOUCHERS, token, data={"amount": "0", "expiry_date": "2030-01-01"}).status_code, 422)
        self.assertEqual(self.call("post", VOUCHERS, token, data={"amount": "5"}).status_code, 422)

    def test_create_is_idempotent(self):
        token = self.token()
        data = {"amount": "5.00", "expiry_date": "2030-01-01"}
        one = self.call("post", VOUCHERS, token, data=data, HTTP_IDEMPOTENCY_KEY="v1")
        two = self.call("post", VOUCHERS, token, data=data, HTTP_IDEMPOTENCY_KEY="v1")
        self.assertEqual(one.json(), two.json())
        self.assertEqual(Voucher.objects.count(), 1)

    def test_list_filters_and_scoping(self):
        a = self.voucher(issued_to=self.child)
        b = self.voucher(is_active=False)
        other, _s, _m = self.other_club()
        self.voucher(club=other)
        token = self.token()
        ids = lambda path: {i["id"] for i in self.call("get", path, token).json()["items"]}  # noqa: E731
        self.assertEqual(ids(VOUCHERS), {str(a.pk), str(b.pk)})
        self.assertEqual(ids(f"{VOUCHERS}?is_active=false"), {str(b.pk)})
        self.assertEqual(ids(f"{VOUCHERS}?q=Kim"), {str(a.pk)})
        self.assertEqual(ids(f"{VOUCHERS}?q={b.number}"), {str(b.pk)})
        page = self.call("get", f"{VOUCHERS}?limit=1", token).json()
        self.assertIsNotNone(page["next_cursor"])

    def test_detail_history_and_patch(self):
        v = self.voucher()
        token = self.token()
        manual = VoucherConsumption.objects.create(voucher=v, amount=Decimal("5.00"), note="Cash", recorded_by=self.user)
        order = Order.objects.create(club=self.club, purchaser=self.member, total=Decimal("10.00"))
        Payment.objects.create(order=order, amount=Decimal("10.00"), method="voucher", status="confirmed", voucher=v, paid_at=manual.recorded_at - datetime.timedelta(hours=1))
        Voucher.objects.filter(pk=v.pk).update(consumed_amount=Decimal("15.00"))
        body = self.call("get", f"{VOUCHERS}/{v.pk}", token).json()
        self.assertEqual(body["available_amount"], "35.00")
        self.assertEqual([r["kind"] for r in body["history"]], ["manual", "order"])
        self.assertEqual(body["history"][0]["consumption_id"], str(manual.pk))
        self.assertEqual(body["history"][0]["detail"], "Cash")
        self.assertEqual(body["history"][1]["order_id"], str(order.pk))
        # patch
        response = self.call("patch", f"{VOUCHERS}/{v.pk}", token, data={"notes": "void", "is_active": False, "issued_to_id": str(self.child.pk)})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual((response.json()["notes"], response.json()["is_active"], response.json()["is_usable"]), ("void", False, False))
        cleared = self.call("patch", f"{VOUCHERS}/{v.pk}", token, data={"issued_to_id": None}).json()
        self.assertIsNone(cleared["issued_to"])
        self.assertTrue(self.call("get", f"{VOUCHERS}/{v.pk}", token).json()["number"] == v.number)
        # below consumed
        self.assertError(self.call("patch", f"{VOUCHERS}/{v.pk}", token, data={"amount": "10.00"}), 422, "validation_error")
        self.assertEqual(self.call("patch", f"{VOUCHERS}/{v.pk}", token, data={"amount": "15.00"}).status_code, 200)
        self.assertError(self.call("patch", f"{VOUCHERS}/{v.pk}", token, data={"notes": None}), 422, "validation_error")

    def test_consumption_create_and_delete(self):
        v = self.voucher()
        token = self.token()
        response = self.call("post", f"{VOUCHERS}/{v.pk}/consumptions", token, data={"amount": "20.00", "note": "Clubhouse cash"})
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual((body["consumption"]["amount"], body["voucher"]["consumed_amount"], body["voucher"]["available_amount"]), ("20.00", "20.00", "30.00"))
        self.assertEqual(body["consumption"]["recorded_by"]["id"], str(self.user.pk))
        cid = body["consumption"]["id"]
        # more than available
        response = self.call("post", f"{VOUCHERS}/{v.pk}/consumptions", token, data={"amount": "31.00", "note": "Too much"})
        self.assertError(response, 422, "validation_error")
        self.assertIn("amount", response.json()["error"]["fields"])
        for bad in ({"amount": "0", "note": "x"}, {"amount": "1", "note": ""}, {"amount": "1"}):
            self.assertEqual(self.call("post", f"{VOUCHERS}/{v.pk}/consumptions", token, data=bad).status_code, 422)
        v.refresh_from_db()
        self.assertEqual(v.consumed_amount, Decimal("20.00"))
        # delete restores
        self.assertEqual(self.call("delete", f"{VOUCHERS}/{v.pk}/consumptions/{cid}", token).status_code, 204)
        v.refresh_from_db()
        self.assertEqual(v.consumed_amount, Decimal("0.00"))
        self.assertFalse(VoucherConsumption.objects.filter(pk=cid).exists())
        self.assertError(self.call("delete", f"{VOUCHERS}/{v.pk}/consumptions/{cid}", token), 404, "not_found")

    def test_consumption_is_idempotent(self):
        v = self.voucher()
        token = self.token()
        data = {"amount": "10.00", "note": "once"}
        one = self.call("post", f"{VOUCHERS}/{v.pk}/consumptions", token, data=data, HTTP_IDEMPOTENCY_KEY="c1")
        two = self.call("post", f"{VOUCHERS}/{v.pk}/consumptions", token, data=data, HTTP_IDEMPOTENCY_KEY="c1")
        self.assertEqual(one.json(), two.json())
        v.refresh_from_db()
        self.assertEqual(v.consumed_amount, Decimal("10.00"))

    def test_delete_consumption_of_another_voucher_is_404(self):
        v, w = self.voucher(), self.voucher()
        c = VoucherConsumption.objects.create(voucher=w, amount=Decimal("1.00"), note="x")
        self.assertError(self.call("delete", f"{VOUCHERS}/{v.pk}/consumptions/{c.pk}", self.token()), 404, "not_found")
