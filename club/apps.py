from django.apps import AppConfig


class ClubConfig(AppConfig):
    name = "club"

    def ready(self):
        from . import signals  # noqa: F401
