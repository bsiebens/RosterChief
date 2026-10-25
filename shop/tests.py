from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.admin.sites import AdminSite
from django.core import mail
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone
from waffle import get_waffle_flag_model

from authentication.models import User
from club.models import Club, ClubMembership, Season
from club.tenancy import reset_current_club, set_current_club
from members.models import Member
from notifications.models import Notification, NotificationCategory
from rosterchief.storage import invoice_cache_storage, write_cached_invoice_pdf
from teams.models import Position, Team

from .admin import ProductAdmin
from .models import (
    AppliedDiscount,
    Cart,
    CartItem,
    Discount,
    DiscountType,
    Invoice,
    Order,
    OrderLine,
    Payment,
    Product,
    ProductCategory,
    ProductionStatus,
    ProductVariant,
    Voucher,
)
from .services.cart import add_to_cart
from .services.checkout import CheckoutError, find_discount, place_order
from .services.inventory import InventoryError, annotate_availability, inventory_overview, receive_stock, return_order_to_stock, set_stock, stock_for, sync_line_stock
from .services.invoices import ShopInvoicePDFError, create_invoice_for_order, invalidate_cached_invoice_pdf, render_invoice_pdf
from .services.notifications import _notify_order_placed_by_id, notify_order_ready_for_pickup
from .services.payments import PaymentError, amount_due, amount_paid, record_payment, sync_payment_status
from .services.pricing import cart_totals, order_total
from .services.production import mark_line_received, mark_lines_in_production, pending_production_lines, production_receiving_summary, receive_production, sync_production_status
from .services.stats import order_kpis, quantity_sold_by_product, quantity_sold_by_variant
from .signals import ProtectedCategoryError


class ProductSlugTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")

    def test_slug_auto_populated_from_name(self):
        product = Product.objects.create(club=self.club, name="Home Jersey")

        self.assertEqual(product.slug, "home-jersey")

    def test_explicit_slug_is_preserved(self):
        product = Product.objects.create(club=self.club, name="Home Jersey", slug="custom")

        self.assertEqual(product.slug, "custom")

    def test_slug_is_unique_per_club_with_suffix(self):
        first = Product.objects.create(club=self.club, name="Home Jersey")
        second = Product.objects.create(club=self.club, name="Home Jersey")

        self.assertEqual(first.slug, "home-jersey")
        self.assertEqual(second.slug, "home-jersey-2")

    def test_same_slug_allowed_in_a_different_club(self):
        other = Club.objects.create(name="Rival FC", slug="rival-fc")
        here = Product.objects.create(club=self.club, name="Home Jersey")
        there = Product.objects.create(club=other, name="Home Jersey")

        self.assertEqual(here.slug, there.slug)

    def test_unsluggable_name_falls_back(self):
        product = Product.objects.create(club=self.club, name="###")

        self.assertEqual(product.slug, "item")

    def test_str_returns_name(self):
        product = Product.objects.create(club=self.club, name="Home Jersey")

        self.assertEqual(str(product), "Home Jersey")


class ProductCategoryModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")

    def test_str_returns_name(self):
        category = ProductCategory.objects.create(club=self.club, name="Merchandise")

        self.assertEqual(str(category), "Merchandise")

    def test_name_is_unique_per_club(self):
        ProductCategory.objects.create(club=self.club, name="Merchandise")

        with self.assertRaises(IntegrityError):
            ProductCategory.objects.create(club=self.club, name="Merchandise")

    def test_the_same_name_is_fine_in_a_different_club(self):
        other_club = Club.objects.create(name="Rival FC", slug="rival-fc")
        ProductCategory.objects.create(club=self.club, name="Merchandise")

        ProductCategory.objects.create(club=other_club, name="Merchandise")  # must not raise

    def test_deleting_a_category_uncategorises_its_products_instead_of_deleting_them(self):
        category = ProductCategory.objects.create(club=self.club, name="Merchandise")
        product = Product.objects.create(club=self.club, name="Home Jersey", category=category)

        category.delete()

        product.refresh_from_db()
        self.assertIsNone(product.category)
        self.assertTrue(Product.objects.filter(pk=product.pk).exists())

    def test_a_new_club_is_seeded_with_the_player_and_volunteer_categories(self):
        club = Club.objects.create(name="Rival FC", slug="rival-fc-2")

        self.assertEqual(ProductCategory.objects.get(club=club, registration_kind=ProductCategory.RegistrationKind.PLAYER).name, "Player")
        self.assertEqual(ProductCategory.objects.get(club=club, registration_kind=ProductCategory.RegistrationKind.VOLUNTEER).name, "Volunteer")

    def test_a_system_category_cannot_be_deleted_directly(self):
        category = ProductCategory.objects.get(club=self.club, registration_kind=ProductCategory.RegistrationKind.PLAYER)

        # The signal's raise happens inside .delete()'s own atomic block --
        # isolate it in its own so the test's outer transaction survives to
        # make the assertion below.
        with self.assertRaises(ProtectedCategoryError), transaction.atomic():
            category.delete()

        self.assertTrue(ProductCategory.objects.filter(pk=category.pk).exists())

    def test_a_system_category_is_still_removed_when_its_whole_club_is_deleted(self):
        club = Club.objects.create(name="Rival FC", slug="rival-fc-3")
        club_id = club.pk

        club.delete()  # must not raise -- a full-tenant wipe isn't "deleting the category"

        self.assertFalse(ProductCategory.objects.filter(club_id=club_id).exists())


class ProductVariantModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        cls.product = Product.objects.create(club=cls.club, name="Home Jersey", price=Decimal("40.00"))

    def test_str_includes_the_product(self):
        variant = ProductVariant.objects.create(product=self.product, name="Small")

        self.assertEqual(str(variant), "Home Jersey — Small")

    def test_effective_price_falls_back_to_the_products_price(self):
        variant = ProductVariant.objects.create(product=self.product, name="Small")

        self.assertEqual(variant.effective_price, Decimal("40.00"))

    def test_effective_price_uses_its_own_price_when_set(self):
        variant = ProductVariant.objects.create(product=self.product, name="XXL", price=Decimal("45.00"))

        self.assertEqual(variant.effective_price, Decimal("45.00"))

    def test_variant_name_is_unique_per_product(self):
        ProductVariant.objects.create(product=self.product, name="Small")

        with self.assertRaises(IntegrityError):
            ProductVariant.objects.create(product=self.product, name="Small")

    def test_the_same_name_is_fine_on_a_different_product(self):
        other_product = Product.objects.create(club=self.club, name="Away Jersey")
        ProductVariant.objects.create(product=self.product, name="Small")

        ProductVariant.objects.create(product=other_product, name="Small")  # must not raise

        self.assertEqual(ProductVariant.objects.filter(name="Small").count(), 2)


class CartItemVariantTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        cls.user = User.objects.create_user(email="shopper@example.com", password="pw")
        cls.product = Product.objects.create(club=cls.club, name="Home Jersey", price=Decimal("40.00"))
        cls.variant = ProductVariant.objects.create(product=cls.product, name="Small")

    def test_two_variants_of_the_same_product_coexist_in_one_cart(self):
        cart = Cart.objects.create(club=self.club, user=self.user)
        other_variant = ProductVariant.objects.create(product=self.product, name="Medium")

        CartItem.objects.create(cart=cart, product=self.product, variant=self.variant, quantity=1, unit_price=Decimal("40"))
        CartItem.objects.create(cart=cart, product=self.product, variant=other_variant, quantity=1, unit_price=Decimal("40"))  # must not raise

        self.assertEqual(cart.items.count(), 2)

    def test_a_variant_and_no_variant_of_the_same_product_coexist(self):
        cart = Cart.objects.create(club=self.club, user=self.user)

        CartItem.objects.create(cart=cart, product=self.product, variant=self.variant, quantity=1, unit_price=Decimal("40"))
        CartItem.objects.create(cart=cart, product=self.product, variant=None, quantity=1, unit_price=Decimal("40"))  # must not raise

        self.assertEqual(cart.items.count(), 2)

    def test_rejects_a_variant_belonging_to_a_different_product(self):
        other_product = Product.objects.create(club=self.club, name="Away Jersey")
        cart = Cart.objects.create(club=self.club, user=self.user)
        item = CartItem(cart=cart, product=other_product, variant=self.variant, quantity=1, unit_price=Decimal("40"))

        with self.assertRaises(ValidationError) as ctx:
            item.full_clean()
        self.assertIn("variant", ctx.exception.error_dict)

    def test_str_includes_the_variant(self):
        cart = Cart.objects.create(club=self.club, user=self.user)
        item = CartItem.objects.create(cart=cart, product=self.product, variant=self.variant, quantity=2, unit_price=Decimal("40"))

        self.assertEqual(str(item), "Home Jersey (Small) - 2x")


class OpenCartConstraintTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        cls.user = User.objects.create_user(email="shopper@example.com", password="pw")

    def test_only_one_open_cart_per_user_per_club(self):
        Cart.objects.create(club=self.club, user=self.user)

        with self.assertRaises(IntegrityError):
            Cart.objects.create(club=self.club, user=self.user)

    def test_open_and_closed_carts_coexist(self):
        Cart.objects.create(club=self.club, user=self.user, status=Cart.CartStatus.CHECKED_OUT)
        Cart.objects.create(club=self.club, user=self.user, status=Cart.CartStatus.ABANDONED)
        Cart.objects.create(club=self.club, user=self.user)

        self.assertEqual(self.user.carts.count(), 3)
        self.assertEqual(self.user.carts.filter(status=Cart.CartStatus.OPEN).count(), 1)

    def test_open_cart_allowed_in_each_club(self):
        other = Club.objects.create(name="Rival FC", slug="rival-fc")
        Cart.objects.create(club=self.club, user=self.user)
        Cart.objects.create(club=other, user=self.user)

        self.assertEqual(self.user.carts.filter(status=Cart.CartStatus.OPEN).count(), 2)

    def test_str(self):
        cart = Cart.objects.create(club=self.club, user=self.user)

        self.assertEqual(str(cart), f"{self.user} - open")


class CartItemTests(TestCase):
    def test_str(self):
        club = Club.objects.create(name="Ajax United", slug="ajax-united")
        user = User.objects.create_user(email="shopper@example.com", password="pw")
        cart = Cart.objects.create(club=club, user=user)
        product = Product.objects.create(club=club, name="Home Jersey")
        item = CartItem.objects.create(cart=cart, product=product, quantity=2, unit_price=Decimal("25.00"))

        self.assertEqual(str(item), "Home Jersey - 2x")


class OrderNumberTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        cls.member = Member.objects.create(first_name="Jane", last_name="Doe")
        cls.year = timezone.now().year

    def make_order(self, **kwargs):
        kwargs.setdefault("club", self.club)
        kwargs.setdefault("purchaser", self.member)
        kwargs.setdefault("total", Decimal("10.00"))
        return Order.objects.create(**kwargs)

    def test_number_is_generated(self):
        order = self.make_order()

        self.assertEqual(order.number, f"ORD-{self.year}-00001")

    def test_number_increments_within_club_and_year(self):
        first = self.make_order()
        second = self.make_order()

        self.assertEqual(first.number, f"ORD-{self.year}-00001")
        self.assertEqual(second.number, f"ORD-{self.year}-00002")

    def test_number_is_scoped_per_club(self):
        other = Club.objects.create(name="Rival FC", slug="rival-fc")
        self.make_order()

        order = self.make_order(club=other)

        self.assertEqual(order.number, f"ORD-{self.year}-00001")

    def test_explicit_number_is_preserved(self):
        order = self.make_order(number="CUSTOM-1")

        self.assertEqual(order.number, "CUSTOM-1")

    def test_resaving_keeps_the_number(self):
        order = self.make_order()
        original = order.number

        order.payment_status = Order.PaymentStatus.PAID
        order.save()

        order.refresh_from_db()
        self.assertEqual(order.number, original)

    def test_non_numeric_suffix_is_ignored(self):
        Order.objects.create(club=self.club, purchaser=self.member, total=Decimal("5"), number=f"ORD-{self.year}-oops")

        order = self.make_order()

        self.assertEqual(order.number, f"ORD-{self.year}-00001")

    def test_club_filled_from_tenant_context(self):
        token = set_current_club(self.club)
        try:
            order = Order.objects.create(purchaser=self.member, total=Decimal("5"))
        finally:
            reset_current_club(token)

        self.assertEqual(order.club, self.club)

    def test_retries_on_collision(self):
        taken = self.make_order().number
        with patch.object(Order, "generate_number", side_effect=[taken, "ORD-2999-00001"]):
            order = Order(club=self.club, purchaser=self.member, total=Decimal("5"))
            order.save()

        self.assertEqual(order.number, "ORD-2999-00001")

    def test_gives_up_after_exhausting_retries(self):
        taken = self.make_order().number
        with patch.object(Order, "generate_number", return_value=taken), self.assertRaises(IntegrityError):
            Order(club=self.club, purchaser=self.member, total=Decimal("5")).save()

    def test_str_is_the_number(self):
        order = self.make_order()

        self.assertEqual(str(order), order.number)


class ShopEntitiesTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        cls.member = Member.objects.create(first_name="Jane", last_name="Doe")
        cls.product = Product.objects.create(club=cls.club, name="Home Jersey")
        cls.order = Order.objects.create(club=cls.club, purchaser=cls.member, total=Decimal("50.00"))
        cls.year = timezone.now().year


class OrderLineTests(ShopEntitiesTestBase):
    def make_line(self, **kwargs):
        kwargs.setdefault("order", self.order)
        kwargs.setdefault("product", self.product)
        kwargs.setdefault("quantity", 2)
        kwargs.setdefault("unit_price", Decimal("25.00"))
        kwargs.setdefault("line_total", Decimal("50.00"))
        return OrderLine.objects.create(**kwargs)

    def test_str(self):
        self.assertEqual(str(self.make_line()), "Home Jersey - 2x")

    def test_deleting_order_cascades_to_lines(self):
        self.make_line()
        self.order.delete()
        self.assertFalse(OrderLine.objects.exists())

    def test_product_is_protected_while_referenced(self):
        self.make_line()
        with self.assertRaises(ProtectedError):
            self.product.delete()


