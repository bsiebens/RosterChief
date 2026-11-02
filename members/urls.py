from django.urls import path

from . import views

app_name = "members"

urlpatterns = [
    path("members/<uuid:pk>/photo/", views.MemberPhotoView.as_view(), name="photo"),
    path("members/<uuid:pk>/player-photo/", views.MemberPhotoView.as_view(field="player_photo"), name="player_photo"),
    path("members/<uuid:pk>/profile-photo/", views.MemberPhotoView.as_view(field="profile_photo"), name="profile_photo"),
]
