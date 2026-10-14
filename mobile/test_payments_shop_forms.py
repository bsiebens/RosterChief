"""The member web app's Payments & dues, Shop, Forms and Re-register screens, brought in line with the
native app (card shapes, copy, states). The older, behaviour-level tests for these screens live in
mobile/tests.py; this file covers what the native-app parity pass added."""

import datetime
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from waffle import get_waffle_flag_model

from club.models import ClubMembership, ClubRole, DuesInvoice, Season
from formbuilder.models import Answer, FormSend, Submission
from formbuilder.models import Field as FormBuilderField
from formbuilder.models import Form as FormBuilderForm
from members.models import Family, FamilyMembership, Member
from registration.models import RegistrationBatch, RegistrationDetails
from rosterchief.test_support import make_club as _make_club
from shop.models import AppliedDiscount, Cart, CartItem, Discount, InventoryMovement, Order, OrderLine, Product, ProductCategory, ProductVariant
from teams.models import NumberPool, Team, TeamMembership

User = get_user_model()

HOST = "ajax-united.rosterchief.app"
SETTINGS = {"ROSTERCHIEF_BASE_DOMAIN": "rosterchief.app", "ALLOWED_HOSTS": ["rosterchief.app", HOST, "testserver"]}


def make_club(**kwargs):
    return _make_club(secondary_color="#e4002b", **kwargs)


def stock(product, variant, quantity):
    InventoryMovement.objects.create(product=product, variant=variant, quantity=quantity, reason=InventoryMovement.Reason.RECEIVED)


@override_settings(**SETTINGS)
class PaymentsCardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.club = make_club(payment_instructions="Transfer to BE12 3456 with the invoice number.")
        today = timezone.localdate()
        cls.season = Season.objects.create(club=cls.club, start_date=today - datetime.timedelta(days=30), end_date=today + datetime.timedelta(days=300))
        cls.user = User.objects.create_user(email="parent@example.com", password="pw-secret-123")
        cls.member = Member.objects.create(first_name="Lars", last_name="Bakker", email="parent@example.com", user=cls.user)
        cls.membership = ClubMembership.objects.create(club=cls.club, member=cls.member, season=cls.season)
        cls.product = Product.objects.create(club=cls.club, name="Player Registration", product_type=Product.ProductType.MEMBERSHIP, season=cls.season, price=Decimal("150.00"))
        cls.u10 = ProductVariant.objects.create(product=cls.product, name="U10", price=Decimal("150.00"))
        cls.team = Team.objects.create(club=cls.club, name="U10 Boys", short_name="U10B")

    def setUp(self):
        self.client.force_login(self.user)

    def _get(self):
        return self.client.get(reverse("mobile:payments"), HTTP_HOST=HOST)

    def _confirmed_batch(self, **kwargs):
        return RegistrationBatch.objects.create(
            club=self.club,
            season=self.season,
            contact_first_name="Lars",
            contact_last_name="Bakker",
            contact_email="parent@example.com",
            invoice_number="REG-1",
            invoice_sent_at=timezone.now(),
            invoice_due_date=timezone.localdate() + datetime.timedelta(days=14),
            **kwargs,
        )

    def test_a_registration_card_lists_lines_with_team_and_discount_and_a_bold_total(self):
        self.membership.fee_amount = Decimal("135.00")
        self.membership.save()
        batch = self._confirmed_batch()
        RegistrationDetails.objects.create(membership=self.membership, batch=batch, product_variant=self.u10, requested_team=self.team, price=Decimal("150.00"), discount_amount=Decimal("15.00"))

        response = self._get()

        self.assertEqual(response.context["cards"][0]["kind"], "registration")
        self.assertContains(response, "Registration")
        self.assertContains(response, str(self.team))
        self.assertContains(response, "Player Registration")
        self.assertContains(response, "To pay")
        self.assertContains(response, "&minus;&euro; 15.00")
        self.assertContains(response, reverse("mobile:registration_invoice_pdf", kwargs={"pk": batch.pk}))
        self.assertContains(response, "How to pay")
        self.assertContains(response, "Transfer to BE12 3456")

    def test_a_single_person_with_one_line_shows_neither_name_nor_balance_row(self):
        self.membership.fee_amount = Decimal("150.00")
        self.membership.save()
        batch = self._confirmed_batch()
        RegistrationDetails.objects.create(membership=self.membership, batch=batch, product_variant=self.u10, price=Decimal("150.00"))

        response = self._get()

        self.assertNotContains(response, "Balance")
        card = response.context["cards"][0]
        self.assertFalse(card["members"][0]["show_name"])

    def test_several_lines_for_one_person_add_a_balance_row(self):
        self.membership.fee_amount = Decimal("250.00")
        self.membership.save()
        batch = self._confirmed_batch()
        RegistrationDetails.objects.create(membership=self.membership, batch=batch, product_variant=self.u10, price=Decimal("150.00"))
        RegistrationDetails.objects.create(membership=self.membership, batch=batch, product_variant=self.u10, price=Decimal("100.00"))

        response = self._get()

        self.assertContains(response, "Balance")

    def test_the_manual_discount_shows_with_its_note(self):
        self.membership.fee_amount = Decimal("140.00")
        self.membership.save()
        batch = self._confirmed_batch(manual_discount_amount=Decimal("10.00"), manual_discount_note="Board member")
        RegistrationDetails.objects.create(membership=self.membership, batch=batch, product_variant=self.u10, price=Decimal("150.00"))

        response = self._get()

        self.assertContains(response, "Board member")
        self.assertEqual(response.context["cards"][0]["manual_discount"], Decimal("10.00"))

    def test_the_due_date_is_a_long_date(self):
        self.membership.fee_amount = Decimal("150.00")
        self.membership.save()
        batch = self._confirmed_batch()
        RegistrationDetails.objects.create(membership=self.membership, batch=batch, product_variant=self.u10, price=Decimal("150.00"))

        response = self._get()

        from django.utils.formats import date_format

        self.assertContains(response, f"Due {date_format(batch.invoice_due_date, 'DATE_FORMAT')}")

    def test_a_membership_billed_by_hand_is_one_season_dues_line_with_the_dues_invoice(self):
        self.membership.fee_amount = Decimal("90.00")
        self.membership.save()
        invoice = DuesInvoice.objects.create(club=self.club, membership=self.membership, amount=Decimal("90.00"), due_date=timezone.localdate() + datetime.timedelta(days=10), number="INV-1")

        response = self._get()

        self.assertContains(response, "Season dues")
        self.assertContains(response, reverse("mobile:dues_invoice_pdf", kwargs={"pk": invoice.pk}))
        self.assertContains(response, "90.00")

    def test_nothing_open_shows_the_nothing_to_pay_state(self):
        response = self._get()

        self.assertContains(response, "Nothing to pay")
        self.assertContains(response, "You have no open payments.")
        self.assertNotContains(response, "How to pay")


