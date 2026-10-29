"""The notification category's push policy (notifications.policy) applied to real APNs/FCM sends and the retry."""

import datetime
import json

import httpx
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from api.models import PushDevice
from notifications.models import Notification, NotificationCategory, NotificationDelivery, NotificationPriority
from notifications.policy import DAY, policy_for

from .delivery import NativePushChannel, deliver, deliver_to_user
from .push import ApnsGateway, FcmGateway, PushGateway
from .tests import PASSWORD, AppApiTestCase, FakeCredentials, apns_key

User = get_user_model()


class PolicyGatewayMixin:
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(email="policy@example.com", password=PASSWORD)
        self.requests = []

    def handler(self, request):
        self.requests.append(request)
        return httpx.Response(200, json={})

    def apns(self, policy, collapse_id=None, **kwargs):
        with override_settings(APP_PUSH_APNS_KEY=apns_key()):
            gateway = ApnsGateway(client=httpx.Client(transport=httpx.MockTransport(self.handler)))
            gateway.publish(PushDevice(user=self.user, installation_id="i1", platform="ios", push_token="tok"), title="t", body="b", data={}, policy=policy, collapse_id=collapse_id, **kwargs)
        return self.requests[-1]

    def fcm(self, policy, collapse_id=None):
        gateway = FcmGateway(client=httpx.Client(transport=httpx.MockTransport(self.handler)), credentials=FakeCredentials())
        gateway.publish(PushDevice(user=self.user, installation_id="i2", platform="android", push_token="tok"), title="t", body="b", data={"kind": "x", "category": "news"}, policy=policy, collapse_id=collapse_id)
        return json.loads(self.requests[-1].content)["message"]


class ApnsPolicyTests(PolicyGatewayMixin, TestCase):
    def test_news_expires_in_a_week_and_may_be_batched(self):
        request = self.apns(policy_for("news"))
        self.assertAlmostEqual(int(request.headers["apns-expiration"]), timezone.now().timestamp() + 7 * DAY, delta=5)
        self.assertEqual(request.headers["apns-priority"], "5")
        self.assertNotIn("apns-collapse-id", request.headers)

    def test_event_is_delivered_now(self):
        self.assertEqual(self.apns(policy_for("event")).headers["apns-priority"], "10")

    def test_game_collapses_on_the_id_and_lives_fifteen_minutes(self):
        request = self.apns(policy_for("game"), collapse_id="e" * 80)
        self.assertAlmostEqual(int(request.headers["apns-expiration"]), timezone.now().timestamp() + 900, delta=5)
        self.assertEqual(request.headers["apns-collapse-id"], "e" * 64)
        self.assertEqual(request.headers["apns-priority"], "10")

    def test_collapse_id_is_ignored_when_the_policy_does_not_collapse(self):
        self.assertNotIn("apns-collapse-id", self.apns(policy_for("news"), collapse_id="club").headers)

    def test_critical_is_immediate(self):
        self.assertEqual(self.apns(policy_for("news", NotificationPriority.CRITICAL)).headers["apns-priority"], "10")

    def test_no_policy_keeps_priority_10_and_no_expiration(self):
        request = self.apns(None)
        self.assertEqual(request.headers["apns-priority"], "10")
        self.assertNotIn("apns-expiration", request.headers)


class FcmPolicyTests(PolicyGatewayMixin, TestCase):
    def test_news_is_normal_priority_on_its_channel(self):
        android = self.fcm(policy_for("news"))["android"]
        self.assertEqual((android["priority"], android["ttl"]), ("NORMAL", f"{7 * DAY}s"))
        self.assertEqual(android["notification"]["channel_id"], "rc_news")
        self.assertNotIn("collapse_key", android)

    def test_event_is_high(self):
        self.assertEqual(self.fcm(policy_for("event"))["android"]["priority"], "HIGH")

    def test_game_collapses_with_the_key(self):
        android = self.fcm(policy_for("game"), collapse_id="event-1")["android"]
        self.assertEqual((android["ttl"], android["collapse_key"], android["priority"]), ("900s", "event-1", "HIGH"))
        self.assertEqual(android["notification"]["channel_id"], "rc_game")

    def test_critical_is_high(self):
        self.assertEqual(self.fcm(policy_for("news", "critical"))["android"]["priority"], "HIGH")

    def test_collapse_key_only_when_the_policy_collapses(self):
        self.assertNotIn("collapse_key", self.fcm(policy_for("event"), collapse_id="x")["android"])

    @override_settings(APP_PUSH_FCM_CHANNEL_ID="legacy")
    def test_without_a_policy_the_configured_channel_is_used(self):
        android = self.fcm(None)["android"]
        self.assertEqual(android["notification"]["channel_id"], "legacy")
        self.assertNotIn("ttl", android)

    def test_data_carries_the_category(self):
        self.assertEqual(self.fcm(policy_for("news"))["data"]["category"], "news")


