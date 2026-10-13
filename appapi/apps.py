from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class AppApiConfig(AppConfig):
    """The authenticated native-app API at /api/app/v1/ (API.md). Separate from the
    public, read-only ``api`` app on purpose: its own NinjaAPI, auth and schema."""

    name = "appapi"
    verbose_name = _("app API")

    def ready(self):
        from notifications.dispatch import register_channel

        from .delivery import NativePushChannel

        register_channel(NativePushChannel())
