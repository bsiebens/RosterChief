"""Shop foundation: pricing, discount validation, stock ledger and vouchers (services). The API tests are in api/tests_shop.py."""

import datetime
from decimal import Decimal
from unittest import mock

from django.db import connection
from django.test import TestCase
from django_tenants.utils import schema_context

from authorization.services import assign_role, create_role
from club.models import Club, Domain
from club.services import create_club, grant_login
from controlpanel.tests import make_user
from features.entitlements import Entitlement, Entitlements
from members.models import Member
from members.services.errors import Invalid, NotAllowed
from shop.models import Discount, DiscountType, InventoryMovement, Order, OrderLine, Product, ProductionStatus, ProductVariant, Voucher
from shop.services import catalog, inventory, pricing, vouchers

WITH_SHOP = Entitlements({"shop": Entitlement(True)})
PERMS = {
    "products": "shop.manage_products",
    "inventory": "shop.manage_inventory",
    "orders": "shop.manage_orders",
    "discounts": "shop.manage_discounts",
    "invoices": "shop.manage_invoices",
    "superset": "shop.manage_shop",
}


class ShopClubMixin:
    """A club with one login per shop permission (``cls.users['products']`` ...), a plain member login and the owner."""

    @classmethod
    def setUpClass(cls):
        cls._mfa = mock.patch("authorization.middleware.is_local_development", return_value=True)
        cls._mfa.start()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        connection.set_schema_to_public()
        cls._mfa.stop()

    @classmethod
    def setUpTestData(cls):
        cls.owner = make_user("owner@shop.test", first="Olivia", last="Owner")
        public = Club(schema_name="public", name="Platform", owner=cls.owner)
        public.auto_create_schema = False
        public.save()
        Domain.objects.create(domain="testserver", tenant=public, is_primary=True)
        cls.club = create_club("Shop FC", cls.owner)
        cls.host = Domain.objects.get(tenant=cls.club, is_primary=True).domain
        cls.users = {}
        with schema_context(cls.club.schema_name):
            cls.buyer = Member.objects.create(first_name="Bea", last_name="Buyer", email="bea@shop.test")
            members = {key: Member.objects.create(first_name=key.title(), last_name="Staff", email=f"{key}@shop.test") for key in [*PERMS, "plain"]}
            roles = {key: create_role(f"Shop {key}", [code]) for key, code in PERMS.items()}
        for key, member in members.items():
            user, _ = grant_login(cls.club, member)
            cls.users[key] = user
            if key in roles:
                with schema_context(cls.club.schema_name):
                    assign_role(user, roles[key])
        cls.owner = type(cls.owner).objects.get(pk=cls.owner.pk)

    def setUp(self):
        self.features = WITH_SHOP
        patcher = mock.patch("features.middleware.entitlements_for_request", side_effect=lambda request: self.features)
        patcher.start()
        self.addCleanup(patcher.stop)
        ctx_features = mock.patch("features.context.get_current_entitlements", side_effect=lambda: self.features)
        ctx_features.start()
        self.addCleanup(ctx_features.stop)
        # the permission backend imported the function by name: patch it where it is used too
        backend_features = mock.patch("features.backends.get_current_entitlements", side_effect=lambda: self.features)
        backend_features.start()
        self.addCleanup(backend_features.stop)
        door = mock.patch("members.services.access.can_access_management", return_value=True)
        door.start()
        self.addCleanup(door.stop)
        ctx = schema_context(self.club.schema_name)
        ctx.__enter__()
        self.addCleanup(ctx.__exit__, None, None, None)

    def merch(self, name="Jersey", price="40.00", **kw):
        return Product.objects.create(name=name, price=Decimal(price), **kw)

    def make_order(self, product, variant=None, quantity=1, status=Order.FulfillmentStatus.NOT_READY):
        order = Order.objects.create(purchaser=self.buyer, total=product.price * quantity, fulfillment_status=status)
        line = OrderLine.objects.create(order=order, product=product, variant=variant, quantity=quantity, unit_price=product.price, line_total=product.price * quantity)
        return order, line


