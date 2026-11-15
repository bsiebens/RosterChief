"""The public interface of the push engine: notify (queued), send now, and the device registry."""

import logging

from django.db import transaction
from django.utils import timezone

from . import outbox
from .delivery import DeliveryResult, deliver_to_user
from .models import PushDevice, WebPushSubscription

logger = logging.getLogger(__name__)

__all__ = ["DeliveryResult", "notify_user", "register_device", "remove_device", "send_now", "subscribe_web", "unsubscribe_web"]


def _kick() -> None:
    """After commit, ask a worker to process the outbox. Beat runs it every minute too, so a broker hiccup only delays."""
    from .tasks import process_outbox

    try:
        process_outbox.delay()
    except Exception:
        logger.exception("notifications.kick_failed")


def notify_user(user, *, title, body, category="general", priority="normal", data=None, club=None, notification_id=None, send_email=False):
    """Queue a notification for ``user``. The outbox entry is written in the caller's transaction; a Celery task delivers it after commit."""
    entry = outbox.enqueue(
        {
            "user_id": str(user.pk),
            "title": title,
            "body": body,
            "category": str(category),
            "priority": str(priority),
            "data": data or {},
            "club_id": str(club.pk) if club else None,
            "notification_id": str(notification_id) if notification_id else None,
            "send_email": bool(send_email),
        }
    )
    transaction.on_commit(_kick)
    return entry


def send_now(user, *, title, body, category="general", priority="normal", data=None, club=None, only=None, force_sandbox=False, respect_preferences=True) -> list[DeliveryResult]:
    """Deliver synchronously and return one result per target. ``only`` limits it to one device or web subscription."""
    return deliver_to_user(user, title=title, body=body, category=category, priority=priority, data=data, club=club, only=only, force_sandbox=force_sandbox, respect_preferences=respect_preferences)


# --- device registry --------------------------------------------------------------------------------------------------------


def register_device(user, *, installation_id, platform, environment, push_token, app_version="", locale="", push_to_start_token="") -> PushDevice:
    """Upsert by installation id: re-enables the device, keeps it with the current account, and disables other rows holding the same token."""
    if platform not in PushDevice.Platform.values:
        raise ValueError(f"unknown platform: {platform}")
    if environment not in PushDevice.Environment.values:
        raise ValueError(f"unknown environment: {environment}")
    if not push_token:
        raise ValueError("push_token is required")
    with transaction.atomic():
        device, _created = PushDevice.objects.update_or_create(
            installation_id=installation_id,
            defaults={
                "user": user,
                "platform": platform,
                "environment": environment,
                "push_token": push_token,
                "push_to_start_token": push_to_start_token,
                "app_version": app_version,
                "locale": locale,
                "enabled": True,
                "disabled_at": None,
            },
        )
        now = timezone.now()
        PushDevice.objects.filter(push_token=push_token, platform=platform, enabled=True).exclude(pk=device.pk).update(enabled=False, disabled_at=now, modified=now)
    return device


def remove_device(user, installation_id) -> bool:
    count, _deleted = PushDevice.objects.filter(user=user, installation_id=installation_id).delete()
    return bool(count)


def subscribe_web(user, *, club, endpoint, p256dh, auth, user_agent="", allow_reassignment=True) -> WebPushSubscription:
    """Trusted callers may move endpoints. Browser clients may update only their own registration."""
    identity = {"endpoint": endpoint} if allow_reassignment else {"endpoint": endpoint, "user": user, "club": club}
    subscription, _created = WebPushSubscription.objects.update_or_create(
        **identity, defaults={"user": user, "club": club, "p256dh": p256dh, "auth": auth, "user_agent": user_agent[:500]}
    )
    return subscription


def unsubscribe_web(user, endpoint) -> bool:
    count, _deleted = WebPushSubscription.objects.filter(user=user, endpoint=endpoint).delete()
    return bool(count)
