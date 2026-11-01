from django.apps import AppConfig


class FeaturesConfig(AppConfig):
    name = "features"

    def ready(self):
        from . import checks, signals  # noqa: F401
