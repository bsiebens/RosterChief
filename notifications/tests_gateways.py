"""Gateway classification per provider (all network mocked), payload building and the credential checks."""

import json
from types import SimpleNamespace
from unittest import mock

import httpx
import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from django.test import SimpleTestCase, TestCase, override_settings
from pywebpush import WebPushException

from . import gateways
from .gateways import DEAD, FAILED, RETRY, SENT, ApnsGateway, FcmGateway, Message, Target, WebPushGateway
from .tests_support import NO_CREDENTIALS, WEB_ENDPOINT, make_device, make_subscription, make_user

P8 = ec.generate_private_key(ec.SECP256R1()).private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
APNS = {"APP_PUSH_APNS_KEY_ID": "KEYID", "APP_PUSH_APNS_TEAM_ID": "TEAM", "APP_PUSH_APNS_BUNDLE_ID": "app.test", "APP_PUSH_APNS_KEY": P8}


def response(status, body=None):
    def parse():
        if body is None:
            raise ValueError("no json")
        return body

    return SimpleNamespace(status_code=status, json=parse)


class FakeClient:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.posts = []

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        item = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(item, Exception):
            raise item
        return item


class FakeCredentials:
    valid = True
    token = "oauth-token"

    def __init__(self):
        self.refreshed = 0

    def refresh(self, request):
        self.refreshed += 1


def message(**extra):
    return Message(title="Hello", body="World", data={"x": 1}, notification_id="n-1", club_slug="sharks", **extra)


@override_settings(**{**NO_CREDENTIALS, **APNS})
class ApnsTests(TestCase):
    def setUp(self):
        self.device = make_device(make_user(), "ios", "sandbox")

    def send(self, *responses, msg=None):
        client = FakeClient(*responses)
        gateway = ApnsGateway(client=client)
        return gateway.send(Target("native", "apns", self.device.installation_id, self.device), msg or message()), client, gateway

    def test_200_is_sent_and_request_is_built_from_the_device_environment(self):
        result, client, _ = self.send(response(200))
        self.assertEqual(result.outcome, SENT)
        url, kwargs = client.posts[0]
        self.assertTrue(url.startswith("https://api.sandbox.push.apple.com/3/device/"))
        self.assertEqual(kwargs["headers"]["apns-topic"], "app.test")
        self.assertEqual(kwargs["headers"]["apns-priority"], "5")
        body = json.loads(kwargs["content"])
        self.assertEqual(body["aps"]["alert"], {"title": "Hello", "body": "World"})
        self.assertEqual((body["kind"], body["notification_id"], body["club"], body["category"], body["data"]), ("notification", "n-1", "sharks", "general", {"x": 1}))

    def test_production_host(self):
        self.device.environment = "production"
        _, client, _ = self.send(response(200))
        self.assertTrue(client.posts[0][0].startswith("https://api.push.apple.com/"))

    def test_dead_reasons(self):
        for status, reason in ((400, "BadDeviceToken"), (410, "Unregistered"), (400, "DeviceTokenNotForTopic"), (410, "")):
            with self.subTest(reason=reason or status):
                result, _, _ = self.send(response(status, {"reason": reason} if reason else None))
                self.assertEqual(result.outcome, DEAD)

    def test_throttle_and_server_errors_retry(self):
        for status in (429, 500, 503):
            with self.subTest(status=status):
                self.assertEqual(self.send(response(status, {"reason": "TooManyRequests"}))[0].outcome, RETRY)

    def test_other_errors_fail_without_retry(self):
        result, _, _ = self.send(response(400, {"reason": "PayloadTooLarge"}))
        self.assertEqual(result.outcome, FAILED)
        self.assertIn("PayloadTooLarge", result.error)

    def test_transport_errors_retry(self):
        self.assertEqual(self.send(httpx.ConnectTimeout("slow"))[0].outcome, RETRY)

    def test_expired_provider_token_is_refreshed_once(self):
        result, client, _ = self.send(response(403, {"reason": "ExpiredProviderToken"}), response(200))
        self.assertEqual(result.outcome, SENT)
        self.assertEqual(len(client.posts), 2)

    def test_provider_token_is_cached(self):
        _, client, gateway = self.send(response(200))
        gateway.send(Target("native", "apns", "x", self.device), message())
        tokens = {kwargs["headers"]["authorization"] for _, kwargs in client.posts}
        self.assertEqual(len(tokens), 1)

    def test_collapse_id_only_for_collapsing_categories(self):
        _, client, _ = self.send(response(200), msg=Message(title="t", body="b", category="game", collapse_id="game:1"))
        self.assertEqual(client.posts[0][1]["headers"]["apns-collapse-id"], "game:1")
        _, client, _ = self.send(response(200), msg=Message(title="t", body="b", category="news", collapse_id="news:1"))
        self.assertNotIn("apns-collapse-id", client.posts[0][1]["headers"])

    def test_critical_is_high_priority_with_a_day_of_ttl(self):
        _, client, _ = self.send(response(200), msg=Message(title="t", body="b", category="game", priority="critical"))
        headers = client.posts[0][1]["headers"]
        self.assertEqual(headers["apns-priority"], "10")

    @override_settings(APP_PUSH_APNS_KEY="")
    def test_not_configured_fails_without_network(self):
        result, client, _ = self.send(response(200))
        self.assertEqual(result.outcome, FAILED)
        self.assertEqual(client.posts, [])


