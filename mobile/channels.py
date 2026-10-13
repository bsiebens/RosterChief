"""The Web Push channel (notifications.dispatch): the PWA's counterpart of the native push."""

from notifications.dispatch import Channel

from .services.push import send_push_for_notifications, send_push_to_user


class WebPushChannel(Channel):
    name = "web"

    def deliver_notifications(self, notifications, *, email=True):
        send_push_for_notifications(notifications)

    def deliver_to_user(self, *, club, user, texts, data, url, thread_id=None):
        # No language preference on accounts or subscriptions: web push goes out in English.
        title, body = texts("en")
        return send_push_to_user(club, user, title=title, body=body, url=url)