class OrderProductionStatusTests(ShopEntitiesTestBase):
    """Order.has_production_lines/production_status -- the rollup is only
    ever meaningful once a merchandise line actually exists on the order."""

    def make_line(self, product=None, **kwargs):
        kwargs.setdefault("order", self.order)
        kwargs.setdefault("product", product or self.product)
        kwargs.setdefault("quantity", 1)
        kwargs.setdefault("unit_price", Decimal("25.00"))
        kwargs.setdefault("line_total", Decimal("25.00"))
        return OrderLine.objects.create(**kwargs)

    def test_has_no_production_lines_when_the_product_isnt_merchandise(self):
        self.product.product_type = Product.ProductType.MEMBERSHIP
        self.product.save(update_fields=["product_type"])
        self.make_line()

        self.assertFalse(self.order.has_production_lines)

    def test_has_production_lines_once_a_merchandise_line_exists(self):
        self.product.product_type = Product.ProductType.MERCHANDISE
        self.product.save(update_fields=["product_type"])
        self.make_line()

        self.assertTrue(self.order.has_production_lines)

    def test_production_status_defaults_to_pending(self):
        self.assertEqual(self.order.production_status, ProductionStatus.PENDING)


class OrderMemberStatusTests(ShopEntitiesTestBase):
    """Order.member_status -- the single, consolidated status the mobile app
    shows, folding production_status into fulfillment_status."""

    def make_line(self, **kwargs):
        kwargs.setdefault("order", self.order)
        kwargs.setdefault("product", self.product)
        kwargs.setdefault("quantity", 1)
        kwargs.setdefault("unit_price", Decimal("25.00"))
        kwargs.setdefault("line_total", Decimal("25.00"))
        return OrderLine.objects.create(**kwargs)

    def test_not_ready_with_no_production_lines(self):
        self.assertEqual(self.order.member_status, Order.MemberStatus.NOT_READY)

    def test_not_ready_while_a_merchandise_line_is_still_pending(self):
        self.product.product_type = Product.ProductType.MERCHANDISE
        self.product.save(update_fields=["product_type"])
        self.make_line()

        self.assertEqual(self.order.member_status, Order.MemberStatus.NOT_READY)

    def test_in_production_once_a_merchandise_line_is_sent(self):
        self.product.product_type = Product.ProductType.MERCHANDISE
        self.product.save(update_fields=["product_type"])
        self.make_line(production_status=ProductionStatus.IN_PRODUCTION)
        sync_production_status(self.order)  # member_status reads the rolled-up Order.production_status, not the line directly

        self.assertEqual(self.order.member_status, Order.MemberStatus.IN_PRODUCTION)

    def test_ready_for_pickup_overrides_production_status(self):
        self.product.product_type = Product.ProductType.MERCHANDISE
        self.product.save(update_fields=["product_type"])
        self.make_line(production_status=ProductionStatus.IN_PRODUCTION)
        self.order.fulfillment_status = Order.FulfillmentStatus.READY_FOR_PICKUP
        self.order.save(update_fields=["fulfillment_status"])

        self.assertEqual(self.order.member_status, Order.MemberStatus.READY_FOR_PICKUP)

    def test_completed_once_delivered(self):
        self.order.fulfillment_status = Order.FulfillmentStatus.DELIVERED
        self.order.save(update_fields=["fulfillment_status"])

        self.assertEqual(self.order.member_status, Order.MemberStatus.COMPLETED)

    def test_cancelled_overrides_everything_else(self):
        self.product.product_type = Product.ProductType.MERCHANDISE
        self.product.save(update_fields=["product_type"])
        self.make_line(production_status=ProductionStatus.IN_PRODUCTION)
        self.order.fulfillment_status = Order.FulfillmentStatus.CANCELLED
        self.order.save(update_fields=["fulfillment_status"])

        self.assertEqual(self.order.member_status, Order.MemberStatus.CANCELLED)


class DiscountTests(ShopEntitiesTestBase):
    def test_slug_and_str(self):
        discount = Discount.objects.create(club=self.club, name="Sibling discount", code="SIBLING")

        self.assertEqual(discount.slug, "sibling-discount")
        self.assertEqual(str(discount), "Sibling discount")

    def test_code_is_stored_uppercased(self):
        discount = Discount.objects.create(club=self.club, name="Sibling discount", code="sibling10")

        self.assertEqual(discount.code, "SIBLING10")

    def test_slug_is_unique_per_club(self):
        Discount.objects.create(club=self.club, name="Sibling", code="SIBLING1")
        second = Discount.objects.create(club=self.club, name="Sibling", code="SIBLING2")

        self.assertEqual(second.slug, "sibling-2")

    def test_code_is_unique_per_club(self):
        Discount.objects.create(club=self.club, name="Sibling", code="SIBLING")

        with self.assertRaises(IntegrityError):
            Discount.objects.create(club=self.club, name="Other", code="sibling")


