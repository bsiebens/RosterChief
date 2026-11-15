"""Provider gateways: APNs (iOS, HTTP/2 + ES256 JWT), FCM (Android, HTTP v1), web push (VAPID) and email, plus the fake
``SandboxGateway`` that captures instead of sending.

Every gateway has ``send(target, message, *, user=None, club=None) -> GatewayResult`` and never raises: the outcome is classified as
``sent``, ``retry`` (throttled, a 5xx, the network failed), ``dead`` (the target is gone for good) or ``failed`` (refused, do not retry).
Payloads are pointers (``kind``, ``notification_id``, ``club``, ``category``, ``data``) next to the visible title and body.

Credentials are read from the environment (``settings`` attribute first, then the process environment / .env):
APP_PUSH_APNS_KEY_ID, APP_PUSH_APNS_TEAM_ID, APP_PUSH_APNS_BUNDLE_ID, APP_PUSH_APNS_KEY | APP_PUSH_APNS_KEY_FILE,
APP_PUSH_FCM_CREDENTIALS | APP_PUSH_FCM_CREDENTIALS_FILE, APP_PUSH_FCM_PROJECT_ID, APP_PUSH_FCM_CHANNEL_ID,
DJANGO_VAPID_PUBLIC_KEY, DJANGO_VAPID_PRIVATE_KEY, DJANGO_VAPID_ADMIN_EMAIL."""

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import jwt
import requests
from decouple import config
from django.conf import settings
from django.core.mail import EmailMessage

from . import sandbox
from .models import PushDevice
from .policy import PushPolicy, policy_for

logger = logging.getLogger(__name__)

SENT, RETRY, DEAD, FAILED = "sent", "retry", "dead", "failed"

APNS_HOSTS = {PushDevice.Environment.PRODUCTION: "https://api.push.apple.com", PushDevice.Environment.SANDBOX: "https://api.sandbox.push.apple.com"}
APNS_TOKEN_SECONDS = 50 * 60  # Apple wants a provider token refreshed no more often than every 20 minutes, and rejects one older than an hour
APNS_DEAD_REASONS = {"BadDeviceToken", "Unregistered", "DeviceTokenNotForTopic"}
FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
FCM_SEND_URL = "https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"
FCM_DEAD_CODES = {"UNREGISTERED"}
HTTP_TIMEOUT = 10.0

PROVIDERS = ("apns", "fcm", "webpush")


def _env(name: str, default: str = "") -> str:
    value = getattr(settings, name, None)
    if value is None:
        value = config(name, default=default)
    return str(value or "")


# --- messages and results ---------------------------------------------------------------------------------------------


@dataclass
class Message:
    """What to deliver to one target. ``to_dict``/``from_dict`` is what a retrying or held delivery keeps in its payload."""

    title: str
    body: str
    data: dict = field(default_factory=dict)
    category: str = "general"
    priority: str = "normal"
    notification_id: str | None = None
    club_slug: str | None = None
    collapse_id: str | None = None

    @property
    def policy(self) -> PushPolicy:
        return policy_for(self.category, self.priority)

    def pointer(self) -> dict:
        """The custom keys the app receives: pointers, the app fetches the rest over the API."""
        pointer = {"kind": "notification", "category": str(self.category), "data": self.data or {}}
        if self.notification_id:
            pointer["notification_id"] = str(self.notification_id)
        if self.club_slug:
            pointer["club"] = self.club_slug
        return pointer

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "body": self.body,
            "data": self.data,
            "category": str(self.category),
            "priority": str(self.priority),
            "notification_id": self.notification_id,
            "club_slug": self.club_slug,
            "collapse_id": self.collapse_id,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> Message:
        known = set(cls.__dataclass_fields__)
        return cls(**{key: value for key, value in (raw or {}).items() if key in known})


@dataclass
class Target:
    """One place a message goes. ``key`` is what the delivery log stores (installation id, endpoint or email address)."""

    channel: str  # NotificationDelivery.DeliveryChannel value
    provider: str
    key: str
    obj: object = None  # PushDevice / WebPushSubscription / None for email


@dataclass
class GatewayResult:
    outcome: str  # sent / retry / dead / failed
    error: str = ""
    status_code: int | None = None
    sandboxed: bool = False


