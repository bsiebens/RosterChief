from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class ApiConfig(AppConfig):
    """The platform's HTTP API, one NinjaAPI at /api/v1/ (api/v1.py): public club routes
    (``api.public``), bearer-token routes for the member/coach apps (``api.protected``) and the
    management panel (``api.panel``), on shared building blocks (``api.core``). API.md."""

    name = "api"
    verbose_name = _("API")

    def ready(self):
        from notifications.dispatch import register_channel

        from .protected.delivery import NativePushChannel

        register_channel(NativePushChannel())
