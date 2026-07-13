from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.admin.sites import AdminSite
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db.models import ProtectedError
from django.test import RequestFactory, TestCase
from django.utils import timezone

from authentication.models import User
from club.models import Club, ClubMembership, Season
from club.tenancy import reset_current_club, set_current_club
from members.models import Member
from teams.models import Position, Team

from .admin import ProductAdmin
from .models import (
    AppliedDiscount,
    Cart,
    CartItem,
    Discount,
    Invoice,
    Order,
    OrderLine,
    Payment,
    Product,
)


class ProductSlugTests(TestCase):
    def setUp(self):
        self.club = Club.objects.create(name="Ajax United", slug="ajax-united")

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


class OpenCartConstraintTests(TestCase):
    def setUp(self):
        self.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        self.user = User.objects.create_user(email="shopper@example.com", password="pw")

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
    def setUp(self):
        self.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        self.member = Member.objects.create(first_name="Jane", last_name="Doe")
        self.year = timezone.now().year

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
    def setUp(self):
        self.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        self.member = Member.objects.create(first_name="Jane", last_name="Doe")
        self.product = Product.objects.create(club=self.club, name="Home Jersey")
        self.order = Order.objects.create(club=self.club, purchaser=self.member, total=Decimal("50.00"))
        self.year = timezone.now().year


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
        discount = Discount.objects.create(club=self.club, name="Sibling discount")

        self.assertEqual(discount.slug, "sibling-discount")
        self.assertEqual(str(discount), "Sibling discount")

    def test_slug_is_unique_per_club(self):
        Discount.objects.create(club=self.club, name="Sibling")
        second = Discount.objects.create(club=self.club, name="Sibling")

        self.assertEqual(second.slug, "sibling-2")


class AppliedDiscountTests(ShopEntitiesTestBase):
    def setUp(self):
        super().setUp()
        self.discount = Discount.objects.create(club=self.club, name="Sibling")

    def apply(self, **kwargs):
        kwargs.setdefault("order", self.order)
        kwargs.setdefault("discount", self.discount)
        kwargs.setdefault("discount_amount", Decimal("10.00"))
        return AppliedDiscount.objects.create(**kwargs)

    def test_str_percentage_shows_percent(self):
        applied = self.apply(discount_type=AppliedDiscount.DiscountType.PERCENTAGE)

        self.assertEqual(str(applied), "Sibling - 10.00%")

    def test_str_fixed_amount_has_no_percent(self):
        applied = self.apply(discount_type=AppliedDiscount.DiscountType.FIXED_AMOUNT)

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
    def setUp(self):
        self.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        self.other = Club.objects.create(name="Rival FC", slug="rival-fc")
        today = timezone.localdate()
        self.season = Season.objects.create(club=self.club, start_date=today, end_date=today + timedelta(days=300))
        self.other_season = Season.objects.create(club=self.other, start_date=today, end_date=today + timedelta(days=300))
        self.member = Member.objects.create(first_name="Jane", last_name="Doe")
        ClubMembership.objects.create(club=self.club, member=self.member, season=self.season)
        self.stranger = Member.objects.create(first_name="Stray", last_name="Ger")

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
        discount = Discount.objects.create(club=self.other, name="Sibling")
        applied = AppliedDiscount(order=order, discount=discount, discount_amount=Decimal("5"))
        with self.assertRaises(ValidationError) as ctx:
            applied.full_clean()
        self.assertIn("discount", ctx.exception.error_dict)

    def test_applieddiscount_rejects_non_member_applied_by(self):
        order = self.make_order(self.club)
        discount = Discount.objects.create(club=self.club, name="Sibling")
        applied = AppliedDiscount(order=order, discount=discount, discount_amount=Decimal("5"), applied_by=self.stranger)
        with self.assertRaises(ValidationError) as ctx:
            applied.full_clean()
        self.assertIn("applied_by", ctx.exception.error_dict)

    def test_applieddiscount_is_unique_per_order(self):
        order = self.make_order(self.club)
        discount = Discount.objects.create(club=self.club, name="Sibling")
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
    def setUp(self):
        self.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        self.other = Club.objects.create(name="Rival FC", slug="rival-fc")
        today = timezone.localdate()
        self.season = Season.objects.create(club=self.club, start_date=today, end_date=today + timedelta(days=300))
        self.other_season = Season.objects.create(club=self.other, start_date=today, end_date=today + timedelta(days=300))

    def test_fk_dropdown_scoped_to_object_club(self):
        product = Product.objects.create(club=self.club, name="Jersey")
        admin_obj = ProductAdmin(Product, AdminSite())
        request = RequestFactory().get("/")
        request._club_obj = product

        field = admin_obj.formfield_for_foreignkey(Product._meta.get_field("season"), request)

        self.assertIn(self.season, field.queryset)
        self.assertNotIn(self.other_season, field.queryset)
