"""The sending sandbox: switch, expiry, scenarios, capture, audit trail."""

from datetime import timedelta
from unittest import mock

from django.test import override_settings
from django.utils import timezone

from . import gateways, sandbox
from .gateways import DEAD, FAILED, RETRY, SENT, Message, Target
from .models import SandboxEvent, SandboxMessage, SandboxState
from .tests_support import PushTestCase, make_device, make_subscription, make_user


class SandboxStateTests(PushTestCase):
    def test_off_by_default_and_follows_the_setting_until_touched(self):
        self.assertFalse(sandbox.is_on())
        with override_settings(PUSH_SANDBOX=True):
            self.assertTrue(sandbox.is_on())
            self.assertTrue(sandbox.get_state()["default_from_settings"])
            sandbox.set_mode(None, False)
            self.assertFalse(sandbox.is_on())  # an explicit off beats the default

    def test_state_shape(self):
        sandbox.set_mode(None, True, 60)
        state = sandbox.get_state()
        self.assertEqual({"enabled", "effective", "expires_at", "scenario", "latency_ms", "default_from_settings"}, set(state))
        self.assertTrue(state["enabled"] and state["effective"])
        self.assertAlmostEqual(state["expires_at"].timestamp(), (timezone.now() + timedelta(hours=1)).timestamp(), delta=5)

    def test_only_listed_expiries_are_allowed(self):
        for minutes in (30, 60, 240, 1440, None):
            sandbox.set_mode(None, True, minutes)
        with self.assertRaises(ValueError):
            sandbox.set_mode(None, True, 45)

    def test_it_expires_on_its_own(self):
        sandbox.set_mode(None, True, 30)
        self.assertTrue(sandbox.is_on())
        later = timezone.now() + timedelta(minutes=31)
        self.assertFalse(sandbox.is_on(later))  # off immediately, even before it is recorded
        self.assertFalse(sandbox.expire_if_due(timezone.now()))
        self.assertTrue(sandbox.expire_if_due(later))
        self.assertFalse(sandbox.is_on())
        self.assertEqual(SandboxState.objects.get().enabled, False)
        self.assertEqual(SandboxEvent.objects.first().action, "expired")

    def test_changes_are_audited_with_the_actor(self):
        user = make_user()
        sandbox.set_mode(user, True)
        sandbox.set_scenario(user, "throttle", 50)
        sandbox.set_mode(user, False)
        self.assertEqual([(e.action, e.user) for e in SandboxEvent.objects.order_by("created")], [("sandbox_on", user), ("scenario", user), ("sandbox_off", user)])

    def test_scenario_needs_the_sandbox_on_and_valid_values(self):
        with self.assertRaises(PermissionError):
            sandbox.set_scenario(None, "throttle", 0)
        sandbox.set_mode(None, True)
        for scenario, latency in (("nope", 0), ("ok", -1), ("ok", 10_001)):
            with self.assertRaises(ValueError):
                sandbox.set_scenario(None, scenario, latency)
        sandbox.set_scenario(None, "error", 10_000)
        self.assertEqual(sandbox.get_state()["scenario"], "error")

    @override_settings(DEBUG=False)
    def test_dead_scenario_is_refused_outside_debug(self):
        sandbox.set_mode(None, True)
        with self.assertRaises(ValueError):
            sandbox.set_scenario(None, "dead", 0)

    @override_settings(DEBUG=True)
    def test_dead_scenario_is_allowed_in_debug(self):
        sandbox.set_mode(None, True)
        sandbox.set_scenario(None, "dead", 0)
        self.assertEqual(sandbox.get_state()["scenario"], "dead")

    @override_settings(DEBUG=False)
    def test_a_stored_dead_scenario_counts_as_ok_outside_debug(self):
        SandboxState.objects.create(enabled=True, scenario="dead")
        self.assertEqual(sandbox.get_state()["scenario"], "ok")


class SandboxGatewayTests(PushTestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user()
        self.device = make_device(self.user, "ios")
        self.target = Target("native", "apns", self.device.installation_id, self.device)
        self.message = Message(title="Hi", body="There", club_slug="sharks")

    def send(self, scenario="ok", latency=0):
        sandbox.set_mode(None, True)
        if scenario != "ok" or latency:
            sandbox.set_scenario(None, scenario, latency)
        return gateways.get_gateway("apns").send(self.target, self.message, user=self.user)

    def test_get_gateway_returns_the_fake_only_when_on_or_forced(self):
        self.assertNotIsInstance(gateways.get_gateway("apns"), gateways.SandboxGateway)
        self.assertIsInstance(gateways.get_gateway("apns", force_sandbox=True), gateways.SandboxGateway)
        sandbox.set_mode(None, True)
        self.assertIsInstance(gateways.get_gateway("fcm"), gateways.SandboxGateway)

    def test_it_captures_the_exact_request_instead_of_sending(self):
        with mock.patch("httpx.Client.post") as post:
            result = self.send()
        post.assert_not_called()
        self.assertEqual((result.outcome, result.sandboxed), (SENT, True))
        captured = SandboxMessage.objects.get()
        self.assertEqual((captured.provider, captured.channel, captured.target, captured.title, captured.outcome, captured.user), ("apns", "native", self.device.installation_id, "Hi", "delivered", self.user))
        self.assertEqual(captured.request["payload"]["aps"]["alert"]["body"], "There")
        self.assertIn("apns-priority", captured.request["headers"])

    def test_scenarios(self):
        for scenario, outcome, stored in (("throttle", RETRY, "throttled"), ("timeout", RETRY, "timeout"), ("error", FAILED, "error")):
            with self.subTest(scenario=scenario):
                result = self.send(scenario)
                self.assertEqual(result.outcome, outcome)
                self.assertEqual(SandboxMessage.objects.latest("created").outcome, stored)

    @override_settings(DEBUG=True)
    def test_dead_scenario(self):
        self.assertEqual(self.send("dead").outcome, DEAD)

    def test_latency_is_applied_and_capped(self):
        with mock.patch("notifications.sandbox.time.sleep") as sleep:
            self.send("ok", 250)
        sleep.assert_called_once_with(0.25)

    def test_webpush_and_fcm_requests_are_captured_too(self):
        sandbox.set_mode(None, True)
        sub = make_subscription(self.user)
        gateways.get_gateway("webpush").send(Target("web", "webpush", sub.endpoint, sub), self.message, user=self.user)
        gateways.get_gateway("fcm").send(Target("native", "fcm", "x", make_device(self.user, "android")), self.message, user=self.user)
        self.assertEqual(set(SandboxMessage.objects.values_list("provider", flat=True)), {"webpush", "fcm"})

    def test_clear_and_purge(self):
        self.send()
        old = SandboxMessage.objects.create(channel="native", provider="apns", target="x", outcome="delivered")
        SandboxMessage.objects.filter(pk=old.pk).update(created=timezone.now() - timedelta(days=8))
        self.assertEqual(sandbox.purge(), 1)
        self.assertEqual(sandbox.clear_messages(self.user), 1)
        self.assertEqual(SandboxMessage.objects.count(), 0)
        self.assertEqual(SandboxEvent.objects.filter(action="cleared").count(), 1)
