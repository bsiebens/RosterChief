"""The desktop shop screens: labelled header buttons on the shop home and a 'back' button on every sub-page."""

from decimal import Decimal

from django.test import override_settings
from django.urls import reverse

from shop.models import Order, Product

from .test_desktop_screens import DESKTOP_SHELL, DESKTOP_UA
from .test_payments_shop_forms import HOST, SETTINGS, ShopTestBase


@override_settings(**SETTINGS)
class DesktopShopTests(ShopTestBase):
    def get_desktop(self, name, *args):
        return self.client.get(reverse(f"mobile:{name}", args=args), HTTP_HOST=HOST, HTTP_USER_AGENT=DESKTOP_UA)

    def test_home_has_labelled_orders_and_cart_buttons(self):
        response = self.get_desktop("shop_home")

        self.assertContains(response, DESKTOP_SHELL)
        self.assertContains(response, f'class="shop-btn" href="{reverse("mobile:shop_orders")}"')
        self.assertContains(response, f'class="shop-btn" href="{reverse("mobile:shop_cart")}"')
        self.assertContains(response, "My orders")
        self.assertContains(response, "Cart")

    def test_sub_pages_have_a_back_button(self):
        product = Product.objects.create(club=self.club, name="Cap", price=Decimal("12.00"))
        order = Order.objects.create(club=self.club, purchaser=self.member, total=Decimal("10.00"))
        shop = reverse("mobile:shop_home")
        for name, args in (("shop_orders", ()), ("shop_cart", ()), ("shop_product_detail", (product.slug,))):
            with self.subTest(name):
                response = self.get_desktop(name, *args)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, f'class="shop-btn" href="{shop}"')
                self.assertContains(response, "Back to the shop")

        response = self.get_desktop("shop_order_detail", order.pk)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'class="shop-btn" href="{reverse("mobile:shop_orders")}"')
        self.assertContains(response, "Back to my orders")
