from django.urls import path

from . import views

app_name = "tournaments"

urlpatterns = [
    path("<slug:tournament_slug>/register/", views.TournamentRegistrationView.as_view(), name="register"),
    path("<slug:tournament_slug>/register/status/<str:token>/", views.TournamentRegistrationStatusView.as_view(), name="registration_status"),
]
