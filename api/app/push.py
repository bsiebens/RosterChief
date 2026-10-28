"""Native push: straight to Apple's APNs over HTTP/2 with token-based auth for iOS, and to
Firebase Cloud Messaging (HTTP v1) for Android (API.md §9).

Everything talks to a ``PushGateway`` so tests (and a server without credentials)
never touch the network:

* ``ApnsGateway`` -- iOS, used when the APNs key is configured;
* ``FcmGateway`` -- Android, used when a Firebase service account is configured;
* ``PlatformGateway`` -- what ``get_gateway()`` returns: routes a device to the gateway of its
  platform; a platform without credentials is simply not ``supports()``-ed;
* ``NullGateway`` -- nothing configured: devices still register, nothing is sent;
* ``api.app.tests`` swaps in a fake via ``APP_PUSH_GATEWAY``.

Settings (all optional; empty = pushes can't be delivered yet)::

    APP_PUSH_APNS_KEY_ID        # the .p8 key's id
    APP_PUSH_APNS_TEAM_ID       # the Apple Developer team id
    APP_PUSH_APNS_BUNDLE_ID     # the app's bundle id = the apns-topic
    APP_PUSH_APNS_KEY           # the .p8 file's text ("\\n" for newlines in an env file)
    APP_PUSH_APNS_KEY_FILE      # or the path of the .p8 file
    APP_PUSH_APNS_ACTIVITY_ATTRIBUTES   # the app's ActivityAttributes type name (Live Activity start)
    APP_PUSH_FCM_CREDENTIALS    # the Firebase service-account JSON (one line)
    APP_PUSH_FCM_CREDENTIALS_FILE   # or the path of that JSON file
    APP_PUSH_FCM_PROJECT_ID     # the Firebase project id (default: the one in the service account)
    APP_PUSH_FCM_CHANNEL_ID     # the Android notification channel the app creates (default "notifications")
    APP_PUSH_GATEWAY            # dotted path to override the gateway class

A device's ``environment`` picks the APNs host: ``sandbox`` -> api.sandbox.push.apple.com
(debug builds from Xcode), ``production`` -> api.push.apple.com (TestFlight and the App
Store). FCM has no sandbox: ``environment`` is ignored for Android. Payloads are pointers, never content (API.md §9): a
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

from api.models import PushDevice
from notifications import sandbox
from notifications.policy import PushPolicy

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

    def publish(self, device: PushDevice, *, title: str, body: str, data: dict, badge: int | None = None, thread_id: str | None = None, policy: PushPolicy | None = None, collapse_id: str | None = None) -> bool:
        """Send one push. ``badge`` is the app icon's unread count (iOS); None leaves it as is.
        ``thread_id`` groups notifications on the lock screen. ``policy`` (notifications.policy) sets
        the TTL, priority and Android channel; ``collapse_id`` names "the same thing" and is used
        only when ``policy.collapse``: a newer push replaces the older one. False means the token is dead:
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

    def publish(self, device, *, title, body, data, badge=None, thread_id=None, policy=None, collapse_id=None):
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


def apns_policy_headers(policy: PushPolicy | None, collapse_id: str | None = None, *, now: float | None = None) -> dict:
    """``apns-expiration`` (an absolute unix time: APNs drops a push it could not deliver by then)
    and, for a collapsing policy, ``apns-collapse-id`` (max 64 bytes)."""
    if policy is None:
        return {}
    headers = {"apns-expiration": str(int((time.time() if now is None else now) + policy.ttl_seconds))}
    if policy.collapse and collapse_id:
        headers["apns-collapse-id"] = collapse_id[:64]
    return headers


