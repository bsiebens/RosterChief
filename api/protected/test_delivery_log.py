"""The native push channel's entries in the delivery log (notifications.NotificationDelivery) and its retry."""

import datetime

import httpx
from django.test import override_settings
from django.utils import timezone

from api.models import PushDevice
from notifications import delivery_log
from notifications.models import Notification, NotificationDelivery

from .delivery import NativePushChannel, deliver, deliver_to_user
from .push import ApnsError, PushGateway
from .tests import AppApiTestCase

Status = NotificationDelivery.Status


class ScriptedGateway(PushGateway):
    """Answers each publish from ``outcomes`` (an exception to raise, or the bool to return); True when empty."""

    outcomes: list = []
    published: list = []

    def publish(self, device, *, title, body, data, badge=None, thread_id=None, policy=None, collapse_id=None):
        ScriptedGateway.published.append((device.installation_id, title, body, data, badge, thread_id))
        outcome = ScriptedGateway.outcomes.pop(0) if ScriptedGateway.outcomes else True
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@override_settings(APP_PUSH_GATEWAY="api.protected.test_delivery_log.ScriptedGateway")
class NativeDeliveryLogTests(AppApiTestCase):
    def setUp(self):
        super().setUp()
        ScriptedGateway.outcomes = []
        ScriptedGateway.published = []
        self.device = PushDevice.objects.create(user=self.user, installation_id="parent-phone", platform="ios", push_token="t")
        self.notification = Notification.objects.create(club=self.club, member=self.member, title="Hi", body="There")

    def only_delivery(self) -> NotificationDelivery:
        self.assertEqual(NotificationDelivery.objects.count(), 1)
        return NotificationDelivery.objects.get()

    def test_a_sent_push_is_logged(self):
        self.assertEqual(deliver([self.notification.pk]), 1)

        delivery = self.only_delivery()
        self.assertEqual((delivery.status, delivery.channel, delivery.target), (Status.SENT, "native", "parent-phone"))
        self.assertEqual((delivery.club, delivery.notification, delivery.user), (self.club, self.notification, self.user))
        self.assertEqual(delivery.payload, {})

    def test_a_dead_token_is_logged_and_the_device_disabled(self):
        ScriptedGateway.outcomes = [False]

        self.assertEqual(deliver([self.notification.pk]), 0)

        self.assertEqual(self.only_delivery().status, Status.DEAD)
        self.device.refresh_from_db()
        self.assertFalse(self.device.enabled)

    def test_a_refusal_is_logged_as_failed_without_retry(self):
        ScriptedGateway.outcomes = [ApnsError("BadDeviceToken", status_code=400)]

        deliver([self.notification.pk])

        delivery = self.only_delivery()
        self.assertEqual(delivery.status, Status.FAILED)
        self.assertIn("BadDeviceToken", delivery.last_error)
        self.assertIsNone(delivery.next_attempt_at)
        self.assertEqual(delivery.payload, {})

    def test_throttling_and_network_errors_wait_to_retry_with_the_payload(self):
        for error in (ApnsError("TooManyRequests", status_code=429), httpx.ConnectError("no route")):
            NotificationDelivery.objects.all().delete()
            ScriptedGateway.outcomes = [error]

            deliver([self.notification.pk])

            delivery = self.only_delivery()
            self.assertEqual(delivery.status, Status.RETRYING, error)
            self.assertGreater(delivery.next_attempt_at, timezone.now())
            self.assertEqual(delivery.payload["title"], "Hi")
            self.assertEqual(delivery.payload["body"], "There")
            self.assertEqual(delivery.payload["thread_id"], self.club.slug)
            self.assertEqual(delivery.payload["data"]["notification_id"], str(self.notification.pk))
            self.assertIn("badge", delivery.payload)

    def test_a_retry_that_succeeds_marks_sent_and_clears_the_payload(self):
        ScriptedGateway.outcomes = [ApnsError("TooManyRequests", status_code=429)]
        deliver([self.notification.pk])
        delivery = self.only_delivery()
        ScriptedGateway.published = []

        NativePushChannel().retry(delivery)

        delivery.refresh_from_db()
        self.assertEqual(delivery.status, Status.SENT)
        self.assertEqual(delivery.payload, {})
        self.assertEqual([(entry[0], entry[1], entry[5]) for entry in ScriptedGateway.published], [("parent-phone", "Hi", self.club.slug)])

    def test_the_retry_job_hands_due_rows_to_the_channel(self):
        ScriptedGateway.outcomes = [httpx.ConnectError("no route")]
        deliver([self.notification.pk])

        self.assertEqual(delivery_log.retry_due(now=timezone.now() + datetime.timedelta(minutes=2)), 1)

        self.assertEqual(self.only_delivery().status, Status.SENT)

    def test_a_retry_with_a_dead_token_disables_the_device(self):
        ScriptedGateway.outcomes = [ApnsError("Unavailable", status_code=503)]
        deliver([self.notification.pk])
        delivery = self.only_delivery()
        ScriptedGateway.outcomes = [False]

        NativePushChannel().retry(delivery)

        delivery.refresh_from_db()
        self.assertEqual(delivery.status, Status.DEAD)
        self.device.refresh_from_db()
        self.assertFalse(self.device.enabled)

    def test_a_retry_for_a_vanished_device_is_dead(self):
        ScriptedGateway.outcomes = [ApnsError("Unavailable", status_code=503)]
        deliver([self.notification.pk])
        delivery = self.only_delivery()
        self.device.delete()

        NativePushChannel().retry(delivery)

        delivery.refresh_from_db()
        self.assertEqual(delivery.status, Status.DEAD)

    def test_a_retry_keeps_trying_until_the_attempts_run_out(self):
        ScriptedGateway.outcomes = [ApnsError("Unavailable", status_code=503)] * (delivery_log.MAX_ATTEMPTS + 1)
        deliver([self.notification.pk])
        delivery = self.only_delivery()

        for _ in range(delivery_log.MAX_ATTEMPTS):
            self.assertEqual(delivery.status, Status.RETRYING)
            NativePushChannel().retry(delivery)

        delivery.refresh_from_db()
        self.assertEqual(delivery.status, Status.FAILED)
        self.assertEqual(delivery.payload, {})
        self.assertIsNone(delivery.next_attempt_at)

    def test_a_retry_failing_for_good_stops(self):
        ScriptedGateway.outcomes = [ApnsError("Unavailable", status_code=503), ApnsError("BadDeviceToken", status_code=400)]
        deliver([self.notification.pk])
        delivery = self.only_delivery()

        NativePushChannel().retry(delivery)

        delivery.refresh_from_db()
        self.assertEqual(delivery.status, Status.FAILED)

    def test_a_game_push_is_logged_without_a_notification(self):
        sent = deliver_to_user(self.user, texts=lambda language: ("Goal", "1-0"), data={"kind": "game"}, thread_id="game-1", club=self.club)

        self.assertEqual(sent, 1)
        delivery = self.only_delivery()
        self.assertEqual((delivery.status, delivery.club, delivery.user), (Status.SENT, self.club, self.user))
        self.assertIsNone(delivery.notification)

    def test_a_failed_game_push_can_be_retried(self):
        ScriptedGateway.outcomes = [httpx.ConnectError("no route")]
        NativePushChannel().deliver_to_user(club=self.club, user=self.user, texts=lambda language: ("Goal", "1-0"), data={"kind": "game"}, url="/x", thread_id="game-1")
        delivery = self.only_delivery()
        self.assertEqual((delivery.status, delivery.payload["thread_id"]), (Status.RETRYING, "game-1"))

        NativePushChannel().retry(delivery)

        delivery.refresh_from_db()
        self.assertEqual(delivery.status, Status.SENT)


