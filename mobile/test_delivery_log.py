import json
from datetime import timedelta
from unittest import mock
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from pywebpush import WebPushException
from requests.exceptions import ConnectionError as RequestsConnectionError

from members.models import Member
from notifications import delivery_log
from notifications.models import NotificationDelivery
from rosterchief.test_support import make_club

from .channels import WebPushChannel
from .models import PushSubscription
from .services.push import send_push_to_member, send_push_to_user

User = get_user_model()
Status = NotificationDelivery.Status
ENDPOINT = "https://fcm.googleapis.com/fcm/send/abc"


def _error(status):
    return WebPushException("boom", response=mock.Mock(status_code=status))


@override_settings(VAPID_PRIVATE_KEY="test-private-key", VAPID_ADMIN_EMAIL="admin@example.com")
class WebPushDeliveryLogTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.club = make_club()
        cls.user = User.objects.create_user(email="lars@example.com", password="pw-secret-123")
        cls.member = Member.objects.create(user=cls.user, first_name="Lars", last_name="Bakker")
        cls.subscription = PushSubscription.objects.create(club=cls.club, member=cls.member, endpoint=ENDPOINT, p256dh="key1", auth="key2")

    def send(self, **kwargs):
        send_push_to_member(self.member, title="Hi", body="There", **kwargs)
        return NotificationDelivery.objects.get()

    def test_a_success_is_logged_as_sent(self):
        with patch("mobile.services.push.webpush"):
            row = self.send()

        self.assertEqual((row.channel, row.status, row.target), ("web", Status.SENT, str(self.subscription.pk)))
        self.assertEqual((row.club, row.user), (self.club, self.user))
        self.assertEqual(row.payload, {})

    def test_a_gone_subscription_is_logged_as_dead_and_deleted(self):
        with patch("mobile.services.push.webpush", side_effect=_error(410)):
            row = self.send()

        self.assertEqual(row.status, Status.DEAD)
        self.assertFalse(PushSubscription.objects.exists())

    def test_a_refusal_is_logged_as_failed(self):
        with patch("mobile.services.push.webpush", side_effect=_error(400)):
            row = self.send()

        self.assertEqual(row.status, Status.FAILED)
        self.assertTrue(PushSubscription.objects.exists())

    def test_a_bad_key_is_logged_as_failed(self):
        with patch("mobile.services.push.webpush", side_effect=ValueError("Invalid EC key.")):
            self.assertEqual(self.send().status, Status.FAILED)

    def test_throttling_and_server_errors_and_network_errors_are_retried(self):
        for exc in (_error(429), _error(503), RequestsConnectionError("down")):
            NotificationDelivery.objects.all().delete()
            with self.subTest(exc=repr(exc)), patch("mobile.services.push.webpush", side_effect=exc):
                row = self.send()
                self.assertEqual(row.status, Status.RETRYING)
                self.assertEqual(row.payload, {"title": "Hi", "body": "There", "url": "/app/"})
                self.assertIsNotNone(row.next_attempt_at)
                self.assertTrue(PushSubscription.objects.exists())

    def test_the_notification_is_recorded_when_known(self):
        from notifications.models import Notification

        notification = Notification.objects.create(club=self.club, member=self.member, title="T", body="B")
        with patch("mobile.services.push.webpush"):
            row = self.send(notification=notification)

        self.assertEqual(row.notification, notification)

    def test_send_push_to_user_logs_too(self):
        with patch("mobile.services.push.webpush"):
            send_push_to_user(self.club, self.user, title="Hi", body="There")

        self.assertEqual(NotificationDelivery.objects.filter(club=self.club, channel="web", status=Status.SENT).count(), 1)

    def test_a_logging_failure_never_breaks_delivery(self):
        with patch("mobile.services.push.webpush") as mock_webpush, patch.object(NotificationDelivery.objects, "create", side_effect=RuntimeError("db")):
            send_push_to_member(self.member, title="Hi", body="There")

        mock_webpush.assert_called_once()
        self.assertFalse(NotificationDelivery.objects.exists())


