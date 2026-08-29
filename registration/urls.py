from django.urls import path

from . import views

app_name = "registration"

urlpatterns = [
    path("", views.RegistrationView.as_view(), name="register"),
    path("status/<str:token>/", views.RegistrationStatusView.as_view(), name="status"),
    path("status/<str:token>/invoice/", views.RegistrationInvoiceView.as_view(), name="invoice"),
    path("status/<str:token>/<uuid:membership_pk>/cancel/", views.RegistrationCancelView.as_view(), name="cancel"),
]
