"""The email channel (notifications.dispatch): plain notification mail to the member and their
guardians, honouring each account's ``email_notifications`` preference. Mail with an attachment
never comes through here (notify_members sends it inline)."""

import time

from . import delivery_log
from .dispatch import Channel
from .models import Notification
from .services import MailboxRefused, resend_email, send_notification_emails


class EmailChannel(Channel):
    name = "email"

    def deliver_notifications(self, notifications, *, email=True):
        if email:
            send_notification_emails(notifications)

    def retry(self, delivery):
        notification = Notification.objects.filter(pk=delivery.payload.get("notification")).select_related("club", "member").first()
        email = delivery.payload.get("email") or delivery.target
        if notification is None:
            delivery_log.mark_dead(delivery, "notification no longer exists")
            return
        started = time.monotonic()
        try:
            resend_email(notification, email)
        except MailboxRefused as error:
            delivery_log.mark_failed(delivery, error, retryable=False, provider="email", duration_ms=delivery_log.elapsed_ms(started))
            return
        except OSError as error:
            delivery_log.mark_failed(delivery, error, retryable=True, provider="email", duration_ms=delivery_log.elapsed_ms(started))
            return
        delivery_log.mark_sent(delivery, provider="email", duration_ms=delivery_log.elapsed_ms(started))