# --- credentials ------------------------------------------------------------------------------------------------------


def credentials_status(provider: str) -> dict:
    """``{"configured": bool, "missing": [ENV NAMES]}`` about the real credentials, whatever the sandbox does."""
    missing = []
    if provider == "apns":
        for name in ("APP_PUSH_APNS_KEY_ID", "APP_PUSH_APNS_TEAM_ID", "APP_PUSH_APNS_BUNDLE_ID"):
            if not _env(name, "app.rosterchief.RosterChief" if name.endswith("BUNDLE_ID") else ""):
                missing.append(name)
        if not (_env("APP_PUSH_APNS_KEY") or _env("APP_PUSH_APNS_KEY_FILE")):
            missing.append("APP_PUSH_APNS_KEY")
    elif provider == "fcm":
        if not (_env("APP_PUSH_FCM_CREDENTIALS") or _env("APP_PUSH_FCM_CREDENTIALS_FILE")):
            missing.append("APP_PUSH_FCM_CREDENTIALS")
    elif provider == "webpush":
        for name in ("DJANGO_VAPID_PUBLIC_KEY", "DJANGO_VAPID_PRIVATE_KEY"):
            if not _env(name):
                missing.append(name)
    elif provider == "email":
        pass
    else:
        raise ValueError(f"unknown provider: {provider}")
    return {"configured": not missing, "missing": missing}


def _apns_bundle_id() -> str:
    return _env("APP_PUSH_APNS_BUNDLE_ID", "app.rosterchief.RosterChief")


def _apns_private_key() -> str:
    key = _env("APP_PUSH_APNS_KEY").replace("\\n", "\n")
    return key or Path(_env("APP_PUSH_APNS_KEY_FILE")).read_text()


def _fcm_service_account() -> dict:
    raw = _env("APP_PUSH_FCM_CREDENTIALS")
    return json.loads(raw) if raw else json.loads(Path(_env("APP_PUSH_FCM_CREDENTIALS_FILE")).read_text())


def validate_credentials(provider: str) -> dict:
    """Local validation, never sends a push: parse the key and build a JWT (APNs), load the service account and fetch an OAuth
    token (FCM), load the VAPID key pair and check it matches the public key (web push)."""
    status = credentials_status(provider)
    if not status["configured"]:
        return {"ok": False, "detail": "Missing: " + ", ".join(status["missing"])}
    try:
        if provider == "apns":
            jwt.encode({"iss": _env("APP_PUSH_APNS_TEAM_ID"), "iat": int(time.time())}, _apns_private_key(), algorithm="ES256", headers={"kid": _env("APP_PUSH_APNS_KEY_ID")})
            return {"ok": True, "detail": "The APNs key is valid and a provider token can be signed."}
        if provider == "fcm":
            from google.auth.transport.requests import Request
            from google.oauth2 import service_account

            info = _fcm_service_account()
            credentials = service_account.Credentials.from_service_account_info(info, scopes=[FCM_SCOPE])
            credentials.refresh(Request())
            return {"ok": True, "detail": f"Service account accepted for project {_env('APP_PUSH_FCM_PROJECT_ID') or info.get('project_id', '?')}."}
        if provider == "webpush":
            vapid = _load_vapid()
            public = _vapid_public_key(vapid)
            configured = _env("DJANGO_VAPID_PUBLIC_KEY")
            if configured and public != configured.strip():
                return {"ok": False, "detail": "The VAPID private key does not belong to DJANGO_VAPID_PUBLIC_KEY."}
            return {"ok": True, "detail": "The VAPID key pair is valid."}
    except Exception as error:
        return {"ok": False, "detail": f"{type(error).__name__}: {error}"[:300]}
    return {"ok": False, "detail": f"Nothing to validate for {provider}."}


def _load_vapid():
    from py_vapid import Vapid

    key = _env("DJANGO_VAPID_PRIVATE_KEY").replace("\\n", "\n").strip()
    return Vapid.from_pem(key.encode()) if key.startswith("-----") else Vapid.from_string(key)