@override_settings(**{**NO_CREDENTIALS, "APP_PUSH_FCM_CREDENTIALS": "{}", "APP_PUSH_FCM_PROJECT_ID": "proj"})
class FcmTests(TestCase):
    def setUp(self):
        self.device = make_device(make_user(), "android")
        self.credentials = FakeCredentials()

    def send(self, *responses, msg=None):
        client = FakeClient(*responses)
        gateway = FcmGateway(client=client, credentials=self.credentials)
        return gateway.send(Target("native", "fcm", self.device.installation_id, self.device), msg or message()), client

    def test_200_is_sent_and_message_has_category_channel(self):
        result, client = self.send(response(200), msg=Message(title="T", body="B", data={"k": [1]}, category="news", notification_id="n", club_slug="c"))
        self.assertEqual(result.outcome, SENT)
        url, kwargs = client.posts[0]
        self.assertTrue(url.endswith("/projects/proj/messages:send"))
        self.assertEqual(kwargs["headers"]["authorization"], "Bearer oauth-token")
        payload = kwargs["json"]["message"]
        self.assertEqual(payload["android"]["notification"]["channel_id"], "rc_news")
        self.assertEqual(payload["android"]["priority"], "NORMAL")
        self.assertEqual(payload["android"]["ttl"], f"{7 * 86400}s")
        self.assertEqual(payload["data"]["data"], '{"k": [1]}')  # structured values travel as JSON text
        self.assertEqual(payload["data"]["kind"], "notification")

    def test_critical_is_high_with_at_least_a_day(self):
        _, client = self.send(response(200), msg=Message(title="T", body="B", category="reminder", priority="critical"))
        android = client.posts[0][1]["json"]["message"]["android"]
        self.assertEqual((android["priority"], android["ttl"]), ("HIGH", "86400s"))

    def test_unregistered_is_dead(self):
        body = {"error": {"status": "NOT_FOUND", "message": "x", "details": [{"errorCode": "UNREGISTERED"}]}}
        self.assertEqual(self.send(response(404, body))[0].outcome, DEAD)
        self.assertEqual(self.send(response(400, body))[0].outcome, DEAD)

    def test_throttle_and_5xx_retry(self):
        for status in (429, 500, 503):
            self.assertEqual(self.send(response(status, {"error": {"status": "UNAVAILABLE"}}))[0].outcome, RETRY)

    def test_other_errors_fail(self):
        self.assertEqual(self.send(response(400, {"error": {"status": "INVALID_ARGUMENT", "message": "bad payload"}}))[0].outcome, FAILED)

    def test_transport_error_retries(self):
        self.assertEqual(self.send(httpx.ReadTimeout("slow"))[0].outcome, RETRY)

    def test_401_refreshes_the_token_once(self):
        result, client = self.send(response(401, {"error": {"status": "UNAUTHENTICATED"}}), response(200))
        self.assertEqual(result.outcome, SENT)
        self.assertEqual((len(client.posts), self.credentials.refreshed), (2, 1))


