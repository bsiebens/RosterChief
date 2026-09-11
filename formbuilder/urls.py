from django.urls import path

from . import views

app_name = "formbuilder"

urlpatterns = [
    path("<str:token>/", views.PublicFormFillView.as_view(), name="fill"),
]