class AppliedDiscountTests(ShopEntitiesTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.discount = Discount.objects.create(club=cls.club, name="Sibling", code="SIBLING")

    def apply(self, **kwargs):
        kwargs.setdefault("order", self.order)
        kwargs.setdefault("discount", self.discount)
        kwargs.setdefault("discount_amount", Decimal("10.00"))
        return AppliedDiscount.objects.create(**kwargs)

    def test_str_percentage_shows_percent(self):
        applied = self.apply(discount_type=DiscountType.PERCENTAGE)

        self.assertEqual(str(applied), "Sibling - 10.00%")

    def test_str_fixed_amount_has_no_percent(self):
        applied = self.apply(discount_type=DiscountType.FIXED_AMOUNT)

        self.assertEqual(str(applied), "Sibling - 10.00")

    def test_deleting_order_cascades(self):
        self.apply()
        self.order.delete()
        self.assertFalse(AppliedDiscount.objects.exists())

    def test_discount_is_protected_while_referenced(self):
        self.apply()
        with self.assertRaises(ProtectedError):
            self.discount.delete()


class PaymentTests(ShopEntitiesTestBase):
    def test_str(self):
        payment = Payment.objects.create(order=self.order, amount=Decimal("50.00"))

        self.assertEqual(str(payment), f"{self.order} - pending")

    def test_deleting_order_cascades(self):
        Payment.objects.create(order=self.order, amount=Decimal("50.00"))
        self.order.delete()
        self.assertFalse(Payment.objects.exists())

    def test_voucher_method_without_a_voucher_is_rejected(self):
        payment = Payment(order=self.order, amount=Decimal("10.00"), method=Payment.PaymentMethod.VOUCHER)

        with self.assertRaises(ValidationError) as ctx:
            payment.full_clean()
        self.assertIn("voucher", ctx.exception.error_dict)

    def test_a_voucher_with_a_non_voucher_method_is_rejected(self):
        voucher = Voucher.objects.create(club=self.club, amount=Decimal("50.00"), expiry_date=timezone.localdate() + timedelta(days=1))
        payment = Payment(order=self.order, amount=Decimal("10.00"), method=Payment.PaymentMethod.CASH, voucher=voucher)

        with self.assertRaises(ValidationError) as ctx:
            payment.full_clean()
        self.assertIn("voucher", ctx.exception.error_dict)

    def test_a_voucher_from_another_club_is_rejected(self):
        other_club = Club.objects.create(name="Rival FC", slug="rival-fc-payment")
        voucher = Voucher.objects.create(club=other_club, amount=Decimal("50.00"), expiry_date=timezone.localdate() + timedelta(days=1))
        payment = Payment(order=self.order, amount=Decimal("10.00"), method=Payment.PaymentMethod.VOUCHER, voucher=voucher)

        with self.assertRaises(ValidationError) as ctx:
            payment.full_clean()
        self.assertIn("voucher", ctx.exception.error_dict)

    def test_a_matching_voucher_payment_is_valid(self):
        voucher = Voucher.objects.create(club=self.club, amount=Decimal("50.00"), expiry_date=timezone.localdate() + timedelta(days=1))
        payment = Payment(order=self.order, amount=Decimal("10.00"), method=Payment.PaymentMethod.VOUCHER, voucher=voucher)

        payment.full_clean()  # must not raise


class VoucherTests(ShopEntitiesTestBase):
    def test_number_is_generated_and_str(self):
        voucher = Voucher.objects.create(club=self.club, amount=Decimal("50.00"), expiry_date=timezone.localdate() + timedelta(days=1))

        self.assertEqual(voucher.number, f"VCH-{self.year}-00001")
        self.assertEqual(str(voucher), voucher.number)

    def test_number_increments_per_club(self):
        Voucher.objects.create(club=self.club, amount=Decimal("10.00"), expiry_date=timezone.localdate() + timedelta(days=1))
        second = Voucher.objects.create(club=self.club, amount=Decimal("20.00"), expiry_date=timezone.localdate() + timedelta(days=1))

        self.assertEqual(second.number, f"VCH-{self.year}-00002")

    def test_available_amount_subtracts_consumed(self):
        voucher = Voucher.objects.create(club=self.club, amount=Decimal("50.00"), consumed_amount=Decimal("20.00"), expiry_date=timezone.localdate() + timedelta(days=1))

        self.assertEqual(voucher.available_amount, Decimal("30.00"))

    def test_consumed_amount_over_the_total_is_rejected(self):
        voucher = Voucher(club=self.club, amount=Decimal("50.00"), consumed_amount=Decimal("60.00"), expiry_date=timezone.localdate() + timedelta(days=1))

        with self.assertRaises(ValidationError) as ctx:
            voucher.full_clean()
        self.assertIn("consumed_amount", ctx.exception.error_dict)

    def test_is_expired(self):
        expired = Voucher.objects.create(club=self.club, amount=Decimal("50.00"), expiry_date=timezone.localdate() - timedelta(days=1))
        not_expired = Voucher.objects.create(club=self.club, amount=Decimal("50.00"), expiry_date=timezone.localdate() + timedelta(days=1))

        self.assertTrue(expired.is_expired)
        self.assertFalse(not_expired.is_expired)

    def test_is_usable_requires_active_unexpired_and_available(self):
        usable = Voucher.objects.create(club=self.club, amount=Decimal("50.00"), expiry_date=timezone.localdate() + timedelta(days=1))
        inactive = Voucher.objects.create(club=self.club, amount=Decimal("50.00"), is_active=False, expiry_date=timezone.localdate() + timedelta(days=1))
        expired = Voucher.objects.create(club=self.club, amount=Decimal("50.00"), expiry_date=timezone.localdate() - timedelta(days=1))
        depleted = Voucher.objects.create(club=self.club, amount=Decimal("50.00"), consumed_amount=Decimal("50.00"), expiry_date=timezone.localdate() + timedelta(days=1))

        self.assertTrue(usable.is_usable)
        self.assertFalse(inactive.is_usable)
        self.assertFalse(expired.is_usable)
        self.assertFalse(depleted.is_usable)

    def test_issued_to_must_be_a_member_of_the_club(self):
        outsider = Member.objects.create(first_name="Otto", last_name="Outsider")
        voucher = Voucher(club=self.club, amount=Decimal("50.00"), expiry_date=timezone.localdate() + timedelta(days=1), issued_to=outsider)

        with self.assertRaises(ValidationError) as ctx:
            voucher.full_clean()
        self.assertIn("issued_to", ctx.exception.error_dict)


class InvoiceTests(ShopEntitiesTestBase):
    def test_number_generated_and_str(self):
        invoice = Invoice.objects.create(club=self.club, order=self.order)

        self.assertEqual(invoice.number, f"INV-{self.year}-00001")
        self.assertEqual(str(invoice), invoice.number)

    def test_number_increments_per_club(self):
        second_order = Order.objects.create(club=self.club, purchaser=self.member, total=Decimal("10.00"))
        Invoice.objects.create(club=self.club, order=self.order)
        second = Invoice.objects.create(club=self.club, order=second_order)

        self.assertEqual(second.number, f"INV-{self.year}-00002")

    def test_one_invoice_per_order(self):
        Invoice.objects.create(club=self.club, order=self.order)

        with self.assertRaises(IntegrityError):
            Invoice.objects.create(club=self.club, order=self.order)

    def test_deleting_order_cascades_to_invoice(self):
        Invoice.objects.create(club=self.club, order=self.order)
        self.order.delete()
        self.assertFalse(Invoice.objects.exists())


class ClubScopeValidationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        cls.other = Club.objects.create(name="Rival FC", slug="rival-fc")
        today = timezone.localdate()
        cls.season = Season.objects.create(club=cls.club, start_date=today, end_date=today + timedelta(days=300))
        cls.other_season = Season.objects.create(club=cls.other, start_date=today, end_date=today + timedelta(days=300))
        cls.member = Member.objects.create(first_name="Jane", last_name="Doe")
        ClubMembership.objects.create(club=cls.club, member=cls.member, season=cls.season)
        cls.stranger = Member.objects.create(first_name="Stray", last_name="Ger")

    def make_cart(self, club):
        user = User.objects.create_user(email=f"u-{club.slug}@example.com", password="pw")
        return Cart.objects.create(club=club, user=user)

    def make_order(self, club):
        return Order.objects.create(club=club, purchaser=self.member, total=Decimal("10.00"))

    # --- Product ---
    def test_product_rejects_cross_club_season(self):
        product = Product(club=self.club, name="Jersey", season=self.other_season)
        with self.assertRaises(ValidationError) as ctx:
            product.full_clean()
        self.assertIn("season", ctx.exception.error_dict)

    def test_product_rejects_cross_club_staff_role(self):
        position = Position.objects.create(club=self.other, name="Coach", short_name="C", staff_position=True)
        product = Product(club=self.club, name="Jersey", staff_role=position)
        with self.assertRaises(ValidationError) as ctx:
            product.full_clean()
        self.assertIn("staff_role", ctx.exception.error_dict)

    def test_product_accepts_same_club_season(self):
        Product(club=self.club, name="Jersey", season=self.season).full_clean()

    # --- CartItem ---
    def test_cartitem_rejects_cross_club_product(self):
        cart = self.make_cart(self.club)
        product = Product.objects.create(club=self.other, name="Jersey")
        item = CartItem(cart=cart, product=product, unit_price=Decimal("5"))
        with self.assertRaises(ValidationError) as ctx:
            item.full_clean()
        self.assertIn("product", ctx.exception.error_dict)

    def test_cartitem_rejects_cross_club_team(self):
        cart = self.make_cart(self.club)
        product = Product.objects.create(club=self.club, name="Jersey")
        team = Team.objects.create(club=self.other, name="First", short_name="1")
        item = CartItem(cart=cart, product=product, team=team, unit_price=Decimal("5"))
        with self.assertRaises(ValidationError) as ctx:
            item.full_clean()
        self.assertIn("team", ctx.exception.error_dict)

    def test_cartitem_rejects_non_member_beneficiary(self):
        cart = self.make_cart(self.club)
        product = Product.objects.create(club=self.club, name="Jersey")
        item = CartItem(cart=cart, product=product, beneficiary=self.stranger, unit_price=Decimal("5"))
        with self.assertRaises(ValidationError) as ctx:
            item.full_clean()
        self.assertIn("beneficiary", ctx.exception.error_dict)

    def test_cartitem_accepts_same_club(self):
        cart = self.make_cart(self.club)
        product = Product.objects.create(club=self.club, name="Jersey")
        CartItem(cart=cart, product=product, beneficiary=self.member, unit_price=Decimal("5")).full_clean()

    def test_cartitem_clean_without_cart_is_noop(self):
        CartItem().clean()

    # --- OrderLine ---
    def test_orderline_rejects_cross_club_product(self):
        order = self.make_order(self.club)
        product = Product.objects.create(club=self.other, name="Jersey")
        line = OrderLine(order=order, product=product, unit_price=Decimal("5"), line_total=Decimal("5"))
        with self.assertRaises(ValidationError) as ctx:
            line.full_clean()
        self.assertIn("product", ctx.exception.error_dict)

    def test_orderline_clean_without_order_is_noop(self):
        OrderLine().clean()

    # --- Order ---
    def test_order_rejects_non_member_purchaser(self):
        order = Order(club=self.club, purchaser=self.stranger, total=Decimal("10"))
        with self.assertRaises(ValidationError) as ctx:
            order.full_clean()
        self.assertIn("purchaser", ctx.exception.error_dict)

    def test_order_accepts_member_purchaser(self):
        Order(club=self.club, purchaser=self.member, total=Decimal("10")).full_clean()

    # --- AppliedDiscount ---
    def test_applieddiscount_rejects_cross_club_discount(self):
        order = self.make_order(self.club)
        discount = Discount.objects.create(club=self.other, name="Sibling", code="SIBLING")
        applied = AppliedDiscount(order=order, discount=discount, discount_amount=Decimal("5"))
        with self.assertRaises(ValidationError) as ctx:
            applied.full_clean()
        self.assertIn("discount", ctx.exception.error_dict)

    def test_applieddiscount_rejects_non_member_applied_by(self):
        order = self.make_order(self.club)
        discount = Discount.objects.create(club=self.club, name="Sibling", code="SIBLING")
        applied = AppliedDiscount(order=order, discount=discount, discount_amount=Decimal("5"), applied_by=self.stranger)
        with self.assertRaises(ValidationError) as ctx:
            applied.full_clean()
        self.assertIn("applied_by", ctx.exception.error_dict)

    def test_applieddiscount_is_unique_per_order(self):
        order = self.make_order(self.club)
        discount = Discount.objects.create(club=self.club, name="Sibling", code="SIBLING")
        AppliedDiscount.objects.create(order=order, discount=discount, discount_amount=Decimal("5"))
        with self.assertRaises(IntegrityError):
            AppliedDiscount.objects.create(order=order, discount=discount, discount_amount=Decimal("5"))

    def test_applieddiscount_clean_without_order_is_noop(self):
        AppliedDiscount().clean()

    # --- Invoice ---
    def test_invoice_rejects_cross_club_order(self):
        order = self.make_order(self.other)
        invoice = Invoice(club=self.club, order=order)
        with self.assertRaises(ValidationError) as ctx:
            invoice.full_clean()
        self.assertIn("order", ctx.exception.error_dict)

    def test_invoice_accepts_same_club_order(self):
        order = self.make_order(self.club)
        Invoice(club=self.club, order=order).full_clean()


class AdminScopingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        cls.other = Club.objects.create(name="Rival FC", slug="rival-fc")
        today = timezone.localdate()
        cls.season = Season.objects.create(club=cls.club, start_date=today, end_date=today + timedelta(days=300))
        cls.other_season = Season.objects.create(club=cls.other, start_date=today, end_date=today + timedelta(days=300))

    def test_fk_dropdown_scoped_to_object_club(self):
        product = Product.objects.create(club=self.club, name="Jersey")
        admin_obj = ProductAdmin(Product, AdminSite())
        request = RequestFactory().get("/")
        request._club_obj = product

        field = admin_obj.formfield_for_foreignkey(Product._meta.get_field("season"), request)

        self.assertIn(self.season, field.queryset)
        self.assertNotIn(self.other_season, field.queryset)


class CartTotalsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        cls.user = User.objects.create_user(email="shopper@example.com", password="pw-secret-123")
        cls.product = Product.objects.create(club=cls.club, name="Home Jersey", price=Decimal("25.00"))

    def make_cart(self, *quantities):
        cart = Cart.objects.create(club=self.club, user=self.user)
        for quantity in quantities:
            CartItem.objects.create(cart=cart, product=self.product, quantity=quantity, unit_price=self.product.price, beneficiary=Member.objects.create(first_name=f"Kid{quantity}", last_name="Doe"))
        return cart

    def test_subtotal_sums_every_line(self):
        cart = self.make_cart(2)
        CartItem.objects.create(cart=cart, product=Product.objects.create(club=self.club, name="Cap", price=Decimal("10.00")), quantity=1, unit_price=Decimal("10.00"))

        totals = cart_totals(cart)

        self.assertEqual(totals["subtotal"], Decimal("60.00"))
        self.assertEqual(totals["total"], Decimal("60.00"))

    def test_percentage_discount(self):
        cart = self.make_cart(2)  # 50.00
        discount = Discount.objects.create(club=self.club, name="10 off", code="TEN", discount_type=DiscountType.PERCENTAGE, discount_amount=Decimal("10"))

        totals = cart_totals(cart, discount)

        self.assertEqual(totals["discount_amount"], Decimal("5.00"))
        self.assertEqual(totals["total"], Decimal("45.00"))

    def test_a_percentage_discount_is_rounded_to_the_nearest_cent(self):
        cart = self.make_cart(1)  # 25.00
        discount = Discount.objects.create(club=self.club, name="A third off", code="THIRD", discount_type=DiscountType.PERCENTAGE, discount_amount=Decimal("33.33"))

        totals = cart_totals(cart, discount)

        # 25.00 * 33.33 / 100 = 8.3325 unrounded -- half up to 8.33, not left
        # carrying a third decimal place.
        self.assertEqual(totals["discount_amount"], Decimal("8.33"))
        self.assertEqual(str(totals["discount_amount"]), "8.33")

    def test_fixed_amount_discount(self):
        cart = self.make_cart(2)  # 50.00
        discount = Discount.objects.create(club=self.club, name="Fiver off", code="FIVER", discount_type=DiscountType.FIXED_AMOUNT, discount_amount=Decimal("5.00"))

        totals = cart_totals(cart, discount)

        self.assertEqual(totals["total"], Decimal("45.00"))

    def test_a_fixed_discount_never_pushes_the_total_negative(self):
        cart = self.make_cart(1)  # 25.00
        discount = Discount.objects.create(club=self.club, name="Huge", code="HUGE", discount_type=DiscountType.FIXED_AMOUNT, discount_amount=Decimal("100.00"))

        totals = cart_totals(cart, discount)

        self.assertEqual(totals["discount_amount"], Decimal("25.00"))
        self.assertEqual(totals["total"], Decimal("0.00"))


class FindDiscountTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        cls.discount = Discount.objects.create(club=cls.club, name="Summer", code="SUMMER10")

    def test_blank_code_is_not_an_error(self):
        self.assertIsNone(find_discount(self.club, ""))

    def test_matches_case_insensitively(self):
        self.assertEqual(find_discount(self.club, "summer10"), self.discount)

    def test_unknown_code_raises(self):
        with self.assertRaises(CheckoutError):
            find_discount(self.club, "NOPE")

    def test_an_inactive_discount_is_not_found(self):
        self.discount.is_active = False
        self.discount.save(update_fields=["is_active"])

        with self.assertRaises(CheckoutError):
            find_discount(self.club, "SUMMER10")


class PlaceOrderTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united", shop_open=True)
        cls.user = User.objects.create_user(email="shopper@example.com", password="pw-secret-123")
        cls.member = Member.objects.create(first_name="Jane", last_name="Doe", email="shopper@example.com", user=cls.user)
        cls.product = Product.objects.create(club=cls.club, name="Home Jersey", price=Decimal("25.00"))

    def setUp(self):
        # dispatch_order_placed_notification fires on a real background thread now (see
        # shop.services.notifications) -- same reasoning as management.tests's own
        # dispatch_send_publish_notification patch: run its underlying, synchronous
        # by-id lookup inline instead, so a test asserting on the Notification/email it
        # produces isn't racing a thread that may not have finished yet.
        patcher = patch("shop.services.checkout.dispatch_order_placed_notification", side_effect=_notify_order_placed_by_id)
        patcher.start()
        self.addCleanup(patcher.stop)

    def make_cart(self, quantity=2):
        cart = Cart.objects.create(club=self.club, user=self.user)
        CartItem.objects.create(cart=cart, product=self.product, quantity=quantity, unit_price=self.product.price)
        return cart

    def test_a_closed_shop_refuses_checkout(self):
        self.club.shop_open = False
        self.club.save(update_fields=["shop_open"])
        cart = self.make_cart()

        with self.assertRaises(CheckoutError):
            place_order(cart, purchaser=self.member)

    def test_an_empty_cart_refuses_checkout(self):
        cart = Cart.objects.create(club=self.club, user=self.user)

        with self.assertRaises(CheckoutError):
            place_order(cart, purchaser=self.member)

    def test_creates_an_order_with_matching_lines_and_total(self):
        cart = self.make_cart(quantity=3)

        order = place_order(cart, purchaser=self.member)

        self.assertEqual(order.total, Decimal("75.00"))
        [line] = order.order_items.all()
        self.assertEqual(line.product, self.product)
        self.assertEqual(line.quantity, 3)
        self.assertEqual(line.line_total, Decimal("75.00"))

    def test_the_cart_is_marked_checked_out(self):
        cart = self.make_cart()

        place_order(cart, purchaser=self.member)

        cart.refresh_from_db()
        self.assertEqual(cart.status, Cart.CartStatus.CHECKED_OUT)

    def test_a_cart_items_variant_carries_through_to_the_order_line(self):
        variant = ProductVariant.objects.create(product=self.product, name="Small", price=Decimal("30.00"))
        cart = Cart.objects.create(club=self.club, user=self.user)
        CartItem.objects.create(cart=cart, product=self.product, variant=variant, quantity=1, unit_price=variant.effective_price)

        order = place_order(cart, purchaser=self.member)

        [line] = order.order_items.all()
        self.assertEqual(line.variant, variant)
        self.assertEqual(line.unit_price, Decimal("30.00"))
        self.assertEqual(order.total, Decimal("30.00"))

    def test_a_cart_items_personalization_carries_through_to_the_order_line(self):
        cart = Cart.objects.create(club=self.club, user=self.user)
        CartItem.objects.create(cart=cart, product=self.product, quantity=1, unit_price=self.product.price, personalization_number="7", personalization_name="DOE")

        order = place_order(cart, purchaser=self.member)

        [line] = order.order_items.all()
        self.assertEqual(line.personalization_number, "7")
        self.assertEqual(line.personalization_name, "DOE")

    def test_an_applied_discount_reduces_the_total_and_is_recorded(self):
        cart = self.make_cart(quantity=2)  # 50.00
        Discount.objects.create(club=self.club, name="Ten off", code="TEN10", discount_type=DiscountType.PERCENTAGE, discount_amount=Decimal("10"))

        order = place_order(cart, purchaser=self.member, discount_code="ten10")

        self.assertEqual(order.total, Decimal("45.00"))
        [applied] = order.applied_discounts.all()
        self.assertEqual(applied.discount.code, "TEN10")
        self.assertEqual(applied.applied_by, self.member)

    def test_an_invalid_discount_code_refuses_checkout_without_creating_an_order(self):
        cart = self.make_cart()

        with self.assertRaises(CheckoutError):
            place_order(cart, purchaser=self.member, discount_code="NOPE")

        self.assertFalse(Order.objects.exists())

    def test_an_invoice_is_created_for_the_order(self):
        cart = self.make_cart()

        order = place_order(cart, purchaser=self.member)

        self.assertTrue(Invoice.objects.filter(order=order).exists())
        self.assertTrue(order.invoice.number.startswith("INV-"))

    def test_the_purchaser_is_notified(self):
        cart = self.make_cart()

        order = place_order(cart, purchaser=self.member)

        notification = Notification.objects.get(member=self.member)
        self.assertIn(order.number, notification.title)
        self.assertEqual(notification.source, order)
        self.assertEqual(notification.category, NotificationCategory.SHOP)

    def test_the_notification_email_has_the_invoice_pdf_attached(self):
        cart = self.make_cart()

        with patch("shop.services.invoices.render_pdf", side_effect=lambda html: html.encode()):
            order = place_order(cart, purchaser=self.member)

        [attachment] = mail.outbox[0].attachments
        filename, _content, mimetype = attachment
        self.assertEqual(filename, f"{order.invoice.number}.pdf")
        self.assertEqual(mimetype, "application/pdf")

    def test_no_attachment_when_pdf_rendering_is_unavailable(self):
        # WeasyPrint missing its native libraries (ShopInvoicePDFError) must
        # never block the notification itself -- see _invoice_attachment's
        # own docstring.
        cart = self.make_cart()

        with patch("shop.services.invoices.render_pdf", side_effect=ShopInvoicePDFError("no native libs")):
            order = place_order(cart, purchaser=self.member)

        self.assertEqual(mail.outbox[0].attachments, [])
        self.assertEqual(Notification.objects.get(member=self.member).source, order)


class OrderReadyForPickupNotificationTests(TestCase):
    """shop.services.notifications.notify_order_ready_for_pickup -- the purchaser told
    (app + email) their order is ready to collect. Tests the synchronous primitive
    directly, not dispatch_order_ready_for_pickup_notification (which just re-fetches
    this same order by id on a background thread -- see that module's own docstring)."""

    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        cls.member = Member.objects.create(first_name="Jane", last_name="Doe", email="shopper@example.com", user=User.objects.create_user(email="shopper@example.com", password="pw-secret-123"))
        cls.order = Order.objects.create(club=cls.club, purchaser=cls.member, fulfillment_status=Order.FulfillmentStatus.READY_FOR_PICKUP, total=Decimal("50.00"))

    def test_creates_an_in_app_notification(self):
        notify_order_ready_for_pickup(self.order)

        notification = Notification.objects.get(member=self.member)
        self.assertIn(self.order.number, notification.title)
        self.assertIn("ready", notification.title.lower())
        self.assertEqual(notification.category, NotificationCategory.SHOP)
        self.assertEqual(notification.source, self.order)

    def test_stores_english_and_dutch_translations(self):
        self.order.pickup_instructions = "Ask at the desk."
        self.order.save(update_fields=["pickup_instructions"])

        notify_order_ready_for_pickup(self.order)

        translations = Notification.objects.get(member=self.member).translations
        self.assertEqual(translations["en"]["title"], f"Order {self.order.number} is ready for pickup")
        self.assertEqual(translations["nl"]["title"], f"Bestelling {self.order.number} staat klaar om op te halen")
        self.assertEqual(translations["nl"]["body"], "Je bestelling staat klaar — je betaalt bij het ophalen. Ask at the desk.")

    def test_emails_the_purchaser(self):
        notify_order_ready_for_pickup(self.order)

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["shopper@example.com"])

    def test_pickup_instructions_are_folded_into_the_body_when_set(self):
        self.order.pickup_instructions = "Ask for it at the clubhouse desk."
        self.order.save(update_fields=["pickup_instructions"])

        notify_order_ready_for_pickup(self.order)

        notification = Notification.objects.get(member=self.member)
        self.assertIn("Ask for it at the clubhouse desk.", notification.body)

    def test_no_instructions_is_a_plain_body(self):
        notify_order_ready_for_pickup(self.order)

        notification = Notification.objects.get(member=self.member)
        self.assertNotIn("None", notification.body)


