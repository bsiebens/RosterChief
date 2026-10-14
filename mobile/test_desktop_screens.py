"""The desktop (wide-screen) Member-mode screens, brought up to the redesigned mobile ones: Home's
two columns, Payments through the shared card, the event page through the shared hero + body, and
the Calendar through the shared rows and month grid. Each is requested with a desktop User-Agent
(club.device.is_mobile_or_tablet is False), which is what makes DesktopTemplateMixin serve
mobile/desktop/*."""

import datetime
from decimal import Decimal

from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from club.models import ClubMembership
from events.models import EventTask
from registration.models import RegistrationBatch, RegistrationDetails
from shop.models import Product, ProductVariant

from .test_member_screens import HOST, MemberScreenFixture, Status, at, setUpModule, tearDownModule  # noqa: F401 (module fixtures)

DESKTOP_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15"
DESKTOP_SHELL = 'class="desktop-sidebar"'


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "testserver"])
class DesktopScreenTests(MemberScreenFixture, TestCase):
    def setUp(self):
        self.client.force_login(self.user)

    def get_desktop(self, url, **extra):
        return self.get(url, HTTP_USER_AGENT=DESKTOP_UA, **extra)

    def test_home_is_two_columns_with_cards_left_and_lists_right(self):
        child = self.add_child()
        event = self.make_event(title="Cup final", start=at(2))
        self.invite(event, self.member)
        self.invite(event, child)
        later = self.make_event(title="Practice later", start=at(5))
        self.invite(later, self.member)
        EventTask.objects.create(event=event, title="Bring oranges", needed_quantity=2)

        response = self.get_desktop(reverse("mobile:home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, DESKTOP_SHELL)
        self.assertContains(response, "lg:grid-cols-")
        self.assertContains(response, 'id="followed-live"')
        self.assertContains(response, 'id="followed-rest"')
        self.assertContains(response, 'id="event-sheet"')
        self.assertContains(response, "card-btn-in")  # the shared next-up card with inline In / Out
        self.assertContains(response, "Bring oranges")
        self.assertContains(response, "answer-btn-line")  # inline Claim
        html = response.content.decode()
        self.assertLess(html.index("card-btn-in"), html.index("Bring oranges"))

    def test_home_without_anything_shows_the_shared_empty_state(self):
        response = self.get_desktop(reverse("mobile:home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "state-card")
        self.assertContains(response, "All Caught Up")

    def test_the_phone_still_gets_the_mobile_home(self):
        response = self.get(reverse("mobile:home"), HTTP_USER_AGENT="Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) Mobile/15E148")

        self.assertNotContains(response, DESKTOP_SHELL)

    def test_payments_empty_uses_the_shared_state_card_in_a_form_width_column(self):
        response = self.get_desktop(reverse("mobile:payments"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, DESKTOP_SHELL)
        self.assertContains(response, "max-w-[520px]")
        self.assertContains(response, "state-card")
        self.assertContains(response, "Nothing to pay")

    def test_payments_render_the_shared_payment_card(self):
        self.club.payment_instructions = "Transfer to BE12 3456."
        self.club.save()
        membership = ClubMembership.objects.get(member=self.member)
        membership.fee_amount = Decimal("135.00")
        membership.save()
        product = Product.objects.create(club=self.club, name="Player Registration", product_type=Product.ProductType.MEMBERSHIP, season=self.season, price=Decimal("150.00"))
        variant = ProductVariant.objects.create(product=product, name="U10", price=Decimal("150.00"))
        batch = RegistrationBatch.objects.create(
            club=self.club,
            season=self.season,
            contact_first_name="Lars",
            contact_last_name="Bakker",
            contact_email="parent@example.com",
            invoice_number="REG-1",
            invoice_sent_at=timezone.now(),
            invoice_due_date=timezone.localdate() + datetime.timedelta(days=14),
        )
        RegistrationDetails.objects.create(membership=membership, batch=batch, product_variant=variant, requested_team=self.team, price=Decimal("150.00"), discount_amount=Decimal("15.00"))

        response = self.get_desktop(reverse("mobile:payments"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, DESKTOP_SHELL)
        self.assertContains(response, "m-card")
        self.assertContains(response, "Registration")
        self.assertContains(response, "Transfer to BE12 3456.")

    def test_event_detail_uses_the_shared_hero_and_body(self):
        event = self.make_event(title="Cup final", start=at(2))
        self.invite(event, self.member)

        response = self.get_desktop(reverse("mobile:event_detail", args=[event.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, DESKTOP_SHELL)
        self.assertContains(response, "max-w-[720px]")
        self.assertContains(response, "Cup final")
        self.assertContains(response, "Your answers")
        self.assertContains(response, "answer-btn")

    def test_calendar_list_uses_the_shared_rows_with_inline_answers(self):
        event = self.make_event(title="Practice", start=at(1))
        self.invite(event, self.member, Status.NO_RESPONSE)

        response = self.get_desktop(reverse("mobile:calendar"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, DESKTOP_SHELL)
        self.assertContains(response, 'id="cal-split"')
        self.assertContains(response, "answer-btn-line")
        self.assertContains(response, 'id="event-sheet"')

    def test_calendar_empty_and_month_render(self):
        empty = self.get_desktop(reverse("mobile:calendar"))
        self.assertEqual(empty.status_code, 200)
        self.assertContains(empty, "state-card")

        month = self.get_desktop(reverse("mobile:calendar") + "?view=month")
        self.assertEqual(month.status_code, 200)
        self.assertContains(month, 'id="cal-grid"')
        self.assertContains(month, "month-cell")

    def test_remaining_desktop_screens_still_render(self):
        for name in ("me", "news_list", "notifications", "forms_list", "calendar_feed_settings", "bug_list", "reregister"):
            with self.subTest(name=name):
                response = self.get_desktop(reverse(f"mobile:{name}"))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, DESKTOP_SHELL)

    def test_every_class_the_shared_partials_need_exists_in_the_desktop_stylesheet_source(self):
        from pathlib import Path

        css = (Path(__file__).resolve().parent.parent / "assets" / "mobile-desktop.css").read_text()
        self.assertIn('@source "../mobile/templates/mobile/_*.html"', css)
        for selector in (".m-card", ".state-card", ".toast", "dialog.sheet", ".answer-btn", ".card-btn", ".menu-panel", ".month-cell", ".btn-rc", ".header-seg", ".sponsor-scroll"):
            with self.subTest(selector=selector):
                self.assertIn(selector, css)


class LayoutOverrideTests(SimpleTestCase):
    """?layout=phone|desktop (club.device) wins over the User-Agent and is remembered in the session."""

    SAFARI_MAC = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/27.0 Safari/605.1.15"

    def _request(self, path="/", session=None):
        from django.test import RequestFactory

        request = RequestFactory().get(path, HTTP_USER_AGENT=self.SAFARI_MAC)
        request.session = {} if session is None else session
        return request

    def test_safari_on_a_mac_is_desktop(self):
        from club.device import is_mobile_or_tablet

        request = self._request()
        self.assertFalse(is_mobile_or_tablet(request))
        self.assertEqual(request.layout_reason, "user-agent:desktop")

    def test_the_override_wins_and_sticks_in_the_session(self):
        from club.device import is_mobile_or_tablet

        session = {}
        self.assertTrue(is_mobile_or_tablet(self._request("/?layout=phone", session)))
        self.assertTrue(is_mobile_or_tablet(self._request("/", session)))
        self.assertFalse(is_mobile_or_tablet(self._request("/?layout=auto", session)))
        self.assertNotIn("layout", session)

    def test_an_unknown_value_is_ignored(self):
        from club.device import is_mobile_or_tablet

        self.assertFalse(is_mobile_or_tablet(self._request("/?layout=banana")))
