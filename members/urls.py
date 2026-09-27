from django.urls import path

from . import views

app_name = "members"

urlpatterns = [
    path("invite/<str:token>/", views.FamilyInviteAcceptView.as_view(), name="family_invite_accept"),
]
