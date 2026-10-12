"""Native push for new notifications (API.md §9): the SNS counterpart of the member web
app's Web Push (mobile.services.push). Both hang off notifications.signals.
notifications_created, the one fan-out point, so a notification reaches every channel.

Recipients are *accounts*, resolved the way email already is (notifications.services.
recipient_emails): the member's own login plus every parent/guardian's in that club. One
install serves every club and child of its account, so an account gets one push per
distinct notification even when it concerns several of its children (a club-wide news
item makes a row per child). Pushes are pointers (API.md §9): the stored, already
localised title/body (the device's registered locale) and ``{kind, notification_id, club_slug, target}``; the app fetches the
rest over the API.
"""

import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction

from club.models import Club, ClubMembership
from members.models import Member
from members.services.family import managed_people
from notifications.models import Notification
from notifications.services import recipient_user_ids, unread_count
from rosterchief.concurrency import run_in_background

from .models import PushDevice
from .presenters import notification_target
from .push import get_gateway

logger = logging.getLogger(__name__)


def push_configured() -> bool:
    return bool(getattr(settings, "APP_PUSH_GATEWAY", "") or settings.APP_PUSH_SNS_APNS_ARN or settings.APP_PUSH_SNS_APNS_SANDBOX_ARN or settings.APP_PUSH_SNS_FCM_ARN)


def payload_data(notification: Notification) -> dict:
    source = notification_target(notification)
    target = {key: str(value) for key, value in source.items() if value is not None} if source else None
    return {"kind": "notification", "notification_id": str(notification.pk), "club_slug": notification.club.slug, "target": target}


def total_unread(user) -> int:
    """The app icon's badge: unread notifications across every club, for everyone this account
    manages. One install serves all of an account's clubs, so the count is account-wide."""
    me = Member.objects.filter(user=user).first()
    if me is None:
        return 0
    club_ids = ClubMembership.objects.filter(member=me).values_list("club_id", flat=True).distinct()
    return sum(unread_count(club, managed_people(me, club)) for club in Club.objects.filter(pk__in=club_ids))


def deliver(notification_ids) -> int:
    """Push the given notifications to their recipients' enabled devices. Returns how many
    pushes were handed to the gateway. A device whose token is dead is disabled."""
    notifications = list(Notification.objects.filter(pk__in=notification_ids).select_related("club", "member").order_by("created"))
    per_user: dict = {}
    for notification in notifications:
        dedupe_key = (notification.club_id, notification.content_type_id, notification.object_id, notification.title, notification.body)
        for user_id in recipient_user_ids(notification):
            per_user.setdefault(user_id, {}).setdefault(dedupe_key, notification)
    if not per_user:
        return 0
    from events.services.game_push import language_for_locale  # deferred: events imports appapi lazily too

    active_users = set(get_user_model().objects.filter(pk__in=per_user, is_active=True).values_list("pk", flat=True))
    devices = PushDevice.objects.filter(user_id__in=active_users, enabled=True).exclude(endpoint_arn="").select_related("user")
    gateway = get_gateway()
    badges = {}
    sent = 0
    for device in devices:
        if device.user_id not in badges:
            badges[device.user_id] = total_unread(device.user)
        for notification in per_user[device.user_id].values():
            try:
                # In the language the app registered with (PushDevice.locale), like game pushes.
                text = notification.text_for(language_for_locale(device.locale))
                alive = gateway.publish(device, title=str(text["title"]), body=str(text["body"]), data=payload_data(notification), badge=badges[device.user_id])
            except Exception:
                # Never fatal for the rest of the fan-out (throttling, a network blip).
                logger.exception("app_push.publish_failed device=%s", device.pk)
                continue
            if not alive:
                device.enabled = False
                device.save(update_fields=["enabled", "modified"])
                break
            sent += 1
    return sent


def on_notifications_created(sender, notifications, **kwargs) -> None:
    """Receiver for notifications_created: after the transaction commits (the rows must
    be visible to the worker thread), off the request thread."""
    if not notifications or not push_configured():
        return
    ids = [notification.pk for notification in notifications]
    transaction.on_commit(lambda: run_in_background(deliver, ids))
