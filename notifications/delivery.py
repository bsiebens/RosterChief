"""Delivering one notification to one account: pick the targets (native devices, web subscriptions, email), apply the account's
preferences and quiet hours, hand each to its gateway and keep the delivery log (``NotificationDelivery``) honest.

Log rules: one row per target; statuses sent / retrying / held / failed / dead. A transient failure becomes RETRYING with the
payload kept and ``next_attempt_at`` from ``PushSettings.retry_delays_minutes``; ``retry_due`` (Celery) sends it again. A push inside
quiet hours is HELD until the window ends (dropped for categories that must not arrive late; critical ignores quiet hours). A dead
token disables the device. A delivery is given up once its category's time to live has passed."""

import logging
import time
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from . import preferences
from .gateways import DEAD, FAILED, RETRY, SENT, GatewayResult, Message, Target, get_gateway
from .models import Channel, NotificationDelivery, Priority, PushDevice, PushSettings, WebPushSubscription

logger = logging.getLogger(__name__)

Status = NotificationDelivery.Status
DeliveryChannel = NotificationDelivery.DeliveryChannel
LEASE = timedelta(minutes=5)  # a due delivery is leased while it is being worked on; a crashed worker's lease simply runs out
RETRY_BATCH = 200


@dataclass
class DeliveryResult:
    channel: str
    provider: str
    target: str
    target_masked: str
    status: str
    error: str = ""
    duration_ms: int | None = None
    delivery_id: object = None
    sandboxed: bool = False


def mask_target(target: str) -> str:
    """Emails are personal data (``a***@example.com``); installation ids and endpoints are truncated."""
    if "@" in target:
        local, _sep, domain = target.rpartition("@")
        return f"{local[:1]}***@{domain}"
    return target if len(target) <= 24 else target[:24] + "…"


def _provider_enabled(provider: str, ps: PushSettings) -> bool:
    return {"apns": ps.apns_enabled, "fcm": ps.fcm_enabled, "webpush": ps.webpush_enabled}.get(provider, True)


def _delays(ps: PushSettings) -> list[int]:
    delays = ps.retry_delays_minutes
    return [int(d) for d in delays] if isinstance(delays, list) and delays else list(PushSettings.DEFAULT_RETRY_DELAYS)


def device_target(device: PushDevice) -> Target:
    return Target(DeliveryChannel.NATIVE, device.provider, device.installation_id, device)


def web_target(subscription: WebPushSubscription) -> Target:
    return Target(DeliveryChannel.WEB, "webpush", subscription.endpoint, subscription)


def collect_targets(user, club, only, ps: PushSettings) -> list[Target]:
    """Push targets: ``only`` (one device or subscription) or every enabled device and the web subscriptions that fit ``club``."""
    if only is not None:
        target = device_target(only) if isinstance(only, PushDevice) else web_target(only)
        return [target] if _provider_enabled(target.provider, ps) else []
    targets = [device_target(device) for device in PushDevice.objects.filter(user=user, enabled=True) if _provider_enabled(device.provider, ps)]
    if ps.webpush_enabled:
        subscriptions = WebPushSubscription.objects.filter(user=user)
        targets += [web_target(sub) for sub in subscriptions if club is None or sub.club_id in (None, club.pk)]
    return targets


# --- sending one target -----------------------------------------------------------------------------------------------------


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.monotonic() - started) * 1000))


def _send(target: Target, message: Message, user, club, force_sandbox: bool) -> tuple[GatewayResult, int]:
    started = time.monotonic()
    try:
        result = get_gateway(target.provider, force_sandbox=force_sandbox).send(target, message, user=user, club=club)
    except Exception as error:  # a gateway should never raise; if it does, the other targets still get theirs
        logger.exception("delivery.gateway_failed provider=%s", target.provider)
        result = GatewayResult(FAILED, f"{type(error).__name__}: {error}"[:500])
    return result, _elapsed_ms(started)


def _handle_dead(target: Target, result: GatewayResult, now) -> None:
    """The provider says the target is gone for good. The fake provider's 'dead' only acts on real data while DEBUG is on."""
    if result.sandboxed and not settings.DEBUG:
        return
    if isinstance(target.obj, PushDevice):
        PushDevice.objects.filter(pk=target.obj.pk).update(enabled=False, disabled_at=now, modified=now)
        target.obj.enabled, target.obj.disabled_at = False, now
    elif isinstance(target.obj, WebPushSubscription):
        target.obj.delete()