def _vapid_public_key(vapid) -> str:
    from cryptography.hazmat.primitives import serialization
    from py_vapid.utils import b64urlencode

    raw = vapid.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    encoded = b64urlencode(raw)
    return encoded.decode() if isinstance(encoded, bytes) else encoded


# --- request builders (shared by the real and the sandbox gateway) -------------------------------------------------------


def apns_payload(message: Message) -> dict:
    aps = {"alert": {"title": message.title, "body": message.body}, "sound": "default", "mutable-content": 1}
    if message.club_slug:
        aps["thread-id"] = message.club_slug
    return {"aps": aps, **message.pointer()}


def apns_headers(message: Message, *, now: float | None = None) -> dict:
    policy = message.policy
    headers = {"apns-topic": _apns_bundle_id(), "apns-push-type": "alert", "apns-priority": str(policy.apns_priority), "apns-expiration": str(int((time.time() if now is None else now) + policy.ttl_seconds))}
    if policy.collapse and message.collapse_id:
        headers["apns-collapse-id"] = message.collapse_id[:64]
    return headers


def fcm_message(token: str, message: Message) -> dict:
    """FCM data values must be strings, so structured values are sent as JSON text."""
    policy = message.policy
    android = {
        "priority": "HIGH" if (policy.android_importance == "high" or policy.apns_priority == 10) else "NORMAL",
        "ttl": f"{policy.ttl_seconds}s",
        "notification": {"channel_id": policy.android_channel_id},
    }
    if policy.collapse and message.collapse_id:
        android["collapse_key"] = message.collapse_id
    if message.club_slug:
        android["notification"]["tag"] = message.club_slug
    data = {key: value if isinstance(value, str) else json.dumps(value) for key, value in message.pointer().items() if value is not None}
    return {"message": {"token": token, "notification": {"title": message.title, "body": message.body}, "data": data, "android": android}}


def webpush_payload(message: Message) -> dict:
    url = (message.data or {}).get("url") or "/"
    return {"title": message.title, "body": message.body, "url": url, **message.pointer()}


# --- real gateways ---------------------------------------------------------------------------------------------------------


def _not_configured(provider: str) -> GatewayResult | None:
    status = credentials_status(provider)
    if status["configured"]:
        return None
    return GatewayResult(FAILED, f"{provider} is not configured (missing {', '.join(status['missing'])})")


def webpush_public_key() -> str:
    return _env("DJANGO_VAPID_PUBLIC_KEY") if credentials_status("webpush")["configured"] else ""


#: The browser vendors' push services. The endpoint is client-supplied and the server POSTs to it, so anything else is refused.
ALLOWED_PUSH_HOSTS = frozenset({"fcm.googleapis.com", "web.push.apple.com"})
ALLOWED_PUSH_HOST_SUFFIXES = (".push.services.mozilla.com", ".notify.windows.com")


def is_allowed_push_endpoint(endpoint) -> bool:
    if not isinstance(endpoint, str) or len(endpoint) > 500:
        return False
    try:
        parts = urlsplit(endpoint)
        port = parts.port
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or parts.username is not None or parts.password is not None or port not in (None, 443):
        return False
    return host in ALLOWED_PUSH_HOSTS or any(host.endswith(suffix) and len(host) > len(suffix) for suffix in ALLOWED_PUSH_HOST_SUFFIXES)


def _classify_status(status_code: int, error: str) -> GatewayResult:
    if status_code == 429 or status_code >= 500:
        return GatewayResult(RETRY, error, status_code)
    return GatewayResult(FAILED, error, status_code)


