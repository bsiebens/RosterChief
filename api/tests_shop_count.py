"""``POST /shop/inventory/count``: gating, differences applied, unchanged skipped, notes, atomic rollback, ledger."""

import json
import uuid

from django.db import connection
from django.test import Client, TestCase

from controlpanel.tests import login_session
from features.entitlements import Entitlements
from shop.models import InventoryMovement, ProductVariant
from shop.services import inventory
from shop.tests import ShopClubMixin

URL = "/api/v1/shop/inventory/count"


class StockCountTests(ShopClubMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.product = self.merch(inventory_managed=True)
        self.small = ProductVariant.objects.create(product=self.product, name="S")
        self.mug = self.merch("Mug", inventory_managed=True)
        self.free = self.merch("Poster")
        actor = self.users["inventory"]
        inventory.receive_stock(actor, [(self.product, self.small, 5), (self.mug, None, 3)])

    def post(self, key, data):
        user = self.owner if key == "owner" else self.users[key]
        response = Client().post(URL, data=json.dumps(data), content_type="application/json", HTTP_HOST=self.host, HTTP_X_SESSION_TOKEN=login_session(user))
        connection.set_tenant(self.club)
        return response

    def line(self, product, counted, variant=None, **kw):
        return {"product_id": str(product.pk), "variant_id": str(variant.pk) if variant else None, "counted": counted, **kw}

    def test_gating(self):
        body = {"counts": [self.line(self.mug, 3)]}
        for key in ["plain", "invoices", "products", "orders", "discounts"]:
            self.assertEqual(self.post(key, body).status_code, 403, key)
        for key in ["inventory", "owner"]:
            self.assertEqual(self.post(key, body).status_code, 200, key)
        self.features = Entitlements({})
        self.assertEqual(self.post("owner", body).status_code, 404)

    def test_differences_applied_and_unchanged_skipped(self):
        r = self.post("inventory", {"counts": [self.line(self.product, 2, self.small), self.line(self.mug, 3)]})
        self.assertEqual(r.status_code, 200, r.content)
        data = r.json()
        self.assertEqual((data["applied"], data["unchanged"], data["total_difference"]), (1, 1, -3))
        by_name = {x["name"]: x for x in data["lines"]}
        self.assertEqual((by_name["Jersey (S)"]["previous"], by_name["Jersey (S)"]["counted"], by_name["Jersey (S)"]["difference"]), (5, 2, -3))
        self.assertEqual(inventory.stock_for(self.product, self.small), 2)
        self.assertEqual(inventory.stock_for(self.mug), 3)

    def test_notes_and_ledger(self):
        self.post("inventory", {"note": "Autumn", "counts": [self.line(self.mug, 5, note="found a box"), self.line(self.product, 5, self.small)]})
        moves = InventoryMovement.objects.filter(reason="adjustment")
        self.assertEqual(moves.count(), 1)
        m = moves.get()
        self.assertEqual((m.product, m.quantity), (self.mug, 2))
        self.assertEqual(m.note, "Stock count: 3 -> 5. found a box. Autumn")
        self.assertIsNotNone(m.created_by)

    def test_invalid_lines_are_422_and_atomic(self):
        good = self.line(self.mug, 9)
        cases = [
            [good, self.line(self.free, 1)],
            [good, {"product_id": str(uuid.uuid4()), "counted": 1}],
            [good, self.line(self.product, 1, ProductVariant(pk=uuid.uuid4()))],
            [good, self.line(self.product, 1)],  # product with variants counted without a variant
            [good, self.line(self.mug, -1)],
            [good, self.line(self.mug, 4)],  # duplicate
            [],
        ]
        for counts in cases:
            self.assertEqual(self.post("inventory", {"counts": counts}).status_code, 422, counts)
        self.assertEqual(inventory.stock_for(self.mug), 3)
        self.assertFalse(InventoryMovement.objects.filter(reason="adjustment").exists())


class CountSheetTests(ShopClubMixin, TestCase):
    """The PDF count sheet (GET /shop/inventory/count-sheet.pdf): a jersey with variant S and a mug in stock, a poster that is not counted."""

    def setUp(self):
        super().setUp()
        self.product = self.merch(inventory_managed=True)
        self.small = ProductVariant.objects.create(product=self.product, name="S")
        self.mug = self.merch("Mug", inventory_managed=True)
        self.merch("Poster")
        inventory.receive_stock(self.users["inventory"], [(self.product, self.small, 5), (self.mug, None, 3)])

    def get(self, key, query=""):
        user = self.owner if key == "owner" else self.users[key]
        response = Client().get(f"/api/v1/shop/inventory/count-sheet.pdf{query}", HTTP_HOST=self.host, HTTP_X_SESSION_TOKEN=login_session(user))
        connection.set_tenant(self.club)
        return response

    def test_rows_filters_and_groups(self):
        from shop.models import ProductCategory
        from shop.services import count_sheet

        shirts = ProductCategory.objects.create(name="Shirts")
        self.product.category = shirts
        self.product.save()
        names = lambda rows: sorted(r.product.name for r in rows)  # noqa: E731
        self.assertEqual(names(count_sheet.sheet_rows()), ["Jersey", "Mug"])  # inventory-managed lines only (no poster)
        self.assertEqual(names(count_sheet.sheet_rows(q="mu")), ["Mug"])
        self.assertEqual(names(count_sheet.sheet_rows(q=" S ")), ["Jersey"])  # also matches the variant name
        self.assertEqual(names(count_sheet.sheet_rows(category=str(shirts.pk))), ["Jersey"])
        self.assertEqual(names(count_sheet.sheet_rows(category="none")), ["Mug"])
        groups = count_sheet.groups(count_sheet.sheet_rows())
        self.assertEqual([(g["name"], names(g["rows"])) for g in groups], [("Shirts", ["Jersey"]), (None, ["Mug"])])  # no category last

    def test_gating(self):
        for key in ["plain", "invoices", "products", "orders", "discounts"]:
            self.assertEqual(self.get(key).status_code, 403, key)
        self.features = Entitlements({})
        self.assertEqual(self.get("owner").status_code, 404)

    def test_pdf_is_served_inline(self):
        try:
            import weasyprint  # noqa: F401
        except (ImportError, OSError):
            r = self.get("inventory")
            self.assertEqual((r.status_code, r.json()["code"]), (503, "pdf_unavailable"))
            return
        for key in ["inventory", "owner"]:
            r = self.get(key, "?q=mug")
            self.assertEqual((r.status_code, r["Content-Type"], r.content[:4]), (200, "application/pdf", b"%PDF"), key)
        self.assertIn("inline", r["Content-Disposition"])
