import io
import json
import tempfile
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from PIL import Image
from waffle import get_waffle_flag_model

from api.protected.tests import CLUB_HOST, AppApiTestCase
from club.models import Club, ClubRole, ShopManager
from shop.models import InventoryMovement, Product, ProductCategory, ProductVariant
from shop.services.inventory import stock_for

BASE = "/manage/shop"


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(buffer, "PNG")
    return buffer.getvalue()


PNG = _png()
MEDIA = tempfile.mkdtemp()


@override_settings(MEDIA_ROOT=MEDIA)
class ShopCatalogTestCase(AppApiTestCase):
    def setUp(self):
        super().setUp()
        get_waffle_flag_model().objects.get_or_create(name="shop")[0].clubs.add(self.club)
        self._token = None

    def as_role(self, role=ClubRole.Roles.ADMIN):
        ClubRole.objects.update_or_create(club=self.club, member=self.member, defaults={"role": role})
        if self._token is None:
            self._token = self.login_with_totp()
        return self._token

    def admin(self):
        return self.as_role()

    def call(self, method, path, token=None, **kwargs):
        return self.api(method, path, token=token, host=CLUB_HOST, **kwargs)

    def multipart(self, path, token, data, **headers):
        return self.client.post(f"/api/v1{path}", data, HTTP_HOST=CLUB_HOST, HTTP_AUTHORIZATION=f"Bearer {token}", **headers)

    def product(self, name="Jersey", club=None, **kwargs):
        return Product.objects.create(club=club or self.club, name=name, price=Decimal("30.00"), **kwargs)

    def stranger_product(self):
        other = Club.objects.create(name="Rival FC", slug="rival-fc")
        product = self.product("Theirs", club=other, inventory_managed=True)
        variant = ProductVariant.objects.create(product=product, name="L")
        category = ProductCategory.objects.create(club=other, name="Theirs")
        return other, product, variant, category


class AccessTests(ShopCatalogTestCase):
    def test_no_token_is_401(self):
        for method, path in (("get", BASE), ("post", BASE + "/toggle"), ("get", BASE + "/products"), ("get", BASE + "/inventory"), ("get", BASE + "/categories")):
            self.assertError(self.call(method, path), 401, "unauthenticated")

    def test_plain_member_and_editor_are_403(self):
        product = self.product()
        for token in (self.login(), self.as_role(ClubRole.Roles.EDITOR)):
            for method, path in (("get", BASE), ("post", BASE + "/toggle"), ("get", BASE + "/products"), ("post", f"{BASE}/products/{product.pk}/toggle-active"), ("get", BASE + "/inventory"), ("get", BASE + "/inventory/history")):
                self.assertError(self.call(method, path, token), 403, "forbidden")

    def test_shop_manager_grant_is_enough(self):
        self.as_role(ClubRole.Roles.MEMBER)
        ShopManager.objects.create(club=self.club, member=self.member)
        self.assertEqual(self.call("get", BASE + "/products", self._token).status_code, 200)

    def test_feature_off_is_404(self):
        token = self.admin()
        product = self.product()
        get_waffle_flag_model().objects.get(name="shop").clubs.clear()
        for method, path in (("get", BASE), ("post", BASE + "/toggle"), ("get", BASE + "/products"), ("get", f"{BASE}/products/{product.pk}"), ("get", BASE + "/categories"), ("get", BASE + "/inventory"), ("get", BASE + "/inventory/history")):
            self.assertError(self.call(method, path, token), 404, "feature_disabled")

    def test_other_clubs_objects_are_404(self):
        _other, product, variant, category = self.stranger_product()
        token = self.admin()
        for method, path, data in (
            ("get", f"{BASE}/products/{product.pk}", None),
            ("post", f"{BASE}/products/{product.pk}/toggle-active", None),
            ("post", f"{BASE}/products/{product.pk}/variants", {"name": "X"}),
            ("patch", f"{BASE}/products/{product.pk}/variants/{variant.pk}", {"name": "X"}),
            ("post", f"{BASE}/products/{product.pk}/variants/{variant.pk}/toggle-active", None),
            ("patch", f"{BASE}/categories/{category.pk}", {"name": "X"}),
            ("delete", f"{BASE}/categories/{category.pk}", None),
            ("post", f"{BASE}/inventory/{product.pk}/adjust", {"counted": 3}),
            ("post", f"{BASE}/inventory/{product.pk}/{variant.pk}/adjust", {"counted": 3}),
        ):
            self.assertError(self.call(method, path, token, data=data), 404, "not_found")
        self.assertEqual(self.call("get", BASE + "/products", token).json()["items"], [])
        self.assertEqual(self.call("get", BASE + "/inventory", token).json()["items"], [])
        self.assertTrue(ProductCategory.objects.filter(pk=category.pk).exists())


