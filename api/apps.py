from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class ApiConfig(AppConfig):
    """Both APIs: the public, read-only club-website API at /api/v1/ (``api.public``) and the
    authenticated native-app API at /api/app/v1/ (``api.app``, API.md). They stay separate
    NinjaAPIs with their own auth and schema; they share the app label, models and migrations."""

    name = "api"
    verbose_name = _("API")

    def ready(self):
        from notifications.dispatch import register_channel

        from .app.delivery import NativePushChannel

        register_channel(NativePushChannel())