@override_settings(VAPID_PRIVATE_KEY="test-private-key", VAPID_ADMIN_EMAIL="admin@example.com")
class WebPushRetryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.club = make_club()
        cls.member = Member.objects.create(first_name="Lars", last_name="Bakker")
        cls.subscription = PushSubscription.objects.create(club=cls.club, member=cls.member, endpoint=ENDPOINT, p256dh="key1", auth="key2")

    def make_delivery(self, **kwargs):
        defaults = {"club": self.club, "channel": "web", "target": self.subscription.pk, "outcome": "retry", "payload": {"title": "Hi", "body": "There", "url": "/app/"}}
        defaults.update(kwargs)
        return delivery_log.record(**defaults)

    def test_success_marks_sent_and_clears_the_payload(self):
        delivery = self.make_delivery()
        with patch("mobile.services.push.webpush") as mock_webpush:
            WebPushChannel().retry(delivery)

        delivery.refresh_from_db()
        self.assertEqual((delivery.status, delivery.payload, delivery.next_attempt_at), (Status.SENT, {}, None))
        self.assertEqual(json.loads(mock_webpush.call_args.kwargs["data"]), {"title": "Hi", "body": "There", "url": "/app/"})

    def test_a_transient_failure_bumps_attempts_and_reschedules(self):
        delivery = self.make_delivery()
        before = timezone.now()
        with patch("mobile.services.push.webpush", side_effect=_error(500)):
            WebPushChannel().retry(delivery)

        delivery.refresh_from_db()
        self.assertEqual((delivery.status, delivery.attempts), (Status.RETRYING, 2))
        self.assertGreater(delivery.next_attempt_at, before)
        self.assertTrue(delivery.payload)

    def test_running_out_of_attempts_fails(self):
        delivery = self.make_delivery()
        NotificationDelivery.objects.filter(pk=delivery.pk).update(attempts=delivery_log.MAX_ATTEMPTS)
        delivery.refresh_from_db()
        with patch("mobile.services.push.webpush", side_effect=_error(500)):
            WebPushChannel().retry(delivery)

        delivery.refresh_from_db()
        self.assertEqual(delivery.status, Status.FAILED)

    def test_a_permanent_refusal_fails_without_retrying(self):
        delivery = self.make_delivery()
        with patch("mobile.services.push.webpush", side_effect=_error(400)):
            WebPushChannel().retry(delivery)

        delivery.refresh_from_db()
        self.assertEqual(delivery.status, Status.FAILED)

    def test_a_gone_registration_deletes_the_subscription_and_marks_dead(self):
        delivery = self.make_delivery()
        with patch("mobile.services.push.webpush", side_effect=_error(404)):
            WebPushChannel().retry(delivery)

        delivery.refresh_from_db()
        self.assertEqual(delivery.status, Status.DEAD)
        self.assertFalse(PushSubscription.objects.exists())

    def test_a_deleted_subscription_marks_dead_without_sending(self):
        delivery = self.make_delivery()
        self.subscription.delete()
        with patch("mobile.services.push.webpush") as mock_webpush:
            WebPushChannel().retry(delivery)

        delivery.refresh_from_db()
        self.assertEqual(delivery.status, Status.DEAD)
        mock_webpush.assert_not_called()

    def test_retry_due_hands_due_rows_to_the_channel(self):
        delivery = self.make_delivery()
        NotificationDelivery.objects.filter(pk=delivery.pk).update(next_attempt_at=timezone.now() - timedelta(minutes=1))
        with patch("mobile.services.push.webpush"):
            self.assertEqual(delivery_log.retry_due(), 1)

        delivery.refresh_from_db()
        self.assertEqual(delivery.status, Status.SENT)

    def test_rows_are_scoped_to_their_club(self):
        other = make_club(name="Other", slug="other")
        delivery = self.make_delivery(club=other)  # target is another club's subscription
        WebPushChannel().retry(delivery)

        delivery.refresh_from_db()
        self.assertEqual((delivery.club, delivery.status), (other, Status.DEAD))
        self.assertTrue(PushSubscription.objects.filter(pk=self.subscription.pk).exists())
