"""The club-branded shop emails (order registered / ready for pickup): recipients, language, content, invoice attachment, pause."""

from decimal import Decimal
from unittest import mock

from django.core import mail
from django.test import TestCase

from members.models import Family, FamilyMembership, Member
from shop.models import AppliedDiscount, Discount, Order, OrderLine, Payment
from shop.services import invoices, notifications
from shop.tests import ShopClubMixin


class ShopEmailTests(ShopClubMixin, TestCase):
    def setUp(self):
        super().setUp()
        p = mock.patch("shop.services.notifications.dispatch")
        p.start()
        self.addCleanup(p.stop)
        self.buyer.language = "en"
        self.buyer.save()

    def order(self, member=None, total="40.00", **kw):
        member = member or self.buyer
        order = Order.objects.create(purchaser=member, total=Decimal(total), pickup_instructions="Desk on Tuesday", **kw)
        product = self.merch("Jersey", total)
        OrderLine.objects.create(order=order, product=product, quantity=1, unit_price=product.price, line_total=product.price, personalization_number="7", personalization_name="DOE")
        return order

    def test_placed_email_content_and_language(self):
        order = self.order()
        d = Discount.objects.create(name="Welcome", discount_type="fixed_amount", discount_amount=Decimal("5"))
        AppliedDiscount.objects.create(order=order, discount=d, discount_type="fixed_amount", discount_amount=Decimal("5"), amount=Decimal("5"), description="Welcome gift")
        notifications.send("placed", order, {"payment_instructions": "IBAN BE12"})
        self.assertEqual(len(mail.outbox), 1)
        m = mail.outbox[0]
        self.assertEqual(m.to, ["bea@shop.test"])
        self.assertIn(order.number, m.subject)
        self.assertIn("Shop FC", m.subject)
        self.assertIn("registered", m.subject)
        html = m.alternatives[0][0]
        for text in ("Jersey", "7 DOE", "Welcome gift", "EUR 40.00", "Amount due", "IBAN BE12", "What happens next"):
            self.assertIn(text, html)
            self.assertIn(text, m.body)
        self.assertEqual(m.attachments, [])

    def test_language_nl_default_and_fr(self):
        self.buyer.language = ""
        self.buyer.save()
        notifications.send("placed", self.order())
        self.assertIn("Bedankt voor je bestelling", mail.outbox[0].alternatives[0][0])
        self.buyer.language = "fr"
        self.buyer.save()
        notifications.send("placed", self.order())
        self.assertIn("Merci pour votre commande", mail.outbox[1].alternatives[0][0])

    def test_paid_order_has_no_payment_instructions_unpaid_has(self):
        paid = self.order(payment_status=Order.PaymentStatus.PAID)
        Payment.objects.create(order=paid, amount=Decimal("40"), method="cash", status="confirmed")
        unpaid = self.order()
        notifications.send("placed", paid, {"payment_instructions": "IBAN BE12"})
        notifications.send("placed", unpaid, {"payment_instructions": "IBAN BE12"})
        self.assertNotIn("IBAN BE12", mail.outbox[0].body)
        self.assertIn("Paid in full", mail.outbox[0].body)
        self.assertIn("IBAN BE12", mail.outbox[1].body)
        self.assertIn("Amount due: EUR 40.00", mail.outbox[1].body)

    def test_ready_email_attaches_invoice_pdf(self):
        order = self.order()
        notifications.send("ready", order)
        m = mail.outbox[0]
        self.assertIn("ready for pickup", m.subject)
        self.assertIn("Desk on Tuesday", m.body)
        invoice = invoices.ensure_invoice(order)
        try:
            from weasyprint import HTML  # noqa: F401
        except ImportError, OSError:
            self.assertEqual(m.attachments, [])
            return
        self.assertEqual(len(m.attachments), 1)
        name, content, mimetype = m.attachments[0]
        self.assertEqual((name, mimetype), (f"{invoice.number}.pdf", "application/pdf"))
        self.assertTrue(content.startswith(b"%PDF"))

    def test_ready_email_without_weasyprint_goes_without_attachment(self):
        order = self.order()
        with mock.patch("shop.services.invoices.render_pdf", side_effect=invoices.PDFUnavailable("no")):
            with self.assertLogs("shop.services.emails", "WARNING"):
                notifications.send("ready", order)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].attachments, [])

    def test_other_kinds_send_no_email(self):
        for kind in ("cancelled", "payment_received"):
            notifications.send(kind, self.order())
        self.assertEqual(mail.outbox, [])

    def test_no_address_no_email(self):
        nobody = Member.objects.create(first_name="No", last_name="Mail")
        notifications.send("placed", self.order(nobody))
        self.assertEqual(mail.outbox, [])

    def test_paused_email_sends_nothing(self):
        with mock.patch("rosterchief.mail.email_paused", return_value=True):
            notifications.send("placed", self.order())
        self.assertEqual(mail.outbox, [])

    def test_guardian_gets_one_email_and_dedupe(self):
        child = Member.objects.create(first_name="Kid", last_name="Buyer", email="kid@shop.test")
        family = Family.objects.create(name="Buyer")
        FamilyMembership.objects.create(family=family, member=child, role=FamilyMembership.FamilyRole.CHILD)
        parent = Member.objects.get(user=self.users["plain"])
        parent.language = "nl"
        parent.save()
        FamilyMembership.objects.create(family=family, member=parent, role=FamilyMembership.GUARDIAN_ROLES[0])
        notifications.send("placed", self.order(child))
        # the child has no login: only the guardian is reached, in their own language, on the child's behalf
        self.assertEqual([m.to for m in mail.outbox], [[self.users["plain"].email]])
        self.assertIn("Bedankt voor je bestelling", mail.outbox[0].alternatives[0][0])
        self.assertIn("Kid", mail.outbox[0].body)

    def test_user_preferences_apply(self):
        from notifications import preferences

        member = Member.objects.get(user=self.users["plain"])
        order = self.order(member)
        preferences.set_preference(self.users["plain"], "shop", "email", False)
        notifications.send("placed", order)
        self.assertEqual(mail.outbox, [])
        preferences.set_preference(self.users["plain"], "shop", "email", True)
        notifications.send("placed", order)
        self.assertEqual(len(mail.outbox), 1)

    def test_email_error_never_fails_notification(self):
        with mock.patch("shop.services.emails.send_message", side_effect=RuntimeError("smtp down")):
            self.assertEqual(notifications.send("placed", self.order(Member.objects.get(user=self.users["plain"]))), 1)