def apns_configured(*, real: bool = False) -> bool:
    """Whether APNs can be used: the key, key id and team id are set -- or the sandbox is on (``real=True`` ignores that)."""
    if sandbox.enabled() and not real:
        return True
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

    def _post(self, device, payload, *, token=None, push_type="alert", topic_suffix="", priority=10, refresh=False, extra_headers=None):
        headers = {
            "authorization": f"bearer {self._provider_token(refresh=refresh)}",
            "apns-topic": settings.APP_PUSH_APNS_BUNDLE_ID + topic_suffix,
            "apns-push-type": push_type,
            "apns-priority": str(priority),
            **(extra_headers or {}),
        }
        return self.client.post(f"{APNS_HOSTS[device.environment]}/3/device/{token or device.push_token}", content=json.dumps(payload), headers=headers)

    def publish(self, device, *, title, body, data, badge=None, thread_id=None, policy=None, collapse_id=None):
        if device.platform != PushDevice.Platform.IOS:
            return True
        headers = apns_policy_headers(policy, collapse_id)
        priority = policy.apns_priority if policy else 10
        return self._send(device, build_payload(title=title, body=body, data=data, badge=badge, thread_id=thread_id), priority=priority, extra_headers=headers)

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
        raise ApnsError(f"APNs answered {response.status_code} {reason}", status_code=response.status_code)


class ApnsError(Exception):
    """APNs refused the request. ``retryable``: throttling (429) or a server error (5xx) -- try
    again later; anything else (a bad key, a malformed payload) will not fix itself."""

    def __init__(self, message, *, status_code=None):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = status_code is not None and (status_code == 429 or status_code >= 500)


def _reason(response) -> str:
    try:
        return response.json().get("reason", "")
    except ValueError:
        return ""


FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
FCM_SEND_URL = "https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"

#: FCM ``errorCode`` values that mean "this token will never work again".
FCM_DEAD_TOKEN_CODES = {"UNREGISTERED"}


def fcm_configured(*, real: bool = False) -> bool:
    """Whether FCM can be used: service-account credentials are set -- or the sandbox is on (``real=True`` ignores that)."""
    if sandbox.enabled() and not real:
        return True
    return bool(settings.APP_PUSH_FCM_CREDENTIALS or settings.APP_PUSH_FCM_CREDENTIALS_FILE)


def _fcm_service_account() -> dict:
    if settings.APP_PUSH_FCM_CREDENTIALS:
        return json.loads(settings.APP_PUSH_FCM_CREDENTIALS)
    return json.loads(Path(settings.APP_PUSH_FCM_CREDENTIALS_FILE).read_text())


def build_fcm_message(device: PushDevice, *, title: str, body: str, data: dict, thread_id: str | None = None, policy: PushPolicy | None = None, collapse_id: str | None = None) -> dict:
    """The FCM HTTP v1 request body. FCM ``data`` values must be strings, so structured values
    (the ``target`` dictionary) are sent as JSON text and absent ones left out. The visible
    notification is shown by the system even when the app is closed; ``thread_id`` becomes the
    notification ``tag`` so a club's notifications stack and replace instead of piling up."""
    if policy is None:
        android = {"priority": "HIGH", "notification": {"channel_id": settings.APP_PUSH_FCM_CHANNEL_ID}}
    else:
        high = policy.android_importance == "high" or policy.apns_priority == 10
        android = {"priority": "HIGH" if high else "NORMAL", "ttl": f"{policy.ttl_seconds}s", "notification": {"channel_id": policy.android_channel_id}}
        if policy.collapse and collapse_id:
            android["collapse_key"] = collapse_id
    if thread_id:
        android["notification"]["tag"] = thread_id
    payload = {value_key: value if isinstance(value, str) else json.dumps(value) for value_key, value in data.items() if value is not None}
    return {"message": {"token": device.push_token, "notification": {"title": title, "body": body}, "data": payload, "android": android}}