class ShopToggleTests(ShopCatalogTestCase):
    def test_toggle_flips_and_replays(self):
        token = self.admin()
        self.assertFalse(self.call("get", BASE, token).json()["shop_open"])
        self.assertTrue(self.call("post", BASE + "/toggle", token).json()["shop_open"])
        self.club.refresh_from_db()
        self.assertTrue(self.club.shop_open)
        self.assertFalse(self.call("post", BASE + "/toggle", token).json()["shop_open"])
        first = self.call("post", BASE + "/toggle", token, HTTP_IDEMPOTENCY_KEY="t-1")
        replay = self.call("post", BASE + "/toggle", token, HTTP_IDEMPOTENCY_KEY="t-1")
        self.assertEqual(first.json(), replay.json())
        self.club.refresh_from_db()
        self.assertTrue(self.club.shop_open)


class CategoryTests(ShopCatalogTestCase):
    def test_crud(self):
        token = self.admin()
        created = self.call("post", BASE + "/categories", token, data={"name": "Gear"})
        self.assertEqual(created.status_code, 201, created.content)
        category_id = created.json()["id"]
        self.assertEqual(self.call("patch", f"{BASE}/categories/{category_id}", token, data={"name": "Kit"}).json()["name"], "Kit")
        self.product(category=ProductCategory.objects.get(pk=category_id))
        items = {item["name"]: item for item in self.call("get", BASE + "/categories", token).json()["items"]}
        self.assertEqual(items["Kit"]["product_count"], 1)
        self.assertEqual(self.call("delete", f"{BASE}/categories/{category_id}", token).status_code, 204)
        self.assertIsNone(Product.objects.get().category)

    def test_duplicate_name_is_422(self):
        token = self.admin()
        ProductCategory.objects.create(club=self.club, name="Gear")
        other = ProductCategory.objects.create(club=self.club, name="Kit")
        for response in (self.call("post", BASE + "/categories", token, data={"name": "Gear"}), self.call("patch", f"{BASE}/categories/{other.pk}", token, data={"name": "Gear"})):
            self.assertEqual(response.status_code, 422)
            self.assertIn("name", response.json()["error"]["fields"])
        self.assertEqual(self.call("post", BASE + "/categories", token, data={"name": "  "}).status_code, 422)

    def test_system_category_cannot_be_deleted(self):
        token = self.admin()
        system = ProductCategory.objects.filter(club=self.club).exclude(registration_kind="").first()
        self.assertIsNotNone(system)
        self.assertError(self.call("delete", f"{BASE}/categories/{system.pk}", token), 409, "category_protected")
        self.assertTrue(ProductCategory.objects.filter(pk=system.pk).exists())

    def test_create_is_idempotent(self):
        token = self.admin()
        for _ in range(2):
            self.assertEqual(self.call("post", BASE + "/categories", token, data={"name": "Gear"}, HTTP_IDEMPOTENCY_KEY="c-1").status_code, 201)
        self.assertEqual(ProductCategory.objects.filter(club=self.club, name="Gear").count(), 1)


