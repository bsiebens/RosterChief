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
import time
from urllib.parse import urlsplit

import requests
from cryptography.hazmat.primitives.asymmetric import ec
from django.conf import settings
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from pywebpush import WebPushException, webpush

from api.app.presenters import notification_target
from members.services.family import managed_people
from notifications import delivery_log, sandbox
from notifications.preferences import is_enabled
from notifications.services import muted_push_users, preference_key, quiet_decisions, recipient_user_ids, unread_count, users_with_native_push

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


def _send(subscription, payload: str) -> tuple[str, str]:
    """One Web Push request and its classification, never raising: ``(outcome, error)`` where
    outcome is ``sent``, ``dead`` (the push service says the registration is gone for good: 404/410),
    ``retry`` (throttled, a server error, a timeout or connection error) or ``failed`` (refused, or
    a stored key pywebpush rejects)."""
    if sandbox.enabled():
        return _sandbox_send(subscription, payload)
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
            return "dead", str(exc)
        if status is None or status == 429 or status >= 500:
            return "retry", str(exc)
        return "failed", str(exc)
    except requests.RequestException as exc:
        return "retry", str(exc)  # timeout / connection error
    except Exception as exc:
        # Never fatal, whatever went wrong with this one subscription (e.g. a stored key
        # pywebpush rejects with a ValueError) -- the remaining subscriptions in the fan-out
        # still get theirs.
        return "failed", str(exc)
    return "sent", ""


def _sandbox_send(subscription, payload: str) -> tuple[str, str]:
    """``_send`` under the sending sandbox: the push is stored (payload + endpoint host) instead of sent."""
    try:
        parsed = json.loads(payload)
    except ValueError:
        parsed = {"raw": payload}
    request = {**(parsed if isinstance(parsed, dict) else {"payload": parsed}), "endpoint": urlsplit(subscription.endpoint).hostname or ""}
    outcome = sandbox.capture(channel="web", provider="webpush", target=subscription.pk, title=request.get("title", ""), body=request.get("body", ""), request=request, user=getattr(subscription.member, "user", None), club=subscription.club)
    Outcome = sandbox.Outcome
    if outcome == Outcome.DELIVERED:
        return "sent", ""
    if outcome == Outcome.DEAD:
        return "dead", "sandbox: subscription gone (410)"
    if outcome in (Outcome.THROTTLED, Outcome.TIMEOUT):
        return "retry", "sandbox: throttled (429)" if outcome == Outcome.THROTTLED else "sandbox: timed out"
    return "failed", "sandbox: push service refused the request (400)"


def _deliver(subscription, payload: str, *, label, notification=None, hold_until=None) -> None:
    pk = subscription.pk
    user = getattr(subscription.member, "user", None)
    club = subscription.club
    if not is_allowed_push_endpoint(subscription.endpoint):
        # Stored before endpoints were validated on subscribe: never sent to, and removed
        # so it doesn't linger (the browser re-subscribes with a valid one when it can).
        subscription.delete()
        delivery_log.record(club=club, channel="web", target=pk, outcome="dead", notification=notification, user=user, error="unsupported push endpoint", provider="webpush")
        return
    if hold_until is not None:
        # Quiet hours: logged for delivery_log.retry_due to send once the window ends.
        try:
            held_payload = {key: value for key, value in json.loads(payload).items() if key in ("title", "body", "url")}
        except ValueError:
            held_payload = {}
        delivery_log.record(club=club, channel="web", target=pk, outcome="held", notification=notification, user=user, payload=held_payload, provider="webpush", hold_until=hold_until)
        return
    started = time.monotonic()
    outcome, error = _send(subscription, payload)
    duration_ms = delivery_log.elapsed_ms(started)
    if outcome == "dead":
        # The browser's push service says this registration is gone for good --
        # not a transient failure, so keeping it around would only mean retrying
        # a subscription that will never accept a push again.
        subscription.delete()
    elif outcome != "sent":
        logger.warning("Push send failed for %s: %s", label, error)
    log_payload = None
    if outcome == "retry":
        try:
            log_payload = {key: value for key, value in json.loads(payload).items() if key in ("title", "body", "url")}
        except ValueError:
            log_payload = {}
    delivery_log.record(club=club, channel="web", target=pk, outcome=outcome, notification=notification, user=user, error=error, payload=log_payload, provider="webpush", duration_ms=duration_ms)


def send_push_to_member(member, *, title: str, body: str, url: str = "/app/", notification=None) -> None:
    if not (settings.VAPID_PRIVATE_KEY or sandbox.enabled()):
        # Dev/no-config default -- see the settings.py comment next to VAPID_PRIVATE_KEY.
        return

    # str(): title/body often arrive as a gettext_lazy proxy (or the still-lazy
    # result of formatting one, e.g. `_("...") % {...}` -- Django's lazy strings
    # stay lazy through `%` too), not a plain str -- json.dumps can't serialize
    # that object directly and raises TypeError. Forcing it here, once, at the
    # one place this payload actually gets encoded, is simpler and safer than
    # auditing every caller across the app that builds a title/body.
    payload = json.dumps({"title": str(title), "body": str(body), "url": url})
    for subscription in models.PushSubscription.objects.filter(member=member).select_related("member__user", "club"):
        _deliver(subscription, payload, label=member, notification=notification)