class FcmGateway(PushGateway):
    """Android push through FCM HTTP v1. OAuth2 access tokens come from the service account
    (``google-auth``) and are refreshed when they expire; one pooled ``httpx`` client per process."""

    def __init__(self, client=None, credentials=None):
        self._client = client
        self._credentials = credentials
        self._project_id = settings.APP_PUSH_FCM_PROJECT_ID
        self._lock = threading.Lock()

    @property
    def client(self):
        with self._lock:
            if self._client is None:
                self._client = httpx.Client(timeout=10.0)
            return self._client

    def supports(self, device):
        return device.platform == PushDevice.Platform.ANDROID

    def _access_token(self, *, refresh=False) -> str:
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account

        with self._lock:
            if self._credentials is None:
                info = _fcm_service_account()
                self._project_id = self._project_id or info["project_id"]
                self._credentials = service_account.Credentials.from_service_account_info(info, scopes=[FCM_SCOPE])
            if refresh or not self._credentials.valid:
                self._credentials.refresh(Request())
            return self._credentials.token

    def _post(self, message, *, refresh=False):
        token = self._access_token(refresh=refresh)
        return self.client.post(FCM_SEND_URL.format(project_id=self._project_id), json=message, headers={"authorization": f"Bearer {token}"})

    def publish(self, device, *, title, body, data, badge=None, thread_id=None, policy=None, collapse_id=None):
        if device.platform != PushDevice.Platform.ANDROID:
            return True
        message = build_fcm_message(device, title=title, body=body, data=data, thread_id=thread_id, policy=policy, collapse_id=collapse_id)
        response = self._post(message)
        if response.status_code == 401:
            response = self._post(message, refresh=True)
        if response.status_code == 200:
            return True
        code, text = _fcm_error(response)
        if response.status_code == 404 or code in FCM_DEAD_TOKEN_CODES or (code == "INVALID_ARGUMENT" and "registration token" in text.lower()):
            return False
        # Quota, a server error, a misconfigured project: the caller logs it and carries on.
        raise FcmError(f"FCM answered {response.status_code} {code}", status_code=response.status_code)

    def live_activity(self, device, **kwargs):
        return True  # Live Activities are iOS-only.


class FcmError(Exception):
    """FCM refused the request. ``retryable``: quota (429) or a server error (5xx)."""

    def __init__(self, message, *, status_code=None):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = status_code is not None and (status_code == 429 or status_code >= 500)


def _fcm_error(response) -> tuple[str, str]:
    """``(errorCode, message)`` of an FCM error body: the specific ``errorCode`` sits in
    ``error.details`` (``FcmError``), falling back to the generic ``error.status``."""
    try:
        error = response.json().get("error", {})
    except ValueError:
        return "", ""
    for detail in error.get("details", []):
        if detail.get("errorCode"):
            return detail["errorCode"], error.get("message", "")
    return error.get("status", ""), error.get("message", "")


class PlatformGateway(PushGateway):
    """Routes each device to the gateway of its platform. A platform without a gateway (not
    configured) is not ``supports()``-ed, so ``push_enabled`` reports it honestly."""

    def __init__(self, ios: PushGateway | None = None, android: PushGateway | None = None):
        self.gateways = {PushDevice.Platform.IOS: ios, PushDevice.Platform.ANDROID: android}

    def _for(self, device):
        return self.gateways.get(device.platform)

    def supports(self, device):
        gateway = self._for(device)
        return gateway is not None and gateway.supports(device)

    def publish(self, device, **kwargs):
        gateway = self._for(device)
        return True if gateway is None else gateway.publish(device, **kwargs)

    def live_activity(self, device, **kwargs):
        gateway = self._for(device)
        return True if gateway is None else gateway.live_activity(device, **kwargs)


def _sandbox_club(data: dict | None):
    """The club a pushed ``club_slug`` names, for the sandbox's inbox (None when absent)."""
    slug = (data or {}).get("club_slug")
    if not slug:
        return None
    from club.models import Club

    return Club.objects.filter(slug=slug).first()