def _apply(delivery: NotificationDelivery, result: GatewayResult, now, delays: list[int]) -> None:
    """Move ``delivery`` to its next state. ``delivery.attempts`` is the number of sends made so far."""
    delivery.last_error = ""
    delivery.next_attempt_at = None
    if result.outcome == SENT:
        delivery.status, delivery.payload = Status.SENT, None
        return
    delivery.last_error = result.error[:500]
    if result.outcome == RETRY and delivery.attempts <= len(delays):
        delivery.status = Status.RETRYING
        delivery.next_attempt_at = now + timedelta(minutes=delays[delivery.attempts - 1])
        return
    delivery.status = Status.DEAD if result.outcome == DEAD else Status.FAILED
    delivery.payload = None


def _result(delivery: NotificationDelivery, target: Target, sandboxed: bool = False) -> DeliveryResult:
    return DeliveryResult(target.channel, target.provider, target.key, mask_target(target.key), delivery.status, delivery.last_error, delivery.duration_ms, delivery.pk, sandboxed)


def _already_delivered(user, club, target_key: str, notification_id) -> bool:
    """At-least-once outbox: a repeated run must not push the same notification to the same target twice. Only a notification id
    identifies "the same notification" (the log does not keep the body, and two different messages can share a title)."""
    if not notification_id:
        return False
    return NotificationDelivery.objects.filter(user=user, club=club, target=target_key, notification_id=notification_id).exists()


def _stored(message: Message, force_sandbox: bool) -> dict:
    return {**message.to_dict(), "sandbox": force_sandbox}


def _first_send(target: Target, message: Message, user, club, *, force_sandbox: bool, ps: PushSettings) -> DeliveryResult:
    result, duration = _send(target, message, user, club, force_sandbox)
    now = timezone.now()
    delivery = NotificationDelivery(
        user=user,
        club=club,
        notification_id=message.notification_id,
        category=message.category,
        title=message.title[:255],
        channel=target.channel,
        provider=target.provider,
        target=target.key[:500],
        duration_ms=duration,
        attempts=1,
        payload=_stored(message, force_sandbox),
    )
    _apply(delivery, result, now, _delays(ps))
    delivery.save()
    if result.outcome == DEAD:
        _handle_dead(target, result, now)
    return _result(delivery, target, result.sandboxed)


def _hold(target: Target, message: Message, user, club, until, *, force_sandbox: bool) -> DeliveryResult:
    delivery = NotificationDelivery.objects.create(
        user=user,
        club=club,
        notification_id=message.notification_id,
        category=message.category,
        title=message.title[:255],
        channel=target.channel,
        provider=target.provider,
        target=target.key[:500],
        status=Status.HELD,
        attempts=0,
        next_attempt_at=until,
        payload=_stored(message, force_sandbox),
    )
    return _result(delivery, target)


def deliver_to_user(
    user,
    *,
    title: str,
    body: str,
    category: str = "general",
    priority: str = "normal",
    data: dict | None = None,
    club=None,
    notification_id=None,
    send_email: bool = False,
    only=None,
    force_sandbox: bool = False,
    respect_preferences: bool = True,
    now=None,
) -> list[DeliveryResult]:
    """Deliver one notification to every target of ``user``. Returns one result per target tried or held."""
    if not getattr(user, "is_active", True):
        return []
    now = now or timezone.now()
    ps = PushSettings.get()
    message = Message(title=title, body=body, data=data or {}, category=str(category), priority=str(priority), notification_id=str(notification_id) if notification_id else None, club_slug=club.slug if club else None)
    message.collapse_id = (data or {}).get("collapse_id") or (f"{category}:{club.slug}" if club else str(category))
    policy = message.policy
    results: list[DeliveryResult] = []

    push_wanted = not respect_preferences or preferences.is_enabled(user, category, Channel.PUSH, club)
    if push_wanted:
        hold_until = None
        if respect_preferences and priority != Priority.CRITICAL:
            active, until = preferences.quiet_state(user, now)
            if active and not policy.hold_in_quiet_hours:
                push_wanted = False  # would be stale by the time the window ends
            elif active:
                hold_until = until
    if push_wanted:
        for target in collect_targets(user, club, only, ps):
            if respect_preferences and _already_delivered(user, club, target.key, message.notification_id):
                continue
            if hold_until is not None:
                results.append(_hold(target, message, user, club, hold_until, force_sandbox=force_sandbox))
            else:
                results.append(_first_send(target, message, user, club, force_sandbox=force_sandbox, ps=ps))

    if send_email and user.email and (not respect_preferences or preferences.should_email(user, category, club, now)):
        target = Target(DeliveryChannel.EMAIL, "email", user.email)
        if not (respect_preferences and _already_delivered(user, club, target.key, message.notification_id)):
            results.append(_first_send(target, message, user, club, force_sandbox=force_sandbox, ps=ps))
    return results


