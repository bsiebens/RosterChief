from django.dispatch import receiver

from notifications.signals import notifications_created

from .services.push import send_push_for_notifications


@receiver(notifications_created)
def push_new_notifications(sender, notifications, **kwargs):
    send_push_for_notifications(notifications)
