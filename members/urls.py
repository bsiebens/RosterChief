from django.urls import path

from . import views

app_name = "members"

urlpatterns = [
    path("claim/", views.ParentClaimView.as_view(), name="parent_claim"),
    path("my-family/", views.MyFamilyView.as_view(), name="my_family"),
]
