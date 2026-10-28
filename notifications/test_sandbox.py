"""The sending sandbox (settings.PUSH_SANDBOX) wired into the native push, web push and email seams."""

import datetime
import io
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from django.core import mail, management
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone

from api.app.delivery import deliver, deliver_to_user
from api.app.tests import AppApiTestCase
from api.models import PushDevice
from features.models import EmailSuppression
from mobile.models import PushSubscription
from mobile.services.push import send_push_to_member
from mobile.tests import FCM_ENDPOINT, push_keys

from . import delivery_log, sandbox
from .models import Notification, NotificationDelivery, SandboxMessage
from .services import _send_email

Status = NotificationDelivery.Status


def _boom(*args, **kwargs):
    raise AssertionError("a real provider was touched while the sandbox is on")


@override_settings(DEBUG=True, PUSH_SANDBOX=True, VAPID_PRIVATE_KEY="", EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend", APP_PUSH_APNS_BUNDLE_ID="app.example")
class SandboxTests(AppApiTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        patchers = [patch("httpx.Client.post", _boom), patch("mobile.services.push.webpush", _boom), patch("smtplib.SMTP", _boom), patch("smtplib.SMTP_SSL", _boom)]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.ios = PushDevice.objects.create(user=self.user, installation_id="iphone", platform="ios", push_token="t1")
        self.android = PushDevice.objects.create(user=self.user, installation_id="pixel", platform="android", push_token="t2")
        self.notification = Notification.objects.create(club=self.club, member=self.member, title="Hi", body="There", category="game")

    def tearDown(self):
        cache.clear()

    def deliveries(self):
        return {row.target: row for row in NotificationDelivery.objects.all()}

    def test_native_pushes_are_captured_with_the_real_requests(self):
        self.assertEqual(deliver([self.notification.pk]), 2)

        apns = SandboxMessage.objects.get(provider="apns")
        self.assertEqual((apns.channel, apns.target, apns.user, apns.club, apns.outcome), ("native", "iphone", self.user, self.club, "delivered"))
        self.assertEqual(apns.request["aps"]["alert"], {"title": "Hi", "body": "There"})
        self.assertEqual(apns.headers["apns-topic"], "app.example")
        self.assertEqual(apns.headers["apns-priority"], "10")
        self.assertEqual(apns.headers["apns-push-type"], "alert")
        self.assertAlmostEqual(int(apns.headers["apns-expiration"]), timezone.now().timestamp() + 900, delta=10)
        self.assertEqual(apns.headers["apns-collapse-id"], self.club.slug) if "apns-collapse-id" in apns.headers else None
        self.assertNotIn("authorization", apns.headers)
        fcm = SandboxMessage.objects.get(provider="fcm")
        self.assertEqual(fcm.request["message"]["token"], "t2")
        self.assertEqual(fcm.request["message"]["notification"], {"title": "Hi", "body": "There"})
        self.assertEqual(fcm.request["message"]["android"]["priority"], "HIGH")
        self.assertEqual(fcm.club, self.club)
        self.assertEqual({row.status for row in NotificationDelivery.objects.all()}, {Status.SENT})

    def test_a_game_push_collapses(self):
        deliver_to_user(self.user, texts=lambda language: ("Goal", "1-0"), data={"kind": "game", "club_slug": self.club.slug}, thread_id="event-1", club=self.club, category="game")

        apns = SandboxMessage.objects.get(provider="apns")
        self.assertEqual(apns.headers["apns-collapse-id"], "event-1")
        self.assertIn("apns-expiration", apns.headers)
        fcm = SandboxMessage.objects.get(provider="fcm")
        self.assertEqual(fcm.request["message"]["android"]["collapse_key"], "event-1")

    def test_throttle_retries_later_and_succeeds_after_ok(self):
        sandbox.set_scenario("throttle")
        deliver([self.notification.pk])
        self.assertEqual({row.status for row in NotificationDelivery.objects.all()}, {Status.RETRYING})

        sandbox.set_scenario("ok")
        delivery_log.retry_due(now=timezone.now() + datetime.timedelta(minutes=2))

        self.assertEqual({row.status for row in NotificationDelivery.objects.all()}, {Status.SENT})

    def test_timeout_is_retrying(self):
        sandbox.set_scenario("timeout")
        deliver([self.notification.pk])
        self.assertEqual({row.status for row in NotificationDelivery.objects.all()}, {Status.RETRYING})

    def test_dead_disables_the_device(self):
        sandbox.set_scenario("dead")
        deliver([self.notification.pk])

        self.assertEqual({row.status for row in NotificationDelivery.objects.all()}, {Status.DEAD})
        self.ios.refresh_from_db()
        self.android.refresh_from_db()
        self.assertFalse(self.ios.enabled or self.android.enabled)

    def test_error_fails_without_retry(self):
        sandbox.set_scenario("error")
        deliver([self.notification.pk])

        rows = list(NotificationDelivery.objects.all())
        self.assertEqual({row.status for row in rows}, {Status.FAILED})
        self.assertTrue(all(row.next_attempt_at is None for row in rows))

    def test_web_push_is_captured_without_a_vapid_key(self):
        keys = push_keys()
        subscription = PushSubscription.objects.create(club=self.club, member=self.member, endpoint=FCM_ENDPOINT, **keys)
        self.ios.delete()
        self.android.delete()

        send_push_to_member(self.member, title="Hello", body="World", url="/app/x")

        message = SandboxMessage.objects.get(provider="webpush")
        self.assertEqual(message.request, {"title": "Hello", "body": "World", "url": "/app/x", "endpoint": "fcm.googleapis.com"})
        self.assertEqual(NotificationDelivery.objects.get().status, Status.SENT)
        self.assertTrue(PushSubscription.objects.filter(pk=subscription.pk).exists())

    def test_web_push_scenarios(self):
        PushSubscription.objects.create(club=self.club, member=self.member, endpoint=FCM_ENDPOINT, **push_keys())
        for scenario, status, remains in (("throttle", Status.RETRYING, True), ("timeout", Status.RETRYING, True), ("error", Status.FAILED, True), ("dead", Status.DEAD, False)):
            NotificationDelivery.objects.all().delete()
            sandbox.set_scenario(scenario)
            send_push_to_member(self.member, title="a", body="b")
            self.assertEqual(NotificationDelivery.objects.get(channel="web").status, status, scenario)
            self.assertEqual(PushSubscription.objects.exists(), remains, scenario)

    def test_email_is_captured(self):
        self.assertEqual(_send_email(self.notification, ["pat@example.com"]), ["pat@example.com"])

        message = SandboxMessage.objects.get(provider="email")
        self.assertEqual((message.channel, message.target), ("email", "pat@example.com"))
        self.assertTrue(message.title.endswith("Hi"))
        self.assertEqual(message.request["to"], ["pat@example.com"])
        self.assertTrue(message.request["subject"].endswith("Hi"))
        self.assertIn("There", message.request["html"])
        self.assertEqual(NotificationDelivery.objects.get().status, Status.SENT)

    def test_email_scenarios(self):
        for scenario, status in (("throttle", Status.RETRYING), ("timeout", Status.RETRYING), ("dead", Status.FAILED), ("error", Status.FAILED)):
            NotificationDelivery.objects.all().delete()
            sandbox.set_scenario(scenario)
            self.assertEqual(_send_email(self.notification, ["pat@example.com", "other@example.com"]), [], scenario)
            self.assertEqual({row.status for row in NotificationDelivery.objects.filter(channel="email")}, {status}, scenario)
            self.assertEqual(NotificationDelivery.objects.count(), 2, scenario)

    def test_a_throttled_email_is_retried_after_ok(self):
        sandbox.set_scenario("throttle")
        _send_email(self.notification, ["pat@example.com"])
        sandbox.set_scenario("ok")

        delivery_log.retry_due(now=timezone.now() + datetime.timedelta(minutes=2))

        self.assertEqual(NotificationDelivery.objects.get().status, Status.SENT)

    def test_pausing_automated_email_captures_nothing(self):
        EmailSuppression.start()
        self.addCleanup(EmailSuppression.stop)

        _send_email(self.notification, ["pat@example.com"])

        self.assertFalse(SandboxMessage.objects.filter(provider="email").exists())

    def test_the_command_purges_old_captures(self):
        old = SandboxMessage.objects.create(channel="email", provider="email", target="x", outcome="delivered")
        SandboxMessage.objects.filter(pk=old.pk).update(created=timezone.now() - datetime.timedelta(days=30))

        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            management.call_command("retry_failed_deliveries")

        self.assertFalse(SandboxMessage.objects.filter(pk=old.pk).exists())

    def test_the_sandbox_counts_as_configured(self):
        from api.app.push import apns_configured, fcm_configured, get_gateway, push_configured

        self.assertTrue(apns_configured() and fcm_configured() and push_configured())
        self.assertTrue(get_gateway().supports(self.ios) and get_gateway().supports(self.android))


class SandboxOffTests(AppApiTestCase):
    def test_nothing_is_captured_and_the_real_gateway_logic_applies(self):
        from api.app.push import NullGateway, get_gateway, push_configured

        self.assertIsInstance(get_gateway(), NullGateway)
        self.assertFalse(push_configured())
        self.assertFalse(SandboxMessage.objects.exists())


class SandboxModeTests(AppApiTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.addCleanup(cache.clear)

    def test_a_timed_sandbox_ends_by_itself(self):
        sandbox.set_mode(None, on=True, expires_in=datetime.timedelta(hours=1))

        self.assertTrue(sandbox.enabled())
        self.assertTrue(sandbox.enabled(now=timezone.now() + datetime.timedelta(minutes=30)))
        self.assertFalse(sandbox.enabled(now=timezone.now() + datetime.timedelta(hours=2)))

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_account_and_invoice_mail_stay_real(self):
        from django.core.mail import EmailMessage

        from rosterchief.mail import send_message

        sandbox.set_mode(None, on=True)
        cache.clear()
        self.assertTrue(sandbox.enabled())

        self.assertTrue(send_message(EmailMessage("Reset", "body", "a@example.com", ["pat@example.com"]), exempt=True))
        self.assertTrue(send_message(EmailMessage("Invoice", "body", "a@example.com", ["pat@example.com"])))

        self.assertEqual([m.subject for m in mail.outbox], ["Reset", "Invoice"])
        self.assertFalse(SandboxMessage.objects.exists())


class DeadScenarioGuardTests(AppApiTestCase):
    """ "Target gone" disables the real devices it hits, so it is only available in DEBUG."""

    @override_settings(DEBUG=False)
    def test_unavailable_without_debug(self):
        self.assertNotIn("dead", sandbox.available_scenarios())
        with self.assertRaises(ValueError):
            sandbox.set_scenario("dead")

    @override_settings(DEBUG=False)
    def test_stored_dead_scenario_acts_as_ok_without_debug(self):
        with override_settings(DEBUG=True):
            sandbox.set_scenario("dead")
        self.assertEqual(sandbox.scenario()["name"], "ok")

    @override_settings(DEBUG=True)
    def test_available_in_debug(self):
        self.assertIn("dead", sandbox.available_scenarios())