class PricingTests(ShopClubMixin, TestCase):
    def test_percentage_rounds_half_up_and_caps(self):
        d = Discount(discount_type=DiscountType.PERCENTAGE, discount_amount=Decimal("33.33"))
        self.assertEqual(pricing.discount_amount_for(Decimal("80.00"), d), Decimal("26.66"))
        self.assertEqual(pricing.discount_amount_for(Decimal("0.05"), Discount(discount_type=DiscountType.PERCENTAGE, discount_amount=Decimal("50"))), Decimal("0.03"))

    def test_fixed_never_exceeds_subtotal(self):
        d = Discount(discount_type=DiscountType.FIXED_AMOUNT, discount_amount=Decimal("100"))
        self.assertEqual(pricing.discount_amount_for(Decimal("30.00"), d), Decimal("30.00"))
        self.assertEqual(pricing.discount_amount_for(Decimal("0"), d), Decimal("0"))

    def test_validity_window_status_and_validation(self):
        today = datetime.date.today()
        day = datetime.timedelta(days=1)
        mk = lambda code, **kw: Discount.objects.create(name=code, code=code, discount_amount=Decimal("10"), **kw)  # noqa: E731
        active = mk("A", valid_from=today, valid_until=today)
        future = mk("B", valid_from=today + day)
        past = mk("C", valid_until=today - day)
        off = mk("D", is_active=False)
        self.assertEqual([pricing.discount_status(x) for x in (active, future, past, off)], ["active", "scheduled", "expired", "inactive"])
        self.assertIsNone(pricing.validate_discount(active))
        for bad in (future, past, off):
            self.assertIsNotNone(pricing.validate_discount(bad))

    def test_max_uses_and_once_per_member_ignore_cancelled(self):
        d = Discount.objects.create(name="X", code="x1", discount_amount=Decimal("5"), max_uses=1)
        self.assertEqual(d.code, "X1")
        order, _line = self.make_order(self.merch())
        order.applied_discounts.create(discount=d, discount_type=d.discount_type, discount_amount=d.discount_amount)
        self.assertEqual(pricing.discount_uses(d), 1)
        self.assertIn("maximum", pricing.validate_discount(d))
        d.max_uses = None
        self.assertIn("already", pricing.validate_discount(d, self.buyer))
        order.fulfillment_status = Order.FulfillmentStatus.CANCELLED
        order.save()
        self.assertEqual(pricing.discount_uses(d), 0)
        self.assertIsNone(pricing.validate_discount(d, self.buyer))

    def test_order_total_refreshes_applied_amounts(self):
        order, _line = self.make_order(self.merch(), quantity=2)
        d = Discount.objects.create(name="P", code="P10", discount_amount=Decimal("10"))
        applied = order.applied_discounts.create(discount=d, discount_type=d.discount_type, discount_amount=d.discount_amount)
        self.assertEqual(pricing.order_total(order), Decimal("72.00"))
        applied.refresh_from_db()
        self.assertEqual(applied.amount, Decimal("8.00"))


