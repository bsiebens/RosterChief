"""Preserve password characters in allauth's JSON password forms."""

from allauth.headless import app_settings
from allauth.headless.account.inputs import ChangePasswordInput, LoginInput, ResetPasswordInput
from allauth.headless.account.views import ChangePasswordView, LoginView, ResetPasswordView
from allauth.headless.constants import Client
from django.forms import CharField
from django.urls import path


class ExactLoginInput(LoginInput):
    password = CharField(strip=False)


class ExactChangePasswordInput(ChangePasswordInput):
    current_password = CharField(required=False, strip=False)
    new_password = CharField(strip=False)


class ExactResetPasswordInput(ResetPasswordInput):
    password = CharField(strip=False)


class ExactLoginView(LoginView):
    input_class = ExactLoginInput


class ExactChangePasswordView(ChangePasswordView):
    input_class = ExactChangePasswordInput


class ExactResetPasswordView(ResetPasswordView):
    input_class = ExactResetPasswordInput


urlpatterns = [
    path(f"{client.value}/v1/{endpoint}", view.as_api_view(client=client))
    for client in (Client.APP, Client.BROWSER)
    if client in app_settings.CLIENTS
    for endpoint, view in (
        ("auth/login", ExactLoginView),
        ("auth/password/reset", ExactResetPasswordView),
        ("account/password/change", ExactChangePasswordView),
    )
]
