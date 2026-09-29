from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class AppApiConfig(AppConfig):
    """The authenticated native-app API at /api/app/v1/ (API.md). Separate from the
    public, read-only ``api`` app on purpose: its own NinjaAPI, auth and schema."""

    name = "appapi"
    verbose_name = _("app API")

    def ready(self):
        from notifications.signals import notifications_created

        from .delivery import on_notifications_created

        notifications_created.connect(on_notifications_created, dispatch_uid="appapi-native-push")