class ApnsGateway:
    """One shared HTTP/2 client per process (created on first use) and a provider token reused for 50 minutes."""

    def __init__(self, client=None):
        self._client = client
        self._lock = threading.Lock()
        self._token = ""
        self._token_issued = 0.0

    @property
    def client(self):
        with self._lock:
            if self._client is None:
                self._client = httpx.Client(http2=True, timeout=HTTP_TIMEOUT)
            return self._client

    def _provider_token(self, *, refresh=False) -> str:
        with self._lock:
            now = time.time()
            if refresh or not self._token or now - self._token_issued > APNS_TOKEN_SECONDS:
                self._token = jwt.encode({"iss": _env("APP_PUSH_APNS_TEAM_ID"), "iat": int(now)}, _apns_private_key(), algorithm="ES256", headers={"kid": _env("APP_PUSH_APNS_KEY_ID")})
                self._token_issued = now
            return self._token

    def _post(self, device, message, *, refresh=False):
        headers = {"authorization": f"bearer {self._provider_token(refresh=refresh)}", **apns_headers(message)}
        return self.client.post(f"{APNS_HOSTS[device.environment]}/3/device/{device.push_token}", content=json.dumps(apns_payload(message)), headers=headers)

    def send(self, target, message, *, user=None, club=None) -> GatewayResult:
        if (missing := _not_configured("apns")) is not None:
            return missing
        try:
            response = self._post(target.obj, message)
            if response.status_code == 403 and _apns_reason(response) == "ExpiredProviderToken":
                response = self._post(target.obj, message, refresh=True)
        except httpx.HTTPError as error:
            return GatewayResult(RETRY, f"APNs transport error: {error}"[:500])
        except Exception as error:
            logger.exception("apns.send_failed")
            return GatewayResult(FAILED, f"{type(error).__name__}: {error}"[:500])
        if response.status_code == 200:
            return GatewayResult(SENT, status_code=200)
        reason = _apns_reason(response)
        error = f"APNs answered {response.status_code} {reason}".strip()
        if response.status_code == 410 or reason in APNS_DEAD_REASONS:
            return GatewayResult(DEAD, error, response.status_code)
        return _classify_status(response.status_code, error)


def _apns_reason(response) -> str:
    try:
        return response.json().get("reason", "")
    except ValueError:
        return ""


class FcmGateway:
    """FCM HTTP v1. OAuth2 access tokens come from the service account (google-auth) and are refreshed when they expire."""

    def __init__(self, client=None, credentials=None):
        self._client = client
        self._credentials = credentials
        self._project_id = ""
        self._lock = threading.Lock()

    @property
    def client(self):
        with self._lock:
            if self._client is None:
                self._client = httpx.Client(timeout=HTTP_TIMEOUT)
            return self._client

    def _access_token(self, *, refresh=False) -> str:
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account

        with self._lock:
            if self._credentials is None:
                info = _fcm_service_account()
                self._project_id = _env("APP_PUSH_FCM_PROJECT_ID") or info["project_id"]
                self._credentials = service_account.Credentials.from_service_account_info(info, scopes=[FCM_SCOPE])
            if not self._project_id:
                self._project_id = _env("APP_PUSH_FCM_PROJECT_ID")
            if refresh or not self._credentials.valid:
                self._credentials.refresh(Request())
            return self._credentials.token

    def _post(self, body, *, refresh=False):
        token = self._access_token(refresh=refresh)
        return self.client.post(FCM_SEND_URL.format(project_id=self._project_id), json=body, headers={"authorization": f"Bearer {token}"})

    def send(self, target, message, *, user=None, club=None) -> GatewayResult:
        if (missing := _not_configured("fcm")) is not None:
            return missing
        body = fcm_message(target.obj.push_token, message)
        try:
            response = self._post(body)
            if response.status_code == 401:
                response = self._post(body, refresh=True)
        except httpx.HTTPError as error:
            return GatewayResult(RETRY, f"FCM transport error: {error}"[:500])
        except Exception as error:
            logger.exception("fcm.send_failed")
            return GatewayResult(FAILED, f"{type(error).__name__}: {error}"[:500])
        if response.status_code == 200:
            return GatewayResult(SENT, status_code=200)
        code, text = _fcm_error(response)
        error = f"FCM answered {response.status_code} {code}".strip()
        if response.status_code == 404 or code in FCM_DEAD_CODES or (code == "INVALID_ARGUMENT" and "registration token" in text.lower()):
            return GatewayResult(DEAD, error, response.status_code)
        return _classify_status(response.status_code, error)


def _fcm_error(response) -> tuple[str, str]:
    try:
        error = response.json().get("error", {})
    except ValueError:
        return "", ""
    for detail in error.get("details", []):
        if detail.get("errorCode"):
            return detail["errorCode"], error.get("message", "")
    return error.get("status", ""), error.get("message", "")