@override_settings(**SETTINGS)
class DuesInvoicePdfViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.club = make_club()
        today = timezone.localdate()
        cls.season = Season.objects.create(club=cls.club, start_date=today - datetime.timedelta(days=30), end_date=today + datetime.timedelta(days=300))
        cls.user = User.objects.create_user(email="parent@example.com", password="pw-secret-123")
        cls.member = Member.objects.create(first_name="Lars", last_name="Bakker", email="parent@example.com", user=cls.user)
        cls.membership = ClubMembership.objects.create(club=cls.club, member=cls.member, season=cls.season, fee_amount=Decimal("90.00"))
        cls.invoice = DuesInvoice.objects.create(club=cls.club, membership=cls.membership, amount=Decimal("90.00"), due_date=today + datetime.timedelta(days=10), number="INV-1")
        cls.stranger = Member.objects.create(first_name="Otto", last_name="Other")
        stranger_membership = ClubMembership.objects.create(club=cls.club, member=cls.stranger, season=cls.season, fee_amount=Decimal("90.00"))
        cls.foreign_invoice = DuesInvoice.objects.create(club=cls.club, membership=stranger_membership, amount=Decimal("90.00"), due_date=today + datetime.timedelta(days=10), number="INV-2")

    def setUp(self):
        self.client.force_login(self.user)

    def test_downloads_the_pdf_of_an_own_invoice(self):
        with patch("mobile.views.dues_invoice_pdf", return_value=b"%PDF-1.4 test"):
            response = self.client.get(reverse("mobile:dues_invoice_pdf", kwargs={"pk": self.invoice.pk}), HTTP_HOST=HOST)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("INV-1.pdf", response["Content-Disposition"])

    def test_another_familys_invoice_is_a_404(self):
        response = self.client.get(reverse("mobile:dues_invoice_pdf", kwargs={"pk": self.foreign_invoice.pk}), HTTP_HOST=HOST)

        self.assertEqual(response.status_code, 404)


class ShopTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.club = make_club(shop_open=True)
        get_waffle_flag_model().objects.get_or_create(name="shop")[0].clubs.add(cls.club)
        cls.user = User.objects.create_user(email="parent@example.com", password="pw-secret-123")
        cls.member = Member.objects.create(first_name="Lars", last_name="Bakker", email="parent@example.com", user=cls.user)
        ClubRole.objects.create(club=cls.club, member=cls.member)

    def setUp(self):
        self.client.force_login(self.user)

    def get(self, name, *args, **params):
        return self.client.get(reverse(f"mobile:{name}", args=args), params, HTTP_HOST=HOST)


@override_settings(**SETTINGS)
class ShopHomeParityTests(ShopTestBase):
    def test_a_price_range_reads_from_and_a_single_price_does_not(self):
        ranged = Product.objects.create(club=self.club, name="Hoodie", price=Decimal("30.00"))
        ProductVariant.objects.create(product=ranged, name="S", price=Decimal("30.00"))
        ProductVariant.objects.create(product=ranged, name="XXL", price=Decimal("35.00"))
        Product.objects.create(club=self.club, name="Cap", price=Decimal("12.00"))

        response = self.get("shop_home")

        self.assertContains(response, "From &euro; 30.00")
        self.assertContains(response, "&euro; 12.00")

    def test_a_sold_out_product_shows_the_badge_and_no_price(self):
        sold_out = Product.objects.create(club=self.club, name="Scarf", price=Decimal("20.00"), inventory_managed=True)

        response = self.get("shop_home")

        self.assertTrue(next(p for p in response.context["products"] if p.pk == sold_out.pk).sold_out)
        self.assertNotContains(response, "&euro; 20.00")
        self.assertContains(response, "opacity-45")
        self.assertContains(response, "Sold out")

    def test_filter_chips_only_with_more_than_one_category_and_an_all_chip(self):
        merch = ProductCategory.objects.create(club=self.club, name="Merchandise")
        fees = ProductCategory.objects.create(club=self.club, name="Fees")
        Product.objects.create(club=self.club, name="Jersey", price=Decimal("25.00"), category=merch)
        Product.objects.create(club=self.club, name="Donation", price=Decimal("5.00"), category=fees)
        Product.objects.create(club=self.club, name="Loose", price=Decimal("5.00"))

        response = self.get("shop_home")

        self.assertContains(response, 'role="tab"', count=3)
        self.assertContains(response, ">All</button>")
        self.assertContains(response, f"category === '{merch.pk}'")
        self.assertContains(response, "x-show=\"category === '' ||", count=3)

    def test_a_single_category_has_no_chip_row(self):
        merch = ProductCategory.objects.create(club=self.club, name="Merchandise")
        Product.objects.create(club=self.club, name="Jersey", price=Decimal("25.00"), category=merch)

        self.assertNotContains(self.get("shop_home"), 'role="tab"')

    def test_the_cart_icon_counts_every_item_in_the_cart(self):
        product = Product.objects.create(club=self.club, name="Jersey", price=Decimal("25.00"))
        cart = Cart.objects.create(club=self.club, user=self.user)
        CartItem.objects.create(cart=cart, product=product, quantity=3, unit_price=product.price)

        self.assertEqual(self.get("shop_home").context["cart_item_count"], 3)

    def test_a_closed_shop_still_loads_with_a_way_to_my_orders(self):
        self.club.shop_open = False
        self.club.save(update_fields=["shop_open"])

        response = self.get("shop_home")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "The shop is closed")
        self.assertContains(response, "You can still see your orders.")
        self.assertContains(response, reverse("mobile:shop_orders"))
        self.assertNotContains(response, reverse("mobile:shop_cart"))

    def test_orders_stay_reachable_while_closed_but_the_cart_does_not(self):
        order = Order.objects.create(club=self.club, purchaser=self.member, total=Decimal("10.00"))
        self.club.shop_open = False
        self.club.save(update_fields=["shop_open"])

        self.assertEqual(self.get("shop_orders").status_code, 200)
        self.assertEqual(self.get("shop_order_detail", order.pk).status_code, 200)
        self.assertRedirects(self.get("shop_cart"), reverse("mobile:home"))

    def test_an_unlicensed_shop_is_still_a_404_whatever_allow_closed_says(self):
        get_waffle_flag_model().objects.get(name="shop").clubs.clear()

        self.assertEqual(self.get("shop_home").status_code, 404)
        self.assertEqual(self.get("shop_orders").status_code, 404)


