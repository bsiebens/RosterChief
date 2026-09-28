from django.apps import AppConfig


class RosterchiefConfig(AppConfig):
    name = "rosterchief"

    def ready(self):
        from . import checks  # noqa: F401  (registers the SECRET_KEY system check)