def send_push_to_user(club, user, *, title: str, body: str, url: str = "/app/", category: str = "general") -> int:
    """Web push to every subscription this account's people in ``club`` hold --
    for pushes that aren't a stored Notification (e.g. live game updates,
    events.services.game_push). Returns how many subscriptions it was sent to."""
    if not (settings.VAPID_PRIVATE_KEY or sandbox.enabled()):
        return 0
    if not is_enabled(user, club, category, "push"):
        return 0  # push muted for this category: native and web alike
    if users_with_native_push([user.pk]):
        return 0  # the phone already got it (events.services.game_push sends native alongside this)
    payload = json.dumps({"title": str(title), "body": str(body), "url": url})
    subscriptions = list(models.PushSubscription.objects.filter(club=club, member__user=user).select_related("member__user", "club"))
    for subscription in subscriptions:
        _deliver(subscription, payload, label=subscription.member)
    return len(subscriptions)


def _notification_extras(notification) -> dict:
    """``{"target": {type, id, slug} | None, "url": deep link | None}`` for a Notification's push payload:
    the same target the native push carries, plus the /app/ page it maps to (what the service worker opens
    when it has no richer rule; older workers only read ``url``)."""
    target = notification_target(notification)
    url = None
    if target:
        kind, ident, slug = target["type"], target.get("id"), target.get("slug")
        if kind == "calendar":
            url = reverse("mobile:calendar")
        elif kind == "news" and slug:
            url = reverse("mobile:news_detail", kwargs={"slug": slug})
        elif kind == "event" and ident:
            url = reverse("mobile:event_detail", kwargs={"pk": ident})
        elif kind == "form_send" and ident:
            url = reverse("mobile:form_fill", kwargs={"pk": ident})
        elif kind == "order" and ident:
            url = reverse("mobile:shop_order_detail", kwargs={"pk": ident})
    return {"target": {key: str(value) for key, value in target.items() if value is not None} if target else None, "url": url}


def _unread_for(subscription, cache) -> int:
    """The subscribing account's unread badge count in that club (for the app-icon badge)."""
    key = (subscription.club_id, subscription.member_id)
    if key not in cache:
        cache[key] = unread_count(subscription.club, managed_people(subscription.member, subscription.club))
    return cache[key]


def send_push_for_notifications(notifications, url: str = "/app/") -> None:
    """Web push for a whole notifications.services.notify_members() fan-out at once.

    Delivered to the same accounts the email and the native push reach
    (notifications.services.recipient_user_ids: the member's own login plus every
    parent/guardian's), through their PushSubscriptions in the notification's club -- so a
    parent is told about their child's notification even though the child has no login. An
    account gets one push per distinct notification even when it concerns several of its
    children, and an account with an active native install is skipped: it gets this through
    the native channel (api.app.delivery)."""
    if not (settings.VAPID_PRIVATE_KEY or sandbox.enabled()) or not notifications:
        return

    recipients = {notification.pk: recipient_user_ids(notification) for notification in notifications}
    # Push is one switch: an account that muted the category gets neither native nor web.
    muted = muted_push_users(notifications, recipients)
    recipients = {notification.pk: recipients[notification.pk] - muted[preference_key(notification)] for notification in notifications}
    quiet = quiet_decisions(notifications, recipients)
    all_user_ids = set().union(*recipients.values())
    if not all_user_ids:
        return
    skip = users_with_native_push(all_user_ids)

    subscriptions_by_account = {}
    for subscription in models.PushSubscription.objects.filter(member__user_id__in=all_user_ids - skip, club_id__in={notification.club_id for notification in notifications}).select_related("member__user", "club"):
        subscriptions_by_account.setdefault((subscription.club_id, subscription.member.user_id), []).append(subscription)

    sent = set()
    unread_cache = {}
    for notification in notifications:
        dedupe_key = (notification.club_id, notification.content_type_id, notification.object_id, notification.title, notification.body)
        extras = _notification_extras(notification)
        for user_id in recipients[notification.pk] - skip:
            action, hold_until = quiet.get((notification.pk, user_id), ("send", None))
            if action == "drop":
                continue  # stale by the time quiet hours end (live games)
            for subscription in subscriptions_by_account.get((notification.club_id, user_id), []):
                if (subscription.pk, dedupe_key) in sent:
                    continue
                sent.add((subscription.pk, dedupe_key))
                # The subscriber's own language (Member.language); the stored English text when they have none.
                text = notification.text_for(subscription.member.language) if subscription.member.language else {"title": str(notification.title), "body": str(notification.body)}
                payload = json.dumps(
                    {
                        "title": text["title"],
                        "body": text["body"],
                        "url": extras["url"] or url,
                        "notification_id": str(notification.pk),
                        "target": extras["target"],
                        "unread": _unread_for(subscription, unread_cache),
                    }
                )
                _deliver(subscription, payload, label=subscription.member, notification=notification, hold_until=hold_until)
