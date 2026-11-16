"""Helpers shared by the notifications tests."""

import itertools
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings

from . import gateways, sandbox
from .gateways import GatewayResult
from .models import PushDevice, PushSettings, WebPushSubscription

User = get_user_model()
_counter = itertools.count(1)

#: Pretend nothing is configured, whatever the developer's .env says.
NO_CREDENTIALS = dict.fromkeys(
    (
        "APP_PUSH_APNS_KEY_ID",
        "APP_PUSH_APNS_TEAM_ID",
        "APP_PUSH_APNS_BUNDLE_ID",
        "APP_PUSH_APNS_KEY",
        "APP_PUSH_APNS_KEY_FILE",
        "APP_PUSH_FCM_CREDENTIALS",
        "APP_PUSH_FCM_CREDENTIALS_FILE",
        "APP_PUSH_FCM_PROJECT_ID",
        "DJANGO_VAPID_PUBLIC_KEY",
        "DJANGO_VAPID_PRIVATE_KEY",
    ),
    "",
)
WEB_ENDPOINT = "https://fcm.googleapis.com/fcm/send/abc123"


def make_user(email=None, **extra):
    n = next(_counter)
    return User.objects.create(email=email or f"user{n}@example.com", first_name="Ann", last_name=f"Example{n}", **extra)


def make_device(user, platform="ios", environment="production", token=None, **extra):
    n = next(_counter)
    return PushDevice.objects.create(user=user, installation_id=f"inst-{n}", platform=platform, environment=environment, push_token=token or f"token-{n}", **extra)


def make_subscription(user, club=None, endpoint=None):
    n = next(_counter)
    return WebPushSubscription.objects.create(user=user, club=club, endpoint=endpoint or f"{WEB_ENDPOINT}{n}", p256dh="p", auth="a")


class FakeGateway:
    """Returns the queued results (the last one repeats) and remembers what it was asked to send."""

    def __init__(self, *results):
        self.results = list(results) or [GatewayResult(gateways.SENT)]
        self.calls = []

    def send(self, target, message, *, user=None, club=None):
        self.calls.append((target, message))
        return self.results.pop(0) if len(self.results) > 1 else self.results[0]


class PushTestCase(TestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        gateways.reset_gateways()
        self.settings = PushSettings.get()

    def use_gateway(self, *results, force=False):
        fake = FakeGateway(*results)
        patcher = mock.patch("notifications.delivery.get_gateway", return_value=fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        return fake

    def sandbox_on(self, scenario="ok", latency=0):
        sandbox.set_mode(None, True)
        if scenario != "ok" or latency:
            sandbox.set_scenario(None, scenario, latency)


no_credentials = override_settings(**NO_CREDENTIALS)