class InventoryServiceTests(ShopClubMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.user = self.users["inventory"]
        self.product = self.merch(inventory_managed=True)
        self.small = ProductVariant.objects.create(product=self.product, name="S")
        self.large = ProductVariant.objects.create(product=self.product, name="L")

    def test_receive_adjust_and_ledger(self):
        inventory.receive_stock(self.user, [(self.product, self.small, 5), (self.product, self.large, 2)], note="delivery")
        self.assertEqual(inventory.stock_for(self.product, self.small), 5)
        m = inventory.set_stock(self.user, self.product, self.small, 3)
        self.assertEqual((m.quantity, m.reason), (-2, "adjustment"))
        self.assertIsNone(inventory.set_stock(self.user, self.product, self.small, 3))
        self.assertEqual(InventoryMovement.objects.filter(product=self.product).count(), 3)
        self.assertEqual({(r.variant.name, r.on_hand) for r in inventory.inventory_overview()}, {("S", 3), ("L", 2)})

    def test_rules(self):
        with self.assertRaises(Invalid):
            inventory.receive_stock(self.user, [(self.product, None, 1)])  # product has variants
        with self.assertRaises(Invalid):
            inventory.receive_stock(self.user, [(self.product, self.small, 0)])
        with self.assertRaises(Invalid):
            inventory.set_stock(self.user, self.product, self.small, -1)
        with self.assertRaises(Invalid):
            inventory.receive_stock(self.user, [(self.merch("Mug", inventory_managed=False), None, 1)])
        with self.assertRaises(NotAllowed):
            inventory.receive_stock(self.users["plain"], [(self.product, self.small, 1)])

    def test_order_flow_no_backorders_and_return(self):
        inventory.receive_stock(self.user, [(self.product, self.small, 2)])
        order, line = self.make_order(self.product, self.small, quantity=3)
        with self.assertRaises(inventory.InventoryError):
            inventory.check_available([line])
        line.quantity = 2
        line.save()
        inventory.lock_products([self.product.pk])
        inventory.check_available([line])
        inventory.take_for_order_lines([line])
        line.refresh_from_db()
        self.assertEqual((inventory.stock_for(self.product, self.small), line.production_status, line.received_quantity), (0, ProductionStatus.RECEIVED, 2))
        self.assertEqual(next(r.committed for r in inventory.inventory_overview() if r.variant == self.small), 2)
        inventory.return_order_to_stock(order)
        inventory.return_order_to_stock(order)  # idempotent
        self.assertEqual(inventory.stock_for(self.product, self.small), 2)

    def test_sync_line_stock(self):
        inventory.receive_stock(self.user, [(self.product, self.small, 5), (self.product, self.large, 1)])
        _order, line = self.make_order(self.product, self.small, quantity=2)
        inventory.take_for_order_lines([line])
        line.variant, line.quantity = self.large, 2
        with self.assertRaises(inventory.InventoryError):
            inventory.sync_line_stock(line, old_variant_id=self.small.pk)
        self.assertEqual(inventory.stock_for(self.product, self.small), 3)
        line.quantity = 1
        inventory.sync_line_stock(line, old_variant_id=self.small.pk)
        self.assertEqual((inventory.stock_for(self.product, self.small), inventory.stock_for(self.product, self.large)), (5, 0))


class VoucherServiceTests(ShopClubMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.user = self.users["discounts"]
        self.expiry = datetime.date.today() + datetime.timedelta(days=30)

    def test_bulk_unique_numbers(self):
        created = vouchers.create_vouchers(self.user, amount=Decimal("25"), expiry_date=self.expiry, count=5)
        numbers = [v.number for v in created]
        self.assertEqual(len(set(numbers)), 5)
        self.assertTrue(all(n.startswith(f"VCH-{datetime.date.today().year}-") for n in numbers))
        self.assertEqual(Voucher.objects.count(), 5)
        with self.assertRaises(Invalid):
            vouchers.create_vouchers(self.user, amount=Decimal("0"), expiry_date=self.expiry)
        with self.assertRaises(Invalid):
            vouchers.create_vouchers(self.user, amount=Decimal("5"), expiry_date=self.expiry, count=201)

    def test_consume_restore_and_limits(self):
        (v,) = vouchers.create_vouchers(self.user, amount=Decimal("20"), expiry_date=self.expiry)
        vouchers.consume(v, Decimal("15"))
        self.assertEqual(Voucher.objects.get(pk=v.pk).available_amount, Decimal("5"))
        with self.assertRaises(vouchers.VoucherError):
            vouchers.consume(v, Decimal("6"))
        vouchers.restore(v, Decimal("100"))
        self.assertEqual(Voucher.objects.get(pk=v.pk).consumed_amount, Decimal("0"))
        v.is_active = False
        v.save()
        with self.assertRaises(vouchers.VoucherError):
            vouchers.consume(v, Decimal("1"))

    def test_manual_consumption_history_delete_rules(self):
        (v,) = vouchers.create_vouchers(self.user, amount=Decimal("20"), expiry_date=self.expiry)
        c = vouchers.record_consumption(self.user, v, amount=Decimal("8"), note="cash")
        self.assertEqual(vouchers.voucher_history(v)[0]["kind"], "manual")
        with self.assertRaises(Exception) as ctx:
            vouchers.delete_voucher(self.user, v)
        self.assertEqual(ctx.exception.code, "has_usage")
        with self.assertRaises(Invalid):
            vouchers.update_voucher(self.user, v, amount=Decimal("5"))
        vouchers.delete_consumption(self.user, c)
        self.assertEqual(Voucher.objects.get(pk=v.pk).consumed_amount, Decimal("0"))
        vouchers.delete_voucher(self.user, v)
        self.assertFalse(Voucher.objects.exists())


class CatalogServiceTests(ShopClubMixin, TestCase):
    def test_delete_rules_and_slugs(self):
        user = self.users["products"]
        a = catalog.create_product(user, name="Cap", price=Decimal("10"), variants=[{"name": "One size"}])
        b = catalog.create_product(user, name="Cap", price=Decimal("10"))
        self.assertNotEqual(a.slug, b.slug)
        self.make_order(a)
        with self.assertRaises(Exception) as ctx:
            catalog.delete_product(user, a)
        self.assertEqual(ctx.exception.code, "has_orders")
        catalog.delete_product(user, b)
        with self.assertRaises(Invalid):
            catalog.create_product(user, name="Fee", price=Decimal("5"), product_type="membership", inventory_managed=True)
        with self.assertRaises(Invalid):
            catalog.create_product(user, name="Neg", price=Decimal("-1"))
        with self.assertRaises(NotAllowed):
            catalog.create_product(self.users["plain"], name="Nope", price=Decimal("1"))
