"""Native push for new notifications (API.md §9): the APNs counterpart of the member web
app's Web Push (mobile.services.push). Both are channels of notifications.dispatch, the one fan-out point, so a notification reaches every channel.

Recipients are *accounts*, resolved the way email already is (notifications.services.
recipient_emails): the member's own login plus every parent/guardian's in that club. One
install serves every club and child of its account, so an account gets one push per
distinct notification even when it concerns several of its children (a club-wide news
item makes a row per child). Pushes are pointers (API.md §9): the stored, already
localised title/body (the device's registered locale) and ``{kind, notification_id, club_slug, target}``; the app fetches the
rest over the API.
"""

import logging
import time
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone

from api.models import PushDevice
from club.models import Club, ClubMembership
from members.models import Member
from members.services.family import managed_people
from notifications import delivery_log
from notifications.dispatch import Channel
from notifications.models import Notification
from notifications.policy import policy_for
from notifications.preferences import is_enabled
from notifications.services import muted_push_users, preference_key, quiet_decisions, recipient_user_ids, unread_count

from .presenters import notification_target
from .push import get_gateway, is_retryable, push_configured

logger = logging.getLogger(__name__)


def payload_data(notification: Notification) -> dict:
    source = notification_target(notification)
    target = {key: str(value) for key, value in source.items() if value is not None} if source else None
    return {"kind": "notification", "notification_id": str(notification.pk), "club_slug": notification.club.slug, "target": target, "category": str(notification.category)}


def total_unread(user) -> int:
    """The app icon's badge: unread notifications across every club, for everyone this account
    manages. One install serves all of an account's clubs, so the count is account-wide."""
    me = Member.objects.filter(user=user).first()
    if me is None:
        return 0
    club_ids = ClubMembership.objects.filter(member=me).values_list("club_id", flat=True).distinct()
    return sum(unread_count(club, managed_people(me, club)) for club in Club.objects.filter(pk__in=club_ids))


PROVIDERS = {PushDevice.Platform.IOS: "apns", PushDevice.Platform.ANDROID: "fcm"}


def _publish_logged(gateway, device, *, club, notification, title, body, data, badge=None, thread_id=None, category="general", priority="normal", collapse_id=None, hold_until=None) -> str:
    """Hand one push to the gateway and log it. With ``hold_until`` (quiet hours) nothing is sent: a
    ``held`` delivery is logged for ``retry_due`` to release and ``held`` is returned. Returns ``sent``, ``dead`` (the token is gone; the
    device is disabled) or ``failed`` (an exception, retried later when it looks transient)."""
    provider = PROVIDERS.get(device.platform, "")
    if hold_until is not None:
        held_payload = {"title": title, "body": body, "data": data, "badge": badge, "thread_id": thread_id, "category": str(category), "priority": str(priority), "collapse_id": collapse_id}
        delivery_log.record(club=club, channel="native", target=device.installation_id, outcome="held", notification=notification, user=device.user, payload=held_payload, provider=provider, hold_until=hold_until)
        return "held"
    started = time.monotonic()
    try:
        alive = gateway.publish(device, title=title, body=body, data=data, badge=badge, thread_id=thread_id, policy=policy_for(category, priority), collapse_id=collapse_id)
    except Exception as error:
        duration_ms = delivery_log.elapsed_ms(started)
        logger.exception("app_push.publish_failed device=%s", device.pk)
        retryable = is_retryable(error)
        payload = {"title": title, "body": body, "data": data, "badge": badge, "thread_id": thread_id, "category": str(category), "priority": str(priority), "collapse_id": collapse_id} if retryable else None
        delivery_log.record(club=club, channel="native", target=device.installation_id, outcome="retry" if retryable else "failed", notification=notification, user=device.user, error=error, payload=payload, provider=provider, duration_ms=duration_ms)
        return "failed"
    duration_ms = delivery_log.elapsed_ms(started)
    if not alive:
        device.enabled = False
        device.save(update_fields=["enabled", "modified"])
        delivery_log.record(club=club, channel="native", target=device.installation_id, outcome="dead", notification=notification, user=device.user, provider=provider, duration_ms=duration_ms)
        return "dead"
    delivery_log.record(club=club, channel="native", target=device.installation_id, outcome="sent", notification=notification, user=device.user, provider=provider, duration_ms=duration_ms)
    return "sent"


def _held_until(quiet: dict, notification, user_id):
    decision = quiet.get((notification.pk, user_id))
    return decision[1] if decision and decision[0] == "hold" else None


