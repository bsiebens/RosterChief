from django.apps import AppConfig


class AuthenticationConfig(AppConfig):
    name = "authentication"

    def ready(self):
        from . import checks  # noqa: F401  (registers the MFA_ENCRYPTION_KEY system check)
