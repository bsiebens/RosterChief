from django.apps import AppConfig


class MobileConfig(AppConfig):
    name = "mobile"

    def ready(self):
        from . import signals  # noqa: F401