def deliver(notification_ids) -> int:
    """Push the given notifications to their recipients' enabled devices. Returns how many
    pushes were handed to the gateway. A device whose token is dead is disabled."""
    notifications = list(Notification.objects.filter(pk__in=notification_ids).select_related("club", "member").order_by("created"))
    per_user: dict = {}
    recipients = {notification.pk: recipient_user_ids(notification) for notification in notifications}
    muted = muted_push_users(notifications, recipients)
    quiet = quiet_decisions(notifications, recipients)
    for notification in notifications:
        dedupe_key = (notification.club_id, notification.content_type_id, notification.object_id, notification.title, notification.body)
        for user_id in recipients[notification.pk] - muted[preference_key(notification)]:
            if quiet.get((notification.pk, user_id), ("send",))[0] == "drop":
                continue
            per_user.setdefault(user_id, {}).setdefault(dedupe_key, notification)
    if not per_user:
        return 0
    from events.services.game_push import language_for_locale  # deferred: events imports api.app lazily too

    active_users = set(get_user_model().objects.filter(pk__in=per_user, is_active=True).values_list("pk", flat=True))
    devices = PushDevice.objects.filter(user_id__in=active_users, enabled=True).select_related("user")
    gateway = get_gateway()
    badges = {}
    sent = 0
    for device in devices:
        if not gateway.supports(device):
            continue
        if device.user_id not in badges:
            badges[device.user_id] = total_unread(device.user)
        for notification in per_user[device.user_id].values():
            # In the language the app registered with (PushDevice.locale), like game pushes.
            text = notification.text_for(language_for_locale(device.locale))
            outcome = _publish_logged(
                gateway,
                device,
                club=notification.club,
                notification=notification,
                title=str(text["title"]),
                body=str(text["body"]),
                data=payload_data(notification),
                badge=badges[device.user_id],
                thread_id=notification.club.slug,
                category=notification.category,
                priority=notification.priority,
                hold_until=_held_until(quiet, notification, device.user_id),
            )
            if outcome == "dead":
                break
            if outcome == "sent":
                sent += 1
    return sent


def deliver_to_user(user, *, texts, data, thread_id=None, club=None, category: str = "general") -> int:
    """One message, no inbox row (a live game update), to every enabled native device of ``user``;
    ``texts(language)`` -> ``(title, body)`` and each device gets its own locale's. A device whose
    token is dead is disabled. Returns how many devices it was handed to."""
    from events.services.game_push import language_for_locale  # deferred: events imports api.app lazily too

    if not push_configured():
        return 0
    if not is_enabled(user, club, category, "push"):
        return 0
    gateway = get_gateway()
    sent = 0
    for device in PushDevice.objects.filter(user=user, enabled=True):
        if not gateway.supports(device):
            continue
        try:
            title, body = texts(language_for_locale(device.locale))
        except Exception:
            logger.exception("app_push.publish_failed device=%s", device.pk)
            continue
        if _publish_logged(gateway, device, club=club, notification=None, title=title, body=body, data=data, thread_id=thread_id, category=category, collapse_id=thread_id) == "sent":
            sent += 1
    return sent


class NativePushChannel(Channel):
    """APNs (iOS) and FCM (Android), through api.app.push's gateway."""

    name = "native"

    def deliver_notifications(self, notifications, *, email=True):
        if push_configured():
            deliver([notification.pk for notification in notifications])

    def deliver_to_user(self, *, club, user, texts, data, url, thread_id=None, category="general"):
        return deliver_to_user(user, texts=texts, data=data, thread_id=thread_id, club=club, category=category)

    def retry(self, delivery):
        device = PushDevice.objects.filter(installation_id=delivery.target, user_id=delivery.user_id).select_related("user").first()
        if device is None or not device.enabled:
            return delivery_log.mark_dead(delivery, "device gone or disabled")
        gateway = get_gateway()
        if not gateway.supports(device):
            return delivery_log.mark_failed(delivery, "push gateway does not support this device", retryable=False)
        payload = delivery.payload
        policy = policy_for(payload.get("category", "general"), payload.get("priority", "normal"))
        if timezone.now() > delivery.created + timedelta(seconds=policy.ttl_seconds):
            return delivery_log.mark_failed(delivery, "expired", retryable=False)
        provider = PROVIDERS.get(device.platform, "")
        started = time.monotonic()
        try:
            alive = gateway.publish(device, title=payload["title"], body=payload["body"], data=payload["data"], badge=payload.get("badge"), thread_id=payload.get("thread_id"), policy=policy, collapse_id=payload.get("collapse_id"))
        except Exception as error:
            logger.exception("app_push.retry_failed device=%s", device.pk)
            return delivery_log.mark_failed(delivery, error, retryable=is_retryable(error), provider=provider, duration_ms=delivery_log.elapsed_ms(started))
        duration_ms = delivery_log.elapsed_ms(started)
        if alive:
            return delivery_log.mark_sent(delivery, provider=provider, duration_ms=duration_ms)
        device.enabled = False
        device.save(update_fields=["enabled", "modified"])
        return delivery_log.mark_dead(delivery, "push token no longer valid", provider=provider, duration_ms=duration_ms)
