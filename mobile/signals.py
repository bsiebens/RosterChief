from django.db.models.signals import post_save
from django.dispatch import receiver

from notifications.models import Notification

from .services.push import send_push_to_member


@receiver(post_save, sender=Notification)
def push_new_notification(sender, instance, created, **kwargs):
    if not created:
        return
    send_push_to_member(instance.member, title=instance.title, body=instance.body)
