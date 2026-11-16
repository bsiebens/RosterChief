"""Delivery log, retries, quiet hours, dead tokens, preferences gating and email."""

import uuid
from datetime import timedelta

from django.core import mail
from django.test import override_settings
from django.utils import timezone

from club.models import Club

from . import delivery, preferences
from .gateways import DEAD, FAILED, RETRY, SENT, GatewayResult
from .models import NotificationDelivery, PushDevice, SandboxMessage, WebPushSubscription
from .services import send_now
from .tests_support import PushTestCase, make_device, make_subscription, make_user

Status = NotificationDelivery.Status


def rows():
    return list(NotificationDelivery.objects.order_by("created"))


class DeliveryLogTests(PushTestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user()
        self.ios = make_device(self.user, "ios")

    def test_sent_creates_one_row_per_target_with_duration_and_no_payload(self):
        android = make_device(self.user, "android")
        sub = make_subscription(self.user)
        gateway = self.use_gateway(GatewayResult(SENT))
        results = send_now(self.user, title="Hi", body="There", category="news")
        self.assertEqual(len(gateway.calls), 3)
        self.assertEqual({(r.provider, r.status) for r in results}, {("apns", "sent"), ("fcm", "sent"), ("webpush", "sent")})
        self.assertEqual({r.target for r in results}, {self.ios.installation_id, android.installation_id, sub.endpoint})
        for row in rows():
            self.assertEqual((row.status, row.attempts, row.category, row.title, row.user), ("sent", 1, "news", "Hi", self.user))
            self.assertIsNone(row.payload)
            self.assertIsNotNone(row.duration_ms)
            self.assertIsNone(row.next_attempt_at)

    def test_masked_targets_in_results(self):
        self.use_gateway()
        (result,) = send_now(self.user, title="t", body="b")
        self.assertTrue(result.target_masked.endswith("…") or result.target_masked == result.target)
        self.assertEqual(result.delivery_id, rows()[0].pk)

    def test_retry_ladder_and_giving_up(self):
        self.use_gateway(GatewayResult(RETRY, "HTTP 503"))
        (result,) = send_now(self.user, title="Hi", body="There")
        self.assertEqual(result.status, "retrying")
        row = rows()[0]
        self.assertEqual((row.status, row.attempts, row.last_error), ("retrying", 1, "HTTP 503"))
        self.assertIsNotNone(row.payload)
        self.assertAlmostEqual(row.next_attempt_at.timestamp(), (timezone.now() + timedelta(minutes=1)).timestamp(), delta=5)
        self.assertEqual(delivery.retry_due(timezone.now()), 0)  # not due yet
        now = timezone.now()
        for expected_attempts, wait in ((2, 5), (3, 30)):
            now += timedelta(hours=1)
            self.assertEqual(delivery.retry_due(now), 1)
            row.refresh_from_db()
            self.assertEqual((row.status, row.attempts), ("retrying", expected_attempts))
            self.assertAlmostEqual(row.next_attempt_at.timestamp(), (now + timedelta(minutes=wait)).timestamp(), delta=5)
        now += timedelta(hours=1)
        delivery.retry_due(now)
        row.refresh_from_db()
        self.assertEqual((row.status, row.attempts, row.payload, row.next_attempt_at), ("failed", 4, None, None))

    def test_delays_come_from_the_settings(self):
        self.settings.retry_delays_minutes = [2]
        self.settings.save()
        self.use_gateway(GatewayResult(RETRY, "503"))
        send_now(self.user, title="t", body="b")
        row = rows()[0]
        self.assertAlmostEqual(row.next_attempt_at.timestamp(), (timezone.now() + timedelta(minutes=2)).timestamp(), delta=5)
        delivery.retry_due(timezone.now() + timedelta(minutes=3))
        row.refresh_from_db()
        self.assertEqual((row.status, row.attempts), ("failed", 2))

    def test_a_retry_that_succeeds_finishes_the_row(self):
        self.use_gateway(GatewayResult(RETRY, "503"), GatewayResult(SENT))
        send_now(self.user, title="t", body="b")
        delivery.retry_due(timezone.now() + timedelta(minutes=2))
        row = rows()[0]
        self.assertEqual((row.status, row.attempts, row.last_error, row.payload), ("sent", 2, "", None))

    def test_non_retryable_failure_is_final(self):
        self.use_gateway(GatewayResult(FAILED, "PayloadTooLarge"))
        send_now(self.user, title="t", body="b")
        row = rows()[0]
        self.assertEqual((row.status, row.last_error, row.payload, row.next_attempt_at), ("failed", "PayloadTooLarge", None, None))

    def test_expired_after_the_category_ttl(self):
        self.use_gateway(GatewayResult(RETRY, "503"))
        send_now(self.user, title="Goal!", body="1-0", category="game")  # 15 minutes to live
        delivery.retry_due(timezone.now() + timedelta(minutes=16))
        row = rows()[0]
        self.assertEqual((row.status, row.last_error, row.payload), ("failed", "expired", None))

    def test_retry_is_not_picked_up_twice(self):
        fake = self.use_gateway(GatewayResult(RETRY, "503"))
        send_now(self.user, title="t", body="b")
        later = timezone.now() + timedelta(minutes=2)
        delivery.retry_due(later)
        delivery.retry_due(later)  # the lease pushed next_attempt_at forward
        self.assertEqual(len(fake.calls), 2)

    def test_dedupe_on_notification_id(self):
        fake = self.use_gateway()
        nid = uuid.uuid4()
        delivery.deliver_to_user(self.user, title="t", body="b", notification_id=nid)
        delivery.deliver_to_user(self.user, title="t", body="b", notification_id=nid)
        self.assertEqual((len(fake.calls), NotificationDelivery.objects.count()), (1, 1))
        delivery.deliver_to_user(self.user, title="t", body="b", notification_id=uuid.uuid4())
        self.assertEqual(len(fake.calls), 2)

    def test_only_sends_to_one_target_and_ignores_preferences(self):
        other = make_device(self.user, "ios")
        preferences.set_master_switch(self.user, "push", False)
        fake = self.use_gateway()
        results = send_now(self.user, title="t", body="b", only=other, respect_preferences=False)
        self.assertEqual([r.target for r in results], [other.installation_id])
        self.assertEqual(len(fake.calls), 1)

    def test_inactive_account_gets_nothing(self):
        self.user.is_active = False
        self.user.save()
        fake = self.use_gateway()
        self.assertEqual(send_now(self.user, title="t", body="b"), [])
        self.assertEqual(fake.calls, [])

    def test_providers_disabled_in_the_settings_are_skipped_without_a_row(self):
        make_device(self.user, "android")
        sub = make_subscription(self.user)
        self.settings.apns_enabled = False
        self.settings.webpush_enabled = False
        self.settings.save()
        fake = self.use_gateway()
        results = send_now(self.user, title="t", body="b")
        self.assertEqual([r.provider for r in results], ["fcm"])
        self.assertEqual(NotificationDelivery.objects.count(), 1)
        self.assertEqual(send_now(self.user, title="t", body="b", only=sub), [])
        self.assertEqual(len(fake.calls), 1)

    def test_web_subscription_of_another_club_is_not_used(self):
        a = Club(name="A", schema_name="del_a", owner=self.user)
        b = Club(name="B", schema_name="del_b", owner=self.user)
        for club in (a, b):
            club.auto_create_schema = False
            club.save()
        make_subscription(self.user, club=a)
        free = make_subscription(self.user, club=None)
        self.use_gateway()
        results = send_now(self.user, title="t", body="b", club=b)
        self.assertEqual({r.target for r in results if r.provider == "webpush"}, {free.endpoint})


class DeadTokenTests(PushTestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user()

    def test_dead_native_token_disables_the_device(self):
        device = make_device(self.user, "ios")
        self.use_gateway(GatewayResult(DEAD, "Unregistered"))
        (result,) = send_now(self.user, title="t", body="b")
        device.refresh_from_db()
        self.assertEqual(result.status, "dead")
        self.assertFalse(device.enabled)
        self.assertIsNotNone(device.disabled_at)
        self.assertEqual(rows()[0].status, "dead")
        self.assertEqual(send_now(self.user, title="t", body="b"), [])  # disabled devices are no longer targets

    def test_dead_web_subscription_is_deleted_but_logged(self):
        sub = make_subscription(self.user)
        self.use_gateway(GatewayResult(DEAD, "410"))
        send_now(self.user, title="t", body="b")
        self.assertFalse(WebPushSubscription.objects.exists())
        row = rows()[0]
        self.assertEqual((row.status, row.target, row.provider), ("dead", sub.endpoint, "webpush"))

    def test_dead_found_by_a_retry_disables_too(self):
        device = make_device(self.user, "android")
        self.use_gateway(GatewayResult(RETRY, "503"), GatewayResult(DEAD, "UNREGISTERED"))
        send_now(self.user, title="t", body="b")
        delivery.retry_due(timezone.now() + timedelta(minutes=2))
        device.refresh_from_db()
        self.assertEqual((rows()[0].status, device.enabled), ("dead", False))

    def test_retry_for_a_device_that_disappeared_is_dead(self):
        device = make_device(self.user, "ios")
        self.use_gateway(GatewayResult(RETRY, "503"))
        send_now(self.user, title="t", body="b")
        device.delete()
        delivery.retry_due(timezone.now() + timedelta(minutes=2))
        row = rows()[0]
        self.assertEqual((row.status, row.last_error), ("dead", "device gone or disabled"))

    @override_settings(DEBUG=False)
    def test_the_sandbox_dead_scenario_does_not_touch_real_devices_outside_debug(self):
        device = make_device(self.user, "ios")
        self.use_gateway(GatewayResult(DEAD, "sandbox", sandboxed=True))
        send_now(self.user, title="t", body="b")
        device.refresh_from_db()
        self.assertTrue(device.enabled)
        self.assertEqual(rows()[0].status, "dead")

    @override_settings(DEBUG=True)
    def test_the_sandbox_dead_scenario_disables_in_debug(self):
        device = make_device(self.user, "ios")
        self.use_gateway(GatewayResult(DEAD, "sandbox", sandboxed=True))
        send_now(self.user, title="t", body="b")
        device.refresh_from_db()
        self.assertFalse(device.enabled)


class SandboxDeliveryTests(PushTestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user()
        self.device = make_device(self.user, "ios")

    def test_force_sandbox_captures_without_the_global_switch(self):
        (result,) = send_now(self.user, title="Hi", body="There", force_sandbox=True)
        self.assertEqual((result.status, result.sandboxed), ("sent", True))
        self.assertEqual(SandboxMessage.objects.count(), 1)

    def test_global_sandbox_captures_everything(self):
        self.sandbox_on()
        send_now(self.user, title="Hi", body="There")
        self.assertEqual(SandboxMessage.objects.get().title, "Hi")

    def test_throttle_scenario_retries_and_the_retry_stays_in_the_sandbox(self):
        self.sandbox_on("throttle")
        send_now(self.user, title="Hi", body="There", force_sandbox=True)
        self.assertEqual(rows()[0].status, "retrying")
        delivery.retry_due(timezone.now() + timedelta(minutes=2))
        self.assertEqual(SandboxMessage.objects.count(), 2)  # never the real gateway

    def test_forced_sandbox_retry_stays_in_the_sandbox_after_the_switch_is_off(self):
        self.sandbox_on("timeout")
        send_now(self.user, title="Hi", body="There", force_sandbox=True)
        from . import sandbox

        sandbox.set_mode(None, False)
        delivery.retry_due(timezone.now() + timedelta(minutes=2))
        self.assertEqual(SandboxMessage.objects.count(), 2)

    def test_error_scenario_fails_without_retry(self):
        self.sandbox_on("error")
        send_now(self.user, title="Hi", body="There")
        self.assertEqual(rows()[0].status, "failed")


class PreferenceGatingTests(PushTestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user()
        make_device(self.user, "ios")
        self.fake = self.use_gateway()

    def test_master_switch_off_sends_nothing(self):
        preferences.set_master_switch(self.user, "push", False)
        self.assertEqual(send_now(self.user, title="t", body="b"), [])
        self.assertEqual(self.fake.calls, [])

    def test_category_off_sends_nothing_but_other_categories_still_do(self):
        preferences.set_preference(self.user, "news", "push", False)
        self.assertEqual(send_now(self.user, title="t", body="b", category="news"), [])
        self.assertEqual(len(send_now(self.user, title="t", body="b", category="event")), 1)

    def test_composer_ignores_preferences(self):
        preferences.set_master_switch(self.user, "push", False)
        self.assertEqual(len(send_now(self.user, title="t", body="b", respect_preferences=False)), 1)


class EmailTests(PushTestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user("fan@example.com")
        self.fake = self.use_gateway()

    def deliver(self, **extra):
        return delivery.deliver_to_user(self.user, title="Match tonight", body="Kick-off 20:00", send_email=True, **extra)

    def test_email_is_sent_and_logged(self):
        # the real EmailGateway through the locmem backend
        with self.settings_patch():
            results = self.deliver()
        self.assertEqual([(r.channel, r.provider, r.status) for r in results], [("email", "email", "sent")])
        self.assertEqual((mail.outbox[0].subject, mail.outbox[0].body, mail.outbox[0].to), ("Match tonight", "Kick-off 20:00", ["fan@example.com"]))
        self.assertEqual(results[0].target_masked, "f***@example.com")

    def settings_patch(self):
        from unittest import mock

        from . import gateways

        return mock.patch("notifications.delivery.get_gateway", side_effect=lambda provider, force_sandbox=False: gateways.EmailGateway())

    def test_email_is_off_unless_asked(self):
        self.assertEqual(delivery.deliver_to_user(self.user, title="t", body="b"), [])

    def test_email_switches_gate_it(self):
        preferences.set_preference(self.user, "general", "email", False)
        self.assertEqual(self.deliver(), [])
        preferences.set_preference(self.user, "general", "email", True)
        self.assertEqual(len(self.deliver()), 1)
        preferences.set_master_switch(self.user, "email", False)
        self.assertEqual(len(self.deliver(notification_id=uuid.uuid4())), 0)

    def test_unless_app_skips_email_when_the_app_can_notify(self):
        preferences.set_email_mode(self.user, "unless_app")
        make_device(self.user, "ios")
        results = self.deliver()
        self.assertEqual([r.channel for r in results], ["native"])  # the push goes, the email does not
        PushDevice.objects.update(enabled=False)
        self.assertEqual([r.channel for r in self.deliver(notification_id=uuid.uuid4())], ["email"])

    def test_email_ignores_quiet_hours(self):
        now = timezone.localtime(timezone.now())
        preferences.set_quiet_hours(self.user, (now - timedelta(hours=1)).time(), (now + timedelta(hours=1)).time(), "Europe/Brussels")
        self.assertEqual([r.channel for r in self.deliver()], ["email"])

    def test_email_goes_to_the_sandbox_when_it_is_on(self):
        self.sandbox_on()
        from unittest import mock

        from . import gateways

        with mock.patch("notifications.delivery.get_gateway", side_effect=lambda provider, force_sandbox=False: gateways.get_gateway(provider, force_sandbox=force_sandbox)):
            self.deliver()
        self.assertEqual(mail.outbox, [])
        captured = SandboxMessage.objects.get()
        self.assertEqual((captured.channel, captured.provider, captured.target), ("email", "email", "fan@example.com"))

    def test_email_failure_is_retried(self):
        from unittest import mock

        with mock.patch("django.core.mail.EmailMessage.send", side_effect=OSError("smtp down")), self.settings_patch():
            (result,) = self.deliver()
        self.assertEqual(result.status, "retrying")
        with self.settings_patch():
            delivery.retry_due(timezone.now() + timedelta(minutes=2))
        self.assertEqual((rows()[0].status, len(mail.outbox)), ("sent", 1))


class QuietHoursDeliveryTests(PushTestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user()
        self.device = make_device(self.user, "ios")
        now = timezone.localtime(timezone.now())
        # A window around "now" (may cross midnight, which is the interesting case anyway).
        preferences.set_quiet_hours(self.user, (now - timedelta(hours=1)).time(), (now + timedelta(hours=1)).time(), "Europe/Brussels")
        self.until = preferences.quiet_state(self.user)[1]
        self.fake = self.use_gateway()

    def test_push_inside_quiet_hours_is_held_until_the_window_ends(self):
        (result,) = send_now(self.user, title="t", body="b")
        self.assertEqual(result.status, "held")
        self.assertEqual(self.fake.calls, [])
        row = rows()[0]
        self.assertEqual((row.status, row.attempts, row.next_attempt_at), ("held", 0, self.until))
        self.assertEqual(row.payload["title"], "t")

    def test_held_push_is_released_when_the_window_ends(self):
        send_now(self.user, title="t", body="b")
        self.assertEqual(delivery.retry_due(timezone.now()), 0)
        self.assertEqual(delivery.retry_due(self.until + timedelta(seconds=1)), 1)
        row = rows()[0]
        self.assertEqual((row.status, row.attempts, row.payload), ("sent", 1, None))
        self.assertEqual(len(self.fake.calls), 1)

    def test_game_updates_are_dropped_instead_of_held(self):
        self.assertEqual(send_now(self.user, title="Goal", body="1-0", category="game"), [])
        self.assertEqual((NotificationDelivery.objects.count(), self.fake.calls), (0, []))

    def test_critical_bypasses_quiet_hours(self):
        (result,) = send_now(self.user, title="t", body="b", priority="critical")
        self.assertEqual(result.status, "sent")
        (result,) = send_now(self.user, title="Goal", body="1-0", category="game", priority="critical")
        self.assertEqual(result.status, "sent")

    def test_critical_still_obeys_the_switches(self):
        preferences.set_preference(self.user, "general", "push", False)
        self.assertEqual(send_now(self.user, title="t", body="b", priority="critical"), [])

    def test_composer_ignores_quiet_hours(self):
        (result,) = send_now(self.user, title="t", body="b", respect_preferences=False)
        self.assertEqual(result.status, "sent")

    def test_held_push_expires_after_the_ttl(self):
        send_now(self.user, title="t", body="b", category="reminder")  # one day to live
        delivery.retry_due(timezone.now() + timedelta(days=2))
        row = rows()[0]
        self.assertEqual((row.status, row.last_error), ("failed", "expired in quiet hours"))

    def test_outside_the_window_pushes_go_out(self):
        preferences.set_quiet_hours(self.user, (timezone.localtime() + timedelta(hours=2)).time(), (timezone.localtime() + timedelta(hours=3)).time(), "Europe/Brussels")
        (result,) = send_now(self.user, title="t", body="b")
        self.assertEqual(result.status, "sent")
