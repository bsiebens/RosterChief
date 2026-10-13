from django.apps import AppConfig


class NotificationsConfig(AppConfig):
    name = "notifications"

    def ready(self):
        from .channels import EmailChannel
        from .dispatch import register_channel

        register_channel(EmailChannel())