class InvoicePdfTests(TestCase):
    """shop.services.invoices.render_invoice_pdf -- mocks render_pdf itself
    (same technique as club.tests.InvoicePdfTests) so this exercises the
    template/context, not WeasyPrint's own native library dependency."""

    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united", legal_name="Ajax United VZW", shop_open=True)
        cls.user = User.objects.create_user(email="shopper@example.com", password="pw-secret-123")
        cls.member = Member.objects.create(first_name="Jane", last_name="Doe", email="shopper@example.com", user=cls.user)
        cls.product = Product.objects.create(club=cls.club, name="Home Jersey", price=Decimal("25.00"))
        cart = Cart.objects.create(club=cls.club, user=cls.user)
        CartItem.objects.create(cart=cart, product=cls.product, quantity=2, unit_price=cls.product.price)
        cls.order = place_order(cart, purchaser=cls.member)

    def setUp(self):
        # setUpTestData's order/invoice is shared (class-level) across every
        # test method here, but render_invoice_pdf now caches its output to
        # disk keyed by invoice.pk -- disk writes aren't rolled back the way
        # the DB is between tests, so without this the first test method to
        # call render() would cache a copy every later one silently reuses
        # instead of exercising the mock.
        invalidate_cached_invoice_pdf(self.order.invoice)

    def render(self):
        with patch("shop.services.invoices.render_pdf", side_effect=lambda html: html) as renderer:
            render_invoice_pdf(self.order.invoice)
        return renderer.call_args[0][0]

    def test_the_header_uses_the_legal_name(self):
        self.assertIn("Ajax United VZW", self.render())

    def test_line_items_are_listed(self):
        html = self.render()

        self.assertIn("Home Jersey", html)
        self.assertIn("50.00", html)
        self.assertIn(self.order.number, html)

    def test_no_logo_falls_back_to_initials(self):
        html = self.render()

        self.assertIn("AU", html)

    def test_white_secondary_colour_does_not_produce_white_on_white_initials(self):
        # A club could set secondary_color to #ffffff -- the fallback badge's
        # background and text color must not both resolve to white.
        self.club.secondary_color = "#ffffff"
        self.club.save(update_fields=["secondary_color"])
        invalidate_cached_invoice_pdf(self.order.invoice)

        html = self.render()

        self.assertIn("background: #ffffff", html)
        self.assertIn("color: #000000", html)

    @override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app")
    def test_a_scannable_qr_code_is_embedded(self):
        html = self.render()

        self.assertIn("data:image/png;base64,", html)
        self.assertIn("Scan at pickup", html)

    @override_settings(ROSTERCHIEF_BASE_DOMAIN="")
    def test_no_qr_code_without_a_base_domain_configured(self):
        html = self.render()

        self.assertNotIn("Scan at pickup", html)

    def test_invoice_gets_its_own_number(self):
        create_invoice_for_order(Order.objects.create(club=self.club, purchaser=self.member, total=Decimal("1")))
        # The order created in setUpTestData already has its own invoice; a second
        # order's invoice must not collide with it.
        self.assertEqual(Invoice.objects.filter(club=self.club).count(), 2)


class RecordPaymentServiceTests(TestCase):
    """shop.services.payments -- record_payment/amount_paid/amount_due/
    sync_payment_status. The management views (OrderAddPaymentView,
    OrderMarkPaidView, PaymentDeleteView) are thin wrappers around this;
    covered again from the view layer in management.tests for the
    request/permission/form side, but the actual business rules (voucher
    balance, status derivation) belong here."""

    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        cls.member = Member.objects.create(first_name="Jane", last_name="Doe")
        cls.order = Order.objects.create(club=cls.club, purchaser=cls.member, total=Decimal("50.00"))

    def make_voucher(self, amount=Decimal("30.00"), club=None, expiry_date=None, **overrides):
        return Voucher.objects.create(club=club or self.club, amount=amount, expiry_date=expiry_date or timezone.localdate() + timedelta(days=1), **overrides)

    def test_amount_paid_sums_only_confirmed_payments(self):
        Payment.objects.create(order=self.order, amount=Decimal("10.00"), status=Payment.PaymentStatus.CONFIRMED)
        Payment.objects.create(order=self.order, amount=Decimal("99.00"), status=Payment.PaymentStatus.FAILED)

        self.assertEqual(amount_paid(self.order), Decimal("10.00"))

    def test_amount_due_is_the_total_minus_amount_paid(self):
        Payment.objects.create(order=self.order, amount=Decimal("20.00"), status=Payment.PaymentStatus.CONFIRMED)

        self.assertEqual(amount_due(self.order), Decimal("30.00"))

    def test_amount_due_never_goes_negative(self):
        Payment.objects.create(order=self.order, amount=Decimal("999.00"), status=Payment.PaymentStatus.CONFIRMED)

        self.assertEqual(amount_due(self.order), Decimal("0"))

    def test_a_zero_or_negative_amount_is_rejected(self):
        with self.assertRaises(PaymentError):
            record_payment(self.order, amount=Decimal("0"), method=Payment.PaymentMethod.CASH)

    def test_a_cash_payment_creates_a_confirmed_payment_and_syncs_status(self):
        payment = record_payment(self.order, amount=Decimal("20.00"), method=Payment.PaymentMethod.CASH, reference="receipt-1")

        self.assertEqual(payment.status, Payment.PaymentStatus.CONFIRMED)
        self.assertEqual(payment.reference, "receipt-1")
        self.assertIsNotNone(payment.paid_at)
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, Order.PaymentStatus.PARTIALLY_PAID)

    def test_paying_the_full_total_marks_the_order_paid(self):
        record_payment(self.order, amount=Decimal("50.00"), method=Payment.PaymentMethod.CASH)

        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, Order.PaymentStatus.PAID)

    def test_voucher_method_without_a_voucher_is_rejected(self):
        with self.assertRaises(PaymentError):
            record_payment(self.order, amount=Decimal("20.00"), method=Payment.PaymentMethod.VOUCHER)

    def test_a_voucher_payment_deducts_from_the_vouchers_balance(self):
        voucher = self.make_voucher(amount=Decimal("30.00"))

        payment = record_payment(self.order, amount=Decimal("20.00"), method=Payment.PaymentMethod.VOUCHER, voucher=voucher)

        self.assertEqual(payment.voucher, voucher)
        voucher.refresh_from_db()
        self.assertEqual(voucher.consumed_amount, Decimal("20.00"))

    def test_a_voucher_payment_over_its_available_balance_is_rejected_and_nothing_is_deducted(self):
        voucher = self.make_voucher(amount=Decimal("10.00"))

        with self.assertRaises(PaymentError):
            record_payment(self.order, amount=Decimal("20.00"), method=Payment.PaymentMethod.VOUCHER, voucher=voucher)

        voucher.refresh_from_db()
        self.assertEqual(voucher.consumed_amount, Decimal("0"))
        self.assertFalse(Payment.objects.filter(order=self.order).exists())

    def test_an_expired_voucher_is_rejected(self):
        voucher = self.make_voucher(amount=Decimal("30.00"), expiry_date=timezone.localdate() - timedelta(days=1))

        with self.assertRaises(PaymentError):
            record_payment(self.order, amount=Decimal("20.00"), method=Payment.PaymentMethod.VOUCHER, voucher=voucher)

    def test_an_inactive_voucher_is_rejected(self):
        voucher = self.make_voucher(amount=Decimal("30.00"), is_active=False)

        with self.assertRaises(PaymentError):
            record_payment(self.order, amount=Decimal("20.00"), method=Payment.PaymentMethod.VOUCHER, voucher=voucher)

    def test_a_voucher_from_another_club_is_rejected(self):
        other_club = Club.objects.create(name="Rival FC", slug="rival-fc-record-payment")
        voucher = self.make_voucher(amount=Decimal("30.00"), club=other_club)

        with self.assertRaises(PaymentError):
            record_payment(self.order, amount=Decimal("20.00"), method=Payment.PaymentMethod.VOUCHER, voucher=voucher)

    def test_a_voucher_passed_with_a_non_voucher_method_is_rejected(self):
        voucher = self.make_voucher(amount=Decimal("30.00"))

        with self.assertRaises(PaymentError):
            record_payment(self.order, amount=Decimal("20.00"), method=Payment.PaymentMethod.CASH, voucher=voucher)

    def test_sync_payment_status_recomputes_from_scratch(self):
        Payment.objects.create(order=self.order, amount=Decimal("50.00"), status=Payment.PaymentStatus.CONFIRMED)
        self.order.payment_status = Order.PaymentStatus.PENDING
        self.order.save(update_fields=["payment_status"])

        sync_payment_status(self.order)

        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, Order.PaymentStatus.PAID)

    def test_sync_payment_status_is_a_noop_when_nothing_changed(self):
        self.order.payment_status = Order.PaymentStatus.PENDING
        self.order.save(update_fields=["payment_status"])

        with self.assertNumQueries(1):  # the amount_paid aggregate only, no write
            sync_payment_status(self.order)


class OrderTotalServiceTests(TestCase):
    """shop.services.pricing.order_total -- re-derives Order.total from its
    own line items and applied discounts, used after a staff edit to an
    existing OrderLine (management.views.OrderLineUpdateView)."""

    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        cls.member = Member.objects.create(first_name="Jane", last_name="Doe")
        cls.product = Product.objects.create(club=cls.club, name="Home Jersey", price=Decimal("25.00"))
        cls.order = Order.objects.create(club=cls.club, purchaser=cls.member, total=Decimal("0"))

    def test_sums_line_totals(self):
        OrderLine.objects.create(order=self.order, product=self.product, quantity=2, unit_price=Decimal("25.00"), line_total=Decimal("50.00"))
        OrderLine.objects.create(order=self.order, product=self.product, quantity=1, unit_price=Decimal("25.00"), line_total=Decimal("25.00"))

        self.assertEqual(order_total(self.order), Decimal("75.00"))

    def test_subtracts_applied_discounts(self):
        OrderLine.objects.create(order=self.order, product=self.product, quantity=2, unit_price=Decimal("25.00"), line_total=Decimal("50.00"))
        discount = Discount.objects.create(club=self.club, name="Ten off", code="TEN10")
        AppliedDiscount.objects.create(order=self.order, discount=discount, discount_type=DiscountType.FIXED_AMOUNT, discount_amount=Decimal("10.00"))

        self.assertEqual(order_total(self.order), Decimal("40.00"))

    def test_never_goes_negative(self):
        OrderLine.objects.create(order=self.order, product=self.product, quantity=1, unit_price=Decimal("5.00"), line_total=Decimal("5.00"))
        discount = Discount.objects.create(club=self.club, name="Big discount", code="BIG")
        AppliedDiscount.objects.create(order=self.order, discount=discount, discount_type=DiscountType.FIXED_AMOUNT, discount_amount=Decimal("999.00"))

        self.assertEqual(order_total(self.order), Decimal("0"))

    def test_no_lines_is_zero(self):
        self.assertEqual(order_total(self.order), Decimal("0"))