class WebPushGateway:
    def send(self, target, message, *, user=None, club=None) -> GatewayResult:
        from pywebpush import WebPushException, webpush

        if (missing := _not_configured("webpush")) is not None:
            return missing
        subscription = target.obj
        if not is_allowed_push_endpoint(subscription.endpoint):
            return GatewayResult(DEAD, "unsupported push endpoint")
        try:
            webpush(
                subscription_info={"endpoint": subscription.endpoint, "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth}},
                data=json.dumps(webpush_payload(message)),
                vapid_private_key=_env("DJANGO_VAPID_PRIVATE_KEY").replace("\\n", "\n"),
                vapid_claims={"sub": f"mailto:{_env('DJANGO_VAPID_ADMIN_EMAIL', 'admin@rosterchief.app')}"},
                ttl=message.policy.ttl_seconds,
                timeout=HTTP_TIMEOUT,
            )
        except WebPushException as error:
            status = error.response.status_code if getattr(error, "response", None) is not None else None
            if status in (404, 410):
                return GatewayResult(DEAD, str(error)[:500], status)
            if status is None:
                return GatewayResult(RETRY, str(error)[:500])
            return _classify_status(status, str(error)[:500])
        except requests.RequestException as error:
            return GatewayResult(RETRY, str(error)[:500])
        except Exception as error:  # e.g. a stored key pywebpush rejects: never fatal for the other targets
            return GatewayResult(FAILED, f"{type(error).__name__}: {error}"[:500])
        return GatewayResult(SENT, status_code=201)


class EmailGateway:
    """Plain notification email to the account's address (subject = title, body = body)."""

    def send(self, target, message, *, user=None, club=None) -> GatewayResult:
        from rosterchief.mail import send_message

        try:
            sent = send_message(EmailMessage(message.title, message.body, to=[target.key]))
        except Exception as error:
            logger.exception("email.send_failed")
            return GatewayResult(RETRY, f"{type(error).__name__}: {error}"[:500])
        return GatewayResult(SENT) if sent else GatewayResult(FAILED, "Automated email is paused.")


# --- sandbox -------------------------------------------------------------------------------------------------------------


class SandboxGateway:
    """Builds the exact request the real provider would get, stores it as a ``SandboxMessage`` and answers as the scenario dictates."""

    def __init__(self, provider: str):
        self.provider = provider

    def _request(self, target, message) -> dict:
        if self.provider == "apns":
            return {"headers": dict(apns_headers(message)), "payload": apns_payload(message)}
        if self.provider == "fcm":
            return fcm_message("<token>", message)
        if self.provider == "webpush":
            return {**webpush_payload(message), "endpoint": urlsplit(target.key).hostname or ""}
        return {"to": target.key, "subject": message.title, "body": message.body}

    def send(self, target, message, *, user=None, club=None) -> GatewayResult:
        outcome = sandbox.capture(channel=target.channel, provider=self.provider, target=target.key, title=message.title, body=message.body, request=self._request(target, message), user=user, club=club)
        label = f"{self.provider} sandbox"
        if outcome == "delivered":
            return GatewayResult(SENT, sandboxed=True)
        if outcome == "dead":
            return GatewayResult(DEAD, f"{label}: target gone", 410, sandboxed=True)
        if outcome == "throttled":
            return GatewayResult(RETRY, f"{label}: throttled (429)", 429, sandboxed=True)
        if outcome == "timeout":
            return GatewayResult(RETRY, f"{label}: timed out", sandboxed=True)
        return GatewayResult(FAILED, f"{label}: request refused (400)", 400, sandboxed=True)


_real: dict = {}


def reset_gateways() -> None:
    """Forget the cached real gateways (tests, or after the credentials changed)."""
    _real.clear()


def get_gateway(provider: str, *, force_sandbox: bool = False):
    """The real gateway of ``provider``, or the fake one when the sandbox is on (or forced)."""
    if provider not in (*PROVIDERS, "email"):
        raise ValueError(f"unknown provider: {provider}")
    if force_sandbox or sandbox.is_on():
        return SandboxGateway(provider)
    if provider not in _real:
        _real[provider] = {"apns": ApnsGateway, "fcm": FcmGateway, "webpush": WebPushGateway, "email": EmailGateway}[provider]()
    return _real[provider]
