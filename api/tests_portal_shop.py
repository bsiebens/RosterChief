import datetime
import json
import uuid

from django.db import connection
from django.test import Client, TestCase
from django.utils import timezone

from controlpanel.tests import login_session
from shop.models import Order, Product
from shop.settings_models import ShopSettings
from shop.tests import ShopClubMixin


class PortalShopTests(ShopClubMixin, TestCase):
    def call(self, method, path="", *, payload=None, buyer="plain"):
        body = {"data": json.dumps(payload), "content_type": "application/json"} if payload is not None else {}
        response = getattr(Client(), method)(
            "/api/v1/portal/shop" + path, HTTP_HOST=self.host, HTTP_X_SESSION_TOKEN=login_session(self.users[buyer]), **body
        )
        connection.set_tenant(self.club)
        return response

    def test_catalog_checkout_and_receipts_are_buyer_scoped(self):
        today = timezone.localdate()
        delivery = str(today + datetime.timedelta(days=30))
        open_product = self.merch(ordering_windows=[{"deadline": str(today), "estimated_delivery": delivery}])
        self.merch("Expired", ordering_windows=[{"deadline": str(today - datetime.timedelta(days=1)), "estimated_delivery": delivery}])
        private = self.merch("Private", is_public=False)
        self.merch("Membership", product_type=Product.ProductType.MEMBERSHIP)
        catalogue = self.call("get")
        self.assertEqual(catalogue.status_code, 200, catalogue.content)
        products = {product["name"]: product for product in catalogue.json()["products"]}
        self.assertEqual(set(products), {"Expired", "Jersey"})
        self.assertFalse(products["Expired"]["in_stock"])
        self.assertEqual(products["Jersey"]["next_ordering_window"], {"deadline": str(today), "estimated_delivery": delivery})

        line = {"product_id": str(open_product.pk), "quantity": 2, "unit_price": "40.00", "ordering_deadline": str(today), "estimated_delivery": delivery}
        payload = {"checkout_id": str(uuid.uuid4()), "lines": [line]}
        private_attempt = self.call("post", "/orders", payload={**payload, "lines": [{**line, "product_id": str(private.pk)}]})
        self.assertEqual(private_attempt.status_code, 422)
        stranger_attempt = self.call("post", "/orders", payload={**payload, "lines": [{**line, "beneficiary_id": str(self.buyer.pk)}]})
        self.assertEqual(stranger_attempt.status_code, 422)
        stale_attempt = self.call("post", "/orders", payload={**payload, "lines": [{**line, "unit_price": "39.00"}]})
        self.assertEqual((stale_attempt.status_code, stale_attempt.json()["code"]), (409, "cart_changed"))
        placed = self.call("post", "/orders", payload=payload)
        self.assertEqual(placed.status_code, 201, placed.content)
        self.assertEqual(placed.json()["total"], "80.00")
        self.assertEqual(self.call("post", "/orders", payload=payload).json()["id"], placed.json()["id"])
        self.assertEqual(Order.objects.count(), 1)

        settings = ShopSettings.current()
        settings.shop_open = False
        settings.save(update_fields=["shop_open"])
        self.assertEqual(self.call("get").json()["products"], [])
        self.assertEqual(self.call("get", "/orders").json()["results"][0]["id"], placed.json()["id"])
        self.assertEqual(self.call("get", "/orders", buyer="orders").json()["results"], [])
        self.assertEqual(self.call("get", f"/orders/{placed.json()['id']}/invoice.pdf", buyer="orders").status_code, 404)
        refused = self.call("post", "/orders", payload={**payload, "checkout_id": str(uuid.uuid4())})
        self.assertEqual((refused.status_code, refused.json()["code"]), (409, "shop_closed"))
