"""The Web Push channel (notifications.dispatch): the PWA's counterpart of the native push."""

import json

from django.core.exceptions import ValidationError

from notifications import delivery_log
from notifications.dispatch import Channel

from . import models
from .services.push import _send, is_allowed_push_endpoint, send_push_for_notifications, send_push_to_user


class WebPushChannel(Channel):
    name = "web"

    def deliver_notifications(self, notifications, *, email=True):
        send_push_for_notifications(notifications)

    def retry(self, delivery):
        try:
            subscription = models.PushSubscription.objects.filter(pk=delivery.target, club=delivery.club).select_related("member").first()
        except ValueError, ValidationError:
            subscription = None
        if subscription is None:
            delivery_log.mark_dead(delivery, "subscription no longer exists")
            return
        if not is_allowed_push_endpoint(subscription.endpoint):
            subscription.delete()
            delivery_log.mark_dead(delivery, "unsupported push endpoint")
            return
        outcome, error = _send(subscription, json.dumps(delivery.payload))
        if outcome == "sent":
            delivery_log.mark_sent(delivery)
        elif outcome == "dead":
            subscription.delete()
            delivery_log.mark_dead(delivery, error)
        else:
            delivery_log.mark_failed(delivery, error, retryable=outcome == "retry")

    def deliver_to_user(self, *, club, user, texts, data, url, thread_id=None):
        # No language preference on accounts or subscriptions: web push goes out in English.
        title, body = texts("en")
        return send_push_to_user(club, user, title=title, body=body, url=url)