# --- retrying and releasing ---------------------------------------------------------------------------------------------------


def _claim_due(now, limit: int) -> list:
    """Lease the due RETRYING / HELD rows (``skip_locked``, so concurrent workers never share a row) and return their ids."""
    with transaction.atomic():
        ids = list(NotificationDelivery.objects.select_for_update(skip_locked=True).filter(status__in=[Status.RETRYING, Status.HELD], next_attempt_at__lte=now).order_by("next_attempt_at").values_list("pk", flat=True)[:limit])
        NotificationDelivery.objects.filter(pk__in=ids).update(next_attempt_at=now + LEASE)
    return ids


def _finish(delivery: NotificationDelivery, status: str, error: str) -> None:
    delivery.status, delivery.last_error, delivery.next_attempt_at, delivery.payload = status, error[:500], None, None
    delivery.save()


def _resolve_target(delivery: NotificationDelivery) -> Target | None:
    if delivery.channel == DeliveryChannel.NATIVE:
        device = PushDevice.objects.filter(installation_id=delivery.target, user_id=delivery.user_id).first()
        return device_target(device) if device and device.enabled else None
    if delivery.channel == DeliveryChannel.WEB:
        sub = WebPushSubscription.objects.filter(endpoint=delivery.target, user_id=delivery.user_id).first()
        return web_target(sub) if sub else None
    if delivery.user is not None and delivery.user.email:
        return Target(DeliveryChannel.EMAIL, "email", delivery.target)
    return None


def retry_one(delivery: NotificationDelivery, now, ps: PushSettings | None = None) -> str:
    """Send a due RETRYING row again, or release a HELD one. Returns the new status."""
    ps = ps or PushSettings.get()
    held = delivery.status == Status.HELD
    stored = delivery.payload or {}
    message = Message.from_dict(stored)
    if delivery.user is None or not delivery.user.is_active:
        _finish(delivery, Status.FAILED, "account gone or inactive")
        return delivery.status
    if now > delivery.created + timedelta(seconds=message.policy.ttl_seconds):
        _finish(delivery, Status.FAILED, "expired in quiet hours" if held else "expired")
        return delivery.status
    target = _resolve_target(delivery)
    if target is None:
        _finish(delivery, Status.DEAD, "device gone or disabled" if delivery.channel != DeliveryChannel.EMAIL else "no email address")
        return delivery.status
    if not _provider_enabled(target.provider, ps):
        _finish(delivery, Status.FAILED, f"{target.provider} is disabled")
        return delivery.status
    force_sandbox = bool(stored.get("sandbox"))
    result, duration = _send(target, message, delivery.user, delivery.club, force_sandbox)
    delivery.attempts += 1
    delivery.duration_ms = duration
    _apply(delivery, result, now, _delays(ps))
    delivery.save()
    if result.outcome == DEAD:
        _handle_dead(target, result, now)
    return delivery.status


def retry_due(now=None, limit: int = RETRY_BATCH) -> int:
    """Retry every due RETRYING delivery and release every HELD one whose quiet hours ended. Returns how many were attempted."""
    now = now or timezone.now()
    ps = PushSettings.get()
    attempted = 0
    for delivery_id in _claim_due(now, limit):
        delivery = NotificationDelivery.objects.select_related("user", "club").filter(pk=delivery_id).first()
        if delivery is None or delivery.status not in (Status.RETRYING, Status.HELD):
            continue
        attempted += 1
        try:
            retry_one(delivery, now, ps)
        except Exception as error:
            logger.exception("delivery.retry_failed delivery=%s", delivery_id)
            delivery.refresh_from_db()
            delivery.attempts += 1
            _apply(delivery, GatewayResult(RETRY, f"{type(error).__name__}: {error}"), timezone.now(), _delays(ps))
            delivery.save()
    return attempted


def get_user(user_id):
    return get_user_model().objects.filter(pk=user_id).first()