@override_settings(**SETTINGS)
class ShopProductParityTests(ShopTestBase):
    def setUp(self):
        super().setUp()
        self.product = Product.objects.create(club=self.club, name="Hoodie", slug="hoodie", price=Decimal("30.00"), inventory_managed=True, personalization_enabled=True, description="<p>Warm <b>fleece</b></p><script>alert(1)</script>")
        self.small = ProductVariant.objects.create(product=self.product, name="S", price=Decimal("30.00"))
        self.large = ProductVariant.objects.create(product=self.product, name="L", price=Decimal("35.00"))
        stock(self.product, self.small, 5)

    def _url(self):
        return reverse("mobile:shop_product_detail", kwargs={"slug": self.product.slug})

    def test_the_page_carries_the_option_prices_and_marks_a_sold_out_option(self):
        response = self.client.get(self._url(), HTTP_HOST=HOST)

        data = {option["name"]: option for option in response.context["variant_data"]}
        self.assertEqual(data["S"]["price"], "30.00")
        self.assertEqual(data["L"]["price"], "35.00")
        self.assertTrue(data["S"]["in_stock"])
        self.assertFalse(data["L"]["in_stock"])
        self.assertContains(response, "line-through")
        self.assertContains(response, "Sold out")
        self.assertContains(response, "Choose an option")
        self.assertContains(response, "Personalisation")
        self.assertContains(response, "Number to print")

    def test_the_description_is_sanitised_html(self):
        response = self.client.get(self._url(), HTTP_HOST=HOST)

        self.assertContains(response, "<b>fleece</b>")
        self.assertNotContains(response, "alert(1)")

    def test_a_plain_text_description_keeps_its_line_breaks(self):
        self.product.description = "First line\nSecond line"
        self.product.save()

        response = self.client.get(self._url(), HTTP_HOST=HOST)

        self.assertContains(response, "First line<br>Second line")

    def test_a_sold_out_product_says_so_and_cannot_be_added(self):
        InventoryMovement.objects.all().delete()

        response = self.client.get(self._url(), HTTP_HOST=HOST)

        self.assertContains(response, "This product is sold out right now.")
        self.assertContains(response, "x-data")
        self.assertNotContains(response, "Add to cart &middot;")

    def test_the_for_picker_only_appears_with_several_people(self):
        self.assertNotContains(self.client.get(self._url(), HTTP_HOST=HOST), 'name="beneficiary"')

        family = Family.objects.create(club=self.club, name="Bakker")
        FamilyMembership.objects.create(family=family, member=self.member, role=FamilyMembership.FamilyRole.PARENT)
        child = Member.objects.create(first_name="Mila", last_name="Bakker")
        FamilyMembership.objects.create(family=family, member=child, role=FamilyMembership.FamilyRole.CHILD)
        ClubMembership.objects.create(club=self.club, member=child, season=Season.objects.create(club=self.club, start_date=datetime.date(2026, 8, 1), end_date=datetime.date(2027, 5, 31)))

        response = self.client.get(self._url(), HTTP_HOST=HOST)

        self.assertContains(response, 'name="beneficiary"')
        self.assertContains(response, "Mila")

    def test_adding_with_htmx_answers_with_the_confirmation_not_a_redirect(self):
        response = self.client.post(self._url(), {"variant": str(self.small.pk), "quantity": "2", "personalization_number": "7", "personalization_name": "Somers"}, HTTP_HOST=HOST, headers={"HX-Request": "true"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Added to your cart")
        self.assertContains(response, reverse("mobile:shop_cart"))
        item = CartItem.objects.get()
        self.assertEqual((item.quantity, item.personalization_number, item.personalization_name), (2, "7", "Somers"))

    def test_adding_with_htmx_reports_why_it_failed(self):
        response = self.client.post(self._url(), {"variant": str(self.large.pk), "quantity": "1"}, HTTP_HOST=HOST, headers={"HX-Request": "true"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "is out of stock")
        self.assertNotContains(response, "Added to your cart")
        self.assertFalse(CartItem.objects.exists())

    def test_adding_without_htmx_still_redirects_to_the_cart(self):
        response = self.client.post(self._url(), {"variant": str(self.small.pk), "quantity": "1"}, HTTP_HOST=HOST)

        self.assertRedirects(response, reverse("mobile:shop_cart"), fetch_redirect_response=False)


@override_settings(**SETTINGS)
class ShopCartParityTests(ShopTestBase):
    def setUp(self):
        super().setUp()
        self.product = Product.objects.create(club=self.club, name="Hoodie", price=Decimal("30.00"), inventory_managed=True, personalization_enabled=True)
        self.small = ProductVariant.objects.create(product=self.product, name="S", price=Decimal("30.00"))
        stock(self.product, self.small, 5)
        self.cart = Cart.objects.create(club=self.club, user=self.user)

    def _item(self, **kwargs):
        kwargs.setdefault("product", self.product)
        kwargs.setdefault("variant", self.small)
        kwargs.setdefault("quantity", 1)
        kwargs.setdefault("unit_price", Decimal("30.00"))
        return CartItem.objects.create(cart=self.cart, **kwargs)

    def test_the_line_shows_who_it_is_for_and_what_is_printed(self):
        family = Family.objects.create(club=self.club, name="Bakker")
        FamilyMembership.objects.create(family=family, member=self.member, role=FamilyMembership.FamilyRole.PARENT)
        child = Member.objects.create(first_name="Mila", last_name="Bakker")
        FamilyMembership.objects.create(family=family, member=child, role=FamilyMembership.FamilyRole.CHILD)
        ClubMembership.objects.create(club=self.club, member=child, season=Season.objects.create(club=self.club, start_date=datetime.date(2026, 8, 1), end_date=datetime.date(2027, 5, 31)))
        self._item(beneficiary=child, personalization_number="17", personalization_name="Somers")

        response = self.get("shop_cart")

        self.assertContains(response, "For Mila · #17 SOMERS")

    def test_a_line_without_enough_stock_says_so_and_blocks_the_order(self):
        self._item(quantity=9)

        response = self.get("shop_cart")

        self.assertContains(response, "Not enough in stock for this quantity. Lower it or remove the item.")
        self.assertContains(response, "be ordered any more. Remove them to continue.")
        self.assertRegex(response.content.decode(), r'btn-rc btn-rc-primary"\s+disabled>Place order')

    def test_a_sold_out_option_can_only_be_removed(self):
        item = self._item()
        InventoryMovement.objects.all().delete()

        response = self.get("shop_cart")

        self.assertContains(response, "Sold out")
        self.assertNotContains(response, 'value="increment"')
        self.assertContains(response, 'value="remove"')
        self.assertTrue(response.context["items"][0].sold_out_option)
        self.assertEqual(response.context["items"][0].pk, item.pk)

    def test_an_unavailable_product_has_its_own_message(self):
        plain = Product.objects.create(club=self.club, name="Cap", price=Decimal("10.00"), is_active=False)
        self._item(product=plain, variant=None, unit_price=Decimal("10.00"))

        self.assertContains(self.get("shop_cart"), "Sold out or no longer available")

    def test_a_healthy_cart_can_be_ordered(self):
        self._item()

        response = self.get("shop_cart")

        self.assertNotRegex(response.content.decode(), r'btn-rc btn-rc-primary"\s+disabled>Place order')
        self.assertContains(response, "Subtotal")
        self.assertContains(response, "You pay at the club when you pick up your order.")

    def test_a_changed_price_shows_the_notice(self):
        self._item(unit_price=Decimal("25.00"))

        self.assertContains(self.get("shop_cart"), "Some prices changed since you added them.")

    def test_quantity_changes_with_htmx_answer_with_the_cart_body_only(self):
        item = self._item()

        response = self.client.post(reverse("mobile:shop_cart_item_update", kwargs={"item_id": item.pk}), {"action": "increment"}, HTTP_HOST=HOST, headers={"HX-Request": "true"})

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "<html")
        self.assertContains(response, "Subtotal")
        item.refresh_from_db()
        self.assertEqual(item.quantity, 2)

    def test_removing_the_last_line_with_htmx_shows_the_empty_cart(self):
        item = self._item()

        response = self.client.post(reverse("mobile:shop_cart_item_update", kwargs={"item_id": item.pk}), {"action": "remove"}, HTTP_HOST=HOST, headers={"HX-Request": "true"})

        self.assertContains(response, "Your cart is empty")
        self.assertFalse(CartItem.objects.exists())

    def test_quantity_changes_keep_the_applied_code(self):
        item = self._item(quantity=2, unit_price=Decimal("30.00"))
        Discount.objects.create(club=self.club, name="Ten off", code="TEN10", discount_amount=Decimal("10"))

        response = self.client.post(reverse("mobile:shop_cart_item_update", kwargs={"item_id": item.pk}), {"action": "increment", "code": "ten10"}, HTTP_HOST=HOST, headers={"HX-Request": "true"})

        self.assertContains(response, "Ten off")
        self.assertEqual(response.context["totals"]["discount_amount"], Decimal("9.00"))

    def test_a_code_applied_with_htmx_shows_the_tag_and_totals(self):
        self._item(quantity=2)
        Discount.objects.create(club=self.club, name="Ten off", code="TEN10", discount_amount=Decimal("10"))

        response = self.client.post(reverse("mobile:shop_cart"), {"code": "ten10"}, HTTP_HOST=HOST, headers={"HX-Request": "true"})

        self.assertNotContains(response, "<html")
        self.assertContains(response, "Ten off &middot; &minus;&euro; 6.00")
        self.assertContains(response, 'name="discount_code" value="ten10"')

    def test_a_wrong_code_shows_an_inline_error_and_is_not_carried_to_checkout(self):
        self._item()

        response = self.client.post(reverse("mobile:shop_cart"), {"code": "NOPE"}, HTTP_HOST=HOST, headers={"HX-Request": "true"})

        self.assertContains(response, 'role="alert"')
        self.assertContains(response, 'name="discount_code" value=""')

    def test_applying_a_code_without_htmx_redirects_to_the_preview(self):
        self._item()

        response = self.client.post(reverse("mobile:shop_cart"), {"code": "TEN10"}, HTTP_HOST=HOST)

        self.assertRedirects(response, f"{reverse('mobile:shop_cart')}?code=TEN10", fetch_redirect_response=False)

    def test_placing_the_order_shows_the_thank_you_banner_once(self):
        self._item()

        response = self.client.post(reverse("mobile:shop_checkout"), {"discount_code": ""}, HTTP_HOST=HOST)
        order = Order.objects.get()
        detail_url = reverse("mobile:shop_order_detail", kwargs={"pk": order.pk})

        self.assertRedirects(response, detail_url, fetch_redirect_response=False)
        first = self.client.get(detail_url, HTTP_HOST=HOST)
        second = self.client.get(detail_url, HTTP_HOST=HOST)
        self.assertContains(first, "Order placed. Thank you!")
        self.assertNotContains(second, "Order placed. Thank you!")


@override_settings(**SETTINGS)
class ShopOrdersParityTests(ShopTestBase):
    def setUp(self):
        super().setUp()
        self.product = Product.objects.create(club=self.club, name="Hoodie", price=Decimal("30.00"))
        self.order = Order.objects.create(club=self.club, purchaser=self.member, total=Decimal("27.00"))
        OrderLine.objects.create(order=self.order, product=self.product, quantity=2, unit_price=Decimal("15.00"), line_total=Decimal("30.00"), personalization_number="9", personalization_name="Lars")

    def test_the_list_uses_the_app_labels(self):
        cases = [
            (Order.PaymentStatus.PENDING, Order.FulfillmentStatus.NOT_READY, "To pay at pickup", "Being prepared", "pill-warn"),
            (Order.PaymentStatus.PAID, Order.FulfillmentStatus.READY_FOR_PICKUP, "Paid", "Ready for pickup", "pill-ok"),
            (Order.PaymentStatus.PARTIALLY_PAID, Order.FulfillmentStatus.DELIVERED, "Partly paid", "Completed", "pill-ok"),
            (Order.PaymentStatus.REFUNDED, Order.FulfillmentStatus.CANCELLED, "Refunded", "Cancelled", "pill-danger"),
        ]
        for payment, fulfillment, payment_label, status_label, pill in cases:
            with self.subTest(payment=payment, fulfillment=fulfillment):
                self.order.payment_status = payment
                self.order.fulfillment_status = fulfillment
                self.order.save()

                response = self.get("shop_orders")

                row = response.context["rows"][0]
                self.assertEqual((str(row["payment_label"]), str(row["status_label"]), row["status_pill_class"]), (payment_label, status_label, pill))
                self.assertContains(response, payment_label)
                self.assertContains(response, status_label)

    def test_the_detail_shows_lines_discounts_total_and_pickup_instructions(self):
        discount = Discount.objects.create(club=self.club, name="Ten off", code="TEN10", discount_amount=Decimal("10"))
        AppliedDiscount.objects.create(order=self.order, discount=discount, discount_amount=Decimal("10"), amount=Decimal("3.00"), description="Ten off")
        self.order.pickup_instructions = "Ask at the desk."
        self.order.save()

        response = self.get("shop_order_detail", self.order.pk)

        self.assertContains(response, "2 &times; Hoodie")
        self.assertContains(response, "#9 LARS")
        self.assertContains(response, "&minus;&euro; 3.00")
        self.assertContains(response, "&euro; 27.00")
        self.assertContains(response, "To pay at pickup")
        self.assertContains(response, "Ask at the desk.")
        self.assertNotContains(response, "Order placed. Thank you!")


@override_settings(**SETTINGS)
class FormsParityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.club = make_club()
        today = timezone.localdate()
        cls.season = Season.objects.create(club=cls.club, start_date=today - datetime.timedelta(days=30), end_date=today + datetime.timedelta(days=300))
        cls.user = User.objects.create_user(email="parent@example.com", password="pw-secret-123")
        cls.member = Member.objects.create(first_name="Lars", last_name="Bakker", email="parent@example.com", user=cls.user)
        ClubMembership.objects.create(club=cls.club, member=cls.member, season=cls.season, status=ClubMembership.StatusChoices.ACTIVE)
        cls.form = FormBuilderForm.objects.create(club=cls.club, title="Medical info", description="Tell us what we should know.")
        cls.name_field = FormBuilderField.objects.create(form=cls.form, key="name", label="Name", field_type=FormBuilderField.FieldType.TEXT, required=True, order=1, section="About you")
        cls.notes_field = FormBuilderField.objects.create(form=cls.form, key="notes", label="Notes", field_type=FormBuilderField.FieldType.TEXTAREA, required=False, order=2, section="About you")
        cls.consent_field = FormBuilderField.objects.create(form=cls.form, key="consent", label="Consent", field_type=FormBuilderField.FieldType.CHECKBOX, required=True, order=3, section="Consent")

    def setUp(self):
        self.client.force_login(self.user)

    def _list(self):
        return self.client.get(reverse("mobile:forms_list"), HTTP_HOST=HOST)

    def test_open_unsubmitted_rows_are_to_do_and_everything_else_is_done_and_closed(self):
        now = timezone.now()
        open_send = FormSend.objects.create(club=self.club, form=self.form, club_wide=True, closes_at=now + datetime.timedelta(days=3))
        closed_send = FormSend.objects.create(club=self.club, form=self.form, club_wide=True, closes_at=now - datetime.timedelta(days=3))
        done_send = FormSend.objects.create(club=self.club, form=self.form, club_wide=True)
        submission = Submission.objects.create(send=done_send, member=self.member)

        response = self._list()

        self.assertEqual([row["send"] for row in response.context["todo_rows"]], [open_send])
        self.assertEqual({row["send"] for row in response.context["rest_rows"]}, {closed_send, done_send})
        self.assertContains(response, "To do")
        self.assertContains(response, "Done and closed")
        self.assertContains(response, "Complete")
        self.assertContains(response, "Closed")
        self.assertContains(response, "Done")
        self.assertContains(response, reverse("mobile:form_response", kwargs={"pk": submission.pk}))

    def test_a_closed_unsubmitted_row_is_not_a_link(self):
        closed_send = FormSend.objects.create(club=self.club, form=self.form, club_wide=True, closes_at=timezone.now() - datetime.timedelta(days=1))

        response = self._list()

        self.assertContains(response, 'aria-disabled="true"')
        self.assertNotContains(response, reverse("mobile:form_fill", kwargs={"pk": closed_send.pk}))

    def test_no_forms_shows_the_empty_state(self):
        response = self._list()

        self.assertContains(response, "No forms")
        self.assertContains(response, "Nothing to fill in right now.")

    def test_the_fill_page_groups_fields_under_their_sections_and_ends_with_submit(self):
        send = FormSend.objects.create(club=self.club, form=self.form, club_wide=True)

        response = self.client.get(reverse("mobile:form_fill", kwargs={"pk": send.pk}), HTTP_HOST=HOST)

        self.assertEqual([(section["title"], [field.name for field in section["fields"]]) for section in response.context["sections"]], [("About you", ["name", "notes"]), ("Consent", ["consent"])])
        self.assertContains(response, "Tell us what we should know.")
        self.assertContains(response, "data-field data-required>", count=2)  # name + consent
        self.assertContains(response, "Please complete the highlighted fields.")
        self.assertNotContains(response, "This form is closed.")
        content = response.content.decode()
        self.assertGreater(content.index("btn-rc-primary"), content.index('name="consent"'))

    def test_a_closed_form_shows_the_banner_and_a_disabled_submit(self):
        send = FormSend.objects.create(club=self.club, form=self.form, club_wide=True, closes_at=timezone.now() - datetime.timedelta(days=1))

        response = self.client.get(reverse("mobile:form_fill", kwargs={"pk": send.pk}), HTTP_HOST=HOST)

        self.assertContains(response, "This form is closed.")
        self.assertRegex(response.content.decode(), r'btn-rc btn-rc-primary" disabled>')

    def test_the_for_picker_lists_everyone_the_form_is_addressed_to(self):
        family = Family.objects.create(club=self.club, name="Bakker")
        FamilyMembership.objects.create(family=family, member=self.member, role=FamilyMembership.FamilyRole.PARENT)
        child = Member.objects.create(first_name="Mila", last_name="Bakker")
        FamilyMembership.objects.create(family=family, member=child, role=FamilyMembership.FamilyRole.CHILD)
        ClubMembership.objects.create(club=self.club, member=child, season=self.season, status=ClubMembership.StatusChoices.ACTIVE)
        send = FormSend.objects.create(club=self.club, form=self.form, club_wide=False)
        send.invited_members.add(self.member, child)

        response = self.client.get(reverse("mobile:form_fill", kwargs={"pk": send.pk}), HTTP_HOST=HOST)

        self.assertContains(response, f"?for={child.pk}")
        self.assertContains(response, f"?for={self.member.pk}")
        self.assertContains(response, "Me")

    def test_the_response_screen_shows_a_dash_for_no_answer(self):
        send = FormSend.objects.create(club=self.club, form=self.form, club_wide=True)
        submission = Submission.objects.create(send=send, member=self.member)
        Answer.objects.create(submission=submission, field=self.name_field, value="Lars")

        response = self.client.get(reverse("mobile:form_response", kwargs={"pk": submission.pk}), HTTP_HOST=HOST)

        self.assertContains(response, "Lars")
        self.assertContains(response, "&mdash;")
        self.assertNotContains(response, "No answer")


@override_settings(**SETTINGS)
class ReRegisterCopyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.club = make_club()
        today = timezone.localdate()
        cls.season = Season.objects.create(club=cls.club, start_date=today - datetime.timedelta(days=30), end_date=today + datetime.timedelta(days=300))
        cls.last_season = Season.objects.create(club=cls.club, start_date=today - datetime.timedelta(days=395), end_date=today - datetime.timedelta(days=31))
        cls.user = User.objects.create_user(email="parent@example.com", password="pw-secret-123")
        cls.member = Member.objects.create(first_name="Lars", last_name="Bakker", email="lars@example.com", user=cls.user)
        ClubMembership.objects.create(club=cls.club, member=cls.member, season=cls.season, status=ClubMembership.StatusChoices.ACTIVE)
        ClubMembership.objects.create(club=cls.club, member=cls.member, season=cls.last_season, status=ClubMembership.StatusChoices.LAPSED)
        pool = NumberPool.objects.create(club=cls.club, name="Youth", min_number=1, max_number=20)
        cls.team = Team.objects.create(club=cls.club, name="U10 Boys", short_name="U10B", pool=pool)
        TeamMembership.objects.create(team=cls.team, member=cls.member, season=cls.last_season, jersey_number=7)
        product = Product.objects.create(club=cls.club, name="Player Registration", product_type=Product.ProductType.MEMBERSHIP, season=cls.season, price=Decimal("100.00"))
        cls.u10 = ProductVariant.objects.create(product=product, name="U10", price=Decimal("80.00"))

    def setUp(self):
        self.client.force_login(self.user)

    def _post(self, action):
        data = {"entries-TOTAL_FORMS": "2", "entries-INITIAL_FORMS": "0", "entries-MIN_NUM_FORMS": "0", "entries-MAX_NUM_FORMS": "1000"}
        data["entries-0-existing_member"] = str(self.member.pk)
        data["entries-0-is_contact"] = "on"
        data["entries-0-product_variant"] = str(self.u10.pk)
        data["entries-0-requested_team"] = str(self.team.pk)
        data["entries-0-requested_jersey_number"] = "7"
        data["action"] = action
        return self.client.post(reverse("mobile:reregister"), data, HTTP_HOST=HOST)

    def test_the_form_uses_the_app_wording(self):
        response = self.client.get(reverse("mobile:reregister"), HTTP_HOST=HOST)

        self.assertContains(response, "See the price")
        self.assertContains(response, "Someone new")
        self.assertContains(response, "No preference")
        self.assertContains(response, "Choose…")
        self.assertContains(response, "Your preference. The club makes the final choice.")
        self.assertContains(response, "Add a registration for each person and role.")
        self.assertNotContains(response, "Calculate price")

    def test_the_price_screen_offers_send_registration_and_keeps_the_current_number(self):
        response = self._post("preview")

        self.assertContains(response, "Send registration")
        self.assertContains(response, "Keep #7")
        self.assertContains(response, "The club reviews your registration and then sends the invoice.")
        self.assertContains(response, "Player Registration &middot; U10")
        self.assertNotContains(response, "Confirm &amp; submit")

    def test_sending_it_flashes_registration_received_with_the_invoice_follows_message(self):
        response = self._post("submit")

        self.assertRedirects(response, reverse("mobile:me"), fetch_redirect_response=False)
        sent = list(response.wsgi_request._messages)
        self.assertEqual(sent[-1].extra_tags, "Registration received")
        messages = [str(message) for message in sent]
        self.assertTrue(any("send the invoice for € 80.00" in message for message in messages), messages)