class ProductTests(ShopCatalogTestCase):
    def create(self, token, **extra):
        data = {"name": "Hoodie", "price": "45.00", **extra}
        return self.multipart(BASE + "/products", token, data)

    def test_create_with_variants_tiers_and_image(self):
        token = self.admin()
        category = ProductCategory.objects.create(club=self.club, name="Gear")
        response = self.create(
            token,
            category_id=str(category.pk),
            description="Warm",
            inventory_managed="true",
            variants=json.dumps([{"name": "S"}, {"name": "M", "price": "50.00"}, {"name": "S"}][:2]),
            tiers=json.dumps([{"min_registrants": 3, "discount_type": "percentage", "discount_amount": "10"}]),
            image=SimpleUploadedFile("h.png", PNG, content_type="image/png"),
        )
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual(body["category_name"], "Gear")
        self.assertTrue(body["inventory_managed"])
        self.assertTrue(body["image_url"].startswith("http"))
        self.assertEqual({v["name"]: v["price"] for v in body["variants"]}, {"S": None, "M": "50.00"})
        self.assertEqual(body["tiers"][0]["min_registrants"], 3)
        self.assertEqual(body["stock_count"], 0)

    def test_membership_product_gets_default_variant(self):
        body = self.create(self.admin(), product_type="membership").json()
        self.assertEqual([v["name"] for v in body["variants"]], ["Hoodie"])

    def test_validation_errors_are_422(self):
        token = self.admin()
        response = self.create(token, product_type="membership", inventory_managed="true")
        self.assertEqual(response.status_code, 422)
        self.assertIn("inventory_managed", response.json()["error"]["fields"])
        response = self.create(token, variants=json.dumps([{"name": "S"}, {"name": "s"}]), tiers=json.dumps([{"min_registrants": 1, "discount_type": "percentage", "discount_amount": "5"}]))
        fields = response.json()["error"]["fields"]
        self.assertEqual(response.status_code, 422)
        self.assertIn("variants.1.name", fields)
        self.assertIn("tiers.0.min_registrants", fields)
        self.assertEqual(self.create(token, variants="not json").status_code, 422)
        self.assertEqual(self.create(token, category_id="00000000-0000-0000-0000-000000000000").status_code, 422)
        self.assertFalse(Product.objects.exists())

    def test_category_of_another_club_is_422(self):
        *_rest, category = self.stranger_product()
        response = self.create(self.admin(), category_id=str(category.pk))
        self.assertEqual(response.status_code, 422)
        self.assertIn("category_id", response.json()["error"]["fields"])

    def test_image_rules(self):
        token = self.admin()
        bad_type = self.create(token, image=SimpleUploadedFile("h.txt", b"hello", content_type="text/plain"))
        self.assertError(bad_type, 415, "unsupported_media_type")
        fake = self.create(token, image=SimpleUploadedFile("h.png", b"not an image", content_type="image/png"))
        self.assertEqual(fake.status_code, 422)
        self.assertIn("image", fake.json()["error"]["fields"])
        with override_settings(API_UPLOAD_MAX_BYTES=10):
            self.assertError(self.create(token, image=SimpleUploadedFile("h.png", PNG, content_type="image/png")), 413, "file_too_large")

    def test_update_replaces_fields_keeps_and_clears_image(self):
        token = self.admin()
        created = self.create(token, image=SimpleUploadedFile("h.png", PNG, content_type="image/png"), tiers=json.dumps([{"min_registrants": 2, "discount_type": "fixed_amount", "discount_amount": "5"}])).json()
        path = f"{BASE}/products/{created['id']}"
        kept = self.multipart(path, token, {"name": "Hoodie 2", "price": "50"})
        self.assertEqual(kept.status_code, 200, kept.content)
        self.assertEqual(kept.json()["name"], "Hoodie 2")
        self.assertIsNotNone(kept.json()["image_url"])
        self.assertEqual(len(kept.json()["tiers"]), 1)
        replaced = self.multipart(path, token, {"name": "Hoodie 2", "price": "50", "tiers": "[]"})
        self.assertEqual(replaced.json()["tiers"], [])
        cleared = self.multipart(path, token, {"name": "Hoodie 2", "price": "50", "clear_image": "true"})
        self.assertIsNone(cleared.json()["image_url"])
        self.assertEqual(self.multipart(path, token, {"price": "5"}).status_code, 422)

    def test_list_filters_and_pages(self):
        token = self.admin()
        category = ProductCategory.objects.create(club=self.club, name="Gear")
        for name in ("A", "B", "C"):
            self.product(name, category=category if name != "C" else None, is_active=name != "B")
        self.assertEqual([p["name"] for p in self.call("get", BASE + "/products", token).json()["items"]], ["A", "B", "C"])
        self.assertEqual([p["name"] for p in self.call("get", BASE + f"/products?category_id={category.pk}", token).json()["items"]], ["A", "B"])
        self.assertEqual([p["name"] for p in self.call("get", BASE + "/products?is_active=false", token).json()["items"]], ["B"])
        self.assertEqual([p["name"] for p in self.call("get", BASE + "/products?q=c", token).json()["items"]], ["C"])
        page = self.call("get", BASE + "/products?limit=2", token).json()
        self.assertEqual(len(page["items"]), 2)
        rest = self.call("get", BASE + f"/products?limit=2&cursor={page['next_cursor']}", token).json()
        self.assertEqual([p["name"] for p in rest["items"]], ["C"])
        self.assertIsNone(rest["next_cursor"])

    def test_toggle_active_and_replay(self):
        token = self.admin()
        product = self.product()
        path = f"{BASE}/products/{product.pk}/toggle-active"
        self.assertFalse(self.call("post", path, token).json()["is_active"])
        self.assertTrue(self.call("post", path, token).json()["is_active"])
        first = self.call("post", path, token, HTTP_IDEMPOTENCY_KEY="p-1")
        replay = self.call("post", path, token, HTTP_IDEMPOTENCY_KEY="p-1")
        self.assertEqual(first.json(), replay.json())
        product.refresh_from_db()
        self.assertFalse(product.is_active)

    def test_detail_has_sold_count_per_variant(self):
        token = self.admin()
        product = self.product()
        ProductVariant.objects.create(product=product, name="S")
        body = self.call("get", f"{BASE}/products/{product.pk}", token).json()
        self.assertEqual(body["variants"][0]["sold_count"], 0)
        self.assertEqual(body["sold_count"], 0)