class ProductionServiceTests(TestCase):
    """shop.services.production -- pending_production_lines/
    sync_production_status/mark_lines_in_production."""

    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        cls.member = Member.objects.create(first_name="Jane", last_name="Doe")
        cls.product = Product.objects.create(club=cls.club, name="Home Jersey", price=Decimal("25.00"), product_type=Product.ProductType.MERCHANDISE)
        cls.order = Order.objects.create(club=cls.club, purchaser=cls.member, total=Decimal("25.00"))

    def make_line(self, order=None, product=None, **kwargs):
        kwargs.setdefault("order", order or self.order)
        kwargs.setdefault("product", product or self.product)
        kwargs.setdefault("quantity", 1)
        kwargs.setdefault("unit_price", Decimal("25.00"))
        kwargs.setdefault("line_total", Decimal("25.00"))
        return OrderLine.objects.create(**kwargs)

    def test_pending_production_lines_excludes_already_started_lines(self):
        pending = self.make_line()
        self.make_line(production_status=ProductionStatus.IN_PRODUCTION)

        self.assertEqual(list(pending_production_lines([self.product])), [pending])

    def test_pending_production_lines_excludes_cancelled_orders(self):
        cancelled_order = Order.objects.create(club=self.club, purchaser=self.member, total=Decimal("25.00"), fulfillment_status=Order.FulfillmentStatus.CANCELLED)
        self.make_line(order=cancelled_order)

        self.assertEqual(list(pending_production_lines([self.product])), [])

    def test_pending_production_lines_is_scoped_to_the_given_products(self):
        other_product = Product.objects.create(club=self.club, name="Away Kit", price=Decimal("20.00"), product_type=Product.ProductType.MERCHANDISE)
        self.make_line(product=other_product)

        self.assertEqual(list(pending_production_lines([self.product])), [])

    def test_sync_is_a_noop_with_no_merchandise_lines(self):
        non_merch = Product.objects.create(club=self.club, name="Membership", price=Decimal("10.00"))
        OrderLine.objects.create(order=self.order, product=non_merch, quantity=1, unit_price=Decimal("10.00"), line_total=Decimal("10.00"))

        sync_production_status(self.order)

        self.order.refresh_from_db()
        self.assertEqual(self.order.production_status, ProductionStatus.PENDING)

    def test_sync_is_pending_while_every_line_is(self):
        self.make_line()
        self.make_line()

        sync_production_status(self.order)

        self.order.refresh_from_db()
        self.assertEqual(self.order.production_status, ProductionStatus.PENDING)

    def test_sync_is_in_production_when_mixed(self):
        self.make_line()
        self.make_line(production_status=ProductionStatus.IN_PRODUCTION)

        sync_production_status(self.order)

        self.order.refresh_from_db()
        self.assertEqual(self.order.production_status, ProductionStatus.IN_PRODUCTION)

    def test_sync_is_received_once_every_line_is(self):
        self.make_line(production_status=ProductionStatus.RECEIVED)
        self.make_line(production_status=ProductionStatus.RECEIVED)

        sync_production_status(self.order)

        self.order.refresh_from_db()
        self.assertEqual(self.order.production_status, ProductionStatus.RECEIVED)

    def test_mark_lines_in_production_flips_lines_and_resyncs_orders(self):
        line = self.make_line()

        mark_lines_in_production([line])

        line.refresh_from_db()
        self.assertEqual(line.production_status, ProductionStatus.IN_PRODUCTION)
        self.order.refresh_from_db()
        self.assertEqual(self.order.production_status, ProductionStatus.IN_PRODUCTION)

    def test_mark_lines_in_production_resyncs_every_affected_order(self):
        other_order = Order.objects.create(club=self.club, purchaser=self.member, total=Decimal("25.00"))
        other_line = self.make_line(order=other_order)
        line = self.make_line()

        mark_lines_in_production([line, other_line])

        other_order.refresh_from_db()
        self.assertEqual(other_order.production_status, ProductionStatus.IN_PRODUCTION)

    def test_mark_line_received_flips_the_line_and_resyncs_its_order(self):
        line = self.make_line(production_status=ProductionStatus.IN_PRODUCTION)

        mark_line_received(line)

        line.refresh_from_db()
        self.assertEqual(line.production_status, ProductionStatus.RECEIVED)
        self.order.refresh_from_db()
        self.assertEqual(self.order.production_status, ProductionStatus.RECEIVED)

    def test_mark_line_received_leaves_the_order_in_production_while_a_sibling_line_isnt_received_yet(self):
        received = self.make_line(production_status=ProductionStatus.IN_PRODUCTION)
        self.make_line(production_status=ProductionStatus.IN_PRODUCTION)

        mark_line_received(received)

        self.order.refresh_from_db()
        self.assertEqual(self.order.production_status, ProductionStatus.IN_PRODUCTION)


class ProductionReceivingTests(TestCase):
    """shop.services.production -- production_receiving_summary/
    receive_production, the bulk "mark everything that just arrived back
    from the manufacturer as received" flow."""

    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        cls.member = Member.objects.create(first_name="Jane", last_name="Doe")
        cls.product = Product.objects.create(club=cls.club, name="Home Jersey", price=Decimal("25.00"), product_type=Product.ProductType.MERCHANDISE)
        cls.variant = ProductVariant.objects.create(product=cls.product, name="Medium")

    def make_order(self, days_ago=0):
        order = Order.objects.create(club=self.club, purchaser=self.member, total=Decimal("25.00"))
        order.created = timezone.now() - timedelta(days=days_ago)
        order.save(update_fields=["created"])
        return order

    def make_line(self, order=None, product=None, variant=None, **kwargs):
        kwargs.setdefault("order", order or self.make_order())
        kwargs.setdefault("product", product or self.product)
        kwargs.setdefault("variant", variant)
        kwargs.setdefault("quantity", 1)
        kwargs.setdefault("unit_price", Decimal("25.00"))
        kwargs.setdefault("line_total", Decimal("25.00"))
        kwargs.setdefault("production_status", ProductionStatus.IN_PRODUCTION)
        return OrderLine.objects.create(**kwargs)

    def test_summary_sums_quantity_per_product_and_variant(self):
        self.make_line(quantity=2, variant=self.variant)
        self.make_line(quantity=3, variant=self.variant)

        summary = production_receiving_summary(self.club)

        self.assertEqual(len(summary), 1)
        self.assertEqual(summary[0]["product"], self.product)
        self.assertEqual(summary[0]["variants"], [{"variant": self.variant, "expected_quantity": 5}])
        self.assertEqual(summary[0]["personalized_count"], 0)

    def test_summary_excludes_pending_and_received_lines(self):
        self.make_line(production_status=ProductionStatus.PENDING, variant=self.variant)
        self.make_line(production_status=ProductionStatus.RECEIVED, variant=self.variant)

        self.assertEqual(production_receiving_summary(self.club), [])

    def test_summary_excludes_cancelled_orders(self):
        cancelled = self.make_order()
        cancelled.fulfillment_status = Order.FulfillmentStatus.CANCELLED
        cancelled.save(update_fields=["fulfillment_status"])
        self.make_line(order=cancelled, variant=self.variant)

        self.assertEqual(production_receiving_summary(self.club), [])

    def test_summary_counts_personalized_lines_separately_from_expected_quantity(self):
        self.make_line(quantity=2, variant=self.variant)
        self.make_line(personalization_name="Sam", variant=self.variant)
        self.make_line(personalization_number="7", variant=self.variant)

        summary = production_receiving_summary(self.club)

        self.assertEqual(summary[0]["variants"], [{"variant": self.variant, "expected_quantity": 2}])
        self.assertEqual(summary[0]["personalized_count"], 2)

    def test_summary_reports_a_product_with_only_personalized_lines(self):
        self.make_line(personalization_name="Sam", variant=self.variant)

        summary = production_receiving_summary(self.club)

        self.assertEqual(summary[0]["variants"], [])
        self.assertEqual(summary[0]["personalized_count"], 1)

    def test_receive_allocates_the_oldest_order_first(self):
        older = self.make_line(order=self.make_order(days_ago=2), quantity=2, variant=self.variant)
        newer = self.make_line(order=self.make_order(days_ago=1), quantity=2, variant=self.variant)

        results = receive_production(self.club, {(self.product, self.variant): 2})

        older.refresh_from_db()
        newer.refresh_from_db()
        self.assertEqual(older.production_status, ProductionStatus.RECEIVED)
        self.assertEqual(newer.production_status, ProductionStatus.IN_PRODUCTION)
        self.assertEqual(results[0].allocated_lines, [older])
        self.assertEqual(results[0].remaining_lines, [newer])

    def test_receive_leaves_a_partially_coverable_line_untouched_and_reports_the_shortfall(self):
        first = self.make_line(order=self.make_order(days_ago=2), quantity=20, variant=self.variant)
        second = self.make_line(order=self.make_order(days_ago=1), quantity=10, variant=self.variant)

        results = receive_production(self.club, {(self.product, self.variant): 25})  # covers `first` fully, not enough left for `second`

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.production_status, ProductionStatus.RECEIVED)
        self.assertEqual(second.production_status, ProductionStatus.IN_PRODUCTION)
        result = results[0]
        self.assertEqual(result.expected_quantity, 30)
        self.assertEqual(result.received_quantity, 25)
        self.assertEqual(result.shortfall, 5)
        self.assertEqual(result.surplus, 0)
        self.assertTrue(result.has_discrepancy)

    def test_receive_reports_an_unexplained_surplus_but_still_allocates_everything_owed(self):
        line = self.make_line(quantity=5, variant=self.variant)

        results = receive_production(self.club, {(self.product, self.variant): 8})

        line.refresh_from_db()
        self.assertEqual(line.production_status, ProductionStatus.RECEIVED)
        result = results[0]
        self.assertEqual(result.shortfall, 0)
        self.assertEqual(result.surplus, 3)
        self.assertTrue(result.has_discrepancy)

    def test_receive_an_exact_match_has_no_discrepancy(self):
        self.make_line(quantity=4, variant=self.variant)

        results = receive_production(self.club, {(self.product, self.variant): 4})

        self.assertFalse(results[0].has_discrepancy)

    def test_receive_never_touches_a_personalized_line(self):
        personalized = self.make_line(personalization_name="Sam", quantity=1, variant=self.variant)

        results = receive_production(self.club, {(self.product, self.variant): 1})

        personalized.refresh_from_db()
        self.assertEqual(personalized.production_status, ProductionStatus.IN_PRODUCTION)
        self.assertEqual(results[0].expected_quantity, 0)  # personalized lines aren't "expected" here at all
        self.assertEqual(results[0].surplus, 1)  # nothing non-personalized for the entered count to match

    def test_receive_resyncs_the_orders_own_rollup(self):
        order = self.make_order()
        line = self.make_line(order=order, quantity=1, variant=self.variant)

        receive_production(self.club, {(self.product, self.variant): 1})

        order.refresh_from_db()
        self.assertEqual(order.production_status, ProductionStatus.RECEIVED)
        self.assertEqual(line.pk, OrderLine.objects.get(pk=line.pk).pk)  # sanity: still the same row

    def test_receive_ignores_a_none_entry(self):
        line = self.make_line(quantity=1, variant=self.variant)

        results = receive_production(self.club, {(self.product, self.variant): None})

        line.refresh_from_db()
        self.assertEqual(line.production_status, ProductionStatus.IN_PRODUCTION)
        self.assertEqual(results, [])

    def make_other_club_line(self, **kwargs):
        other_club, _ = Club.objects.get_or_create(slug="rival-fc", defaults={"name": "Rival FC"})
        other_product, _ = Product.objects.get_or_create(club=other_club, name="Away Jersey", defaults={"price": Decimal("25.00"), "product_type": Product.ProductType.MERCHANDISE})
        other_variant, _ = ProductVariant.objects.get_or_create(product=other_product, name="Large")
        other_order = Order.objects.create(club=other_club, purchaser=self.member, total=Decimal("25.00"))
        return self.make_line(order=other_order, product=other_product, variant=other_variant, **kwargs)

    def test_summary_only_lists_the_given_clubs_lines(self):
        self.make_line(quantity=2, variant=self.variant)
        self.make_other_club_line(quantity=4)
        self.make_other_club_line(personalization_name="Sam")

        summary = production_receiving_summary(self.club)

        self.assertEqual([entry["product"] for entry in summary], [self.product])
        self.assertEqual(summary[0]["variants"], [{"variant": self.variant, "expected_quantity": 2}])

    def test_receive_never_touches_another_clubs_lines_even_for_its_own_product_key(self):
        other_line = self.make_other_club_line(quantity=1)

        results = receive_production(self.club, {(other_line.product, other_line.variant): 1})

        other_line.refresh_from_db()
        self.assertEqual(other_line.production_status, ProductionStatus.IN_PRODUCTION)
        self.assertEqual(results, [])

    def test_receive_ignores_another_clubs_order_lines_for_this_clubs_product(self):
        # A line whose order belongs to a different club than its product: neither
        # club's receiving screen should be able to act on it.
        other_club = Club.objects.create(name="Rival FC", slug="rival-fc")
        stray_order = Order.objects.create(club=other_club, purchaser=self.member, total=Decimal("25.00"))
        stray = self.make_line(order=stray_order, quantity=1, variant=self.variant)
        own = self.make_line(order=self.make_order(days_ago=1), quantity=1, variant=self.variant)

        results = receive_production(self.club, {(self.product, self.variant): 1})

        stray.refresh_from_db()
        own.refresh_from_db()
        self.assertEqual(stray.production_status, ProductionStatus.IN_PRODUCTION)
        self.assertEqual(own.production_status, ProductionStatus.RECEIVED)
        self.assertEqual(results[0].expected_quantity, 1)


class OrderKPIsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        cls.member = Member.objects.create(first_name="Jane", last_name="Doe")
        cls.product = Product.objects.create(club=cls.club, name="Home Jersey", price=Decimal("25.00"))

    def make_order(self, payment_status=Order.PaymentStatus.PENDING, fulfillment_status=Order.FulfillmentStatus.NOT_READY, total=Decimal("50.00"), quantity=2):
        order = Order.objects.create(club=self.club, purchaser=self.member, payment_status=payment_status, fulfillment_status=fulfillment_status, total=total)
        OrderLine.objects.create(order=order, product=self.product, quantity=quantity, unit_price=Decimal("25.00"), line_total=total)
        return order

    def test_open_orders_excludes_only_delivered_and_paid_orders(self):
        self.make_order(Order.PaymentStatus.PENDING, Order.FulfillmentStatus.NOT_READY)
        self.make_order(Order.PaymentStatus.PAID, Order.FulfillmentStatus.NOT_READY)
        self.make_order(Order.PaymentStatus.PARTIALLY_PAID, Order.FulfillmentStatus.NOT_READY)
        self.make_order(Order.PaymentStatus.PAID, Order.FulfillmentStatus.DELIVERED)
        # Delivered but unpaid still counts as open -- payment still needs collecting.
        self.make_order(Order.PaymentStatus.PENDING, Order.FulfillmentStatus.DELIVERED)
        self.make_order(Order.PaymentStatus.REFUNDED, Order.FulfillmentStatus.CANCELLED)

        self.assertEqual(order_kpis(self.club)["open_orders"], 5)

    def test_total_orders_counts_every_status(self):
        self.make_order(Order.PaymentStatus.PENDING, Order.FulfillmentStatus.NOT_READY)
        self.make_order(Order.PaymentStatus.REFUNDED, Order.FulfillmentStatus.CANCELLED)

        self.assertEqual(order_kpis(self.club)["total_orders"], 2)

    def test_total_sold_only_counts_paid_orders_regardless_of_fulfillment(self):
        self.make_order(Order.PaymentStatus.PAID, Order.FulfillmentStatus.NOT_READY, total=Decimal("30.00"))
        self.make_order(Order.PaymentStatus.PAID, Order.FulfillmentStatus.DELIVERED, total=Decimal("20.00"))
        self.make_order(Order.PaymentStatus.PENDING, Order.FulfillmentStatus.DELIVERED, total=Decimal("999.00"))
        self.make_order(Order.PaymentStatus.REFUNDED, Order.FulfillmentStatus.CANCELLED, total=Decimal("999.00"))

        self.assertEqual(order_kpis(self.club)["total_sold"], Decimal("50.00"))

    def test_items_sold_matches_total_sold_scope(self):
        self.make_order(Order.PaymentStatus.PAID, Order.FulfillmentStatus.NOT_READY, quantity=3)
        self.make_order(Order.PaymentStatus.PENDING, Order.FulfillmentStatus.NOT_READY, quantity=99)

        self.assertEqual(order_kpis(self.club)["items_sold"], 3)

    def test_zero_orders_returns_zero_not_none(self):
        kpis = order_kpis(self.club)

        self.assertEqual(kpis, {"open_orders": 0, "total_orders": 0, "total_sold": Decimal("0"), "items_sold": 0})

    def test_kpis_are_scoped_to_the_club(self):
        other_club = Club.objects.create(name="Rival FC", slug="rival-fc")
        Order.objects.create(club=other_club, purchaser=self.member, payment_status=Order.PaymentStatus.PAID, total=Decimal("100.00"))

        self.assertEqual(order_kpis(self.club)["total_orders"], 0)


class QuantitySoldTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        cls.member = Member.objects.create(first_name="Jane", last_name="Doe")
        cls.product = Product.objects.create(club=cls.club, name="Home Jersey", price=Decimal("25.00"))
        cls.small = ProductVariant.objects.create(product=cls.product, name="Small")
        cls.large = ProductVariant.objects.create(product=cls.product, name="Large")

    def make_line(self, payment_status, quantity, variant=None, product=None, fulfillment_status=Order.FulfillmentStatus.NOT_READY):
        order = Order.objects.create(club=self.club, purchaser=self.member, payment_status=payment_status, fulfillment_status=fulfillment_status, total=Decimal("1.00"))
        OrderLine.objects.create(order=order, product=product or self.product, variant=variant, quantity=quantity, unit_price=Decimal("25.00"), line_total=Decimal("1.00"))

    def test_only_paid_lines_count_regardless_of_fulfillment(self):
        self.make_line(Order.PaymentStatus.PAID, 2, fulfillment_status=Order.FulfillmentStatus.NOT_READY)
        self.make_line(Order.PaymentStatus.PAID, 1, fulfillment_status=Order.FulfillmentStatus.DELIVERED)
        # Delivered but unpaid must not count -- no revenue actually collected.
        self.make_line(Order.PaymentStatus.PENDING, 99, fulfillment_status=Order.FulfillmentStatus.DELIVERED)
        self.make_line(Order.PaymentStatus.REFUNDED, 99, fulfillment_status=Order.FulfillmentStatus.CANCELLED)

        self.assertEqual(quantity_sold_by_product(self.club), {self.product.pk: 3})

    def test_products_with_no_sales_are_absent_not_zero(self):
        other_product = Product.objects.create(club=self.club, name="Away Jersey")

        self.assertNotIn(other_product.pk, quantity_sold_by_product(self.club))

    def test_variant_breakdown_is_per_variant(self):
        self.make_line(Order.PaymentStatus.PAID, 2, variant=self.small)
        self.make_line(Order.PaymentStatus.PAID, 5, variant=self.large)
        self.make_line(Order.PaymentStatus.PAID, 1)  # no variant -- base sale

        breakdown = quantity_sold_by_variant(self.product)

        self.assertEqual(breakdown, {self.small.pk: 2, self.large.pk: 5})

    def test_another_products_sales_dont_leak_into_this_breakdown(self):
        other_product = Product.objects.create(club=self.club, name="Away Jersey")
        other_variant = ProductVariant.objects.create(product=other_product, name="Small")
        self.make_line(Order.PaymentStatus.PAID, 4, variant=other_variant, product=other_product)

        self.assertEqual(quantity_sold_by_variant(self.product), {})


class InvoicePdfCachingTests(TestCase):
    """render_invoice_pdf's own caching, and shop.signals' invalidation of it
    on Order.payment_status/fulfillment_status change -- see that module's own
    docstring."""

    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        cls.member = Member.objects.create(first_name="Jane", last_name="Doe")
        cls.order = Order.objects.create(club=cls.club, purchaser=cls.member, payment_status=Order.PaymentStatus.PENDING, total=Decimal("25.00"))
        cls.invoice = create_invoice_for_order(cls.order)

    def setUp(self):
        invalidate_cached_invoice_pdf(self.invoice)

    def test_a_second_render_reuses_the_cached_copy(self):
        with patch("shop.services.invoices.render_pdf", side_effect=lambda html: b"%PDF-fake") as renderer:
            first = render_invoice_pdf(self.invoice)
            second = render_invoice_pdf(self.invoice)

        renderer.assert_called_once()
        self.assertEqual(first, second)

    def test_changing_the_orders_payment_status_busts_the_cache(self):
        with patch("shop.services.invoices.render_pdf", side_effect=lambda html: b"%PDF-fake"):
            render_invoice_pdf(self.invoice)

        self.order.payment_status = Order.PaymentStatus.PAID
        self.order.save(update_fields=["payment_status"])

        with patch("shop.services.invoices.render_pdf", side_effect=lambda html: b"%PDF-fake-2") as renderer:
            second = render_invoice_pdf(self.invoice)

        renderer.assert_called_once()
        self.assertEqual(second, b"%PDF-fake-2")

    def test_saving_an_order_without_a_status_change_keeps_the_cache(self):
        with patch("shop.services.invoices.render_pdf", side_effect=lambda html: b"%PDF-fake"):
            render_invoice_pdf(self.invoice)

        self.order.save(update_fields=["payment_status"])  # same status, re-saved

        with patch("shop.services.invoices.render_pdf") as renderer:
            render_invoice_pdf(self.invoice)

        renderer.assert_not_called()

    def test_a_freshly_created_order_has_nothing_cached_to_invalidate(self):
        # pre_save's own pk-less branch -- must not raise on a brand-new Order.
        Order.objects.create(club=self.club, purchaser=self.member, payment_status=Order.PaymentStatus.PENDING, total=Decimal("1"))  # must not raise

    def test_the_rendered_pdf_is_never_written_to_public_default_storage(self):
        # default_storage is served publicly (Caddy /media/, or an unsigned bucket
        # URL) -- a cached invoice there would be readable by anyone guessing the path.
        from django.core.files.storage import default_storage

        from shop.services.invoices import _cache_path

        with patch("shop.services.invoices.render_pdf", side_effect=lambda html: b"%PDF-fake"):
            render_invoice_pdf(self.invoice)

        self.assertFalse(default_storage.exists(_cache_path(self.invoice)))
        self.assertTrue(invoice_cache_storage.exists(_cache_path(self.invoice)))
        with self.assertRaises(ValueError):
            invoice_cache_storage.url(_cache_path(self.invoice))

    def test_a_cached_copy_in_private_storage_is_served_without_rerendering(self):
        from shop.services.invoices import _cache_path

        write_cached_invoice_pdf(_cache_path(self.invoice), b"%PDF-private")

        with patch("shop.services.invoices.render_pdf") as renderer:
            pdf = render_invoice_pdf(self.invoice)

        renderer.assert_not_called()
        self.assertEqual(pdf, b"%PDF-private")


class ShopRenderPdfUrlFetcherTests(TestCase):
    """shop.services.invoices.render_pdf must hand WeasyPrint the data:-only URL fetcher, never its default one."""

    def test_render_pdf_passes_the_restricted_url_fetcher(self):
        import sys
        from unittest import mock as _mock

        from shop.services.invoices import render_pdf as _render_pdf

        weasyprint = _mock.MagicMock()
        weasyprint.HTML.return_value.write_pdf.return_value = b"%PDF-1.7"
        fetcher = object()
        with _mock.patch.dict(sys.modules, {"weasyprint": weasyprint}), _mock.patch("shop.services.invoices.pdf_url_fetcher", return_value=fetcher):
            self.assertEqual(_render_pdf("<p>hi</p>"), b"%PDF-1.7")

        weasyprint.HTML.assert_called_once_with(string="<p>hi</p>", url_fetcher=fetcher)


class SafeUrlFetcherTests(TestCase):
    """rosterchief.pdf -- the data:-only URL rule and the WeasyPrint fetcher every PDF uses."""

    def test_non_data_urls_are_refused(self):
        from rosterchief.pdf import safe_url_fetcher

        for url in ["file:///etc/passwd", "FILE:///etc/hosts", "http://169.254.169.254/latest/meta-data/", "https://example.com/logo.png", "ftp://example.com/x", "/etc/passwd", "logo.png"]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                safe_url_fetcher(url)

    def test_data_urls_pass_the_rule(self):
        from rosterchief.pdf import safe_url_fetcher

        self.assertIsNone(safe_url_fetcher("data:text/plain;base64,aGVsbG8="))

    def _native_or_skip(self):
        try:
            import weasyprint  # noqa: F401
        except ImportError, OSError:
            self.skipTest("WeasyPrint's native libraries are not available")

    def test_the_fetcher_resolves_data_urls(self):
        self._native_or_skip()
        from rosterchief.pdf import pdf_url_fetcher

        response = pdf_url_fetcher().fetch("data:text/plain;base64,aGVsbG8=")
        try:
            self.assertEqual(response.read(), b"hello")
        finally:
            response.close()

    def test_the_fetcher_refuses_file_and_http(self):
        self._native_or_skip()
        from rosterchief.pdf import pdf_url_fetcher

        fetcher = pdf_url_fetcher()
        for url in ["file:///etc/hosts", "http://169.254.169.254/", "https://example.com/x.png"]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                fetcher.fetch(url)

    def test_a_refused_resource_is_skipped_not_fatal(self):
        # A real render: the file: image is dropped with a warning, the PDF still comes out.
        self._native_or_skip()
        from weasyprint import HTML

        from rosterchief.pdf import pdf_url_fetcher

        fetcher = pdf_url_fetcher()
        refused = []
        original_fetch = fetcher.fetch

        def spy(url, headers=None):
            try:
                return original_fetch(url, headers)
            except ValueError:
                refused.append(url)
                raise

        fetcher.fetch = spy
        pdf = HTML(string='<p>hi</p><img src="file:///etc/hosts"><img src="data:image/gif;base64,R0lGODlhAQABAAAAACw=">', url_fetcher=fetcher).write_pdf()

        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertEqual(refused, ["file:///etc/hosts"])


class _PlaceOrderTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united", shop_open=True)
        cls.user = User.objects.create_user(email="buyer@example.com", password="pw-secret-123")
        cls.member = Member.objects.create(first_name="Jane", last_name="Doe", email="buyer@example.com", user=cls.user)
        cls.other_user = User.objects.create_user(email="other@example.com", password="pw-secret-123")
        cls.other_member = Member.objects.create(first_name="John", last_name="Roe", email="other@example.com", user=cls.other_user)
        cls.product = Product.objects.create(club=cls.club, name="Home Jersey", price=Decimal("25.00"))

    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        self.addCleanup(cache.clear)
        patcher = patch("shop.services.checkout.dispatch_order_placed_notification")
        patcher.start()
        self.addCleanup(patcher.stop)

    def make_cart(self, quantity=2, user=None, product=None, variant=None, unit_price=None):
        cart, _created = Cart.objects.get_or_create(club=self.club, user=user or self.user, status=Cart.CartStatus.OPEN)
        product = product or self.product
        price = unit_price if unit_price is not None else (variant.effective_price if variant is not None else product.price)
        CartItem.objects.create(cart=cart, product=product, variant=variant, quantity=quantity, unit_price=price)
        return cart


