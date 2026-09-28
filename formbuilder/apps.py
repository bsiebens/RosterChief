from django.apps import AppConfig


class FormbuilderConfig(AppConfig):
    name = "formbuilder"

    def ready(self):
        from . import signals  # noqa: F401