class VariantTests(ShopCatalogTestCase):
    def test_create_edit_toggle(self):
        token = self.admin()
        product = self.product()
        path = f"{BASE}/products/{product.pk}/variants"
        created = self.call("post", path, token, data={"name": "S", "price": "31.00"})
        self.assertEqual(created.status_code, 201, created.content)
        variant_id = created.json()["id"]
        edited = self.call("patch", f"{path}/{variant_id}", token, data={"name": "Small", "price": None})
        self.assertEqual((edited.json()["name"], edited.json()["price"]), ("Small", None))
        self.assertFalse(self.call("post", f"{path}/{variant_id}/toggle-active", token).json()["is_active"])
        self.assertFalse(ProductVariant.objects.get(pk=variant_id).is_active)

    def test_duplicate_name_is_422(self):
        token = self.admin()
        product = self.product()
        ProductVariant.objects.create(product=product, name="S")
        other = ProductVariant.objects.create(product=product, name="M")
        path = f"{BASE}/products/{product.pk}/variants"
        for response in (self.call("post", path, token, data={"name": "S"}), self.call("patch", f"{path}/{other.pk}", token, data={"name": "S"})):
            self.assertEqual(response.status_code, 422)
            self.assertIn("name", response.json()["error"]["fields"])
        self.assertEqual(self.call("patch", f"{path}/{other.pk}", token, data={"name": "M", "is_active": False}).status_code, 200)

    def test_variant_of_another_product_is_404(self):
        token = self.admin()
        mine, theirs = self.product("A"), self.product("B")
        variant = ProductVariant.objects.create(product=theirs, name="S")
        self.assertError(self.call("patch", f"{BASE}/products/{mine.pk}/variants/{variant.pk}", token, data={"name": "X"}), 404, "not_found")

    def test_create_is_idempotent(self):
        token = self.admin()
        product = self.product()
        for _ in range(2):
            self.assertEqual(self.call("post", f"{BASE}/products/{product.pk}/variants", token, data={"name": "S"}, HTTP_IDEMPOTENCY_KEY="v-1").status_code, 201)
        self.assertEqual(product.variants.count(), 1)