class SandboxGateway(PushGateway):
    """``settings.PUSH_SANDBOX``: builds the exact APNs/FCM request, stores it as a ``SandboxMessage`` instead
    of sending it, and answers as the active failure scenario dictates (notifications.sandbox)."""

    def supports(self, device):
        return device.platform in (PushDevice.Platform.IOS, PushDevice.Platform.ANDROID)

    def _outcome(self, outcome, error_class, provider):
        Outcome = sandbox.Outcome
        if outcome == Outcome.DELIVERED:
            return True
        if outcome == Outcome.DEAD:
            return False  # the caller disables the device, as for a real dead token
        if outcome == Outcome.THROTTLED:
            raise error_class(f"{provider} sandbox: throttled", status_code=429)
        if outcome == Outcome.TIMEOUT:
            raise httpx.ConnectTimeout(f"{provider} sandbox: timed out")
        raise error_class(f"{provider} sandbox: request refused", status_code=400)

    def _apns(self, device, payload, *, title, body, headers, club):
        outcome = sandbox.capture(channel="native", provider="apns", target=device.installation_id, title=title, body=body, request=payload, headers=headers, user=device.user, club=club)
        return self._outcome(outcome, ApnsError, "APNs")

    def publish(self, device, *, title, body, data, badge=None, thread_id=None, policy=None, collapse_id=None):
        club = _sandbox_club(data)
        if device.platform == PushDevice.Platform.IOS:
            headers = {
                "apns-topic": settings.APP_PUSH_APNS_BUNDLE_ID,
                "apns-push-type": "alert",
                "apns-priority": str(policy.apns_priority if policy else 10),
                **apns_policy_headers(policy, collapse_id),
            }
            payload = build_payload(title=title, body=body, data=data, badge=badge, thread_id=thread_id)
            return self._apns(device, payload, title=title, body=body, headers=headers, club=club)
        if device.platform == PushDevice.Platform.ANDROID:
            message = build_fcm_message(device, title=title, body=body, data=data, thread_id=thread_id, policy=policy, collapse_id=collapse_id)
            outcome = sandbox.capture(channel="native", provider="fcm", target=device.installation_id, title=title, body=body, request=message, user=device.user, club=club)
            return self._outcome(outcome, FcmError, "FCM")
        return True

    def live_activity(self, device, *, token, event, content_state, attributes=None, alert=None, stale_at=None, dismiss_at=None, priority=10):
        if device.platform != PushDevice.Platform.IOS:
            return True
        payload = build_live_activity_payload(event=event, content_state=content_state, attributes=attributes, alert=alert, stale_at=stale_at, dismiss_at=dismiss_at)
        headers = {"apns-topic": settings.APP_PUSH_APNS_BUNDLE_ID + ".push-type.liveactivity", "apns-push-type": "liveactivity", "apns-priority": str(priority)}
        title = (alert or {}).get("title", "") or f"Live Activity {event}"
        return self._apns(device, payload, title=str(title), body=str((alert or {}).get("body", "")), headers=headers, club=None)


_apns_gateway: ApnsGateway | None = None
_fcm_gateway: FcmGateway | None = None


def push_configured() -> bool:
    """Whether any platform can be pushed to (or a gateway override is set, as tests do, or the sandbox is on)."""
    return bool(getattr(settings, "APP_PUSH_GATEWAY", "") or sandbox.enabled() or apns_configured() or fcm_configured())


def is_retryable(error: Exception) -> bool:
    """Whether a gateway failure is worth another try later: an APNs/FCM throttle or server error, or
    the network failing before an answer came back."""
    if isinstance(error, ApnsError | FcmError):
        return error.retryable
    return isinstance(error, httpx.TransportError | httpx.TimeoutException)


def get_gateway() -> PushGateway:
    global _apns_gateway, _fcm_gateway
    override = getattr(settings, "APP_PUSH_GATEWAY", "")
    if override:
        return import_string(override)()
    if sandbox.enabled():
        return SandboxGateway()
    if not (apns_configured() or fcm_configured()):
        return NullGateway()
    if apns_configured() and _apns_gateway is None:
        _apns_gateway = ApnsGateway()
    if fcm_configured() and _fcm_gateway is None:
        _fcm_gateway = FcmGateway()
    return PlatformGateway(ios=_apns_gateway if apns_configured() else None, android=_fcm_gateway if fcm_configured() else None)
