from django.urls import path

from . import views

app_name = "tournaments"

urlpatterns = [
    path("", views.TournamentListView.as_view(), name="list"),
    path("<slug:tournament_slug>/register/", views.TournamentRegistrationView.as_view(), name="register"),
    path("<slug:tournament_slug>/register/status/<str:token>/", views.TournamentRegistrationStatusView.as_view(), name="registration_status"),
    path("<slug:slug>/schedule/rows/", views.TournamentScheduleRowsView.as_view(), name="schedule_rows"),
    path("<slug:slug>/schedule/", views.TournamentScheduleView.as_view(), name="schedule"),
    path("<slug:slug>/standings/rows/", views.TournamentStandingsRowsView.as_view(), name="standings_rows"),
    path("<slug:slug>/standings/", views.TournamentStandingsView.as_view(), name="standings"),
    path("<slug:slug>/teams/<slug:team_slug>/", views.TournamentTeamScheduleView.as_view(), name="team_schedule"),
    path("<slug:slug>/", views.TournamentDetailView.as_view(), name="detail"),
]