class InventoryTests(ShopCatalogTestCase):
    def setUp(self):
        super().setUp()
        self.shirt = self.product("Shirt", inventory_managed=True)
        self.small = ProductVariant.objects.create(product=self.shirt, name="S")
        self.large = ProductVariant.objects.create(product=self.shirt, name="L")
        self.ball = self.product("Ball", inventory_managed=True)
        self.product("Made to order")

    def rows(self, token):
        return self.call("get", BASE + "/inventory", token).json()

    def test_overview_lists_managed_rows_only(self):
        body = self.rows(self.admin())
        self.assertEqual([(r["product_name"], r["variant_name"]) for r in body["items"]], [("Ball", None), ("Shirt", "L"), ("Shirt", "S")])
        self.assertEqual(body["out_of_stock_count"], 3)

    def test_receive_adds_stock_and_history(self):
        token = self.admin()
        response = self.call("post", BASE + "/inventory/receive", token, data={"items": [{"product_id": str(self.shirt.pk), "variant_id": str(self.small.pk), "quantity": 5}, {"product_id": str(self.ball.pk), "quantity": 2}], "note": "Delivery"})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json(), {"rows": 2, "units": 7})
        self.assertEqual(stock_for(self.shirt, self.small), 5)
        self.assertEqual(self.rows(token)["out_of_stock_count"], 1)
        history = self.call("get", BASE + f"/inventory/history?product_id={self.ball.pk}", token).json()
        self.assertEqual([(m["product_name"], m["quantity"], m["reason"], m["note"]) for m in history["items"]], [("Ball", 2, "received", "Delivery")])
        self.assertEqual(len(self.call("get", BASE + "/inventory/history?limit=1", token).json()["items"]), 1)

    def test_receive_validation_books_nothing(self):
        token = self.admin()
        good = {"product_id": str(self.ball.pk), "quantity": 2}
        for items in (
            [good, {"product_id": str(self.shirt.pk), "quantity": 1}],  # product with variants needs a variant
            [good, {"product_id": str(self.ball.pk), "quantity": 1}],  # twice
            [good, {"product_id": "00000000-0000-0000-0000-000000000000", "quantity": 1}],
            [{"product_id": str(self.ball.pk), "variant_id": str(self.small.pk), "quantity": 1}],
        ):
            response = self.call("post", BASE + "/inventory/receive", token, data={"items": items})
            self.assertEqual(response.status_code, 422, response.content)
        self.assertEqual(self.call("post", BASE + "/inventory/receive", token, data={"items": [{"product_id": str(self.ball.pk), "quantity": 0}]}).status_code, 422)
        self.assertEqual(self.call("post", BASE + "/inventory/receive", token, data={"items": []}).status_code, 422)
        self.assertFalse(InventoryMovement.objects.exists())

    def test_receive_is_idempotent(self):
        token = self.admin()
        data = {"items": [{"product_id": str(self.ball.pk), "quantity": 4}]}
        for _ in range(2):
            self.assertEqual(self.call("post", BASE + "/inventory/receive", token, data=data, HTTP_IDEMPOTENCY_KEY="r-1").status_code, 200)
        self.assertEqual(stock_for(self.ball), 4)

    def test_adjust_product_and_variant(self):
        token = self.admin()
        InventoryMovement.objects.create(product=self.ball, quantity=5, reason=InventoryMovement.Reason.RECEIVED)
        response = self.call("post", f"{BASE}/inventory/{self.ball.pk}/adjust", token, data={"counted": 3, "note": "Recount"})
        self.assertEqual(response.json(), {"changed": True, "on_hand": 3})
        self.assertEqual(self.call("post", f"{BASE}/inventory/{self.ball.pk}/adjust", token, data={"counted": 3}).json(), {"changed": False, "on_hand": 3})
        variant = self.call("post", f"{BASE}/inventory/{self.shirt.pk}/{self.large.pk}/adjust", token, data={"counted": 9})
        self.assertEqual(variant.json(), {"changed": True, "on_hand": 9})
        self.assertEqual(InventoryMovement.objects.filter(reason="adjustment").count(), 2)

    def test_adjust_errors(self):
        token = self.admin()
        self.assertEqual(self.call("post", f"{BASE}/inventory/{self.ball.pk}/adjust", token, data={"counted": -1}).status_code, 422)
        self.assertEqual(self.call("post", f"{BASE}/inventory/{self.ball.pk}/adjust", token, data={"counted": "x"}).status_code, 422)
        plain = Product.objects.get(name="Made to order")
        self.assertError(self.call("post", f"{BASE}/inventory/{plain.pk}/adjust", token, data={"counted": 1}), 404, "not_found")
        stray = ProductVariant.objects.create(product=plain, name="Z")
        self.assertError(self.call("post", f"{BASE}/inventory/{self.shirt.pk}/{stray.pk}/adjust", token, data={"counted": 1}), 404, "not_found")
