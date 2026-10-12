"""Native push straight to Apple's APNs over HTTP/2 with token-based auth (API.md §9).

Everything talks to a ``PushGateway`` so tests (and a server without APNs credentials)
never touch the network:

* ``ApnsGateway`` -- the real one, used when the APNs key is configured;
* ``NullGateway`` -- nothing configured: devices still register, nothing is sent;
* ``appapi.tests`` swaps in a fake via ``APP_PUSH_GATEWAY``.

Settings (all optional; empty = pushes can't be delivered yet)::

    APP_PUSH_APNS_KEY_ID        # the .p8 key's id
    APP_PUSH_APNS_TEAM_ID       # the Apple Developer team id
    APP_PUSH_APNS_BUNDLE_ID     # the app's bundle id = the apns-topic
    APP_PUSH_APNS_KEY           # the .p8 file's text ("\\n" for newlines in an env file)
    APP_PUSH_APNS_KEY_FILE      # or the path of the .p8 file
    APP_PUSH_APNS_ACTIVITY_ATTRIBUTES   # the app's ActivityAttributes type name (Live Activity start)
    APP_PUSH_GATEWAY            # dotted path to override the gateway class

A device's ``environment`` picks the APNs host: ``sandbox`` -> api.sandbox.push.apple.com
(debug builds from Xcode), ``production`` -> api.push.apple.com (TestFlight and the App
Store). Android is not served yet. Payloads are pointers, never content (API.md §9): a
localised title/body plus ``{kind, club_slug, target, ...}``; the app fetches the rest over
the API.
"""

import json
import logging
import threading
import time
from pathlib import Path

import httpx
import jwt
from django.conf import settings
from django.utils.module_loading import import_string

from .models import PushDevice

logger = logging.getLogger(__name__)

APNS_HOSTS = {PushDevice.Environment.PRODUCTION: "https://api.push.apple.com", PushDevice.Environment.SANDBOX: "https://api.sandbox.push.apple.com"}

#: Apple asks for a provider token to be refreshed no more often than every 20 minutes and
#: rejects one older than an hour.
TOKEN_REFRESH_SECONDS = 50 * 60

#: APNs reasons that mean "this token will never work again": the device is disabled.
DEAD_TOKEN_REASONS = {"BadDeviceToken", "Unregistered", "DeviceTokenNotForTopic"}


class PushGateway:
    def supports(self, device: PushDevice) -> bool:
        """Whether pushes can be delivered to ``device`` at all (what ``push_enabled`` reports)."""
        return True

    def publish(self, device: PushDevice, *, title: str, body: str, data: dict, badge: int | None = None, thread_id: str | None = None) -> bool:
        """Send one push. ``badge`` is the app icon's unread count (iOS); None leaves it as is.
        ``thread_id`` groups notifications on the lock screen. False means the token is dead:
        the caller disables the device."""
        raise NotImplementedError

    def live_activity(self, device: PushDevice, *, token: str, event: str, content_state: dict, attributes: dict | None = None, alert: dict | None = None, stale_at: int | None = None, dismiss_at: int | None = None, priority: int = 10) -> bool:
        """Start (``event="start"``, sent to the device's push-to-start token), update or end (sent
        to the activity's own token) one Live Activity. False means that token is dead."""
        raise NotImplementedError


class NullGateway(PushGateway):
    def supports(self, device):
        return False

    def live_activity(self, device, **kwargs):
        return True

    def publish(self, device, *, title, body, data, badge=None, thread_id=None):
        return True


def build_payload(*, title: str, body: str, data: dict, badge: int | None = None, thread_id: str | None = None) -> dict:
    """The APNs JSON body: the ``aps`` dictionary next to the custom keys."""
    aps = {"alert": {"title": title, "body": body}, "sound": "default", "mutable-content": 1}
    if thread_id:
        aps["thread-id"] = thread_id
    if badge is not None:
        aps["badge"] = badge
    return {"aps": aps, **data}


