from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone

from members.models import Member
from rosterchief.test_support import make_club

from . import delivery_log
from .channels import EmailChannel
from .models import Notification, NotificationDelivery
from .services import _send_email, send_notification_emails

User = get_user_model()
Status = NotificationDelivery.Status


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class EmailDeliveryLogTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.club = make_club()
        cls.user = User.objects.create_user(email="jamie@example.com", password="pw-secret-123")
        cls.member = Member.objects.create(user=cls.user, first_name="Jamie", last_name="Doe", email="jamie@example.com")
        cls.notification = Notification.objects.create(club=cls.club, member=cls.member, title="Big win", body="We won 3-0.")

    def test_a_sent_mail_is_logged_per_address(self):
        _send_email(self.notification, ["jamie@example.com", "other@example.com"])

        rows = {row.target: row for row in NotificationDelivery.objects.all()}
        self.assertEqual(set(rows), {"jamie@example.com", "other@example.com"})
        for row in rows.values():
            self.assertEqual((row.channel, row.status, row.club, row.notification), ("email", Status.SENT, self.club, self.notification))
        self.assertEqual(rows["jamie@example.com"].user, self.user)
        self.assertIsNone(rows["other@example.com"].user)

    def test_an_oserror_is_logged_for_retry_and_other_addresses_still_go_out(self):
        calls = []

        def flaky(message, **kwargs):
            if message.to == ["a@example.com"]:
                raise OSError("smtp down")
            calls.append(message.to)

        with patch("notifications.services.send_message", side_effect=flaky):
            delivered = _send_email(self.notification, ["a@example.com", "b@example.com"])

        self.assertEqual(delivered, ["b@example.com"])
        failed = NotificationDelivery.objects.get(target="a@example.com")
        self.assertEqual((failed.status, failed.payload), (Status.RETRYING, {"notification": str(self.notification.pk), "email": "a@example.com"}))
        self.assertIn("smtp down", failed.last_error)
        self.assertEqual(NotificationDelivery.objects.get(target="b@example.com").status, Status.SENT)

    def test_mail_with_attachments_is_logged_failed_without_a_payload(self):
        with patch("notifications.services.send_message", side_effect=OSError("smtp down")):
            _send_email(self.notification, ["a@example.com"], attachments=[("a.pdf", b"x", "application/pdf")])

        row = NotificationDelivery.objects.get()
        self.assertEqual((row.status, row.payload), (Status.FAILED, {}))
        self.assertIn("smtp down", row.last_error)

    def test_sent_at_is_only_set_when_an_address_succeeded(self):
        with patch("notifications.services.send_message", side_effect=OSError("down")):
            send_notification_emails([self.notification])

        self.notification.refresh_from_db()
        self.assertIsNone(self.notification.sent_at)

        send_notification_emails([self.notification])
        self.notification.refresh_from_db()
        self.assertEqual(self.notification.sent_to_emails, ["jamie@example.com"])

    def test_a_logging_failure_never_breaks_delivery(self):
        with patch.object(NotificationDelivery.objects, "create", side_effect=RuntimeError("db")):
            delivered = _send_email(self.notification, ["jamie@example.com"])

        self.assertEqual(delivered, ["jamie@example.com"])
        self.assertEqual(len(mail.outbox), 1)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class EmailRetryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.club = make_club()
        cls.member = Member.objects.create(first_name="Jamie", last_name="Doe")
        cls.notification = Notification.objects.create(club=cls.club, member=cls.member, title="Big win", body="We won 3-0.")

    def make_delivery(self, notification=None):
        notification = notification or self.notification
        return delivery_log.record(club=self.club, channel="email", target="a@example.com", outcome="retry", notification=notification, payload={"notification": str(notification.pk), "email": "a@example.com"})

    def test_success_sends_to_that_single_address_and_clears_the_payload(self):
        delivery = self.make_delivery()
        EmailChannel().retry(delivery)

        delivery.refresh_from_db()
        self.assertEqual((delivery.status, delivery.payload), (Status.SENT, {}))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["a@example.com"])
        self.assertIn("Big win", mail.outbox[0].subject)

    def test_a_transient_failure_bumps_attempts_and_reschedules(self):
        delivery = self.make_delivery()
        before = timezone.now()
        with patch("notifications.services.send_message", side_effect=OSError("down")):
            EmailChannel().retry(delivery)

        delivery.refresh_from_db()
        self.assertEqual((delivery.status, delivery.attempts), (Status.RETRYING, 2))
        self.assertGreater(delivery.next_attempt_at, before)

    def test_exhausted_attempts_fail(self):
        delivery = self.make_delivery()
        NotificationDelivery.objects.filter(pk=delivery.pk).update(attempts=delivery_log.MAX_ATTEMPTS)
        delivery.refresh_from_db()
        with patch("notifications.services.send_message", side_effect=OSError("down")):
            EmailChannel().retry(delivery)

        delivery.refresh_from_db()
        self.assertEqual(delivery.status, Status.FAILED)

    def test_a_deleted_notification_is_dead(self):
        delivery = self.make_delivery()
        self.notification.delete()  # SET_NULL keeps the row, its payload still names the notification
        delivery.refresh_from_db()
        EmailChannel().retry(delivery)

        delivery.refresh_from_db()
        self.assertEqual(delivery.status, Status.DEAD)
        self.assertEqual(len(mail.outbox), 0)

    def test_rows_are_club_scoped(self):
        _send_email(self.notification, ["a@example.com"])

        self.assertEqual(NotificationDelivery.objects.filter(club=self.club).count(), 1)
        self.assertFalse(NotificationDelivery.objects.exclude(club=self.club).exists())