class DeviceCleanupTests(AppApiTestCase):
    def test_purge_drops_long_disabled_and_long_unseen_devices(self):
        from . import cleanup

        now = timezone.now()
        keep = PushDevice.objects.create(user=self.user, installation_id="keep", platform="ios", push_token="a")
        PushDevice.objects.create(user=self.user, installation_id="recent-dead", platform="ios", push_token="b", enabled=False)
        old_dead = PushDevice.objects.create(user=self.user, installation_id="old-dead", platform="ios", push_token="c", enabled=False)
        unseen = PushDevice.objects.create(user=self.user, installation_id="unseen", platform="ios", push_token="d")
        PushDevice.objects.filter(pk=old_dead.pk).update(last_seen=now - datetime.timedelta(days=31))
        PushDevice.objects.filter(pk=unseen.pk).update(last_seen=now - datetime.timedelta(days=181))
        PushDevice.objects.filter(pk=keep.pk).update(last_seen=now - datetime.timedelta(days=100))

        self.assertEqual(cleanup.purge(now), 2)
        self.assertEqual(set(PushDevice.objects.values_list("installation_id", flat=True)), {"keep", "recent-dead"})

    def test_registering_a_token_drops_the_earlier_registration_of_it(self):
        old = PushDevice.objects.create(user=self.user, installation_id="old-install-id", platform="ios", push_token="same-token")
        other = PushDevice.objects.create(user=self.user, installation_id="other", platform="ios", push_token="different")
        token = self.login()
        response = self.api("post", "/devices", token=token, data={"installation_id": "new-install-id", "platform": "ios", "push_token": "same-token", "app_version": "1", "locale": "en"})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(PushDevice.objects.filter(pk=old.pk).exists())
        self.assertTrue(PushDevice.objects.filter(pk=other.pk).exists())
        self.assertTrue(PushDevice.objects.filter(installation_id="new-install-id").exists())
