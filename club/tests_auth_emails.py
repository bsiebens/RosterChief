"""allauth's password-reset and email-confirmation emails use the branded layout (club logo/colours, platform on the base domain)."""

from allauth.core import context
from django.core import mail
from django.test import RequestFactory

from club.adapters import AccountAdapter
from club.models import Sport
from club.services import create_club
from controlpanel.tests import UserApiBase


class AuthEmailTests(UserApiBase):
    def setUp(self):
        super().setUp()
        self.club = create_club("Brand FC", self.admin)
        self.club.sport = Sport.objects.create(name="Hockey")
        self.club.primary_color, self.club.secondary_color = "#0a2540", "#12b886"
        self.club.save()

    def send(self, prefix, ctx, tenant):
        request = RequestFactory().get("/", HTTP_HOST="brand.localhost")
        request.tenant = tenant
        mail.outbox.clear()
        with context.request_context(request):
            AccountAdapter(request).send_mail(prefix, "who@example.com", {"user": self.admin, **ctx})
        (msg,) = mail.outbox
        return msg, msg.alternatives[0][0]

    def test_password_reset_is_branded_with_the_club(self):
        msg, html = self.send("account/email/password_reset_key", {"password_reset_url": "https://x.test/reset/abc"}, self.club)
        self.assertEqual(msg.subject, "Reset your Brand FC password")
        for needle in ("#0a2540", "#12b886", "Brand FC", "https://x.test/reset/abc"):
            self.assertIn(needle, html)
        self.assertIn("https://x.test/reset/abc", msg.body)

    def test_email_confirmation_is_branded_and_has_the_link(self):
        msg, html = self.send("account/email/email_confirmation", {"activate_url": "https://x.test/verify/k1"}, self.club)
        self.assertEqual(msg.subject, "Confirm your email address for Brand FC")
        self.assertIn("https://x.test/verify/k1", html)
        msg, html = self.send("account/email/email_confirmation", {"code": "123456"}, self.club)
        self.assertIn("123456", html)

    def test_platform_emails_use_the_platform_brand(self):
        from types import SimpleNamespace

        public = SimpleNamespace(schema_name="public")
        msg, html = self.send("account/email/password_reset_key", {"password_reset_url": "https://x.test/r"}, public)
        self.assertEqual(msg.subject, "Reset your RosterChief password")
        self.assertIn("RosterChief", html)

    def test_notification_emails_are_branded_with_details_and_a_security_note(self):
        cases = {
            "password_changed": ("Your Brand FC password was changed", {}),
            "password_set": ("A password was set on your Brand FC account", {}),
            "password_reset": ("Your Brand FC password was reset", {}),
            "email_confirm": ("Your email address for Brand FC is confirmed", {}),
            "email_changed": ("Your Brand FC email address was changed", {"from_email": "old@example.com", "to_email": "new@example.com"}),
            "email_deleted": ("An email address was removed from your Brand FC account", {"deleted_email": "gone@example.com"}),
        }
        for kind, (subject, extra) in cases.items():
            with self.subTest(kind=kind):
                ctx = {"ip": "203.0.113.7", "user_agent": "TestBrowser/1.0", **extra}
                msg, html = self.send(f"account/email/{kind}", ctx, self.club)
                self.assertEqual(msg.subject, subject)
                for needle in ("#0a2540", "Brand FC", "203.0.113.7", "TestBrowser/1.0", "secure your account"):
                    self.assertIn(needle, html)
                for value in extra.values():
                    self.assertIn(value, html)
                    self.assertIn(value, msg.body)
                self.assertIn("203.0.113.7", msg.body)

    def test_code_emails_show_the_code(self):
        for kind, subject in (("login_code", "Your Brand FC sign-in code"), ("password_reset_code", "Your Brand FC password reset code")):
            with self.subTest(kind=kind):
                msg, html = self.send(f"account/email/{kind}", {"code": "A1B2C3"}, self.club)
                self.assertEqual(msg.subject, subject)
                self.assertIn("A1B2C3", html)
                self.assertIn("A1B2C3", msg.body)
                self.assertIn("#0a2540", html)

    def test_account_already_exists_and_unknown_account_have_a_button(self):
        msg, html = self.send("account/email/account_already_exists", {"email": "who@example.com", "password_reset_url": "https://x.test/forgot"}, self.club)
        self.assertEqual(msg.subject, "You already have a Brand FC account")
        self.assertIn("https://x.test/forgot", html)
        self.assertIn("https://x.test/forgot", msg.body)
        self.assertIn("#12b886", html)
        msg, html = self.send("account/email/unknown_account", {"email": "who@example.com", "signup_url": "https://x.test/signup"}, self.club)
        self.assertEqual(msg.subject, "We don't know this email address at Brand FC")
        self.assertIn("https://x.test/signup", html)
        self.assertIn("https://x.test/signup", msg.body)

    def test_new_emails_are_translated(self):
        from django.utils import translation

        with translation.override("nl"):
            msg, html = self.send("account/email/password_changed", {"ip": "1.2.3.4"}, self.club)
        self.assertEqual(msg.subject, "Je Brand FC-wachtwoord is gewijzigd")
        self.assertIn("IP-adres", html)
