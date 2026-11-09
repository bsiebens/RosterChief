from django.apps import AppConfig


class ShopConfig(AppConfig):
    name = "shop"

    def ready(self):
        from . import settings_models  # noqa: F401  (registers ShopSettings)
