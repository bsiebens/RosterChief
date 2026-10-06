"""Web Push delivery -- the push channel for notifications.Notification.

Wired in from mobile.signals (a notifications.signals.notifications_created
receiver), deliberately kept out of the notifications app itself, which stays
channel-agnostic (see that app's models.py docstring: "the whole point is
reusing this for other kinds of activity later"). A backward dependency the
other way -- notifications importing mobile -- would be the wrong direction:
notifications has no reason to know a PWA exists.
"""

import base64
import binascii
import json
import logging
from datetime import timedelta
from urllib.parse import urlsplit

from cryptography.hazmat.primitives.asymmetric import ec
from django.conf import settings
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from pywebpush import WebPushException, webpush

from .. import models

logger = logging.getLogger(__name__)

#: Seconds before giving up on one push service request -- a slow or hung push
#: service must not stall the rest of a fan-out (or the request/job running it).
PUSH_TIMEOUT = 10

#: The browser vendors' push services. The endpoint is client-supplied and the
#: server POSTs to it, so anything else is refused at subscribe time rather than
#: letting a subscription point the server at an arbitrary (e.g. internal) URL.
ALLOWED_PUSH_HOSTS = frozenset({"fcm.googleapis.com", "web.push.apple.com"})
ALLOWED_PUSH_HOST_SUFFIXES = (".push.services.mozilla.com", ".notify.windows.com")


#: A native install that has checked in within this many days counts as the account's phone:
#: its web push subscriptions are then skipped for notifications (below), so one event does not
#: buzz the same person twice. Older installs stop suppressing, in case the token went stale
#: without the push service ever reporting it dead.
NATIVE_DEVICE_ACTIVE_DAYS = 60


def users_with_native_push(user_ids) -> set:
    """Of ``user_ids``, the accounts that currently receive native (iOS/Android) push: an enabled
    device with a live SNS endpoint that has been seen recently."""
    from appapi.models import PushDevice  # deferred: appapi imports back into mobile

    user_ids = {user_id for user_id in user_ids if user_id}
    if not user_ids:
        return set()
    cutoff = timezone.now() - timedelta(days=NATIVE_DEVICE_ACTIVE_DAYS)
    return set(PushDevice.objects.filter(user_id__in=user_ids, enabled=True, last_seen__gte=cutoff).exclude(endpoint_arn="").values_list("user_id", flat=True))


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


def _b64url_decode(value) -> bytes | None:
    if not isinstance(value, str) or not value or len(value) > 255:
        return None
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except binascii.Error, ValueError:
        return None


def subscription_error(endpoint, p256dh, auth):
    """Why a client-supplied subscription can't be stored, or None if it can:
    an allowlisted HTTPS push-service endpoint, a p256dh that is an uncompressed
    P-256 public key (65 bytes, 0x04 prefix, on the curve) and a 16-byte auth
    secret -- what the Push API hands every browser."""
    if not is_allowed_push_endpoint(endpoint):
        return _("Unsupported push service endpoint.")
    key = _b64url_decode(p256dh)
    if key is None or len(key) != 65 or key[0] != 0x04:
        return _("Invalid p256dh key.")
    try:
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), key)
    except ValueError:
        return _("Invalid p256dh key.")
    secret = _b64url_decode(auth)
    if secret is None or len(secret) != 16:
        return _("Invalid auth secret.")
    return None


def _deliver(subscription, payload: str, *, label) -> None:
    if not is_allowed_push_endpoint(subscription.endpoint):
        # Stored before endpoints were validated on subscribe: never sent to, and removed
        # so it doesn't linger (the browser re-subscribes with a valid one when it can).
        subscription.delete()
        return
    try:
        webpush(
            subscription_info=subscription.as_subscription_info(),
            data=payload,
            vapid_private_key=settings.VAPID_PRIVATE_KEY,
            vapid_claims={"sub": f"mailto:{settings.VAPID_ADMIN_EMAIL}"},
            timeout=PUSH_TIMEOUT,
        )
    except WebPushException as exc:
        status = exc.response.status_code if exc.response is not None else None
        if status in (404, 410):
            # The browser's push service says this registration is gone for good --
            # not a transient failure, so keeping it around would only mean retrying
            # a subscription that will never accept a push again.
            subscription.delete()
        else:
            logger.warning("Push send failed for %s: %s", label, exc)
    except Exception as exc:
        # Never fatal, whatever went wrong with this one subscription (network
        # error/timeout, or e.g. a stored key pywebpush rejects with a ValueError) --
        # the remaining subscriptions in the fan-out still get theirs. Same reasoning
        # as every other branded send in this app (see e.g.
        # notifications.services._send_email).
        logger.warning("Push send failed for %s: %s", label, exc)


def send_push_to_member(member, *, title: str, body: str, url: str = "/app/") -> None:
    if not settings.VAPID_PRIVATE_KEY:
        # Dev/no-config default -- see the settings.py comment next to VAPID_PRIVATE_KEY.
        return

    # str(): title/body often arrive as a gettext_lazy proxy (or the still-lazy
    # result of formatting one, e.g. `_("...") % {...}` -- Django's lazy strings
    # stay lazy through `%` too), not a plain str -- json.dumps can't serialize
    # that object directly and raises TypeError. Forcing it here, once, at the
    # one place this payload actually gets encoded, is simpler and safer than
    # auditing every caller across the app that builds a title/body.
    payload = json.dumps({"title": str(title), "body": str(body), "url": url})
    for subscription in models.PushSubscription.objects.filter(member=member):
        _deliver(subscription, payload, label=member)


def send_push_to_user(club, user, *, title: str, body: str, url: str = "/app/") -> int:
    """Web push to every subscription this account's people in ``club`` hold --
    for pushes that aren't a stored Notification (e.g. live game updates,
    events.services.game_push). Returns how many subscriptions it was sent to."""
    if not settings.VAPID_PRIVATE_KEY:
        return 0
    if users_with_native_push([user.pk]):
        return 0  # the phone already got it (events.services.game_push sends native alongside this)
    payload = json.dumps({"title": str(title), "body": str(body), "url": url})
    subscriptions = list(models.PushSubscription.objects.filter(club=club, member__user=user).select_related("member"))
    for subscription in subscriptions:
        _deliver(subscription, payload, label=subscription.member)
    return len(subscriptions)


def send_push_for_notifications(notifications, url: str = "/app/") -> None:
    """Batched send_push_to_member for a whole notifications.services.
    notify_members() fan-out at once: every notification's member's
    PushSubscriptions are fetched in a single query instead of one query per
    member (what a post_save-per-instance signal would otherwise cost),
    still delivering each notification's own title/body."""
    if not settings.VAPID_PRIVATE_KEY or not notifications:
        return

    member_ids = {notification.member_id for notification in notifications}
    subscriptions = list(models.PushSubscription.objects.filter(member_id__in=member_ids).select_related("member"))
    # An account with an active native install gets this through the native channel
    # (appapi.delivery), so its web subscriptions sit this one out.
    on_native = users_with_native_push(subscription.member.user_id for subscription in subscriptions)
    subscriptions_by_member = {}
    for subscription in subscriptions:
        if subscription.member.user_id in on_native:
            continue
        subscriptions_by_member.setdefault(subscription.member_id, []).append(subscription)

    for notification in notifications:
        subscriptions = subscriptions_by_member.get(notification.member_id)
        if not subscriptions:
            continue
        payload = json.dumps({"title": str(notification.title), "body": str(notification.body), "url": url})
        for subscription in subscriptions:
            _deliver(subscription, payload, label=subscription.member)
