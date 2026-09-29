"""Native-app sessions live as long as the app's refresh token (API.md §3.3).

allauth's JWT strategy keeps the list of valid refresh tokens *inside* the server session, so a
refresh token is dead as soon as its session expires -- whatever the token's own ``exp`` says.
Django's default session lives two weeks from its last save, which would sign an app out after
two idle weeks. Logins through the headless "app" client therefore get a session of
``APP_SESSION_LIFETIME`` seconds. ``set_expiry`` stores a relative lifetime, and every
``/tokens/refresh`` re-saves the session, so an app in regular use stays signed in; web logins
are untouched.
"""

from allauth.headless.constants import Client
from django.conf import settings
from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver


def is_app_client_request(request) -> bool:
    headless = getattr(getattr(request, "allauth", None), "headless", None)
    return getattr(headless, "client", None) == Client.APP


@receiver(user_logged_in, dispatch_uid="authentication.app_session_lifetime")
def extend_app_session(sender, request, user, **kwargs):
    if request is not None and is_app_client_request(request):
        request.session.set_expiry(settings.APP_SESSION_LIFETIME)
