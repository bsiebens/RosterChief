from django.apps import AppConfig


class ControlpanelConfig(AppConfig):
    name = "controlpanel"

    def ready(self):
        from . import signals  # noqa: F401