class PercentageDiscountAfterStaffEditTests(_PlaceOrderTestBase):
    """A percentage discount must stay a percentage once it's on an order --
    re-deriving the order total after a staff edit to one of its lines used
    to subtract the stored *rate* (10) as if it were euros."""

    def test_the_applied_discount_stores_the_euro_amount_actually_deducted(self):
        Discount.objects.create(club=self.club, name="Ten off", code="TEN10", discount_type=DiscountType.PERCENTAGE, discount_amount=Decimal("10"))
        order = place_order(self.make_cart(quantity=2), purchaser=self.member, discount_code="TEN10")  # 50.00

        [applied] = order.applied_discounts.all()
        self.assertEqual(applied.amount, Decimal("5.00"))
        self.assertEqual(applied.discount_type, DiscountType.PERCENTAGE)
        self.assertEqual(applied.discount_amount, Decimal("10"))

    def test_order_total_after_a_line_edit_keeps_the_percentage(self):
        Discount.objects.create(club=self.club, name="Ten off", code="TEN10", discount_type=DiscountType.PERCENTAGE, discount_amount=Decimal("10"))
        order = place_order(self.make_cart(quantity=2), purchaser=self.member, discount_code="TEN10")
        self.assertEqual(order_total(order), Decimal("45.00"))  # unchanged order re-derives to what was charged

        [line] = order.order_items.all()
        line.quantity = 3
        line.line_total = line.unit_price * 3  # 75.00
        line.save()

        self.assertEqual(order_total(order), Decimal("67.50"))  # 75.00 - 10%, not 75.00 - €10
        [applied] = order.applied_discounts.all()
        self.assertEqual(applied.amount, Decimal("7.50"))

    def test_a_fixed_discount_stays_fixed_after_a_line_edit(self):
        Discount.objects.create(club=self.club, name="Five off", code="FIVE", discount_type=DiscountType.FIXED_AMOUNT, discount_amount=Decimal("5"))
        order = place_order(self.make_cart(quantity=2), purchaser=self.member, discount_code="FIVE")
        [line] = order.order_items.all()
        line.quantity = 3
        line.line_total = Decimal("75.00")
        line.save()

        self.assertEqual(order_total(order), Decimal("70.00"))


class DiscountCodeLimitTests(_PlaceOrderTestBase):
    """Validity window, total use cap, one use per member, and wrong-code
    throttling -- enforced on preview (find_discount) and again at placement."""

    def make_discount(self, **kwargs):
        defaults = {"club": self.club, "name": "Ten off", "code": "TEN10", "discount_type": DiscountType.FIXED_AMOUNT, "discount_amount": Decimal("10")}
        defaults.update(kwargs)
        return Discount.objects.create(**defaults)

    def assert_generic_invalid(self, **kwargs):
        with self.assertRaisesMessage(CheckoutError, "This code isn't valid."):
            find_discount(self.club, "TEN10", member=self.member, **kwargs)

    def test_unknown_code_gets_the_generic_message(self):
        with self.assertRaisesMessage(CheckoutError, "This code isn't valid."):
            find_discount(self.club, "NOPE", member=self.member)

    def test_not_yet_valid(self):
        self.make_discount(valid_from=timezone.localdate() + timedelta(days=1))
        self.assert_generic_invalid()

    def test_expired(self):
        self.make_discount(valid_until=timezone.localdate() - timedelta(days=1))
        self.assert_generic_invalid()

    def test_valid_on_its_first_and_last_day(self):
        today = timezone.localdate()
        discount = self.make_discount(valid_from=today, valid_until=today)
        self.assertEqual(find_discount(self.club, "ten10", member=self.member), discount)

    def test_used_up(self):
        self.make_discount(max_uses=1)
        place_order(self.make_cart(user=self.other_user), purchaser=self.other_member, discount_code="TEN10")

        self.assert_generic_invalid()

    def test_a_cancelled_order_gives_its_use_back(self):
        self.make_discount(max_uses=1)
        order = place_order(self.make_cart(user=self.other_user), purchaser=self.other_member, discount_code="TEN10")
        order.fulfillment_status = Order.FulfillmentStatus.CANCELLED
        order.save(update_fields=["fulfillment_status"])

        self.assertIsNotNone(find_discount(self.club, "TEN10", member=self.member))

    def test_one_use_per_member(self):
        self.make_discount()
        place_order(self.make_cart(), purchaser=self.member, discount_code="TEN10")

        with self.assertRaisesMessage(CheckoutError, "You've already used this code."):
            find_discount(self.club, "TEN10", member=self.member)
        # Someone else can still use it.
        self.assertIsNotNone(find_discount(self.club, "TEN10", member=self.other_member))

    def test_a_code_allowing_repeat_use_by_the_same_member(self):
        self.make_discount(once_per_member=False)
        place_order(self.make_cart(), purchaser=self.member, discount_code="TEN10")

        self.assertIsNotNone(find_discount(self.club, "TEN10", member=self.member))

    def test_placement_rechecks_the_cap_even_after_a_valid_preview(self):
        # Two members both preview a one-use code, then both place their order.
        self.make_discount(max_uses=1)
        first = self.make_cart(user=self.other_user)
        second = self.make_cart()
        self.assertIsNotNone(find_discount(self.club, "TEN10", member=self.member))

        place_order(first, purchaser=self.other_member, discount_code="TEN10")
        with self.assertRaisesMessage(CheckoutError, "This code isn't valid."):
            place_order(second, purchaser=self.member, discount_code="TEN10")
        self.assertEqual(Order.objects.count(), 1)

    def test_placement_rechecks_one_use_per_member(self):
        self.make_discount()
        place_order(self.make_cart(), purchaser=self.member, discount_code="TEN10")

        with self.assertRaises(CheckoutError):
            place_order(self.make_cart(), purchaser=self.member, discount_code="TEN10")

    def test_wrong_codes_are_throttled_per_member(self):
        self.make_discount()
        for attempt in range(10):
            with self.assertRaisesMessage(CheckoutError, "This code isn't valid."):
                find_discount(self.club, f"WRONG{attempt}", member=self.member, ip=f"10.0.0.{attempt}")

        # Even the right code is refused while the member is locked out...
        with self.assertRaisesMessage(CheckoutError, "Too many wrong codes"):
            find_discount(self.club, "TEN10", member=self.member, ip="10.0.1.1")
        # ...but another member isn't.
        self.assertIsNotNone(find_discount(self.club, "TEN10", member=self.other_member, ip="10.0.1.1"))

    def test_wrong_codes_are_throttled_per_ip(self):
        self.make_discount()
        for attempt in range(10):
            member = self.member if attempt % 2 else self.other_member
            with self.assertRaises(CheckoutError):
                find_discount(self.club, f"WRONG{attempt}", member=member, ip="10.0.0.1")

        with self.assertRaisesMessage(CheckoutError, "Too many wrong codes"):
            find_discount(self.club, "TEN10", member=Member.objects.create(first_name="New", last_name="Person"), ip="10.0.0.1")

    def test_right_codes_are_not_counted_as_wrong_attempts(self):
        self.make_discount(once_per_member=False)
        for _attempt in range(15):
            self.assertIsNotNone(find_discount(self.club, "TEN10", member=self.member, ip="10.0.0.1"))


class PlaceOrderRepricingTests(_PlaceOrderTestBase):
    """A cart line keeps the price it was added at -- place_order re-prices
    every line from the current product/variant and refuses lines that can no
    longer be bought."""

    def test_a_changed_price_is_refreshed_and_the_member_asked_to_confirm(self):
        cart = self.make_cart(quantity=2)
        self.product.price = Decimal("30.00")
        self.product.save(update_fields=["price"])

        with self.assertRaisesMessage(CheckoutError, "price"):
            place_order(cart, purchaser=self.member)
        self.assertFalse(Order.objects.exists())
        self.assertEqual(cart.items.get().unit_price, Decimal("30.00"))

        # Placing again (now at the shown price) goes through at the new price.
        order = place_order(cart, purchaser=self.member)
        self.assertEqual(order.total, Decimal("60.00"))
        self.assertEqual(order.order_items.get().unit_price, Decimal("30.00"))

    def test_a_variant_price_change_is_picked_up(self):
        variant = ProductVariant.objects.create(product=self.product, name="XL", price=Decimal("28.00"))
        cart = self.make_cart(quantity=1, variant=variant)
        variant.price = None  # back to the product's own price
        variant.save(update_fields=["price"])

        with self.assertRaises(CheckoutError):
            place_order(cart, purchaser=self.member)
        self.assertEqual(place_order(cart, purchaser=self.member).total, Decimal("25.00"))

    def assert_refused(self, cart):
        with self.assertRaisesMessage(CheckoutError, "no longer available"):
            place_order(cart, purchaser=self.member)
        self.assertFalse(Order.objects.exists())
        cart.refresh_from_db()
        self.assertEqual(cart.status, Cart.CartStatus.OPEN)

    def test_an_inactive_product_is_refused(self):
        cart = self.make_cart()
        Product.objects.filter(pk=self.product.pk).update(is_active=False)
        self.assert_refused(cart)

    def test_a_product_taken_out_of_the_shop_is_refused(self):
        cart = self.make_cart()
        Product.objects.filter(pk=self.product.pk).update(is_public=False)
        self.assert_refused(cart)

    def test_a_membership_product_is_refused(self):
        cart = self.make_cart()
        Product.objects.filter(pk=self.product.pk).update(product_type=Product.ProductType.MEMBERSHIP)
        self.assert_refused(cart)

    def test_an_inactive_variant_is_refused(self):
        variant = ProductVariant.objects.create(product=self.product, name="XL")
        cart = self.make_cart(variant=variant)
        ProductVariant.objects.filter(pk=variant.pk).update(is_active=False)
        self.assert_refused(cart)

    def test_an_already_checked_out_cart_cannot_be_ordered_twice(self):
        cart = self.make_cart()
        stale = Cart.objects.get(pk=cart.pk)
        place_order(cart, purchaser=self.member)

        with self.assertRaises(CheckoutError):
            place_order(stale, purchaser=self.member)
        self.assertEqual(Order.objects.count(), 1)


class VoucherConcurrencyTests(TestCase):
    """record_payment/record_manual_consumption lock the voucher row and
    re-check its balance inside the transaction -- a stale in-memory voucher
    (what a second, concurrent request or a double submit holds) must not be
    able to spend the same balance twice. True parallelism isn't reproducible
    on SQLite; these prove the re-read-under-lock logic deterministically."""

    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        cls.member = Member.objects.create(first_name="Jane", last_name="Doe")

    def setUp(self):
        self.voucher = Voucher.objects.create(club=self.club, amount=Decimal("30.00"), expiry_date=timezone.localdate() + timedelta(days=1))
        self.order_a = Order.objects.create(club=self.club, purchaser=self.member, total=Decimal("30.00"))
        self.order_b = Order.objects.create(club=self.club, purchaser=self.member, total=Decimal("30.00"))

    def test_a_stale_voucher_instance_cannot_overspend_through_record_payment(self):
        first, second = Voucher.objects.get(pk=self.voucher.pk), Voucher.objects.get(pk=self.voucher.pk)
        record_payment(self.order_a, amount=Decimal("30.00"), method=Payment.PaymentMethod.VOUCHER, voucher=first)

        with self.assertRaises(PaymentError):
            record_payment(self.order_b, amount=Decimal("30.00"), method=Payment.PaymentMethod.VOUCHER, voucher=second)

        self.voucher.refresh_from_db()
        self.assertEqual(self.voucher.consumed_amount, Decimal("30.00"))
        self.assertEqual(Payment.objects.filter(voucher=self.voucher).count(), 1)

    def test_partial_payments_through_stale_instances_add_up(self):
        first, second = Voucher.objects.get(pk=self.voucher.pk), Voucher.objects.get(pk=self.voucher.pk)
        record_payment(self.order_a, amount=Decimal("10.00"), method=Payment.PaymentMethod.VOUCHER, voucher=first)
        record_payment(self.order_b, amount=Decimal("15.00"), method=Payment.PaymentMethod.VOUCHER, voucher=second)

        self.voucher.refresh_from_db()
        self.assertEqual(self.voucher.consumed_amount, Decimal("25.00"))
        self.assertEqual(second.consumed_amount, Decimal("25.00"))

    def test_a_stale_voucher_instance_cannot_overspend_through_manual_consumption(self):
        from .services.vouchers import record_manual_consumption

        first, second = Voucher.objects.get(pk=self.voucher.pk), Voucher.objects.get(pk=self.voucher.pk)
        record_manual_consumption(first, amount=Decimal("20.00"), note="cash")

        with self.assertRaises(PaymentError):
            record_manual_consumption(second, amount=Decimal("20.00"), note="cash again")
        self.voucher.refresh_from_db()
        self.assertEqual(self.voucher.consumed_amount, Decimal("20.00"))

    def test_deleting_a_voucher_payment_restores_onto_the_current_balance(self):
        from .services.payments import delete_payment

        stale = Voucher.objects.get(pk=self.voucher.pk)
        payment = record_payment(self.order_a, amount=Decimal("10.00"), method=Payment.PaymentMethod.VOUCHER, voucher=stale)
        loaded_before = Payment.objects.select_related("voucher").get(pk=payment.pk)  # voucher shows 10.00 consumed
        record_payment(self.order_b, amount=Decimal("5.00"), method=Payment.PaymentMethod.VOUCHER, voucher=Voucher.objects.get(pk=self.voucher.pk))

        delete_payment(loaded_before)

        self.voucher.refresh_from_db()
        self.assertEqual(self.voucher.consumed_amount, Decimal("5.00"))

    def test_deleting_the_same_voucher_payment_twice_restores_it_once(self):
        from .services.payments import delete_payment

        payment = record_payment(self.order_a, amount=Decimal("10.00"), method=Payment.PaymentMethod.VOUCHER, voucher=Voucher.objects.get(pk=self.voucher.pk))
        record_payment(self.order_b, amount=Decimal("5.00"), method=Payment.PaymentMethod.VOUCHER, voucher=Voucher.objects.get(pk=self.voucher.pk))
        first, second = Payment.objects.select_related("voucher").get(pk=payment.pk), Payment.objects.select_related("voucher").get(pk=payment.pk)  # both requests loaded it before either deleted it

        delete_payment(first)
        delete_payment(second)

        self.voucher.refresh_from_db()
        self.assertEqual(self.voucher.consumed_amount, Decimal("5.00"))  # the other payment's 5.00 stays used
        self.assertFalse(Payment.objects.filter(pk=payment.pk).exists())

    def test_a_voucher_payment_refunded_in_the_meantime_is_not_credited_back(self):
        from .services.payments import delete_payment

        payment = record_payment(self.order_a, amount=Decimal("10.00"), method=Payment.PaymentMethod.VOUCHER, voucher=Voucher.objects.get(pk=self.voucher.pk))
        loaded = Payment.objects.select_related("voucher").get(pk=payment.pk)  # still shows confirmed
        Payment.objects.filter(pk=payment.pk).update(status=Payment.PaymentStatus.REFUNDED)

        delete_payment(loaded)

        self.voucher.refresh_from_db()
        self.assertEqual(self.voucher.consumed_amount, Decimal("10.00"))

    def test_deleting_the_same_manual_consumption_twice_restores_it_once(self):
        from .models import VoucherConsumption
        from .services.vouchers import delete_manual_consumption, record_manual_consumption

        consumption = record_manual_consumption(Voucher.objects.get(pk=self.voucher.pk), amount=Decimal("10.00"), note="cash")
        record_payment(self.order_a, amount=Decimal("5.00"), method=Payment.PaymentMethod.VOUCHER, voucher=Voucher.objects.get(pk=self.voucher.pk))
        first, second = VoucherConsumption.objects.select_related("voucher").get(pk=consumption.pk), VoucherConsumption.objects.select_related("voucher").get(pk=consumption.pk)

        delete_manual_consumption(first)
        delete_manual_consumption(second)

        self.voucher.refresh_from_db()
        self.assertEqual(self.voucher.consumed_amount, Decimal("5.00"))


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "testserver"])
class ShopCartRepricingAndThrottleViewTests(TestCase):
    """mobile.views.ShopCartView/ShopCheckoutView on top of the checkout
    service: current prices on the cart, withdrawn items flagged, and wrong
    codes throttled per client IP."""

    @classmethod
    def setUpTestData(cls):
        from club.models import ClubRole
        from rosterchief.test_support import make_club

        cls.club = make_club(shop_open=True, secondary_color="#e4002b")
        get_waffle_flag_model().objects.get_or_create(name="shop")[0].clubs.add(cls.club)
        cls.user = User.objects.create_user(email="parent@example.com", password="pw-secret-123")
        cls.member = Member.objects.create(first_name="Lars", last_name="Bakker", email="parent@example.com", user=cls.user)
        ClubRole.objects.create(club=cls.club, member=cls.member)
        cls.product = Product.objects.create(club=cls.club, name="Home Jersey", price=Decimal("25.00"))

    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        self.addCleanup(cache.clear)
        self.cart = Cart.objects.create(club=self.club, user=self.user)
        self.item = CartItem.objects.create(cart=self.cart, product=self.product, quantity=2, unit_price=Decimal("20.00"))
        self.client.force_login(self.user)

    def get_cart(self, **params):
        from django.urls import reverse

        return self.client.get(reverse("mobile:shop_cart"), params, HTTP_HOST="ajax-united.rosterchief.app")

    def test_the_cart_shows_the_current_price(self):
        response = self.get_cart()

        self.assertEqual(response.context["totals"]["subtotal"], Decimal("50.00"))
        self.assertTrue(response.context["prices_changed"])
        self.item.refresh_from_db()
        self.assertEqual(self.item.unit_price, Decimal("25.00"))

    def test_a_withdrawn_product_is_flagged(self):
        Product.objects.filter(pk=self.product.pk).update(is_active=False)

        response = self.get_cart()

        self.assertTrue(response.context["has_unavailable"])
        self.assertContains(response, "Sold out or no longer available")

    def test_checkout_with_a_changed_price_goes_back_to_the_cart(self):
        from django.urls import reverse

        response = self.client.post(reverse("mobile:shop_checkout"), {}, HTTP_HOST="ajax-united.rosterchief.app")

        self.assertRedirects(response, reverse("mobile:shop_cart"), fetch_redirect_response=False)
        self.assertFalse(Order.objects.exists())

    def test_wrong_codes_are_throttled_per_ip(self):
        Discount.objects.create(club=self.club, name="Ten off", code="TEN10", discount_amount=Decimal("10"))
        for attempt in range(10):
            self.assertEqual(self.get_cart(code=f"WRONG{attempt}").context["discount_error"], "This code isn't valid.")

        response = self.get_cart(code="TEN10")

        self.assertIn("Too many wrong codes", response.context["discount_error"])
        self.assertIsNone(response.context["discount"])