class RecordingGateway(PushGateway):
    published: list = []
    fail = False

    def publish(self, device, *, title, body, data, badge=None, thread_id=None, policy=None, collapse_id=None):
        RecordingGateway.published.append({"data": data, "thread_id": thread_id, "policy": policy, "collapse_id": collapse_id})
        if RecordingGateway.fail:
            raise httpx.ConnectError("no route")
        return True


@override_settings(APP_PUSH_GATEWAY="api.protected.test_push_policy.RecordingGateway")
class DeliveryPolicyTests(AppApiTestCase):
    def setUp(self):
        super().setUp()
        RecordingGateway.published, RecordingGateway.fail = [], False
        PushDevice.objects.create(user=self.user, installation_id="phone", platform="ios", push_token="t")

    def notification(self, **kwargs):
        return Notification.objects.create(club=self.club, member=self.member, title="Hi", body="There", **kwargs)

    def test_deliver_passes_the_policy_and_the_category_in_the_data(self):
        deliver([self.notification(category=NotificationCategory.NEWS).pk])

        (sent,) = RecordingGateway.published
        self.assertEqual(sent["policy"], policy_for("news"))
        self.assertEqual(sent["data"]["category"], "news")

    def test_a_critical_notification_is_loud(self):
        deliver([self.notification(category=NotificationCategory.NEWS, priority=NotificationPriority.CRITICAL).pk])

        self.assertEqual(RecordingGateway.published[0]["policy"].apns_priority, 10)

    def test_a_live_game_push_collapses_per_event(self):
        deliver_to_user(self.user, texts=lambda language: ("t", "b"), data={"kind": "game_moment", "category": "game"}, thread_id="event-1", club=self.club, category="game")

        (sent,) = RecordingGateway.published
        self.assertEqual((sent["policy"], sent["collapse_id"]), (policy_for("game"), "event-1"))

    def test_the_retry_payload_keeps_the_policy(self):
        RecordingGateway.fail = True
        deliver([self.notification(category=NotificationCategory.EVENT, priority=NotificationPriority.CRITICAL).pk])
        delivery = NotificationDelivery.objects.get()
        self.assertEqual((delivery.payload["category"], delivery.payload["priority"]), ("event", "critical"))
        RecordingGateway.published, RecordingGateway.fail = [], False

        NativePushChannel().retry(delivery)

        (sent,) = RecordingGateway.published
        self.assertEqual(sent["policy"], policy_for("event", "critical"))

    def test_a_game_retry_keeps_the_collapse_id(self):
        RecordingGateway.fail = True
        deliver_to_user(self.user, texts=lambda language: ("t", "b"), data={}, thread_id="event-1", club=self.club, category="game")
        delivery = NotificationDelivery.objects.get()
        RecordingGateway.published, RecordingGateway.fail = [], False

        NativePushChannel().retry(delivery)

        self.assertEqual(RecordingGateway.published[0]["collapse_id"], "event-1")

    def test_an_expired_retry_is_not_sent(self):
        RecordingGateway.fail = True
        deliver_to_user(self.user, texts=lambda language: ("t", "b"), data={}, thread_id="event-1", club=self.club, category="game")
        delivery = NotificationDelivery.objects.get()
        NotificationDelivery.objects.filter(pk=delivery.pk).update(created=timezone.now() - datetime.timedelta(minutes=20))
        delivery.refresh_from_db()
        RecordingGateway.published, RecordingGateway.fail = [], False

        NativePushChannel().retry(delivery)

        delivery.refresh_from_db()
        self.assertEqual(RecordingGateway.published, [])
        self.assertEqual((delivery.status, delivery.last_error), (NotificationDelivery.Status.FAILED, "expired"))
