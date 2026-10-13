from django.apps import AppConfig


class MobileConfig(AppConfig):
    name = "mobile"

    def ready(self):
        from notifications.dispatch import register_channel

        from .channels import WebPushChannel

        register_channel(WebPushChannel())
