"""Shop management API (``/shop/categories|products|inventory|discounts|vouchers``): permission matrix, feature gate, CRUD,
variants, images, stock, discounts, vouchers and money serialisation."""

import datetime
import io
import json
import uuid
from decimal import Decimal
from types import SimpleNamespace

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import Client, TestCase
from django.utils import timezone

from controlpanel.tests import login_session
from features.entitlements import Entitlements
from shop.models import Discount, InventoryMovement, ProductAttachment, ProductImage, ProductVariant, Voucher
from shop.tests import ShopClubMixin

BASE = "/api/v1/shop"


class ShopApiTests(ShopClubMixin, TestCase):
    def call(self, method, path, user=None, data=None, files=None):
        headers = {"HTTP_HOST": self.host}
        if user is not None:
            headers["HTTP_X_SESSION_TOKEN"] = login_session(user)
        body = {"data": json.dumps(data), "content_type": "application/json"} if data is not None else {}
        if files:
            body = {"data": files}
        response = getattr(Client(), method)(BASE + path, **body, **headers)
        connection.set_tenant(self.club)
        return response

    def as_(self, key, method, path, data=None, image=None):
        return self.call(method, path, self.owner if key == "owner" else self.users[key], data, files={"image": image} if image is not None else None)

    def png(self, size=(4, 4)):
        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", size, "red").save(buf, "PNG")
        return SimpleUploadedFile("p.png", buf.getvalue(), content_type="image/png")

    # -- permission matrix --

    def test_permission_matrix(self):
        pid, vid = uuid.uuid4(), uuid.uuid4()
        endpoints = [  # (method, path, body, permission keys that may pass)
            ("get", "/categories", None, {"products", "inventory", "orders"}),
            ("get", "/products", None, {"products", "inventory", "orders"}),
            ("get", f"/products/{pid}", None, {"products", "inventory", "orders"}),
            ("post", "/categories", {"name": "Cat"}, {"products"}),
            ("post", "/products", {"name": "P", "price": "1.00"}, {"products"}),
            ("delete", f"/products/{pid}/variants/{vid}", None, {"products"}),
            ("get", "/inventory", None, {"inventory"}),
            ("get", "/inventory/movements", None, {"inventory"}),
            ("post", "/inventory/receive", {"items": [{"product_id": str(pid), "quantity": 1}]}, {"inventory"}),
            ("get", "/discounts", None, {"discounts"}),
            ("get", "/vouchers", None, {"discounts"}),
            ("get", f"/vouchers/{pid}", None, {"discounts"}),
        ]
        for method, path, body, allowed in endpoints:
            for key in ["plain", "invoices", "products", "inventory", "orders", "discounts"]:
                response = self.as_(key, method, path, body)
                if key in allowed:
                    self.assertNotEqual(response.status_code, 403, (key, method, path))
                else:
                    self.assertEqual(response.status_code, 403, (key, method, path))
            for key in ("owner", "superset"):
                self.assertNotEqual(self.as_(key, method, path, body).status_code, 403, (key, method, path))
        self.assertEqual(self.call("get", "/products").status_code, 401)

    def test_no_feature_is_404_for_owner_and_403_for_holders(self):
        self.features = Entitlements({})
        self.assertEqual(self.as_("owner", "get", "/products").status_code, 404)
        self.assertEqual(self.as_("owner", "get", "/vouchers").status_code, 404)
        self.assertEqual(self.as_("owner", "post", "/inventory/adjust", {"product_id": str(uuid.uuid4()), "counted": 1}).status_code, 404)
        self.assertEqual(self.as_("plain", "get", "/products").status_code, 403)

    # -- me --

    def test_me_flags(self):
        flags = ["can_manage_shop_products", "can_manage_shop_inventory", "can_manage_shop_orders", "can_manage_shop_discounts", "can_manage_shop_invoices"]

        def me(key):
            user = self.owner if key == "owner" else self.users[key]
            return Client().get("/api/v1/me", HTTP_HOST=self.host, HTTP_X_SESSION_TOKEN=login_session(user)).json()

        self.assertEqual([me("owner")[f] for f in flags], [True] * 5)
        self.assertEqual([me("superset")[f] for f in flags], [True] * 5)
        self.assertEqual([me("products")[f] for f in flags], [True, False, False, False, False])
        self.assertEqual([me("plain")[f] for f in flags], [False] * 5)
        self.features = Entitlements({})
        self.assertEqual([me("owner")[f] for f in flags], [False] * 5)

    # -- categories / products / variants --

    def test_category_crud(self):
        r = self.as_("products", "post", "/categories", {"name": "Merch"})
        self.assertEqual(r.status_code, 201, r.content)
        cid = r.json()["id"]
        self.assertEqual(self.as_("products", "post", "/categories", {"name": "merch"}).status_code, 422)
        self.assertEqual(self.as_("products", "patch", f"/categories/{cid}", {"name": "Gear"}).json()["name"], "Gear")
        pid = self.as_("products", "post", "/products", {"name": "Cap", "price": "9.50", "category_id": cid}).json()["id"]
        self.assertEqual(next(c for c in self.as_("orders", "get", "/categories").json()["results"] if c["id"] == cid)["product_count"], 1)
        self.assertEqual(self.as_("products", "delete", f"/categories/{cid}").status_code, 204)
        self.assertIsNone(self.as_("products", "get", f"/products/{pid}").json()["category"])

    def test_player_and_volunteer_are_fixed_system_categories(self):
        rows = self.as_("products", "get", "/categories").json()["results"]
        self.assertEqual([(c["name"], c["system_key"], c["is_system"]) for c in rows[:2]], [("Player", "player", True), ("Volunteer", "volunteer", True)])  # listed first
        self.assertTrue(all(not c["is_system"] for c in rows[2:]))
        player = rows[0]
        r = self.as_("products", "patch", f"/categories/{player['id']}", {"name": "Players"})
        self.assertEqual((r.status_code, r.json()["code"]), (422, "system_category"))
        r = self.as_("products", "delete", f"/categories/{player['id']}")
        self.assertEqual((r.status_code, r.json()["code"]), (409, "system_category"))
        self.assertEqual(self.as_("products", "post", "/categories", {"name": "player"}).status_code, 422)  # the name is taken
        # A product can be put in one; the product payload says so.
        r = self.as_("products", "post", "/products", {"name": "Season fee", "price": "10", "category_id": player["id"]})
        self.assertEqual(r.json()["category"]["system_key"], "player")

    def test_product_crud_money_strings_and_filters(self):
        r = self.as_("products", "post", "/products", {"name": "Jersey", "price": "40.5", "variants": [{"name": "S"}, {"name": "M", "price": "42.00"}]})
        self.assertEqual(r.status_code, 201, r.content)
        body = r.json()
        self.assertEqual(body["price"], "40.50")
        self.assertEqual([(v["name"], v["price"], v["effective_price"]) for v in body["variants"]], [("S", None, "40.50"), ("M", "42.00", "42.00")])
        self.assertEqual(body["slug"], "jersey")
        pid = body["id"]
        self.assertEqual(self.as_("products", "post", "/products", {"name": "Fee", "price": "5", "product_type": "event_fee", "is_public": False}).status_code, 201)
        self.assertEqual(self.as_("orders", "get", "/products?q=jers").json()["count"], 1)
        self.assertEqual(self.as_("orders", "get", "/products?public=false").json()["count"], 1)
        self.assertEqual(self.as_("orders", "get", "/products?product_type=event_fee").json()["results"][0]["name"], "Fee")
        patched = self.as_("products", "patch", f"/products/{pid}", {"price": "45", "is_active": False, "description": "Nice"}).json()
        self.assertEqual((patched["price"], patched["is_active"], patched["description"]), ("45.00", False, "Nice"))
        self.assertEqual(self.as_("products", "patch", f"/products/{pid}", {"price": "-1"}).status_code, 422)
        self.assertEqual(self.as_("products", "post", "/products", {"name": "X", "price": "1", "product_type": "membership", "inventory_managed": True}).status_code, 422)
        self.assertEqual(self.as_("products", "get", f"/products/{uuid.uuid4()}").status_code, 404)
        self.assertEqual(self.as_("products", "delete", f"/products/{pid}").status_code, 204)

    def test_ordering_windows_are_inclusive_and_enforced_on_order(self):
        today = timezone.localdate()
        yesterday = (today - datetime.timedelta(days=1)).isoformat()
        next_week = (today + datetime.timedelta(days=7)).isoformat()
        expired = {"deadline": yesterday, "estimated_delivery": today.isoformat()}
        open_now = {"deadline": today.isoformat(), "estimated_delivery": next_week}
        response = self.as_("products", "post", "/products", {"name": "Pre-order jersey", "price": "25", "ordering_windows": [open_now, expired]})
        self.assertEqual(response.status_code, 201, response.content)
        product = response.json()
        self.assertEqual(product["ordering_windows"], [expired, open_now])
        self.assertEqual(product["next_ordering_window"], open_now)
        self.assertTrue(product["in_stock"])
        path = f"/products/{product['id']}"
        rejected = self.as_("products", "patch", path, {"ordering_windows": [open_now, open_now]})
        self.assertEqual(rejected.status_code, 422)
        closed = self.as_("products", "patch", path, {"ordering_windows": [expired]}).json()
        self.assertFalse(closed["in_stock"])
        self.assertTrue(closed["ordering_closed"])
        order = {"purchaser_id": str(self.buyer.pk), "lines": [{"product_id": product["id"], "quantity": 1}]}
        self.assertEqual(self.as_("orders", "post", "/orders", order).status_code, 422)
        cleared = self.as_("products", "patch", path, {"ordering_windows": []}).json()
        self.assertTrue(cleared["in_stock"])
        self.assertFalse(cleared["ordering_closed"])

    def test_negative_prices_are_only_for_membership_products(self):
        """A negative price is a credit on a registration: membership products and their variants only."""
        ok = self.as_("owner", "post", "/products", {"name": "Volunteer credit", "price": "-20.00", "product_type": "membership"})
        self.assertEqual(ok.status_code, 201, ok.content)
        self.assertEqual(ok.json()["price"], "-20.00")
        pid = ok.json()["id"]
        v = self.as_("owner", "post", f"/products/{pid}/variants", {"name": "Half", "price": "-10.00"})
        self.assertEqual(v.status_code, 201, v.content)
        self.assertEqual(self.as_("owner", "patch", f"/products/{pid}/variants/{v.json()['id']}", {"price": "-15.00"}).status_code, 200)
        # merchandise and event fees stay at zero or more, and a product can't become one while a variant is negative
        for kind in ("merchandise", "event_fee"):
            r = self.as_("owner", "post", "/products", {"name": f"Bad {kind}", "price": "-1.00", "product_type": kind})
            self.assertEqual(r.status_code, 422, kind)
            self.assertIn("price", r.json()["errors"])
        merch = self.as_("owner", "post", "/products", {"name": "Shirt", "price": "10.00", "product_type": "merchandise"}).json()["id"]
        r = self.as_("owner", "post", f"/products/{merch}/variants", {"name": "S", "price": "-1.00"})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(self.as_("owner", "patch", f"/products/{pid}", {"product_type": "merchandise", "price": "5.00"}).status_code, 422)

    def test_a_negative_product_cannot_be_ordered(self):
        pid = self.as_("owner", "post", "/products", {"name": "Credit", "price": "-5.00", "product_type": "membership"}).json()["id"]
        r = self.as_("owner", "post", "/orders", {"purchaser_id": str(self.buyer.pk), "lines": [{"product_id": pid, "quantity": 1}]})
        self.assertEqual(r.status_code, 422, r.content)
        self.assertIn("credit", r.content.decode())  # refused for that reason, not for a malformed request

    def test_product_with_orders_cannot_be_deleted(self):
        product = self.merch()
        self.make_order(product)
        r = self.as_("products", "delete", f"/products/{product.pk}")
        self.assertEqual((r.status_code, r.json()["code"]), (409, "has_orders"))

    def test_variants(self):
        product = self.merch()
        base = f"/products/{product.pk}/variants"
        r = self.as_("products", "post", base, {"name": "S"})
        self.assertEqual(r.status_code, 201, r.content)
        vid = r.json()["id"]
        self.assertEqual(self.as_("products", "post", base, {"name": "s"}).status_code, 422)
        r = self.as_("products", "patch", f"{base}/{vid}", {"price": "12.00", "is_active": False})
        self.assertEqual((r.json()["price"], r.json()["is_active"], r.json()["stock"]), ("12.00", False, None))
        r = self.as_("products", "patch", f"{base}/{vid}", {"price": None})
        self.assertIsNone(r.json()["price"])
        self.assertEqual(self.as_("products", "get", f"{base}/{uuid.uuid4()}").status_code, 405)
        self.assertEqual(self.as_("products", "delete", f"/products/{uuid.uuid4()}/variants/{vid}").status_code, 404)
        self.make_order(product, ProductVariant.objects.get(pk=vid))
        r = self.as_("products", "delete", f"{base}/{vid}")
        self.assertEqual((r.status_code, r.json()["code"]), (409, "has_orders"))

    def upload(self, key, path, files, **form):
        """A multipart POST with several files under one field (``files`` = {field: [file, ...]}) plus text fields."""
        return self.call("post", path, self.owner if key == "owner" else self.users[key], files={**files, **form})

    def test_product_gallery(self):
        from unittest import mock

        product = self.merch()
        base = f"/products/{product.pk}"
        r = self.upload("products", f"{base}/images", {"images": [self.png(), self.png((8, 8))]})
        self.assertEqual(r.status_code, 200, r.content)
        images = r.json()["images"]
        self.assertEqual([(i["is_main"], i["ordering"]) for i in images], [(True, 0), (False, 1)])
        self.assertEqual(r.json()["image_url"], images[0]["url"])  # the list shows the main picture
        # rules: type, size, count (all or nothing), permission
        bad = SimpleUploadedFile("x.png", b"not an image", content_type="image/png")
        r = self.upload("products", f"{base}/images", {"images": [self.png(), bad]})
        self.assertEqual(r.status_code, 422)
        self.assertIn("not a valid image", r.json()["errors"]["images"][0])
        self.assertEqual(len(self.as_("products", "get", base).json()["images"]), 2)  # nothing of the failed batch was kept
        self.assertEqual(self.upload("inventory", f"{base}/images", {"images": [self.png()]}).status_code, 403)
        with mock.patch("shop.services.catalog.MAX_IMAGE_BYTES", 10):
            self.assertEqual(self.upload("products", f"{base}/images", {"images": [self.png()]}).status_code, 422)
        with mock.patch("shop.services.catalog.MAX_IMAGES", 3):
            r = self.upload("products", f"{base}/images", {"images": [self.png(), self.png()]})
            self.assertEqual(r.status_code, 422)
            self.assertIn("at most 3 pictures", r.json()["errors"]["images"][0])

        # main picture, description, order, delete
        first, second = images[0]["id"], images[1]["id"]
        r = self.as_("products", "post", f"{base}/images/{second}/set-main")
        self.assertEqual([(i["id"], i["is_main"]) for i in r.json()["images"]], [(first, False), (second, True)])
        self.assertEqual(r.json()["image_url"], images[1]["url"])
        r = self.as_("products", "patch", f"{base}/images/{first}", {"alt": "  Front  "})
        self.assertEqual(r.json()["images"][0]["alt"], "Front")
        r = self.as_("products", "put", f"{base}/images/order", {"ids": [second, first]})
        self.assertEqual([i["id"] for i in r.json()["images"]], [second, first])
        self.assertEqual(self.as_("products", "put", f"{base}/images/order", {"ids": [first]}).status_code, 422)  # every picture, once
        stored = ProductImage.objects.get(pk=second).image.name
        self.assertTrue(ProductImage.objects.get(pk=second).image.storage.exists(stored))
        with self.captureOnCommitCallbacks(execute=True):
            r = self.as_("products", "delete", f"{base}/images/{second}")  # the main one: the next takes over
        self.assertEqual([(i["id"], i["is_main"]) for i in r.json()["images"]], [(first, True)])
        self.assertFalse(ProductImage.objects.filter(pk=second).exists())
        self.assertFalse(ProductImage.objects.get(pk=first).image.storage.exists(stored))  # the removed file left storage
        self.assertEqual(self.as_("products", "delete", f"{base}/images/{second}").status_code, 409)  # already gone
        self.as_("products", "delete", f"{base}/images/{first}")
        self.assertIsNone(self.as_("products", "get", base).json()["image_url"])
        # a picture of another product is not reachable through this one
        other = self.merch("Other")
        pic = self.upload("products", f"/products/{other.pk}/images", {"images": [self.png()]}).json()["images"][0]["id"]
        self.assertEqual(self.as_("products", "delete", f"{base}/images/{pic}").status_code, 409)

    def test_product_attachments(self):
        from unittest import mock

        product = self.merch()
        base = f"/products/{product.pk}"
        guide = SimpleUploadedFile("Size guide.pdf", b"%PDF-1.4 sample", content_type="application/pdf")
        r = self.upload("products", f"{base}/attachments", {"files": [guide]}, title="Sizes")
        self.assertEqual(r.status_code, 200, r.content)
        (att,) = r.json()["attachments"]
        self.assertEqual((att["title"], att["name"], att["size"], att["content_type"]), ("Sizes", "Size guide.pdf", 15, "application/pdf"))
        self.assertTrue(att["url"])
        # several files: each titled by its file name (the title field only names a single file)
        two = [SimpleUploadedFile("care.docx", b"x", content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document"), SimpleUploadedFile("chart.png", b"y", content_type="image/png")]
        r = self.upload("products", f"{base}/attachments", {"files": two}, title="ignored")
        self.assertEqual([a["title"] for a in r.json()["attachments"]], ["Sizes", "care", "chart"])
        # rules: allowed types only, not empty, size, count, permission; nothing is kept from a refused batch
        html = SimpleUploadedFile("x.html", b"<script>", content_type="text/html")
        r = self.upload("products", f"{base}/attachments", {"files": [html]})
        self.assertEqual((r.status_code, "not an allowed file type" in r.json()["errors"]["files"][0]), (422, True))
        self.assertEqual(self.upload("products", f"{base}/attachments", {"files": [SimpleUploadedFile("e.pdf", b"")]}).status_code, 422)
        self.assertEqual(self.upload("inventory", f"{base}/attachments", {"files": [guide]}).status_code, 403)
        with mock.patch("shop.services.catalog.MAX_ATTACHMENT_BYTES", 5):
            self.assertEqual(self.upload("products", f"{base}/attachments", {"files": [SimpleUploadedFile("big.pdf", b"123456")]}).status_code, 422)
        with mock.patch("shop.services.catalog.MAX_ATTACHMENTS", 3):
            r = self.upload("products", f"{base}/attachments", {"files": [SimpleUploadedFile("a.pdf", b"1")]})
            self.assertEqual((r.status_code, "at most 3 files" in r.json()["errors"]["files"][0]), (422, True))
        self.assertEqual(ProductAttachment.objects.filter(product=product).count(), 3)
        # rename and delete (the file leaves storage once the transaction commits)
        r = self.as_("products", "patch", f"{base}/attachments/{att['id']}", {"title": "  Size guide 2026 "})
        self.assertEqual(r.json()["attachments"][0]["title"], "Size guide 2026")
        self.assertEqual(self.as_("products", "patch", f"{base}/attachments/{att['id']}", {"title": "  "}).status_code, 422)
        stored = ProductAttachment.objects.get(pk=att["id"])
        name, storage = stored.file.name, stored.file.storage
        with self.captureOnCommitCallbacks(execute=True):
            r = self.as_("products", "delete", f"{base}/attachments/{att['id']}")
        self.assertEqual(len(r.json()["attachments"]), 2)
        self.assertFalse(storage.exists(name))
        self.assertEqual(self.as_("products", "delete", f"{base}/attachments/{att['id']}").status_code, 409)

    def test_single_picture_moves_into_the_gallery(self):
        """The data migration copies each product's old single picture into the gallery as its main picture."""
        import importlib

        from django.apps import apps as django_apps

        migration = importlib.import_module("shop.migrations.0006_product_gallery_attachments")
        product = self.merch()
        legacy = SimpleNamespace(image=SimpleNamespace(name="shop/products/legacy/photo.png"), pk=product.pk)

        class FakeProducts:
            @staticmethod
            def exclude(**kwargs):
                return [legacy]

        class FakeModel:
            objects = FakeProducts()

        created = []
        fake_images = SimpleNamespace(objects=SimpleNamespace(create=lambda **kw: created.append(kw)))
        fake_apps = SimpleNamespace(get_model=lambda app, name: FakeModel if name == "Product" else fake_images)
        migration.copy_images_to_gallery(fake_apps, None)
        self.assertEqual(created, [{"product": legacy, "image": "shop/products/legacy/photo.png", "is_main": True, "ordering": 0}])
        self.assertTrue(django_apps.get_model("shop", "ProductImage"))

    # -- inventory --

    def test_inventory_receive_adjust_movements(self):
        product = self.merch(inventory_managed=True)
        s = ProductVariant.objects.create(product=product, name="S")
        plain = self.merch("Mug", inventory_managed=True)
        r = self.as_("inventory", "post", "/inventory/receive", {"note": "box 1", "items": [{"product_id": str(product.pk), "variant_id": str(s.pk), "quantity": 6}, {"product_id": str(plain.pk), "quantity": 2}]})
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual([m["quantity"] for m in r.json()["movements"]], [6, 2])
        self.assertEqual(self.as_("inventory", "post", "/inventory/receive", {"items": [{"product_id": str(product.pk), "quantity": 1}]}).status_code, 422)
        self.assertEqual(self.as_("inventory", "post", "/inventory/receive", {"items": [{"product_id": str(plain.pk), "quantity": 0}]}).status_code, 422)
        r = self.as_("inventory", "post", "/inventory/adjust", {"product_id": str(product.pk), "variant_id": str(s.pk), "counted": 4, "note": "recount"})
        self.assertEqual((r.json()["movement"]["quantity"], r.json()["movement"]["reason"]), (-2, "adjustment"))
        self.assertIsNone(self.as_("inventory", "post", "/inventory/adjust", {"product_id": str(product.pk), "variant_id": str(s.pk), "counted": 4}).json()["movement"])
        self.assertEqual(self.as_("inventory", "post", "/inventory/adjust", {"product_id": str(plain.pk), "counted": -1}).status_code, 422)
        stock = {(x["product_name"], x["variant_name"]): x["on_hand"] for x in self.as_("inventory", "get", "/inventory").json()["results"]}
        self.assertEqual(stock, {("Jersey", "S"): 4, ("Mug", None): 2})
        InventoryMovement.objects.create(product=plain, quantity=-2, reason="order")
        self.assertEqual([x["product_name"] for x in self.as_("inventory", "get", "/inventory?low=true").json()["results"]], ["Mug"])
        moves = self.as_("inventory", "get", f"/inventory/movements?product_id={product.pk}").json()
        self.assertEqual((moves["count"], moves["results"][0]["reason"]), (2, "adjustment"))
        self.assertEqual(self.as_("inventory", "get", "/inventory/movements?reason=order").json()["count"], 1)
        listed = self.as_("products", "get", f"/products/{product.pk}").json()
        self.assertEqual((listed["variants"][0]["stock"], listed["in_stock"]), (4, True))
        self.assertFalse(self.as_("products", "get", f"/products/{plain.pk}").json()["in_stock"])

    def test_inventory_product_with_stock_history_cannot_be_deleted(self):
        product = self.merch(inventory_managed=True)
        InventoryMovement.objects.create(product=product, quantity=1, reason="received")
        r = self.as_("products", "delete", f"/products/{product.pk}")
        self.assertEqual((r.status_code, r.json()["code"]), (409, "has_stock_history"))

    # -- discounts --

    def test_discounts(self):
        today = datetime.date.today()
        data = {"name": "Summer", "code": "summer10", "discount_type": "percentage", "discount_amount": "10", "valid_from": str(today), "valid_until": str(today + datetime.timedelta(days=5))}
        r = self.as_("discounts", "post", "/discounts", data)
        self.assertEqual(r.status_code, 201, r.content)
        d = r.json()
        self.assertEqual((d["code"], d["discount_amount"], d["status"], d["used_count"]), ("SUMMER10", "10.00", "active", 0))
        self.assertEqual(self.as_("discounts", "post", "/discounts", {**data, "name": "Dup", "code": "SUMMER10"}).json()["errors"]["code"], ["Already in use."])
        for bad in ({"discount_amount": "120"}, {"discount_type": "fixed_amount", "discount_amount": "0"}, {"valid_until": str(today - datetime.timedelta(days=1))}):
            self.assertEqual(self.as_("discounts", "post", "/discounts", {**data, "code": "OTHER", **bad}).status_code, 422, bad)
        self.as_("discounts", "post", "/discounts", {"name": "Later", "code": "LATER", "discount_amount": "5", "discount_type": "fixed_amount", "valid_from": str(today + datetime.timedelta(days=3))})
        self.assertEqual(self.as_("discounts", "get", "/discounts?status=scheduled").json()["results"][0]["code"], "LATER")
        self.assertEqual(self.as_("discounts", "get", "/discounts?q=summ").json()["count"], 1)
        r = self.as_("discounts", "patch", f"/discounts/{d['id']}", {"is_active": False, "max_uses": 3, "valid_until": None})
        self.assertEqual((r.json()["status"], r.json()["max_uses"], r.json()["valid_until"]), ("inactive", 3, None))
        order, _ = self.make_order(self.merch())
        discount = Discount.objects.get(pk=d["id"])
        order.applied_discounts.create(discount=discount, discount_type=discount.discount_type, discount_amount=discount.discount_amount)
        self.assertEqual(self.as_("discounts", "get", f"/discounts/{d['id']}").json()["used_count"], 1)
        r = self.as_("discounts", "delete", f"/discounts/{d['id']}")
        self.assertEqual((r.status_code, r.json()["code"]), (409, "has_orders"))
        later = Discount.objects.get(code="LATER")
        self.assertEqual(self.as_("discounts", "delete", f"/discounts/{later.pk}").status_code, 204)

    # -- vouchers --

    def test_vouchers(self):
        expiry = str(datetime.date.today() + datetime.timedelta(days=30))
        r = self.as_("discounts", "post", "/vouchers", {"amount": "25", "expiry_date": expiry, "count": 3, "issued_to_id": str(self.buyer.pk), "notes": "gift"})
        self.assertEqual(r.status_code, 201, r.content)
        results = r.json()["results"]
        self.assertEqual((r.json()["count"], len({v["number"] for v in results}), results[0]["amount"], results[0]["status"]), (3, 3, "25.00", "usable"))
        self.assertEqual(results[0]["issued_to"]["name"], str(self.buyer))
        self.assertEqual(self.as_("discounts", "post", "/vouchers", {"amount": "0", "expiry_date": expiry}).status_code, 422)
        self.assertEqual(self.as_("discounts", "post", "/vouchers", {"amount": "5", "expiry_date": expiry, "count": 500}).status_code, 422)
        self.assertEqual(self.as_("discounts", "post", "/vouchers", {"amount": "5", "expiry_date": expiry, "issued_to_id": str(uuid.uuid4())}).status_code, 422)
        v = results[0]
        found = self.as_("discounts", "get", f"/vouchers/lookup?number={v['number'].lower()}")
        self.assertEqual(found.json()["id"], v["id"])
        self.assertEqual(self.as_("discounts", "get", "/vouchers/lookup?number=NOPE").status_code, 404)
        self.assertEqual(self.as_("discounts", "get", "/vouchers?q=Buyer").json()["count"], 3)
        c = self.as_("discounts", "post", f"/vouchers/{v['id']}/consumptions", {"amount": "10", "note": "cash at the bar"})
        self.assertEqual((c.status_code, c.json()["kind"], c.json()["amount"]), (201, "manual", "10.00"))
        self.assertEqual(self.as_("discounts", "post", f"/vouchers/{v['id']}/consumptions", {"amount": "16"}).status_code, 422)
        detail = self.as_("discounts", "get", f"/vouchers/{v['id']}").json()
        self.assertEqual((detail["consumed_amount"], detail["available_amount"], len(detail["history"])), ("10.00", "15.00", 1))
        self.assertEqual(self.as_("discounts", "patch", f"/vouchers/{v['id']}", {"amount": "5"}).status_code, 422)
        r = self.as_("discounts", "delete", f"/vouchers/{v['id']}")
        self.assertEqual((r.status_code, r.json()["code"]), (409, "has_usage"))
        self.assertEqual(self.as_("discounts", "delete", f"/vouchers/{v['id']}/consumptions/{c.json()['consumption_id']}").status_code, 204)
        self.assertEqual(self.as_("discounts", "get", f"/vouchers/{v['id']}").json()["consumed_amount"], "0.00")
        d = self.as_("discounts", "post", f"/vouchers/{v['id']}/deactivate").json()
        self.assertEqual((d["is_active"], d["status"]), (False, "inactive"))
        self.assertEqual(self.as_("discounts", "get", "/vouchers?status=inactive").json()["count"], 1)
        self.assertEqual(self.as_("discounts", "post", f"/vouchers/{v['id']}/activate").json()["status"], "usable")
        self.assertEqual(self.as_("discounts", "delete", f"/vouchers/{v['id']}").status_code, 204)
        self.assertEqual(Voucher.objects.count(), 2)
        Voucher.objects.update(expiry_date=datetime.date.today() - datetime.timedelta(days=1))
        self.assertEqual(self.as_("discounts", "get", "/vouchers?status=expired").json()["count"], 2)
        self.assertEqual(Decimal(self.as_("discounts", "get", "/vouchers").json()["results"][0]["amount"]), Decimal("25"))