def build_live_activity_payload(*, event: str, content_state: dict, attributes: dict | None = None, alert: dict | None = None, stale_at: int | None = None, dismiss_at: int | None = None, now: int | None = None) -> dict:
    """The APNs JSON body of a Live Activity push: ``event`` is start, update or end."""
    aps = {"timestamp": now or int(time.time()), "event": event, "content-state": content_state}
    if event == "start":
        aps["attributes-type"] = settings.APP_PUSH_APNS_ACTIVITY_ATTRIBUTES
        aps["attributes"] = attributes or {}
    if alert:
        aps["alert"] = alert
    if stale_at:
        aps["stale-date"] = stale_at
    if dismiss_at:
        aps["dismissal-date"] = dismiss_at
    return {"aps": aps}


def apns_configured() -> bool:
    return bool(settings.APP_PUSH_APNS_KEY_ID and settings.APP_PUSH_APNS_TEAM_ID and (settings.APP_PUSH_APNS_KEY or settings.APP_PUSH_APNS_KEY_FILE))


def _private_key() -> str:
    if settings.APP_PUSH_APNS_KEY:
        return settings.APP_PUSH_APNS_KEY
    return Path(settings.APP_PUSH_APNS_KEY_FILE).read_text()


class ApnsGateway(PushGateway):
    """One shared HTTP/2 connection pool per process, created on first use (so after gunicorn
    forks) and a provider token reused for 50 minutes."""

    def __init__(self, client=None):
        self._client = client
        self._lock = threading.Lock()
        self._token = ""
        self._token_issued = 0.0

    @property
    def client(self):
        with self._lock:
            if self._client is None:
                self._client = httpx.Client(http2=True, timeout=10.0)
            return self._client

    def supports(self, device):
        return device.platform == PushDevice.Platform.IOS

    def _provider_token(self, *, refresh=False) -> str:
        with self._lock:
            now = time.time()
            if refresh or not self._token or now - self._token_issued > TOKEN_REFRESH_SECONDS:
                self._token = jwt.encode({"iss": settings.APP_PUSH_APNS_TEAM_ID, "iat": int(now)}, _private_key(), algorithm="ES256", headers={"kid": settings.APP_PUSH_APNS_KEY_ID})
                self._token_issued = now
            return self._token

    def _post(self, device, payload, *, token=None, push_type="alert", topic_suffix="", priority=10, refresh=False):
        headers = {
            "authorization": f"bearer {self._provider_token(refresh=refresh)}",
            "apns-topic": settings.APP_PUSH_APNS_BUNDLE_ID + topic_suffix,
            "apns-push-type": push_type,
            "apns-priority": str(priority),
        }
        return self.client.post(f"{APNS_HOSTS[device.environment]}/3/device/{token or device.push_token}", content=json.dumps(payload), headers=headers)

    def publish(self, device, *, title, body, data, badge=None, thread_id=None):
        if device.platform != PushDevice.Platform.IOS:
            return True
        return self._send(device, build_payload(title=title, body=body, data=data, badge=badge, thread_id=thread_id))

    def live_activity(self, device, *, token, event, content_state, attributes=None, alert=None, stale_at=None, dismiss_at=None, priority=10):
        if device.platform != PushDevice.Platform.IOS:
            return True
        payload = build_live_activity_payload(event=event, content_state=content_state, attributes=attributes, alert=alert, stale_at=stale_at, dismiss_at=dismiss_at)
        return self._send(device, payload, token=token, push_type="liveactivity", topic_suffix=".push-type.liveactivity", priority=priority)

    def _send(self, device, payload, **options):
        response = self._post(device, payload, **options)
        if response.status_code == 403 and _reason(response) == "ExpiredProviderToken":
            response = self._post(device, payload, refresh=True, **options)
        if response.status_code == 200:
            return True
        reason = _reason(response)
        if response.status_code == 410 or reason in DEAD_TOKEN_REASONS:
            return False
        # Throttling, a server error, a bad key: the caller logs it and carries on.
        raise ApnsError(f"APNs answered {response.status_code} {reason}")


class ApnsError(Exception):
    pass


def _reason(response) -> str:
    try:
        return response.json().get("reason", "")
    except ValueError:
        return ""


_gateway: PushGateway | None = None


def get_gateway() -> PushGateway:
    global _gateway
    override = getattr(settings, "APP_PUSH_GATEWAY", "")
    if override:
        return import_string(override)()
    if apns_configured():
        if _gateway is None:
            _gateway = ApnsGateway()
        return _gateway
    return NullGateway()
