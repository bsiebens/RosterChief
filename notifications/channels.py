"""The email channel (notifications.dispatch): plain notification mail to the member and their
guardians, honouring each account's ``email_notifications`` preference. Mail with an attachment
never comes through here (notify_members sends it inline)."""

from .dispatch import Channel
from .services import send_notification_emails


class EmailChannel(Channel):
    name = "email"

    def deliver_notifications(self, notifications, *, email=True):
        if email:
            send_notification_emails(notifications)