@override_settings(**{**NO_CREDENTIALS, "DJANGO_VAPID_PUBLIC_KEY": "pub", "DJANGO_VAPID_PRIVATE_KEY": "priv"})
class WebPushTests(TestCase):
    def setUp(self):
        self.sub = make_subscription(make_user())

    def send(self, side_effect=None, sub=None):
        sub = sub or self.sub
        with mock.patch("pywebpush.webpush", side_effect=side_effect) as webpush:
            result = WebPushGateway().send(Target("web", "webpush", sub.endpoint, sub), message())
        return result, webpush

    def failure(self, status):
        return WebPushException("boom", response=SimpleNamespace(status_code=status) if status else None)

    def test_sent_with_vapid_and_pointer_payload(self):
        result, webpush = self.send()
        self.assertEqual(result.outcome, SENT)
        kwargs = webpush.call_args.kwargs
        self.assertEqual(kwargs["vapid_private_key"], "priv")
        payload = json.loads(kwargs["data"])
        self.assertEqual((payload["title"], payload["kind"], payload["club"]), ("Hello", "notification", "sharks"))

    def test_gone_is_dead(self):
        for status in (404, 410):
            self.assertEqual(self.send(self.failure(status))[0].outcome, DEAD)

    def test_throttle_server_error_and_no_response_retry(self):
        for status in (429, 500, 502, None):
            self.assertEqual(self.send(self.failure(status))[0].outcome, RETRY)

    def test_other_status_fails(self):
        self.assertEqual(self.send(self.failure(400))[0].outcome, FAILED)

    def test_network_error_retries_and_unexpected_error_fails(self):
        self.assertEqual(self.send(requests.ConnectionError("down"))[0].outcome, RETRY)
        self.assertEqual(self.send(ValueError("bad key"))[0].outcome, FAILED)

    def test_endpoint_outside_the_allowlist_is_dead_and_never_contacted(self):
        sub = make_subscription(self.sub.user, endpoint="https://internal.example.com/push")
        result, webpush = self.send(sub=sub)
        self.assertEqual(result.outcome, DEAD)
        webpush.assert_not_called()


class CredentialsTests(SimpleTestCase):
    @override_settings(**NO_CREDENTIALS)
    def test_nothing_configured_lists_the_env_names(self):
        self.assertEqual(gateways.credentials_status("apns"), {"configured": False, "missing": ["APP_PUSH_APNS_KEY_ID", "APP_PUSH_APNS_TEAM_ID", "APP_PUSH_APNS_BUNDLE_ID", "APP_PUSH_APNS_KEY"]})
        self.assertEqual(gateways.credentials_status("fcm"), {"configured": False, "missing": ["APP_PUSH_FCM_CREDENTIALS"]})
        self.assertEqual(gateways.credentials_status("webpush")["missing"], ["DJANGO_VAPID_PUBLIC_KEY", "DJANGO_VAPID_PRIVATE_KEY"])
        self.assertFalse(gateways.validate_credentials("apns")["ok"])

    @override_settings(**{**NO_CREDENTIALS, **APNS})
    def test_apns_key_is_validated_locally(self):
        self.assertTrue(gateways.credentials_status("apns")["configured"])
        self.assertTrue(gateways.validate_credentials("apns")["ok"])

    @override_settings(**{**NO_CREDENTIALS, **APNS, "APP_PUSH_APNS_KEY": "not a key"})
    def test_garbage_key_is_reported(self):
        result = gateways.validate_credentials("apns")
        self.assertFalse(result["ok"])
        self.assertTrue(result["detail"])

    @override_settings(**{**NO_CREDENTIALS, "APP_PUSH_FCM_CREDENTIALS": "not json"})
    def test_garbage_fcm_credentials_are_reported(self):
        self.assertFalse(gateways.validate_credentials("fcm")["ok"])

    def test_vapid_pair_is_validated_and_must_match_the_public_key(self):
        from py_vapid import Vapid

        vapid = Vapid()
        vapid.generate_keys()
        pem = vapid.private_pem().decode()
        public = gateways._vapid_public_key(vapid)
        with override_settings(**{**NO_CREDENTIALS, "DJANGO_VAPID_PUBLIC_KEY": public, "DJANGO_VAPID_PRIVATE_KEY": pem}):
            self.assertTrue(gateways.validate_credentials("webpush")["ok"])
        with override_settings(**{**NO_CREDENTIALS, "DJANGO_VAPID_PUBLIC_KEY": "other", "DJANGO_VAPID_PRIVATE_KEY": pem}):
            self.assertFalse(gateways.validate_credentials("webpush")["ok"])

    def test_unknown_provider(self):
        with self.assertRaises(ValueError):
            gateways.credentials_status("sms")
        self.assertIn("web.push.apple.com", "https://web.push.apple.com/x")
        self.assertTrue(gateways.is_allowed_push_endpoint(WEB_ENDPOINT))
        self.assertFalse(gateways.is_allowed_push_endpoint("http://fcm.googleapis.com/x"))
