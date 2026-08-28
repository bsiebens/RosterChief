from django.urls import path

from . import views

app_name = "registration"

urlpatterns = [
    path("", views.RegistrationView.as_view(), name="register"),
    path("status/<str:token>/", views.RegistrationStatusView.as_view(), name="status"),
]
