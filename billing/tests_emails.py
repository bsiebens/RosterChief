"""Platform invoice emails use the branded layout (RosterChief branding) and keep the PDF attached."""

from decimal import Decimal
from unittest import mock

from django.core import mail
from django.core.cache import cache

from controlpanel.models import EmailSuppression

from .models import BillingSettings
from .services import BillingError
from .services import invoices as invoices_svc
from .services import reminders
from .tests import BillingTestBase, make_club


class InvoiceEmailTests(BillingTestBase):
    def setUp(self):
        super().setUp()
        s = BillingSettings.current()
        s.iban, s.bic = "BE68539007547034", "BBRUBEBB"
        s.save()
        self.due = self.open(make_club("Mail Club", vat_id="BE999", country="BE"))
        self.invoice = invoices_svc.issue_invoice(self.due)
        mail.outbox.clear()

    def send(self):
        with mock.patch.object(invoices_svc, "club_recipients", return_value=["admin@club.test"]), mock.patch.object(invoices_svc, "invoice_pdf", return_value=b"%PDF-1.4 fake"):
            return invoices_svc.send_invoice(self.invoice)

    def test_club_invoice_email_is_branded_and_has_the_pdf(self):
        self.send()
        (msg,) = mail.outbox
        self.assertEqual(msg.subject, f"Invoice {self.invoice.number} from RosterChief")
        self.assertEqual(msg.to, ["admin@club.test"])
        html = msg.alternatives[0][0]
        for needle in (self.invoice.number, "EUR 121.00", "BE68539007547034", "RosterChief", "Mail Club"):
            self.assertIn(needle, html)
        self.assertIn(f"Please find attached invoice {self.invoice.number}", msg.body)
        (attachment,) = msg.attachments
        self.assertEqual((attachment[0], attachment[2]), (f"{self.invoice.number}.pdf", "application/pdf"))

    def test_nothing_is_sent_while_email_is_paused(self):
        sw = EmailSuppression.current()
        sw.is_active = True
        sw.save()
        cache.delete(EmailSuppression._cache_key())
        self.addCleanup(cache.delete, EmailSuppression._cache_key())
        with self.assertRaises(BillingError):
            self.send()
        self.assertEqual(mail.outbox, [])
        self.invoice.refresh_from_db()
        self.assertIsNone(self.invoice.sent_at)

    def test_reminder_email_is_branded(self):
        with mock.patch.object(reminders, "club_recipients", return_value=["admin@club.test"]):
            self.assertTrue(reminders.send_reminder(self.due, reminders.ERROR))
        (msg,) = mail.outbox
        self.assertEqual(msg.subject, "Payment overdue - Mail Club")
        html = msg.alternatives[0][0]
        self.assertIn(f"EUR {Decimal(self.due.balance)}", html)
        self.assertIn("moved to the free plan", html)
