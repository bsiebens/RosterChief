from django.urls import path

from . import views

app_name = "formbuilder"

urlpatterns = [
    path("answers/<uuid:pk>/file/", views.AnswerFileView.as_view(), name="answer_file"),
    path("<str:token>/", views.PublicFormFillView.as_view(), name="fill"),
]