class InventoryTests(TestCase):
    """Inventory-managed products: stock is the sum of InventoryMovements, orders draw
    it down and skip production, and a sold-out product/variant can't be ordered."""

    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united", shop_open=True)
        cls.user = User.objects.create_user(email="shopper@example.com", password="pw-secret-123")
        cls.member = Member.objects.create(first_name="Jane", last_name="Doe", email="shopper@example.com", user=cls.user)
        cls.scarf = Product.objects.create(club=cls.club, name="Scarf", price=Decimal("10.00"), inventory_managed=True)
        cls.jersey = Product.objects.create(club=cls.club, name="Jersey", price=Decimal("25.00"))
        cls.small = ProductVariant.objects.create(product=cls.scarf, name="Small")

    def setUp(self):
        patcher = patch("shop.services.checkout.dispatch_order_placed_notification")
        patcher.start()
        self.addCleanup(patcher.stop)

    def stock(self, product, quantity, variant=None):
        receive_stock(self.club, {(product, variant): quantity})

    def cart_with(self, *items):
        cart = Cart.objects.create(club=self.club, user=self.user)
        for product, variant, quantity in items:
            CartItem.objects.create(cart=cart, product=product, variant=variant, quantity=quantity, unit_price=product.price)
        return cart

    def test_only_merchandise_can_be_inventory_managed(self):
        product = Product(club=self.club, name="Dues", product_type=Product.ProductType.MEMBERSHIP, inventory_managed=True)

        with self.assertRaises(ValidationError) as caught:
            product.full_clean(exclude=["slug"])

        self.assertIn("inventory_managed", caught.exception.message_dict)

    def test_stock_is_the_sum_of_movements(self):
        self.stock(self.scarf, 10, self.small)
        set_stock(self.club, self.scarf, self.small, 7)

        self.assertEqual(stock_for(self.scarf, self.small), 7)
        self.assertEqual(stock_for(self.scarf, None), 0)

    def test_receiving_skips_unmanaged_products_and_bad_quantities(self):
        movements = receive_stock(self.club, {(self.jersey, None): 5, (self.scarf, self.small): 0})

        self.assertEqual(movements, [])

    def test_a_product_with_variants_is_stocked_per_variant(self):
        self.assertEqual(receive_stock(self.club, {(self.scarf, None): 5}), [])
        self.assertEqual(len(receive_stock(self.club, {(self.scarf, self.small): 5})), 1)

    def test_a_correction_to_the_same_count_records_nothing(self):
        self.stock(self.scarf, 4, self.small)

        self.assertIsNone(set_stock(self.club, self.scarf, self.small, 4))

    def test_a_negative_count_is_refused(self):
        with self.assertRaises(InventoryError):
            set_stock(self.club, self.scarf, self.small, -1)

    def test_a_managed_order_takes_stock_and_is_received_without_production(self):
        self.stock(self.scarf, 5, self.small)

        order = place_order(self.cart_with((self.scarf, self.small, 2)), purchaser=self.member)

        [line] = order.order_items.all()
        self.assertEqual(line.production_status, ProductionStatus.RECEIVED)
        self.assertEqual(order.production_status, ProductionStatus.RECEIVED)
        self.assertEqual(stock_for(self.scarf, self.small), 3)
        self.assertFalse(pending_production_lines([self.scarf]).exists())

    def test_an_unmanaged_line_still_goes_through_production(self):
        self.stock(self.scarf, 5, self.small)

        order = place_order(self.cart_with((self.scarf, self.small, 1), (self.jersey, None, 1)), purchaser=self.member)

        statuses = {line.product.name: line.production_status for line in order.order_items.select_related("product")}
        self.assertEqual(statuses, {"Scarf": ProductionStatus.RECEIVED, "Jersey": ProductionStatus.PENDING})
        self.assertEqual(order.production_status, ProductionStatus.IN_PRODUCTION)

    def test_a_sold_out_item_refuses_checkout_and_leaves_stock_alone(self):
        self.stock(self.scarf, 1, self.small)

        with self.assertRaises(CheckoutError):
            place_order(self.cart_with((self.scarf, self.small, 2)), purchaser=self.member)

        self.assertEqual(stock_for(self.scarf, self.small), 1)
        self.assertFalse(Order.objects.exists())

    def test_the_same_item_on_several_lines_is_counted_together(self):
        self.stock(self.scarf, 3, self.small)
        cart = self.cart_with((self.scarf, self.small, 2))
        other = Member.objects.create(first_name="Kid", last_name="Doe")
        CartItem.objects.create(cart=cart, product=self.scarf, variant=self.small, beneficiary=other, quantity=2, unit_price=self.scarf.price)

        with self.assertRaises(CheckoutError):
            place_order(cart, purchaser=self.member)

        self.assertEqual(stock_for(self.scarf, self.small), 3)

    def test_the_last_item_can_be_ordered_and_then_nothing_is_left(self):
        self.stock(self.scarf, 2, self.small)

        place_order(self.cart_with((self.scarf, self.small, 2)), purchaser=self.member)

        self.assertEqual(stock_for(self.scarf, self.small), 0)
        with self.assertRaises(CheckoutError):
            add_to_cart(self.club, self.user, self.scarf, variant=self.small)

    def test_adding_to_the_cart_is_capped_at_what_is_in_stock(self):
        self.stock(self.scarf, 3, self.small)

        item = add_to_cart(self.club, self.user, self.scarf, quantity=5, variant=self.small)
        self.assertEqual(item.quantity, 3)

        with self.assertRaises(CheckoutError):
            add_to_cart(self.club, self.user, self.scarf, quantity=1, variant=self.small)

    def test_new_stock_makes_a_sold_out_item_orderable_again(self):
        with self.assertRaises(CheckoutError):
            add_to_cart(self.club, self.user, self.scarf, variant=self.small)

        self.stock(self.scarf, 1, self.small)

        self.assertEqual(add_to_cart(self.club, self.user, self.scarf, variant=self.small).quantity, 1)

    def test_a_cart_line_that_sells_out_is_unavailable(self):
        self.stock(self.scarf, 1, self.small)
        cart = self.cart_with((self.scarf, self.small, 1))
        set_stock(self.club, self.scarf, self.small, 0)

        with self.assertRaises(CheckoutError) as caught:
            place_order(cart, purchaser=self.member)

        self.assertIn("Scarf", str(caught.exception))

    def test_unmanaged_products_are_never_limited(self):
        self.assertEqual(add_to_cart(self.club, self.user, self.jersey, quantity=50).quantity, 50)

    def test_availability_is_annotated_per_variant(self):
        large = ProductVariant.objects.create(product=self.scarf, name="Large")
        self.stock(self.scarf, 1, large)

        [scarf, jersey] = annotate_availability(Product.objects.filter(club=self.club).prefetch_related("variants").order_by("-name"))

        self.assertEqual(scarf.name, "Scarf")
        self.assertTrue(scarf.in_stock)
        self.assertEqual({variant.name: variant.in_stock for variant in scarf.variants.all()}, {"Small": False, "Large": True})
        self.assertTrue(jersey.in_stock)

    def test_a_sold_out_product_without_variants_is_not_in_stock(self):
        hat = Product.objects.create(club=self.club, name="Hat", inventory_managed=True)

        self.assertFalse(annotate_availability(Product.objects.filter(pk=hat.pk).prefetch_related("variants"))[0].in_stock)

    def test_cancelling_returns_stock_once(self):
        self.stock(self.scarf, 5, self.small)
        order = place_order(self.cart_with((self.scarf, self.small, 2)), purchaser=self.member)

        return_order_to_stock(order)
        return_order_to_stock(order)

        self.assertEqual(stock_for(self.scarf, self.small), 5)

    def test_editing_a_line_moves_stock_between_variants(self):
        large = ProductVariant.objects.create(product=self.scarf, name="Large")
        self.stock(self.scarf, 5, self.small)
        self.stock(self.scarf, 5, large)
        order = place_order(self.cart_with((self.scarf, self.small, 2)), purchaser=self.member)
        line = order.order_items.get()

        line.variant, line.quantity = large, 3
        sync_line_stock(line, old_variant_id=self.small.pk)

        self.assertEqual(stock_for(self.scarf, self.small), 5)
        self.assertEqual(stock_for(self.scarf, large), 2)

    def test_editing_a_line_beyond_stock_is_refused_and_changes_nothing(self):
        self.stock(self.scarf, 3, self.small)
        order = place_order(self.cart_with((self.scarf, self.small, 2)), purchaser=self.member)
        line = order.order_items.get()

        line.quantity = 5
        with self.assertRaises(InventoryError):
            sync_line_stock(line, old_variant_id=self.small.pk)

        self.assertEqual(stock_for(self.scarf, self.small), 1)

    def test_the_overview_counts_what_open_orders_hold(self):
        self.stock(self.scarf, 5, self.small)
        place_order(self.cart_with((self.scarf, self.small, 2)), purchaser=self.member)

        [row] = inventory_overview(self.club)

        self.assertEqual((row.on_hand, row.committed), (3, 2))


class DefaultVariantTests(TestCase):
    def test_registration_product_without_options_gets_one_named_after_it(self):
        from shop.services.variants import ensure_default_variant

        club = Club.objects.create(name="Default Variant FC", slug="default-variant-fc")
        product = Product.objects.create(club=club, name="Weekly training", product_type=Product.ProductType.MEMBERSHIP, price=410)
        variant = ensure_default_variant(product)
        self.assertEqual(variant.full_name, "Weekly training")
        self.assertEqual(variant.effective_price, 410)
        self.assertIsNone(ensure_default_variant(product))  # idempotent

    def test_merchandise_and_products_with_options_are_left_alone(self):
        from shop.services.variants import ensure_default_variant

        club = Club.objects.create(name="Default Variant FC 2", slug="default-variant-fc-2")
        merch = Product.objects.create(club=club, name="Cap", product_type=Product.ProductType.MERCHANDISE, price=10)
        self.assertIsNone(ensure_default_variant(merch))
        membership = Product.objects.create(club=club, name="Player", product_type=Product.ProductType.MEMBERSHIP, price=10)
        ProductVariant.objects.create(product=membership, name="U12")
        self.assertIsNone(ensure_default_variant(membership))
        self.assertEqual(membership.variants.count(), 1)
