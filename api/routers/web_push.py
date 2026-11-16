"""Device-level web push registration for members on their own club host."""

import base64
import binascii

from cryptography.hazmat.primitives.asymmetric import ec
from django.db import IntegrityError
from ninja import Field, Router, Schema
from ninja.errors import HttpError

from api.auth import CookieAuth, TokenAuth
from notifications import gateways, services
from notifications.models import PushSettings, WebPushSubscription

from .notifications_inbox import notification_club

router = Router(auth=[TokenAuth(door=False), CookieAuth(door=False)])


class PushEndpoint(Schema):
    endpoint: str = Field(min_length=1, max_length=500)


class PushKeys(Schema):
    p256dh: str = Field(min_length=1, max_length=255)
    auth: str = Field(min_length=1, max_length=255)


class PushRegistration(PushEndpoint):
    keys: PushKeys


class PushConfig(Schema):
    public_key: str | None


class PushStatus(Schema):
    subscribed: bool


def _public_key() -> str | None:
    return (gateways.webpush_public_key() or None) if PushSettings.get().webpush_enabled else None


def _decode(raw: str) -> bytes:
    return base64.b64decode(raw + "=" * (-len(raw) % 4), altchars=b"-_", validate=True)


def _valid_keys(keys: PushKeys) -> bool:
    try:
        point = _decode(keys.p256dh)
        if len(point) != 65 or point[0] != 4:
            return False
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), point)
        return len(_decode(keys.auth)) == 16
    except (ValueError, binascii.Error):
        return False


def _registrations(request, endpoint):
    return WebPushSubscription.objects.filter(user=request.user, club=notification_club(request), endpoint=endpoint)


@router.get("/config", response=PushConfig)
def web_push_config(request):
    notification_club(request)
    return {"public_key": _public_key()}


@router.post("/status", response=PushStatus)
def web_push_status(request, payload: PushEndpoint):
    return {"subscribed": _registrations(request, payload.endpoint).exists()}


@router.post("/subscriptions", response={204: None})
def register_web_push(request, payload: PushRegistration):
    club = notification_club(request)
    if not _public_key():
        raise HttpError(503, "Web push is unavailable.")
    if not gateways.is_allowed_push_endpoint(payload.endpoint) or not _valid_keys(payload.keys):
        raise HttpError(400, "Invalid web push subscription.")
    try:
        services.subscribe_web(
            request.user,
            club=club,
            endpoint=payload.endpoint,
            p256dh=payload.keys.p256dh,
            auth=payload.keys.auth,
            user_agent=request.headers.get("User-Agent", ""),
            allow_reassignment=False,
        )
    except IntegrityError as exc:
        raise HttpError(409, "This push endpoint is already registered.") from exc
    return 204, None


@router.delete("/subscriptions", response={204: None})
def unregister_web_push(request, payload: PushEndpoint):
    _registrations(request, payload.endpoint).delete()
    return 204, None
