from django.urls import path

from . import views

app_name = "controlpanel"

urlpatterns = [
    path("", views.DashboardView.as_view(), name="dashboard"),
    path("clubs/", views.ClubListView.as_view(), name="club_list"),
    path("clubs/new/", views.ClubCreateView.as_view(), name="club_create"),
    path("clubs/<uuid:pk>/", views.ClubDetailView.as_view(), name="club_detail"),
    path("clubs/<uuid:pk>/edit/", views.ClubUpdateView.as_view(), name="club_update"),
    path("clubs/<uuid:pk>/archive/", views.ClubArchiveView.as_view(), name="club_archive"),
    path("clubs/<uuid:pk>/restore/", views.ClubRestoreView.as_view(), name="club_restore"),
    path("clubs/<uuid:pk>/admins/add/", views.ClubAdminAddView.as_view(), name="club_admin_add"),
    path("clubs/<uuid:pk>/admins/<uuid:role_pk>/remove/", views.ClubAdminRemoveView.as_view(), name="club_admin_remove"),
]
