from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.admin.sites import AdminSite
from django.core import mail
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db.models import ProtectedError
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from authentication.models import User
from club.models import Club, ClubMembership, Season
from club.tenancy import reset_current_club, set_current_club
from members.models import Member
from notifications.models import Notification
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
    ProductVariant,
)
from .services.checkout import CheckoutError, find_discount, place_order
from .services.invoices import ShopInvoicePDFError, create_invoice_for_order, invalidate_cached_invoice_pdf, render_invoice_pdf
from .services.notifications import dispatch_order_ready_for_pickup_notification
from .services.pricing import cart_totals
from .services.stats import order_kpis, quantity_sold_by_product, quantity_sold_by_variant


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

        order.status = Order.OrderStatus.PAID
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
    """shop.services.notifications.dispatch_order_ready_for_pickup_notification --
    the purchaser told (app + email) their order is ready to collect."""

    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        cls.member = Member.objects.create(first_name="Jane", last_name="Doe", email="shopper@example.com", user=User.objects.create_user(email="shopper@example.com", password="pw-secret-123"))
        cls.order = Order.objects.create(club=cls.club, purchaser=cls.member, status=Order.OrderStatus.READY_FOR_PICKUP, total=Decimal("50.00"))

    def test_creates_an_in_app_notification(self):
        dispatch_order_ready_for_pickup_notification(self.order)

        notification = Notification.objects.get(member=self.member)
        self.assertIn(self.order.number, notification.title)
        self.assertIn("ready", notification.title.lower())
        self.assertEqual(notification.source, self.order)

    def test_emails_the_purchaser(self):
        dispatch_order_ready_for_pickup_notification(self.order)

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["shopper@example.com"])

    def test_pickup_instructions_are_folded_into_the_body_when_set(self):
        self.order.pickup_instructions = "Ask for it at the clubhouse desk."
        self.order.save(update_fields=["pickup_instructions"])

        dispatch_order_ready_for_pickup_notification(self.order)

        notification = Notification.objects.get(member=self.member)
        self.assertIn("Ask for it at the clubhouse desk.", notification.body)

    def test_no_instructions_is_a_plain_body(self):
        dispatch_order_ready_for_pickup_notification(self.order)

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


class OrderKPIsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        cls.member = Member.objects.create(first_name="Jane", last_name="Doe")
        cls.product = Product.objects.create(club=cls.club, name="Home Jersey", price=Decimal("25.00"))

    def make_order(self, status, total=Decimal("50.00"), quantity=2):
        order = Order.objects.create(club=self.club, purchaser=self.member, status=status, total=total)
        OrderLine.objects.create(order=order, product=self.product, quantity=quantity, unit_price=Decimal("25.00"), line_total=total)
        return order

    def test_open_orders_excludes_delivered_cancelled_and_refunded(self):
        self.make_order(Order.OrderStatus.PENDING)
        self.make_order(Order.OrderStatus.PAID)
        self.make_order(Order.OrderStatus.PARTIALLY_PAID)
        self.make_order(Order.OrderStatus.DELIVERED)
        self.make_order(Order.OrderStatus.CANCELLED)
        self.make_order(Order.OrderStatus.REFUNDED)

        self.assertEqual(order_kpis(self.club)["open_orders"], 3)

    def test_total_orders_counts_every_status(self):
        self.make_order(Order.OrderStatus.PENDING)
        self.make_order(Order.OrderStatus.CANCELLED)

        self.assertEqual(order_kpis(self.club)["total_orders"], 2)

    def test_total_sold_only_counts_paid_and_delivered(self):
        self.make_order(Order.OrderStatus.PAID, total=Decimal("30.00"))
        self.make_order(Order.OrderStatus.DELIVERED, total=Decimal("20.00"))
        self.make_order(Order.OrderStatus.PENDING, total=Decimal("999.00"))
        self.make_order(Order.OrderStatus.CANCELLED, total=Decimal("999.00"))

        self.assertEqual(order_kpis(self.club)["total_sold"], Decimal("50.00"))

    def test_items_sold_matches_total_sold_scope(self):
        self.make_order(Order.OrderStatus.PAID, quantity=3)
        self.make_order(Order.OrderStatus.PENDING, quantity=99)

        self.assertEqual(order_kpis(self.club)["items_sold"], 3)

    def test_zero_orders_returns_zero_not_none(self):
        kpis = order_kpis(self.club)

        self.assertEqual(kpis, {"open_orders": 0, "total_orders": 0, "total_sold": Decimal("0"), "items_sold": 0})

    def test_kpis_are_scoped_to_the_club(self):
        other_club = Club.objects.create(name="Rival FC", slug="rival-fc")
        Order.objects.create(club=other_club, purchaser=self.member, status=Order.OrderStatus.PAID, total=Decimal("100.00"))

        self.assertEqual(order_kpis(self.club)["total_orders"], 0)


class QuantitySoldTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        cls.member = Member.objects.create(first_name="Jane", last_name="Doe")
        cls.product = Product.objects.create(club=cls.club, name="Home Jersey", price=Decimal("25.00"))
        cls.small = ProductVariant.objects.create(product=cls.product, name="Small")
        cls.large = ProductVariant.objects.create(product=cls.product, name="Large")

    def make_line(self, status, quantity, variant=None, product=None):
        order = Order.objects.create(club=self.club, purchaser=self.member, status=status, total=Decimal("1.00"))
        OrderLine.objects.create(order=order, product=product or self.product, variant=variant, quantity=quantity, unit_price=Decimal("25.00"), line_total=Decimal("1.00"))

    def test_only_paid_and_delivered_lines_count(self):
        self.make_line(Order.OrderStatus.PAID, 2)
        self.make_line(Order.OrderStatus.DELIVERED, 1)
        self.make_line(Order.OrderStatus.PENDING, 99)
        self.make_line(Order.OrderStatus.CANCELLED, 99)

        self.assertEqual(quantity_sold_by_product(self.club), {self.product.pk: 3})

    def test_products_with_no_sales_are_absent_not_zero(self):
        other_product = Product.objects.create(club=self.club, name="Away Jersey")

        self.assertNotIn(other_product.pk, quantity_sold_by_product(self.club))

    def test_variant_breakdown_is_per_variant(self):
        self.make_line(Order.OrderStatus.PAID, 2, variant=self.small)
        self.make_line(Order.OrderStatus.PAID, 5, variant=self.large)
        self.make_line(Order.OrderStatus.PAID, 1)  # no variant -- base sale

        breakdown = quantity_sold_by_variant(self.product)

        self.assertEqual(breakdown, {self.small.pk: 2, self.large.pk: 5})

    def test_another_products_sales_dont_leak_into_this_breakdown(self):
        other_product = Product.objects.create(club=self.club, name="Away Jersey")
        other_variant = ProductVariant.objects.create(product=other_product, name="Small")
        self.make_line(Order.OrderStatus.PAID, 4, variant=other_variant, product=other_product)

        self.assertEqual(quantity_sold_by_variant(self.product), {})


class InvoicePdfCachingTests(TestCase):
    """render_invoice_pdf's own caching, and shop.signals' invalidation of it
    on Order.status change -- see that module's own docstring."""

    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        cls.member = Member.objects.create(first_name="Jane", last_name="Doe")
        cls.order = Order.objects.create(club=cls.club, purchaser=cls.member, status=Order.OrderStatus.PENDING, total=Decimal("25.00"))
        cls.invoice = create_invoice_for_order(cls.order)

    def setUp(self):
        invalidate_cached_invoice_pdf(self.invoice)

    def test_a_second_render_reuses_the_cached_copy(self):
        with patch("shop.services.invoices.render_pdf", side_effect=lambda html: b"%PDF-fake") as renderer:
            first = render_invoice_pdf(self.invoice)
            second = render_invoice_pdf(self.invoice)

        renderer.assert_called_once()
        self.assertEqual(first, second)

    def test_changing_the_orders_status_busts_the_cache(self):
        with patch("shop.services.invoices.render_pdf", side_effect=lambda html: b"%PDF-fake"):
            render_invoice_pdf(self.invoice)

        self.order.status = Order.OrderStatus.PAID
        self.order.save(update_fields=["status"])

        with patch("shop.services.invoices.render_pdf", side_effect=lambda html: b"%PDF-fake-2") as renderer:
            second = render_invoice_pdf(self.invoice)

        renderer.assert_called_once()
        self.assertEqual(second, b"%PDF-fake-2")

    def test_saving_an_order_without_a_status_change_keeps_the_cache(self):
        with patch("shop.services.invoices.render_pdf", side_effect=lambda html: b"%PDF-fake"):
            render_invoice_pdf(self.invoice)

        self.order.save(update_fields=["status"])  # same status, re-saved

        with patch("shop.services.invoices.render_pdf") as renderer:
            render_invoice_pdf(self.invoice)

        renderer.assert_not_called()

    def test_a_freshly_created_order_has_nothing_cached_to_invalidate(self):
        # pre_save's own pk-less branch -- must not raise on a brand-new Order.
        Order.objects.create(club=self.club, purchaser=self.member, status=Order.OrderStatus.PENDING, total=Decimal("1"))  # must not raise
